"""Transform normalized CTM documents → MatchMiner clinical + genomic dicts.

MatchMiner expects two MongoDB collections:
  clinical  — one document per patient sample
  genomic   — one document per alteration, linked via SAMPLE_ID + CLINICAL_ID

This module is pure (no I/O). Callers handle MongoDB writes.
"""
import re
from datetime import datetime, timezone
from typing import Any

from ..schemas.raw.normalized import Finding, Patient

# ── variant_type → VARIANT_CATEGORY ──────────────────────────────────────────
# Canonical vocabulary (SNV/INDEL/fusion/cnv/…) plus legacy ad-hoc values
# that exist in Excel data before vocabulary was standardized.
_VARIANT_CATEGORY: dict[str, str | None] = {
    # Canonical
    "SNV": "MUTATION",
    "INDEL": "MUTATION",
    "fusion": "SV",
    "cnv": "CNV",
    "pertinent_negative": "MUTATION",
    "tumor_biomarker": "SIGNATURE",
    "germline": "MUTATION",
    "hla": None,
    "indeterminate": None,
    # Legacy / ad-hoc (Excel data before vocabulary standardization)
    "mutation": "MUTATION",
    "somatic_mutation": "MUTATION",
    "amplification": "CNV",
    "deletion": "MUTATION",        # Default: INDEL. For CNV deletions use "cnv".
    "structural_variant": "SV",
    "tumor biomarker": "SIGNATURE",  # space variant
    "negative": "MUTATION",        # pertinent negative (detected = False)
    "expression": None,
    "immunotherapy_marker": None,
    # Empty / placeholder values — silently skip
    "": None,
    "na": None,
    "NA": None,
}

# variant_types that represent absence of a finding (WILDTYPE)
_WILDTYPE_TYPES = {"pertinent_negative", "negative"}

# CNV call overrides for legacy variant_type values
_CNV_CALL_OVERRIDE: dict[str, str] = {
    "amplification": "High level amplification",
    "deletion": "Heterozygous deletion",
}

_GENDER_MAP = {"male": "Male", "female": "Female", "m": "Male", "f": "Female"}

# Gene names (uppercased) that map to MatchMiner clinical/signature fields
_TMB_GENES = {"TMB", "TUMOR MUTATIONAL BURDEN"}
_MMR_GENES = {"MMR", "MSI", "MICROSATELLITE INSTABILITY", "MISMATCH REPAIR",
               "MLH1", "MSH2", "MSH6", "MSH5", "PMS2"}
_POLE_GENES = {"POLE"}
_APOBEC_GENES = {"APOBEC"}
_TOBACCO_GENES = {"TOBACCO", "SMOKING"}


def _sample_id(patient: Patient) -> str:
    return patient.mrn or str(patient.pt_uuid)


def _parse_tmb(s: str) -> float | None:
    """Extract a float from strings like '4.1 mut/Mb', '1.6m/MB', 'TMB-Low (4.1)'."""
    m = re.search(r"(\d+\.?\d*)", s)
    return float(m.group(1)) if m else None


def _normalize_gender(sex: str | None) -> str | None:
    return _GENDER_MAP.get((sex or "").lower().strip())


def _mmr_status(result_summary: str) -> str:
    rs = result_summary.lower()
    if any(k in rs for k in ("deficient", "dmmr", "msi-h", "msi-high", "loss")):
        return "Deficient (MMR-D / MSI-H)"
    if any(k in rs for k in ("proficient", "pmmr", "mss", "intact", "stable")):
        return "Proficient (MMR-P / MSS)"
    return result_summary


def _split_fusion(gene: str) -> tuple[str, str | None]:
    """'PML/RARA' → ('PML', 'RARA');  'CD74-ROS1' → ('CD74', 'ROS1')."""
    for sep in ("::", "/", "-"):
        if sep in gene:
            left, right = gene.split(sep, 1)
            return left.strip(), right.strip()
    return gene, None


