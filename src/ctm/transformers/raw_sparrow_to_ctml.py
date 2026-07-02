"""Transform RawSparrowTrial → ClinicalTrialNormalized.

Sparrow trials are identified by NCT ID. This transformer fetches the full
trial from the ClinicalTrials.gov API, normalizes it using the existing
CTGov pipeline, then:
  - sets entity = "sparrow"
  - merges Sparrow-specific metadata (contact, study_name, etc.) into _raw
"""
from ..schemas.raw.models import RawSparrowTrial
from .ctgov_to_raw import fetch
from .raw_ctgov_to_ctml import to_ctml


def to_ctml_dict(trial: RawSparrowTrial) -> dict:
    ctgov = fetch(trial.nct_id)
    normalized = to_ctml(ctgov)
    normalized.entity = "sparrow"

    d = normalized.model_dump()
    d["_summary"] = d.pop("summary")
    d["_raw"] = d.pop("raw")
    d["_raw"]["_sparrow"] = trial.model_dump(exclude={"nct_id"})
    return d
