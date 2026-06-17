from pydantic import BaseModel


class SimilarPatientMatch(BaseModel):
    patient_id: str
    similarity_score: float


class SimilarPatients(BaseModel):
    patient_id: str
    top_n: int
    matches: list[SimilarPatientMatch]
