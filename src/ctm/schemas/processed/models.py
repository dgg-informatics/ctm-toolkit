from pydantic import BaseModel


class PatientGeneral(BaseModel):
    mrn: str | None = None
    gender: str | None = None
    vital_status: str | None = None
    diagnosis: str | None = None
    tmb: float | None = None
    ecog: str | None = None
    prior_lines_of_therapy: str | None = None
    smoking_history: str | None = None
    brain_metastases: str | None = None


class PatientGenetic(BaseModel):
    sample_id: str | None = None
    mrn: str | None = None
    gene: str | None = None
    protein_change: str | None = None
    cdna_change: str | None = None
    variant_classification: str | None = None
    variant_category: str | None = None
    allele_fraction: float | None = None
    tier: int | None = None
    chromosome: str | None = None
    position: int | None = None
    reference_allele: str | None = None
    wildtype: bool | None = None


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


class SimilarPatientMatch(BaseModel):
    patient_id: str
    similarity_score: float


class SimilarPatients(BaseModel):
    patient_id: str
    top_n: int
    matches: list[SimilarPatientMatch]
