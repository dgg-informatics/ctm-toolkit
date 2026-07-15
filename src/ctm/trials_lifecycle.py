"""Weekly trial update pipeline: identify which trials need re-curation.

Splits a fresh trial normalization against the previous curated master by
eligibility-criteria equality only. Metadata changes (status, PI, sponsor,
etc.) and the deterministic parts of treatment_list refresh for free on
every `ctm-mm trials` run regardless — they never trigger the expensive
LLM (ctm-ctml) + manual-curation path.
"""
import hashlib
import json


def trial_key(trial: dict) -> str:
    """Identity key for a trial: protocol_no for AMC, nct_id otherwise.

    West and Sparrow trials are always fetched fresh from ClinicalTrials.gov
    and normalized through the CTGov path, so they never have a protocol_no.
    """
    if trial.get("entity") == "amc":
        return trial["protocol_no"]
    return trial["nct_id"]


def compute_trial_hash(trial: dict) -> str:
    """Fingerprint of a trial's raw source data (its `_raw` blob).

    Stable across curation — computed only from `_raw`, which curation
    never touches. Used for later audit, not for routing decisions.
    """
    raw = trial.get("_raw", {})
    serialized = json.dumps(raw, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()
