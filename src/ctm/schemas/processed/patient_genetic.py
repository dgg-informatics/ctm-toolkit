from pydantic import BaseModel


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
