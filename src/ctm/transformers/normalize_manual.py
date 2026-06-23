"""Normalize raw Excel row models → MongoDB-ready normalized models.

Pattern for every source:
  1. Canonical fields (gene, variant_type, result_summary) pass through as-is.
  2. All raw_* fields are collected into Finding.raw, keyed by column name,
     with None values dropped.
  3. source is propagated from the corresponding ReportMetadata row.
"""
from ..schemas.raw.models import (
    RawPatientGeneral,
    RawReportMetadata,
    RawTempusFinding,
    RawCarisFinding,
    RawAmbryFinding,
    RawAmcNgsFinding,
    RawOgmFinding,
    RawPmlRaraFinding,
    RawTumorBiomarker,
)
from ..schemas.raw.normalized import Finding, Patient, ReportMetadata


def _raw_fields(row: object) -> dict:
    """Collect all raw_* fields from a raw model into a dict, dropping Nones."""
    return {
        k: v
        for k, v in row.model_dump().items()
        if k.startswith("raw_") and v is not None
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
    )


def normalize_report_metadata(row: RawReportMetadata) -> ReportMetadata:
    return ReportMetadata(**row.model_dump())


def _finding(row: object, source: str) -> Finding:
    return Finding(
        pt_uuid=row.pt_uuid,
        report_uuid=row.report_uuid,
        source=source,
        gene=row.gene,
        protein=getattr(row, "protein", None),
        nucleotide=getattr(row, "nucleotide", None),
        variant_type=row.variant_type,
        result_summary=row.result_summary,
        raw=_raw_fields(row),
    )


def normalize_tempus(row: RawTempusFinding, source: str = "tempus") -> Finding:
    return _finding(row, source)


def normalize_caris(row: RawCarisFinding, source: str = "caris") -> Finding:
    return _finding(row, source)


def normalize_ambry(row: RawAmbryFinding, source: str = "ambry") -> Finding:
    return _finding(row, source)


def normalize_amc_ngs(row: RawAmcNgsFinding, source: str = "amc_ngs") -> Finding:
    return _finding(row, source)


def normalize_ogm(row: RawOgmFinding, source: str = "ogm") -> Finding:
    return _finding(row, source)


def normalize_pml_rara(row: RawPmlRaraFinding, source: str = "pml_rara") -> Finding:
    return _finding(row, source)


def normalize_tumor_biomarker(row: RawTumorBiomarker, source: str = "tumor_biomarker") -> Finding:
    return _finding(row, source)


# Map sheet name → (raw model class, normalize function)
SHEET_NORMALIZERS = {
    "tempus_findings":   (RawTempusFinding,   normalize_tempus),
    "caris_findings":    (RawCarisFinding,    normalize_caris),
    "ambry_findings":    (RawAmbryFinding,    normalize_ambry),
    "amc_ngs_findings":  (RawAmcNgsFinding,  normalize_amc_ngs),
    "ogm_findings":      (RawOgmFinding,      normalize_ogm),
    "pml_rara_findings": (RawPmlRaraFinding,  normalize_pml_rara),
    "tumor_biomarkers":  (RawTumorBiomarker,  normalize_tumor_biomarker),
}
