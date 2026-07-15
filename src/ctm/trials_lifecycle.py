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


def split_by_eligibility(
    new_trials: list[dict], master_trials: list[dict]
) -> tuple[list[dict], list[dict], list[dict]]:
    """Partition new_trials into (unchanged, changed, deleted) vs. master_trials.

    unchanged: eligibility identical to the master's copy. treatment_list is
      replaced with the master's (carrying forward curated match nodes);
      every other field comes from the fresh new_trials entry.
    changed: no master entry for this key (new trial), or eligibility
      differs from the master's copy. Untouched — ready for ctm-ctml.
    deleted: trials present in master_trials but absent from new_trials.

    An empty master_trials (e.g. first-ever run) routes everything to
    changed, which is correct: nothing has been curated yet.
    """
    master_by_key = {trial_key(t): t for t in master_trials}
    new_keys = set()

    unchanged = []
    changed = []

    for trial in new_trials:
        key = trial_key(trial)
        new_keys.add(key)
        master_trial = master_by_key.get(key)

        if master_trial is None or trial["eligibility"] != master_trial["eligibility"]:
            changed.append(trial)
        else:
            carried = {**trial, "treatment_list": master_trial["treatment_list"]}
            unchanged.append(carried)

    deleted = [t for t in master_trials if trial_key(t) not in new_keys]

    return unchanged, changed, deleted
