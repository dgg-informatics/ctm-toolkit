"""Raw Pydantic models — one per Excel sheet row, directly from manual entry.

Fields mirror the Excel column names exactly so openpyxl row dicts can be
passed straight in with model_validate(). All fields are optional except the
join keys (pt_uuid, report_uuid), which must be present for the normalizer
to link documents correctly.
"""
from datetime import UTC, date, datetime

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


def _str_id(v: object) -> object:
    """Coerce a join-key cell to str. Excel may hand back an int (117) or the
    prefixed form ('pt_0000001'); both normalize to the same string key."""
    return str(v) if v is not None else v


class RawPatientGeneral(BaseModel):
    model_config = ConfigDict(extra='allow')
    pt_uuid: str
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
    referring_clinician: str | None = None
    source: str | None = None
    trb_date: date | datetime | str | None = None

    @field_validator("pt_uuid", mode="before")
    @classmethod
    def _ids(cls, v: object) -> object:
        return _str_id(v)

    @field_validator("dob", "trb_date", mode="before")
    @classmethod
    def coerce_dates(cls, v: object) -> date | None:
        return _to_date(v)


class RawReportMetadata(BaseModel):
    """Only identity columns are modeled; every other report column is soaked up
    by extra='allow' and captured verbatim into ReportMetadata.raw."""
    model_config = ConfigDict(extra='allow')
    report_uuid: str
    pt_uuid: str
    source: str
    test_name: str | None = None
    unique_test_id: str | None = None
    unique_test_id_source: str | None = None
    ordering_physician: str | None = None

    @field_validator("report_uuid", "pt_uuid", "unique_test_id", mode="before")
    @classmethod
    def _ids(cls, v: object) -> object:
        return _str_id(v)


class RawFinding(BaseModel):
    """One row from any *_findings sheet. All sheets share this canonical block;
    per-source columns arrive as extras (extra='allow') and are captured into
    Finding.raw, so nothing from the workbook is lost."""
    model_config = ConfigDict(extra='allow')
    pt_uuid: str
    report_uuid: str
    biomarker: str | None = None
    variant_category: str | None = None
    protein_change: str | None = None
    cnv_call: str | None = None
    signature_level: str | None = None
    wildtype: bool | None = None
    nucleotide_change: str | None = None

    @field_validator("pt_uuid", "report_uuid", mode="before")
    @classmethod
    def _ids(cls, v: object) -> object:
        return _str_id(v)

    @field_validator("wildtype", mode="before")
    @classmethod
    def _coerce_wildtype(cls, v: object) -> bool | None:
        if v is None or isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("true", "yes", "y", "1"):
            return True
        if s in ("false", "no", "n", "0"):
            return False
        return None


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
        default_factory=lambda: datetime.now(tz=UTC)
    )


class RawWestTrial(BaseModel):
    """One row from the West (CRCWM) trials Excel sheet."""
    group: str | None = None             # CRCWM Adult / COG Pediatric
    disease_category: str | None = None
    sponsor: str | None = None
    title: str | None = None
    protocol_id: str | None = None       # ID column
    nct_id: str | None = None            # NCT Number column


class RawSparrowTrial(BaseModel):
    """One row from the legacy Sparrow marketing trials Excel sheet.

    Superseded by RawDdotsTrial but deliberately unchanged: the two sources stay
    separate models so a trial's provenance is unambiguous from its shape alone.
    """
    study_name: str | None = None           # Study Name
    description: str | None = None          # Description
    contact_name: str | None = None         # Contact Name
    contact_phone: str | None = None        # Contact Phone Number
    trial_category: str | None = None       # Trial Category
    trial_subcategory: str | None = None    # Trial SubCategory
    nct_id: str | None = None              # NCT # (cleaned)
    contact_email: str | None = None        # Contact Email
    pi: str | None = None                   # PI


class RawDdotsTrial(BaseModel):
    """One protocol from the DDOTS /protocol endpoint — Sparrow's own registry.

    Field names mirror the API's exactly (lowercased from its UPPERCASE COLUMNS),
    the same convention the Excel models follow for their sheet columns.

    Kept separate from RawSparrowTrial rather than merged into it so that
    ``_raw._ddots`` vs ``_raw._sparrow`` tells you which pipeline produced a trial
    without inspecting ``entity``.

    ``eligibility`` is stored and otherwise unused. The normalized
    inclusion/exclusion structure still comes from ClinicalTrials.gov, because
    across real payloads this field arrives in at least three incompatible
    layouts (``<br />``-numbered, unnumbered with ALL-CAPS headers, ``<p>``-wrapped
    with ``3.2.1`` section numbers), sometimes flattens ``>=``/``<=`` to ``=``, and
    is sometimes explicitly abridged ("PLEASE SEE THE CURRENT VERSION OF PROTOCOL
    FOR FULL ELIGIBILITY LIST"). Teaching the LLM stage to read it is future work;
    storing it now means that can happen without another API pull.
    """
    model_config = ConfigDict(extra='allow')

    # Identity. nct_number is verbatim from the API (unprefixed digits); nct_id is
    # the normalized NCT-prefixed form used everywhere else in the pipeline.
    nct_id: str | None = None
    nct_number: str | int | None = None
    protocol: str | None = None                 # national/legacy protocol no. e.g. "0424"
    protocol_id: int | str | None = None        # DDOTS internal autonumber
    local_id: str | None = None                 # local IRB id

    protocol_title: str | None = None
    protocol_title_short: str | None = None
    protocol_summary: str | None = None
    protocol_type: str | None = None            # e.g. "Clinical Trial"
    eligibility: str | None = None              # stored only — see class docstring

    # 0 and 125 are "no limit" sentinels rather than real bounds.
    min_age: int | float | str | None = None
    max_age: int | float | str | None = None

    status: str | None = None                   # status AT THIS INSTITUTION, e.g. "OPEN TO ACCRUAL"
    status_short: str | None = None             # O | C | P …

    disease_site: str | None = None
    disease_site_list: str | None = None        # comma-delimited
    disease_category: str | None = None

    investigator: str | None = None             # "Narayan MD, Samir" — degree sits in the surname field
    investigator_email: str | None = None
    coordinator: str | None = None
    coordinator_email: str | None = None

    department_name: str | None = None
    hospital: str | None = None
    hospital_id: int | str | None = None        # queried on, to scope to one institution
    hospital_email: str | None = None

    nct_link: str | None = None
    documents: dict | None = None               # parsed from the JSON-in-JSON string

    # UTC timestamp of the DDOTS pull. Distinct from the ClinicalTrials.gov
    # fetched_at on the same trial: the two are separate calls to separate APIs,
    # and dating one from the other would be a guess.
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class RawAMCTrial(BaseModel):
    model_config = ConfigDict(extra='allow')
    # UTC timestamp of the pull, so a trial records when its source data was
    # obtained rather than leaving it inferred from the ClinicalTrials.gov call.
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
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
    categorys: list[dict] = Field(default_factory=list)  # <CATEGORYS> parsed as [{cat1,cat2,cat3}]
    satellite_sites: str | None = None          # <SATELLITE_SITES>
