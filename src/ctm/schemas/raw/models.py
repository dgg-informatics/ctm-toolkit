"""Raw Pydantic models — one per Excel sheet row, directly from manual entry.

Fields mirror the Excel column names exactly so openpyxl row dicts can be
passed straight in with model_validate(). All fields are optional except the
join keys (pt_uuid, report_uuid), which must be present for the normalizer
to link documents correctly.
"""
from datetime import date, datetime
from pydantic import BaseModel, field_validator


def _to_date(v: object) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%d-%b-%y", "%m/%d/%Y"):
            try:
                return datetime.strptime(v, fmt).date()
            except ValueError:
                continue
    return None


class RawPatientGeneral(BaseModel):
    pt_uuid: int
    mrn: str | int | None = None
    first_name: str | None = None
    last_name: str | None = None
    dob: date | datetime | str | None = None
    sex: str | None = None
    entity: str | None = None
    primary_dx: str | None = None
    metastasis_sites: str | None = None

    @field_validator("dob", mode="before")
    @classmethod
    def coerce_dob(cls, v: object) -> date | None:
        return _to_date(v)


class RawReportMetadata(BaseModel):
    report_uuid: int
    pt_uuid: int
    source: str
    test_name: str | None = None
    accession_no: str | None = None
    physician: str | None = None
    specimen_type: str | None = None
    date_collected: date | datetime | str | None = None
    date_received: date | datetime | str | None = None
    date_completed: date | datetime | str | None = None
    obtained_from: str | None = None
    link: str | None = None
    notes: str | None = None

    @field_validator("date_collected", "date_received", "date_completed", mode="before")
    @classmethod
    def coerce_dates(cls, v: object) -> date | None:
        return _to_date(v)


class RawTempusFinding(BaseModel):
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    raw_biomarker: str | None = None
    raw_result: str | None = None
    raw_category: str | None = None
    raw_nucleotide_type: str | None = None
    raw_therapies_current_dx: str | None = None
    raw_therapies_other: str | None = None
    raw_trials: str | None = None


class RawCarisFinding(BaseModel):
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    # specimen info — repeats on every finding row for this report
    raw_specimen_id: str | None = None
    raw_primary_tumor_site: str | None = None
    raw_specimen_site: str | None = None
    raw_specimen_collected: date | datetime | str | None = None
    raw_test_report_date: date | datetime | str | None = None
    raw_completion_of_addendum: date | datetime | str | None = None
    raw_ordered_by_location: str | None = None
    # finding fields
    raw_section: str | None = None
    raw_biomarker: str | None = None
    raw_method: str | None = None
    raw_analyte: str | None = None
    raw_result: str | None = None
    raw_benefit: str | None = None
    raw_therapy_assoc: str | None = None
    raw_biomarker_level: str | None = None
    raw_protein_alteration: str | None = None
    raw_exon: str | int | None = None
    raw_dna_alteration: str | None = None
    raw_frequency_pct: str | float | None = None
    raw_genotype: str | None = None
    raw_hla_class: str | None = None

    @field_validator(
        "raw_specimen_collected", "raw_test_report_date", "raw_completion_of_addendum",
        mode="before",
    )
    @classmethod
    def coerce_dates(cls, v: object) -> date | None:
        return _to_date(v)


class RawAmbryFinding(BaseModel):
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    raw_pathogenic_mutations: str | None = None
    raw_vus: str | None = None
    raw_gross_deletions_dups: str | None = None
    raw_summary: str | None = None


class RawAmcNgsFinding(BaseModel):
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    # specimen info — repeats per row
    raw_specimen_id: str | None = None
    raw_block_id: str | None = None
    raw_body_site: str | None = None
    # finding fields
    raw_finding_level: str | None = None
    raw_variant_name: str | None = None
    raw_dna_change: str | None = None
    raw_amino_acid_change: str | None = None
    raw_transcript: str | None = None
    raw_interpretation: str | None = None
    raw_therapeutic_implications: str | None = None
    raw_pertinent_negatives: str | None = None


class RawOgmFinding(BaseModel):
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    raw_selected_results: str | None = None
    raw_interpretation: str | None = None
    raw_iscn_karyotype: str | None = None
    raw_additional_results: str | None = None


class RawPmlRaraFinding(BaseModel):
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    raw_test_result: str | None = None
    raw_interpretation: str | None = None


class RawTumorBiomarker(BaseModel):
    pt_uuid: int
    report_uuid: int
    gene: str | None = None           # biomarker name: TMB, MSI, PD-L1, etc.
    variant_type: str | None = None   # always: tumor_biomarker
    result_summary: str | None = None
    raw_tmb: str | None = None
    raw_msi: str | None = None
    raw_pd_l1: str | None = None
    raw_loh: str | None = None
    raw_hrd: str | None = None
    raw_mmr: str | None = None
    raw_tumor_fraction: str | float | None = None
    raw_tumor_normal: str | None = None
    raw_rna_expression: str | None = None
    raw_rna_fusion: str | None = None
