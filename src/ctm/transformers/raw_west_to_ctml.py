"""Transform RawWestTrial → ClinicalTrialNormalized.

Fetches full trial data from ClinicalTrials.gov by NCT ID, normalizes via
the existing CTGov pipeline, and stores West metadata in _raw._west.
"""
from ..schemas.raw.models import RawWestTrial
from .ctgov_to_raw import fetch
from .raw_ctgov_to_ctml import to_ctml


def to_ctml_dict(trial: RawWestTrial) -> dict:
    ctgov = fetch(trial.nct_id)
    normalized = to_ctml(ctgov)
    normalized.entity = "west"

    d = normalized.model_dump()
    d["_summary"] = d.pop("summary")
    d["_raw"] = d.pop("raw")
    d["_raw"]["_west"] = trial.model_dump(exclude={"nct_id"})
    return d
