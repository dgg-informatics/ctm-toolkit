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


# Provenance recorded alongside the source data, not part of it. Excluded from the
# hash because they change on every pull: leaving `fetched_at` in made trial_hash
# differ run to run for identical source data, defeating the audit purpose the
# README describes ("notice a trial's metadata quietly changed under an
# `unchanged` routing") and making the hash useless as a cross-run join key.
_VOLATILE_RAW_KEYS = frozenset({"fetched_at"})


def _without_volatile(value):
    """Copy of *value* with provenance keys stripped at every depth.

    Recursive because the keys nest: a Sparrow trial carries one under `_raw`
    from ClinicalTrials.gov and another under `_raw._ddots` from DDOTS.
    """
    if isinstance(value, dict):
        return {k: _without_volatile(v) for k, v in value.items()
                if k not in _VOLATILE_RAW_KEYS}
    if isinstance(value, list):
        return [_without_volatile(v) for v in value]
    return value


def compute_trial_hash(trial: dict) -> str:
    """Fingerprint of a trial's raw source data (its `_raw` blob).

    Stable across curation — computed only from `_raw`, which curation never
    touches — and stable across pulls, because pull timestamps are excluded. Used
    for later audit and as the storage key, not for routing decisions.
    """
    raw = _without_volatile(trial.get("_raw", {}))
    serialized = json.dumps(raw, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def split_by_eligibility(
    new_trials: list[dict], master_trials: list[dict]
) -> tuple[list[dict], list[dict], list[dict]]:
    """Partition new_trials into (unchanged, changed, deleted) vs. master_trials.

    unchanged: eligibility identical to the master's copy. treatment_list and
      _llm_curation (if present on the master) are replaced with the
      master's — carrying forward curated match nodes and the LLM
      suggestions/biomarker references behind them; every other field comes
      from the fresh new_trials entry.
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
            if "_llm_curation" in master_trial:
                carried["_llm_curation"] = master_trial["_llm_curation"]
            unchanged.append(carried)

    deleted = [t for t in master_trials if trial_key(t) not in new_keys]

    return unchanged, changed, deleted


def merge_master(unchanged: list[dict], curated_changed: list[dict]) -> list[dict]:
    """Combine carried-forward and freshly-curated trials into a new master.

    Plain concatenation — split_by_eligibility already partitions by
    identity key with no overlap, so there's nothing to deduplicate.
    """
    return unchanged + curated_changed
