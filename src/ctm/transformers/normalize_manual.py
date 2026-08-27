"""Normalize raw Excel row models → MongoDB-ready normalized models.

Pattern:
  1. Canonical columns are promoted to typed fields.
  2. Every other column is captured verbatim into a ``raw`` dict, keyed by
     column name, Nones dropped — lossless, so nothing from the workbook is
     silently discarded.
  3. A finding's source is propagated from its ReportMetadata row.
"""
from ..schemas.raw.models import RawFinding, RawPatientGeneral, RawReportMetadata
from ..schemas.raw.normalized import Finding, Patient, ReportMetadata

# Columns promoted to typed fields; everything else on the row → the raw dict.
_FINDING_FIELDS = {
    "pt_uuid", "report_uuid", "biomarker", "variant_category", "protein_change",
    "cnv_call", "signature_level", "wildtype", "nucleotide_change",
}
_REPORT_FIELDS = {
    "report_uuid", "pt_uuid", "source", "test_name", "unique_test_id",
    "unique_test_id_source", "ordering_physician",
}
_PATIENT_FIELDS = {
    "pt_uuid", "mrn", "first_name", "last_name", "dob", "sex", "vital_status",
    "entity", "primary_dx", "oncotree_primary_diagnosis", "metastasis_sites",
    "referring_clinician", "source", "trb_date",
}


def _raw_fields(row: object, promoted: set[str]) -> dict:
    """Every column not promoted to a typed field, keyed by column name, Nones dropped."""
    return {
        k: v
        for k, v in row.model_dump().items()
        if k not in promoted and v is not None
    }


def normalize_patient(row: RawPatientGeneral) -> Patient:
    sites = (
        [s.strip() for s in row.metastasis_sites.split(",") if s.strip()]
        if row.metastasis_sites
        else []
    )
    return Patient(
        pt_uuid=row.pt_uuid,
        mrn=str(row.mrn) if row.mrn is not None else None,
        first_name=row.first_name,
        last_name=row.last_name,
        dob=row.dob,
        sex=row.sex,
        vital_status=row.vital_status,
        entity=row.entity,
        primary_dx=row.primary_dx,
        oncotree_primary_diagnosis=row.oncotree_primary_diagnosis,
        metastasis_sites=sites,
        referring_clinician=row.referring_clinician,
        source=row.source,
        trb_date=row.trb_date,
        raw=_raw_fields(row, _PATIENT_FIELDS),
    )


def normalize_report_metadata(row: RawReportMetadata) -> ReportMetadata:
    return ReportMetadata(
        report_uuid=row.report_uuid,
        pt_uuid=row.pt_uuid,
        source=row.source,
        test_name=row.test_name,
        unique_test_id=row.unique_test_id,
        unique_test_id_source=row.unique_test_id_source,
        ordering_physician=row.ordering_physician,
        raw=_raw_fields(row, _REPORT_FIELDS),
    )


def normalize_finding(row: RawFinding, source: str) -> Finding:
    return Finding(
        pt_uuid=row.pt_uuid,
        report_uuid=row.report_uuid,
        source=source,
        biomarker=row.biomarker,
        variant_category=row.variant_category,
        protein_change=row.protein_change,
        cnv_call=row.cnv_call,
        signature_level=row.signature_level,
        wildtype=row.wildtype,
        nucleotide_change=row.nucleotide_change,
        raw=_raw_fields(row, _FINDING_FIELDS),
    )


# Every *_findings sheet shares the one canonical block, so a single model and
# normalizer serve them all. The value tuple shape is kept for the reader, which
# unpacks (raw_cls, norm_fn). Source is resolved by the reader from
# report_metadata (falling back to the sheet name).
FINDING_SHEETS = (
    "tempus_findings", "caris_findings", "ambry_findings", "amc_ngs_findings",
    "ogm_findings", "pml_rara_findings", "mayo_findings", "henry_ford_findings",
    "guardant360_findings", "foundation_findings",
)
SHEET_NORMALIZERS = dict.fromkeys(FINDING_SHEETS, (RawFinding, normalize_finding))
