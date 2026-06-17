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
