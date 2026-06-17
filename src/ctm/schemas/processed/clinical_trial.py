from pydantic import BaseModel


class ClinicalTrial(BaseModel):
    nct_id: str | None = None
    protocol_no: str | None = None
    title: str | None = None
    phase: str | None = None
    status: str | None = None
    sponsor: str | None = None
    eligibility_criteria: str | None = None
    match_level: str | None = None
    reason_type: str | None = None
    cancer_type_match: str | None = None
    match_type: str | None = None
