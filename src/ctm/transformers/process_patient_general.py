"""Patient general (demographics / clinical) transformer.

Raw input: see ctm/schemas/raw/patient_general.py
Processed output: PatientGeneral (ctm/schemas/processed/models.py)
"""
from ctm.schemas.processed.models import PatientGeneral

__version__ = "0.1.0"


def process(raw: dict) -> dict:
    model = PatientGeneral(
        mrn=raw.get("mrn"),
        gender=raw.get("gender"),
        vital_status=raw.get("vital_status"),
        diagnosis=raw.get("oncotree_primary_diagnosis_name"),
        tmb=raw.get("tumor_mutational_burden_per_megabase"),
        ecog=raw.get("ecog_performance_status"),
        prior_lines_of_therapy=raw.get("prior_lines_of_therapy"),
        smoking_history=raw.get("smoking_history"),
        brain_metastases=raw.get("brain_metastases"),
    )
    return model.model_dump()
