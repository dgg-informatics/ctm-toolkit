"""The run log as a dashboard source (2.7.0 baseline).

A weekly run's log must say on its own what the run changed, without a Mongo
query: which trials were new, changed or deleted (trials.diff), how specific the
resulting set is (trials.filtered), and what each AI-built clause holds
(llm.autofill). These only add fields to existing events; nothing the pipeline
stores changes.
"""
import logging

from test_mm_cli_trials_pipeline import _filter_args, _master_row, _three_bucket_case

from ctm.transformers.filter_trials import leaf_counts, match_level


def _events(caplog, name):
    return [r for r in caplog.records if getattr(r, "event", None) == name]


# ── trials.diff ──────────────────────────────────────────────────────────────

def test_trials_diff_separates_new_from_changed_and_names_them(tmp_path, fake_mongo, caplog):
    import json

    from ctm.mm_cli import _cmd_trials_diff

    master, new, args = _three_bucket_case(tmp_path)
    # An existing trial whose eligibility changed, next to the brand-new 2021.070.
    master.append({"entity": "amc", "protocol_no": "2010.001", "nct_id": None,
                   "eligibility": {"inclusion": [], "exclusion": []},
                   "treatment_list": {"step": []}, "trial_hash": "5" * 64, "_raw": {}})
    new.append({"entity": "amc", "protocol_no": "2010.001", "nct_id": None,
                "eligibility": {"inclusion": [{"text": "Edited", "sub_criteria": []}],
                                "exclusion": []},
                "treatment_list": {"step": []}, "trial_hash": "6" * 64, "_raw": {}})
    (tmp_path / "master.json").write_text(json.dumps(master))
    (tmp_path / "new.json").write_text(json.dumps(new))

    caplog.set_level(logging.INFO)
    _cmd_trials_diff(args)

    [event] = _events(caplog, "trials.diff")
    assert (event.unchanged, event.changed, event.new, event.deleted) == (1, 1, 1, 1)
    assert event.new_keys == ["2021.070"]
    assert event.changed_keys == ["2010.001"]
    assert event.deleted_keys == ["2019.058"]


def test_trials_diff_stored_statuses_are_unchanged(tmp_path, fake_mongo):
    """Logging only: a new trial is still diff_status "changed" in 02."""
    from ctm.mm_cli import _cmd_trials_diff

    _, _, args = _three_bucket_case(tmp_path)
    _cmd_trials_diff(args)
    by_key = {d["trial_key"]: d["diff_status"] for d in fake_mongo["written"]["docs"]}
    assert by_key["2021.070"] == "changed"


# ── trials.filtered ──────────────────────────────────────────────────────────

def test_trials_filtered_counts_match_levels(fake_mongo, caplog):
    from ctm.mm_cli import _cmd_trials_filter

    genomic = _master_row("amc", "NCT1", ["A"], "a" * 64)
    genomic["treatment_list"] = {"step": [{"match": [{"genomic": {"hugo_symbol": "BRAF"}}]}]}
    age = _master_row("amc", "NCT2", ["B"], "b" * 64)
    age["treatment_list"] = {"step": [{"match": [{"clinical": {"age_numerical": ">=18"}}]}]}
    empty = _master_row("amc", "NCT3", ["C"], "c" * 64)
    fake_mongo["master"] = [genomic, age, empty]

    caplog.set_level(logging.INFO)
    _cmd_trials_filter(_filter_args())

    [event] = _events(caplog, "trials.filtered")
    assert event.kept == 3
    assert event.match_levels == {"3": 1, "2": 0, "1": 1, "0": 1}


# ── llm.autofill ─────────────────────────────────────────────────────────────

def test_leaf_counts():
    match = [{"and": [{"genomic": {"hugo_symbol": "BRAF"}},
                      {"or": [{"clinical": {"oncotree_primary_diagnosis": "Glioma"}},
                              {"clinical": {"oncotree_primary_diagnosis": "LGG"}}]},
                      {"clinical": {"age_numerical": ">=18"}}]}]
    assert leaf_counts(match) == (4, 1)
    assert leaf_counts([]) == (0, 0)
    assert leaf_counts([{"genomic": {}}]) == (0, 0)   # an empty node is not a criterion


def test_llm_autofill_event_describes_the_clause(fake_mongo, monkeypatch, caplog):
    from test_autofill_ctml import AGE, _auto, _cli_trial

    from ctm import db as ctm_db
    from ctm import llm_cli

    monkeypatch.setattr(llm_cli, "load_env", lambda: None)
    fake_mongo["collections"] = {ctm_db.LLM_GENOMIC_COLLECTION: [_cli_trial()]}
    caplog.set_level(logging.INFO)
    llm_cli.main(["autofill-ctml", "--no-disk"])

    [event] = _events(caplog, "llm.autofill")
    stored = fake_mongo["upserted"][0]["doc"]
    assert event.filled is True
    assert (event.leaves, event.genomic_leaves) == (1, 0)   # the one age leaf
    assert stored["treatment_list"]["step"][0]["match"] == [_auto(AGE)]
    # The logged level is the one trials-filter will store for this trial.
    assert event.match_level == match_level(stored)
