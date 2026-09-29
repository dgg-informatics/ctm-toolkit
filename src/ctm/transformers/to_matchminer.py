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
import logging
from collections import defaultdict
from datetime import UTC, datetime

from ..schemas.raw.normalized import Finding, Patient, _is_malformed_protein_change

log = logging.getLogger(__name__)

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

# The only wildtype values a MUTATION/CNV/SV row may carry (blank counts as
# false). Anything else non-blank is flagged and the row is skipped.
_WILDTYPE_VALUES = {"true", "false", "indeterminate"}


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


def _biomarker_key(f: Finding) -> tuple[str, str, str] | None:
    """What two reports must share to compete: patient, gene, variant_category
    (case-insensitive). None for a row with no biomarker or category — nothing
    to match on, so nothing to resolve."""
    if not f.biomarker or not f.variant_category:
        return None
    return (f.pt_uuid, f.biomarker.strip().upper(), f.variant_category.strip().upper())


def _result(f: Finding) -> tuple:
    """The reported result, for telling whether two same-date reports disagree.
    A blank wildtype counts as false (detected), as it does in to_genomic_docs."""
    return (f.wildtype or "false", f.protein_change, f.nucleotide_change,
            f.cnv_call, f.signature_level)


def select_latest_findings(findings: list[Finding]) -> list[Finding]:
    """Resolve findings reported by more than one report — the most recent wins.

    Findings are grouped by patient + gene + variant_category. Within a group,
    every row from the report(s) with the latest report_date is kept; rows from
    older reports come back with superseded_by set to the winning report_uuid(s),
    so to_genomic_docs skips them while patient_data still records them. The
    newer report wins whether or not the results differ.

    Two or more reports sharing the latest date are never resolved silently: all
    of them are kept, and if their results disagree an error is logged naming
    the reports for a person to review.

    Findings are returned in their input order. A finding with no report_date
    never beats a dated one.
    """
    groups: dict[tuple, list[Finding]] = defaultdict(list)
    for f in findings:
        if (key := _biomarker_key(f)) is not None:
            groups[key].append(f)

    superseded: dict[int, str] = {}   # id(finding) → winning report_uuid(s)
    for (pt_uuid, biomarker, category), group in groups.items():
        if len({f.report_uuid for f in group}) < 2:
            continue
        dates = [f.report_date for f in group if f.report_date is not None]
        if not dates:
            continue
        latest = max(dates)
        winners = sorted({f.report_uuid for f in group if f.report_date == latest})

        if len(winners) > 1:
            results = {r: {_result(f) for f in group if f.report_uuid == r} for r in winners}
            if len({frozenset(v) for v in results.values()}) > 1:
                log.error(
                    "  %s %s %s: reports %s share report_date %s but disagree — "
                    "all kept for matching; review which is correct",
                    pt_uuid, biomarker, category, ", ".join(winners), latest.isoformat(),
                    extra={"event": "genomic.report_date_tie", "pt_uuid": pt_uuid,
                           "biomarker": biomarker, "variant_category": category,
                           "report_uuids": winners, "report_date": latest.isoformat()},
                )

        losers = sorted({f.report_uuid for f in group if f.report_uuid not in winners})
        if not losers:
            continue
        winner_ids = ", ".join(winners)
        for f in group:
            if f.report_uuid in losers:
                superseded[id(f)] = winner_ids
        log.info(
            "  %s %s %s: using report %s (%s); superseded %s",
            pt_uuid, biomarker, category, winner_ids, latest.isoformat(), ", ".join(losers),
            extra={"event": "genomic.finding_superseded", "pt_uuid": pt_uuid,
                   "biomarker": biomarker, "variant_category": category,
                   "winning_report_uuids": winners, "superseded_report_uuids": losers},
        )

    return [
        f.model_copy(update={"superseded_by": superseded[id(f)]}) if id(f) in superseded else f
        for f in findings
    ]