def to_clinical(
    patient: Patient,
    findings: list[Finding],
    report_date: str | None = None,
) -> dict:
    """Build a MatchMiner clinical document from a Patient + their findings."""
    tmb: float | None = None
    for f in findings:
        if (f.gene or "").upper() in _TMB_GENES and f.result_summary:
            tmb = _parse_tmb(f.result_summary)
            if tmb is not None:
                break

    return {
        "SAMPLE_ID": _sample_id(patient),
        "ONCOTREE_PRIMARY_DIAGNOSIS_NAME": patient.oncotree_primary_diagnosis,
        "PRIMARY_DIAGNOSIS_RAW": patient.primary_dx,
        "BIRTH_DATE": patient.dob.isoformat() if patient.dob else None,
        "VITAL_STATUS": patient.vital_status or "Alive",
        "GENDER": _normalize_gender(patient.sex),
        "TUMOR_MUTATIONAL_BURDEN_PER_MEGABASE": tmb,
        "REPORT_DATE": report_date,
        "_updated": datetime.now(tz=timezone.utc).isoformat(),
    }


def to_genomic_docs(
    patient: Patient,
    findings: list[Finding],
    clinical_id: Any = None,
) -> list[dict]:
    """Build MatchMiner genomic documents from a patient's findings.

    clinical_id: ObjectId of the corresponding clinical doc (None for dry-run).
    TMB findings are consumed by to_clinical(); they produce no genomic doc.
    Findings with unknown variant_type emit a warning and are skipped.
    """
    sample_id = _sample_id(patient)
    docs: list[dict] = []
    unknown_vt: set[str] = set()

    for f in findings:
        vt = (f.variant_type or "").strip()
        gene_upper = (f.gene or "").upper()

        # TMB → clinical field only
        if vt in ("tumor_biomarker", "tumor biomarker") and gene_upper in _TMB_GENES:
            continue

        if vt not in _VARIANT_CATEGORY:
            unknown_vt.add(vt)
            continue

        variant_category = _VARIANT_CATEGORY[vt]
        if variant_category is None:
            continue  # hla, indeterminate, expression, etc.

        doc: dict = {
            "SAMPLE_ID": sample_id,
            "TRUE_HUGO_SYMBOL": f.gene,
            "VARIANT_CATEGORY": variant_category,
            "WILDTYPE": vt in _WILDTYPE_TYPES,
            "_updated": datetime.now(tz=timezone.utc).isoformat(),
        }

        if clinical_id is not None:
            doc["CLINICAL_ID"] = clinical_id

        protein = f.protein if f.protein and f.protein.upper() != "NA" else None
        nucleotide = f.nucleotide if f.nucleotide and f.nucleotide.upper() != "NA" else None
        if protein:
            doc["TRUE_PROTEIN_CHANGE"] = protein
        if nucleotide:
            doc["TRUE_CDNA_CHANGE"] = nucleotide

        # Fusion: split gene "GENE1::GENE2" into partner fields
        if vt in ("fusion", "structural_variant") and f.gene:
            left, right = _split_fusion(f.gene)
            doc["LEFT_PARTNER_GENE"] = left
            doc["RIGHT_PARTNER_GENE"] = right
            doc["TRUE_HUGO_SYMBOL"] = left

        # CNV: prefer result_summary, fall back to legacy override
        if variant_category == "CNV":
            call = f.result_summary or _CNV_CALL_OVERRIDE.get(vt)
            if call:
                doc["CNV_CALL"] = call

        # Signature / tumor biomarkers (non-TMB)
        if variant_category == "SIGNATURE":
            rs = f.result_summary or ""
            if gene_upper in _MMR_GENES:
                doc["MMR_STATUS"] = _mmr_status(rs)
            elif gene_upper in _POLE_GENES:
                doc["POLE_STATUS"] = rs
            elif gene_upper in _APOBEC_GENES:
                doc["APOBEC_STATUS"] = rs
            elif gene_upper in _TOBACCO_GENES:
                doc["TOBACCO_STATUS"] = rs

        # Skip docs with no gene — MatchMiner can't match on them
        if not doc.get("TRUE_HUGO_SYMBOL"):
            continue

        docs.append(doc)

    if unknown_vt:
        import sys
        print(
            f"  Warning: skipped findings with unrecognized variant_type: "
            f"{sorted(unknown_vt)}",
            file=sys.stderr,
        )

    return docs
