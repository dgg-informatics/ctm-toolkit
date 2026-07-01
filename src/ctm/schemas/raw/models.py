"""Raw Pydantic models — one per Excel sheet row, directly from manual entry.

Fields mirror the Excel column names exactly so openpyxl row dicts can be
passed straight in with model_validate(). All fields are optional except the
join keys (pt_uuid, report_uuid), which must be present for the normalizer
to link documents correctly.
"""
from datetime import date, datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    vital_status: str | None = None
    entity: str | None = None
    primary_dx: str | None = None
    oncotree_primary_diagnosis: str | None = None
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
    model_config = ConfigDict(extra='allow')
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    protein: str | None = None
    nucleotide: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    raw_test: str | None = None
    raw_result: str | None = None
    raw_category: str | None = None
    raw_nucleotide_type: str | None = None
    raw_therapies_current_dx: str | None = None
    raw_therapies_other_indications: str | None = None
    raw_trials: str | None = None


class RawCarisFinding(BaseModel):
    model_config = ConfigDict(extra='allow')
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    protein: str | None = None
    nucleotide: str | None = None
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
    model_config = ConfigDict(extra='allow')
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    protein: str | None = None
    nucleotide: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    raw_pathogenic_mutations: str | None = None
    raw_vus: str | None = None
    raw_gross_deletions_dups: str | None = None
    raw_summary: str | None = None


class RawAmcNgsFinding(BaseModel):
    model_config = ConfigDict(extra='allow')
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    protein: str | None = None
    nucleotide: str | None = None
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
    model_config = ConfigDict(extra='allow')
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    protein: str | None = None
    nucleotide: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    raw_selected_results: str | None = None
    raw_interpretation: str | None = None
    raw_iscn_karyotype: str | None = None
    raw_additional_results: str | None = None


class RawPmlRaraFinding(BaseModel):
    model_config = ConfigDict(extra='allow')
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    protein: str | None = None
    nucleotide: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    raw_test_result: str | None = None
    raw_interpretation: str | None = None


class RawTumorBiomarker(BaseModel):
    model_config = ConfigDict(extra='allow')
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


class RawCTGovTrial(BaseModel):
    """Flat capture of a single study from the ClinicalTrials.gov API v2 response.

    Populated by ctgov_to_raw.from_study() which accepts the dict at
    studies[n] (the full study object, not just protocolSection).
    """
    model_config = ConfigDict(extra='allow')
    nct_id: str                             # unique key for DB upserts
    brief_title: str | None = None          # identificationModule.briefTitle
    official_title: str | None = None       # identificationModule.officialTitle
    overall_status: str | None = None       # statusModule.overallStatus
    phases: list[str] = Field(default_factory=list)   # designModule.phases (e.g. ["PHASE2"])
    lead_sponsor: str | None = None         # sponsorCollaboratorsModule.leadSponsor.name
    brief_summary: str | None = None        # descriptionModule.briefSummary
    conditions: list[str] = Field(default_factory=list)  # conditionsModule.conditions
    sex: str | None = None                  # eligibilityModule.sex: ALL|MALE|FEMALE
    minimum_age: str | None = None          # eligibilityModule.minimumAge (e.g. "18 Years")
    maximum_age: str | None = None          # eligibilityModule.maximumAge
    std_ages: list[str] = Field(default_factory=list)  # CHILD|ADULT|OLDER_ADULT
    eligibility_criteria: str | None = None # eligibilityModule.eligibilityCriteria (markdown)
    principal_investigator: str | None = None  # first PRINCIPAL_INVESTIGATOR in overallOfficials
    drug_interventions: list[str] = Field(default_factory=list)  # DRUG/BIOLOGICAL names
    fetched_at: datetime = Field(          # UTC timestamp of the API pull
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )


class RawAMCTrial(BaseModel):
    model_config = ConfigDict(extra='allow')
    amc_id: str | None = None                   # <ID>
    protocol_no: str | None = None              # <NO>
    nct_number: str | None = None               # <NCT_NUMBER>
    status: str | None = None                   # <STATUS>
    title: str | None = None                    # <TITLE> (abbreviated)
    full_title: str | None = None               # <FULL_TITLE>
    summary_obj: str | None = None              # <SUMMARY_OBJ>
    secondary_protocol_no: str | None = None    # <SECONDARY_PROTOCOL_NO>
    sponsor_type: str | None = None             # <SPONSOR_TYPE>
    age_group: str | None = None                # <AGE_GROUP>: Adults/Children/Both/Unspecified
    phase: str | None = None                    # <PHASE>
    cancer_prevention: str | None = None        # <CANCER_PREVENTION>
    scope: str | None = None                    # <SCOPE>
    disease_site: str | None = None             # <DISEASE_SITE> (semicolon-separated)
    lay_description: str | None = None          # <LAY_DESCRIPTION>
    pi: str | None = None                       # <PI>
    institutions: str | None = None             # <INSTITUTIONS>
    oncology_group: str | None = None           # <ONCOLOGY_GROUP>
    management_group: str | None = None         # <MANAGEMENT_GROUP>
    summary4_type: str | None = None            # <SUMMARY4_TYPE>
    octsu_genes_interest: str | None = None     # <OCTSU_GENES_INTEREST> (free-text gene names)
    eligibility: str | None = None              # <ELIGIBILITY> (||~-delimited free text)
    categorys: str | None = None                # <CATEGORYS>
    satellite_sites: str | None = None          # <SATELLITE_SITES>