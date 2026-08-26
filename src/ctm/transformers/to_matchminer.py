"""Transform normalized CTM documents → MatchMiner clinical + genomic dicts.

MatchMiner expects two MongoDB collections:
  clinical  — one document per patient sample
  genomic   — one document per alteration, linked via SAMPLE_ID + CLINICAL_ID

The curator-facing template columns are already close to MatchMiner's shape, so
this module is mostly a straight field copy. The exceptions are two value remaps
(cnv_call, signature_level) that MUST match matchengine's DFCIQueryTransformers
exactly — a curated trial clause is transformed to the *patient-stored* value at
query time, so if the patient doc stores the friendly label instead, the clause
silently never matches.

This module is pure (no I/O). Callers handle MongoDB writes.
"""
from datetime import UTC, datetime

from ..schemas.raw.normalized import Finding, Patient

# ── Value remaps — mirror matchengine/plugins/DFCIQueryTransformers.py ─────────
# Keys are the curator label lowercased (input casing does not matter); values
# are the EXACT strings matchengine compares the patient doc against — those must
# not change (e.g. the lowercase "d" in "Homozygous deletion", or "Low
# Amplification" → "Gain", which is not obvious).
_CNV_CALL_MAP = {
    "high amplification": "High level amplification",
    "low amplification": "Gain",
    "homozygous deletion": "Homozygous deletion",
    "heterozygous deletion": "Heterozygous deletion",
}

# signature_level → MMR_STATUS (mmr_ms_map). Proficient and Stable both collapse
# to the single MSS string.
_SIGNATURE_LEVEL_MAP = {
    "deficient": "Deficient (MMR-D / MSI-H)",
    "proficient": "Proficient (MMR-P / MSS)",
    "stable": "Proficient (MMR-P / MSS)",
}


def _remap(mapping: dict, value: str) -> str:
    """Case-insensitive lookup of a curator label → its exact stored value.
    Falls back to the value as typed when it is not a recognized label."""
    return mapping.get(value.strip().lower(), value)

_GENDER_MAP = {"male": "Male", "female": "Female", "m": "Male", "f": "Female"}

# variant_category values the patient genomic doc understands (compared
# uppercased, so curator casing like "Mutation" or "sv" still matches).
_MATCHABLE_CATEGORIES = {"MUTATION", "CNV", "SIGNATURE", "SV"}
# Kept in patient_data but never promoted to a genomic doc (still rides
# losslessly in the extras rollup).
_SKIP_CATEGORIES = {"OTHER"}


def _sample_id(patient: Patient) -> str:
    # pt_uuid, never mrn — the clinical/genomic docs must carry no PHI.
    return patient.pt_uuid


def _normalize_gender(sex: str | None) -> str | None:
    return _GENDER_MAP.get((sex or "").lower().strip())


def _split_fusion(gene: str) -> tuple[str, str | None]:
    """'SV1::SV2' → ('SV1', 'SV2');  'CD74-ROS1' → ('CD74', 'ROS1')."""
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
    """Build a MatchMiner clinical document from a Patient + their findings.

    ``findings`` is currently unused (TMB, its only former consumer, is deferred
    until the template carries a numeric TMB column) but kept in the signature so
    callers need not change when TMB returns.
    """
    return {
        "SAMPLE_ID": _sample_id(patient),
        "ONCOTREE_PRIMARY_DIAGNOSIS_NAME": patient.oncotree_primary_diagnosis,
        "PRIMARY_DIAGNOSIS_RAW": patient.primary_dx,
        "BIRTH_DATE": patient.dob.isoformat() if patient.dob else None,
        "VITAL_STATUS": patient.vital_status or "alive",
        "GENDER": _normalize_gender(patient.sex),
        "TUMOR_MUTATIONAL_BURDEN_PER_MEGABASE": None,  # TMB deferred (no column yet)
        "REPORT_DATE": report_date,
        "_updated": datetime.now(tz=UTC).isoformat(),
    }


def to_genomic_docs(
    patient: Patient,
    findings: list[Finding],
    clinical_id: object = None,
) -> list[dict]:
    """Build MatchMiner genomic documents from a patient's findings — one per row.

    clinical_id: ObjectId of the corresponding clinical doc (None for dry-run).
    Rows are skipped (no genomic doc, but still present in patient_data) when:
      * variant_category is blank or "Other"
      * a SIGNATURE row has no signature_level (statusless, unmatchable)
      * there is no biomarker to key on
    """
    sample_id = _sample_id(patient)
    docs: list[dict] = []
    unknown: set[str] = set()

    for f in findings:
        category = (f.variant_category or "").strip().upper()
        if not category or category in _SKIP_CATEGORIES:
            continue
        if category not in _MATCHABLE_CATEGORIES:
            unknown.add(category)
            continue
        if not f.biomarker:
            continue

        doc: dict = {
            "SAMPLE_ID": sample_id,
            "TRUE_HUGO_SYMBOL": f.biomarker,
            "VARIANT_CATEGORY": category,
            "_updated": datetime.now(tz=UTC).isoformat(),
        }
        if clinical_id is not None:
            doc["CLINICAL_ID"] = clinical_id
        if f.protein_change:
            doc["TRUE_PROTEIN_CHANGE"] = f.protein_change
        if f.nucleotide_change:
            doc["TRUE_CDNA_CHANGE"] = f.nucleotide_change

        # WILDTYPE applies only to MUTATION/CNV; a blank column defaults to False.
        if category in ("MUTATION", "CNV"):
            doc["WILDTYPE"] = bool(f.wildtype)

        if category == "CNV" and f.cnv_call:
            doc["CNV_CALL"] = _remap(_CNV_CALL_MAP, f.cnv_call)

        if category == "SV":
            left, right = _split_fusion(f.biomarker)
            doc["TRUE_HUGO_SYMBOL"] = left
            doc["LEFT_PARTNER_GENE"] = left
            doc["RIGHT_PARTNER_GENE"] = right

        if category == "SIGNATURE":
            if not f.signature_level:
                continue  # nothing to store in MMR_STATUS → unmatchable, skip
            doc["MMR_STATUS"] = _remap(_SIGNATURE_LEVEL_MAP, f.signature_level)

        docs.append(doc)

    if unknown:
        import sys
        print(
            f"  Warning: skipped findings with unrecognized variant_category: "
            f"{sorted(unknown)}",
            file=sys.stderr,
        )

    return docs