def to_clinical(patient: Patient, report_date: str | None = None) -> dict:
    """Build a MatchMiner clinical document from a Patient.

    TMB (TUMOR_MUTATIONAL_BURDEN_PER_MEGABASE) is emitted as None until the
    template carries a numeric TMB column.
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
      * a newer report covers the same biomarker (superseded_by is set — see
        select_latest_findings)
      * variant_category is blank or "Other"
      * a SIGNATURE row's signature_level isn't Deficient/Proficient/Stable —
        i.e. blank, or an explicit no-result sentinel like "Indeterminate" /
        "Not Detected" (recorded, but nothing matchable to store)
      * a MUTATION/CNV/SV row's wildtype is "Indeterminate" (tested, inconclusive)
        or invalid — not TRUE/FALSE/INDETERMINATE (warned)
      * an SV row is marked wildtype=TRUE — a tested-negative fusion; there is no
        matchable "negative SV" in matchengine, so it is recorded only
      * there is no biomarker to key on
    """
    sample_id = _sample_id(patient)
    docs: list[dict] = []
    unknown: set[str] = set()
    invalid_wildtype: set[str] = set()
    malformed_protein: set[str] = set()

    for f in findings:
        if f.superseded_by:
            continue
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
            # Prefixed and uppercased on the Finding model. A value that isn't
            # shaped like a protein change was prefixed all the same, so it is
            # stored and still matchable on gene + category — but the protein
            # change itself can never match, so name it (with its gene, the
            # curator's only handle on the row) for them to fix in the workbook.
            doc["TRUE_PROTEIN_CHANGE"] = f.protein_change
            if _is_malformed_protein_change(f.protein_change):
                malformed_protein.add(f"{f.biomarker}: {f.protein_change}")
        if f.nucleotide_change:
            doc["TRUE_CDNA_CHANGE"] = f.nucleotide_change

        # Wildtype applies to the alteration categories (MUTATION/CNV/SV), never
        # SIGNATURE. It must be TRUE / FALSE / INDETERMINATE — blank counts as
        # FALSE (detected). Two states suppress the genomic doc (recorded only):
        #   * INDETERMINATE — tested but inconclusive.
        #   * TRUE on an SV — a tested-negative fusion. matchengine has no
        #     "negative SV" (its structured-SV query keys on the partner genes and
        #     ignores WILDTYPE), so an SV doc would falsely match a fusion trial.
        # Any other non-blank value is invalid: warned and skipped.
        if category in ("MUTATION", "CNV", "SV"):
            wt = f.wildtype
            if wt is not None and wt not in _WILDTYPE_VALUES:
                invalid_wildtype.add(wt)
                continue
            if wt == "indeterminate":
                continue
            if category == "SV" and wt == "true":
                continue
            if category in ("MUTATION", "CNV"):
                doc["WILDTYPE"] = wt == "true"

        if category == "CNV" and f.cnv_call:
            doc["CNV_CALL"] = _remap(_CNV_CALL_MAP, f.cnv_call)

        if category == "SV":
            left, right = _split_fusion(f.biomarker)
            doc["TRUE_HUGO_SYMBOL"] = left
            doc["LEFT_PARTNER_GENE"] = left
            doc["RIGHT_PARTNER_GENE"] = right

        if category == "SIGNATURE":
            # Only the three recognized levels produce a matchable MMR_STATUS.
            # Anything else — a blank cell, or an explicit no-result sentinel such
            # as "Indeterminate" / "Not Detected" — yields no genomic doc (so it
            # never matches), while the row itself still rides into patient_data
            # as a record of what was tested. Curators write a sentinel rather
            # than leaving the cell blank so an intentional no-result stays
            # distinguishable from a not-yet-filled-in row.
            mapped = _SIGNATURE_LEVEL_MAP.get((f.signature_level or "").strip().lower())
            if mapped is None:
                continue
            doc["MMR_STATUS"] = mapped

        docs.append(doc)

    if unknown:
        log.warning(
            "  skipped findings with unrecognized variant_category: %s",
            sorted(unknown),
            extra={"event": "genomic.finding_skipped", "reason": "unknown_category",
                   "values": sorted(unknown), "sample_id": sample_id},
        )
    if invalid_wildtype:
        log.warning(
            "  skipped findings with invalid wildtype (must be "
            "TRUE/FALSE/INDETERMINATE): %s",
            sorted(invalid_wildtype),
            extra={"event": "genomic.finding_skipped", "reason": "invalid_wildtype",
                   "values": sorted(invalid_wildtype), "sample_id": sample_id},
        )
    if malformed_protein:
        log.error(
            "  protein_change values that are not protein changes (stored with "
            "the p. prefix anyway, and will not match): %s",
            sorted(malformed_protein),
            extra={"event": "genomic.malformed_protein_change",
                   "values": sorted(malformed_protein), "sample_id": sample_id},
        )

    return docs
