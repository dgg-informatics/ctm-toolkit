"""Tests for the weekly trial update pipeline (trials_lifecycle.py)."""


def test_trial_key_amc_uses_protocol_no():
    from ctm.trials_lifecycle import trial_key
    trial = {"entity": "amc", "protocol_no": "2021.070", "nct_id": "NCT04858334"}
    assert trial_key(trial) == "2021.070"


def test_trial_key_west_uses_nct_id():
    from ctm.trials_lifecycle import trial_key
    trial = {"entity": "west", "protocol_no": None, "nct_id": "NCT04858334"}
    assert trial_key(trial) == "NCT04858334"


def test_trial_key_sparrow_uses_nct_id():
    from ctm.trials_lifecycle import trial_key
    trial = {"entity": "sparrow", "protocol_no": None, "nct_id": "NCT05812807"}
    assert trial_key(trial) == "NCT05812807"


def test_compute_trial_hash_same_raw_same_hash():
    from ctm.trials_lifecycle import compute_trial_hash
    trial_a = {"_raw": {"status": "open", "title": "A study"}}
    trial_b = {"_raw": {"status": "open", "title": "A study"}}
    assert compute_trial_hash(trial_a) == compute_trial_hash(trial_b)


def test_compute_trial_hash_different_raw_different_hash():
    from ctm.trials_lifecycle import compute_trial_hash
    trial_a = {"_raw": {"status": "open"}}
    trial_b = {"_raw": {"status": "closed"}}
    assert compute_trial_hash(trial_a) != compute_trial_hash(trial_b)


def test_compute_trial_hash_ignores_treatment_list():
    from ctm.trials_lifecycle import compute_trial_hash
    trial_a = {"_raw": {"status": "open"}, "treatment_list": {"step": []}}
    trial_b = {"_raw": {"status": "open"}, "treatment_list": {"step": [{"step_internal_id": 1}]}}
    assert compute_trial_hash(trial_a) == compute_trial_hash(trial_b)


def _trial(entity, key, eligibility, treatment_list=None, **extra):
    key_field = "protocol_no" if entity == "amc" else "nct_id"
    other_key_field = "nct_id" if entity == "amc" else "protocol_no"
    return {
        "entity": entity,
        key_field: key,
        other_key_field: None,
        "eligibility": eligibility,
        "treatment_list": treatment_list or {"step": []},
        "_raw": {"status": "open"},
        **extra,
    }


def test_split_new_trial_not_in_master_is_changed():
    from ctm.trials_lifecycle import split_by_eligibility
    new_trials = [_trial("amc", "2021.070", {"inclusion": [], "exclusion": []})]
    unchanged, changed, deleted = split_by_eligibility(new_trials, [])
    assert unchanged == []
    assert changed == new_trials
    assert deleted == []


def test_split_identical_eligibility_is_unchanged_with_master_treatment_list():
    from ctm.trials_lifecycle import split_by_eligibility
    eligibility = {"inclusion": [{"text": "Age >= 18", "sub_criteria": []}], "exclusion": []}
    master_treatment_list = {"step": [{"step_internal_id": 1, "match": [{"clinical": {"age_numerical": ">=18"}}]}]}
    master = [_trial("amc", "2021.070", eligibility, treatment_list=master_treatment_list)]
    new = [_trial("amc", "2021.070", eligibility, treatment_list={"step": []}, status="closed")]

    unchanged, changed, deleted = split_by_eligibility(new, master)

    assert changed == []
    assert deleted == []
    assert len(unchanged) == 1
    assert unchanged[0]["treatment_list"] == master_treatment_list
    assert unchanged[0]["status"] == "closed"  # fresh field carried through


def test_split_differing_eligibility_is_changed():
    from ctm.trials_lifecycle import split_by_eligibility
    master = [_trial("amc", "2021.070", {"inclusion": [{"text": "Age >= 18", "sub_criteria": []}], "exclusion": []})]
    new = [_trial("amc", "2021.070", {"inclusion": [{"text": "Age >= 21", "sub_criteria": []}], "exclusion": []})]

    unchanged, changed, deleted = split_by_eligibility(new, master)

    assert unchanged == []
    assert changed == new
    assert deleted == []


def test_split_master_trial_absent_from_new_is_deleted():
    from ctm.trials_lifecycle import split_by_eligibility
    master = [_trial("amc", "2021.070", {"inclusion": [], "exclusion": []})]
    unchanged, changed, deleted = split_by_eligibility([], master)

    assert unchanged == []
    assert changed == []
    assert deleted == master


def test_merge_master_concatenates_unchanged_and_curated_changed():
    from ctm.trials_lifecycle import merge_master
    unchanged = [_trial("amc", "2015.063", {"inclusion": [], "exclusion": []})]
    curated_changed = [_trial("amc", "2021.070", {"inclusion": [], "exclusion": []})]

    result = merge_master(unchanged, curated_changed)

    assert result == unchanged + curated_changed
