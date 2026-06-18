"""TrialMatchAI patient (query) schema — placeholder.

TrialMatchAI expects a patient document describing clinical and genomic
attributes used to match against trial eligibility criteria. Fields here
should be populated once the TrialMatchAI input spec is confirmed.
"""
from pydantic import BaseModel


class TrialMatchAIPatient(BaseModel):
    # TODO: define fields from TrialMatchAI patient input spec
    pass
