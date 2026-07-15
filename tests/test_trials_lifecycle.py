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
