"""ctm-pipeline (the weekly run, AI straight through to the master) and
ctm-apply-edits (a hand-edited file merged into the master any day).

2.6.0 replaces ctm-pre-curate / ctm-post-curate: nobody stands between 04 and
06 any more, so 05_manual_curated_trials only holds manual edits.
"""
import argparse
import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ctm import apply_edits_cli, pipeline_cli
from ctm.db import (
    CURATED_AUTO,
    CURATED_MANUAL_EDIT,
    DEFAULT_MASTER_COLLECTION,
    DIFF_COLLECTION,
    LLM_AUTOFILL_COLLECTION,
    MANUAL_COLLECTION,
)

REPO = Path(__file__).resolve().parent.parent


# ── stage names ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, key", [
    ("07", "07"), ("07_filtered_trials", "07"), ("03c", "03c"),
    ("03c_clinical_trials", "03c"), ("04_autofill_trials", "04"), ("01", "01"),
])
def test_stage_names_accept_prefix_or_collection(name, key):
    assert pipeline_cli.resolve_stage(name) == key


def test_05_is_not_a_pipeline_stage(caplog):
    """05 holds manual edits only; say where they go instead."""
    with pytest.raises(SystemExit):
        pipeline_cli.resolve_stage("05_manual_curated_trials")
    assert "ctm-apply-edits" in caplog.text


def test_unknown_stage_lists_the_valid_ones(caplog):
    with pytest.raises(SystemExit):
        pipeline_cli.resolve_stage("08")
    assert "03c" in caplog.text and "07" in caplog.text


# ── stage selection ──────────────────────────────────────────────────────────

def _args(*argv):
    return pipeline_cli.build_parser().parse_args(list(argv))


def _labels(*argv):
    args = _args(*argv)
    start, end = pipeline_cli.check_args(args)
    return [s.label for s in pipeline_cli.build_stages(args, start, end, "2026-11-16")]


def test_full_run_skips_05():
    assert _labels("--end", "07_filtered_trials") == [
        "01 ctm-mm trials --amc --ddots --west",
        "02 ctm-mm trials-diff",
        "03c ctm-llm clinical",
        "03g ctm-llm genomic",
        "04 ctm-llm autofill-ctml",
        "06 ctm-mm trials-merge --from 04_autofill_trials",
        "07 ctm-mm trials-filter",
    ]


def test_end_defaults_to_07():
    assert _labels()[-1] == "07 ctm-mm trials-filter"


def test_end_stops_early():
    assert _labels("--end", "03c") == [
        "01 ctm-mm trials --amc --ddots --west", "02 ctm-mm trials-diff",
        "03c ctm-llm clinical"]


def test_resume_needs_the_runs_database(caplog):
    """Starting after 01 reads collections an earlier run wrote; guessing
    today's database would read an empty one."""
    with pytest.raises(SystemExit):
        pipeline_cli.check_args(_args("--start", "03g"))
    assert "--db" in caplog.text
    assert _labels("--start", "03g", "--db", "2026-11-09")[0] == "03g ctm-llm genomic"


def test_start_after_end_is_rejected():
    with pytest.raises(SystemExit):
        pipeline_cli.check_args(_args("--start", "06", "--end", "03c", "--db", "x"))


def test_trials_file_only_feeds_03c(caplog):
    with pytest.raises(SystemExit):
        pipeline_cli.check_args(_args("--trials", "t.json"))
    assert "--start 03c" in caplog.text


def test_backfill_passes_the_file_and_overwrite_on():
    args = _args("--start", "03c", "--trials", "prod06.json", "--db", "backfill",
                 "--overwrite", "--yes")
    start, end = pipeline_cli.check_args(args)
    stages = {s.label.split()[0]: s for s in pipeline_cli.build_stages(args, start, end, "backfill")}
    assert stages["03c"].argv[-2:] == ["--trials", "prod06.json"]
    assert "--overwrite" in stages["04"].argv
    assert all("--yes" in stages[k].argv for k in ("03c", "03g", "04"))
    assert all("backfill" in stages[k].argv for k in stages)


# ── a week with no changed trials ────────────────────────────────────────────

def test_llm_stages_skip_when_nothing_changed(fake_mongo, caplog):
    """A quiet week must exit 0 (no cron mail) and still reach merge, which
    applies the week's deletions."""
    fake_mongo["collections"] = {DIFF_COLLECTION: []}
    ran = []
    guard = pipeline_cli.ChangedGuard("2026-11-16")
    stage = guard.wrap(lambda: ran.append(1))
    caplog.set_level(logging.INFO)
    assert stage() == 0 and stage() == 0
    assert ran == []
    assert sum(getattr(r, "event", None) == "pipeline.no_changes" for r in caplog.records) == 1


def test_llm_stages_run_when_something_changed(fake_mongo):
    fake_mongo["collections"] = {DIFF_COLLECTION: [{"diff_status": "changed"}]}
    ran = []
    pipeline_cli.ChangedGuard("2026-11-16").wrap(lambda: ran.append(1))()
    assert ran == [1]


# ── trials-merge --from 04 ───────────────────────────────────────────────────

def _merge_args(**over):
    base = {"db": None, "master_db": None, "master_collection": None,
            "run_date": "2026-11-16", "out": None, "allow_empty_master": False,
            "source_collection": LLM_AUTOFILL_COLLECTION}
    return argparse.Namespace(**{**base, **over})


def _trial(key, **extra):
    return {"entity": "amc", "protocol_no": key, "nct_id": None,
            "trial_hash": hashlib.sha256(key.encode()).hexdigest(),
            "eligibility": {"inclusion": [], "exclusion": []},
            "treatment_list": {"step": []}, **extra}


def _master(key, curated_by="manual-curation"):
    return _trial(key, curated_by=curated_by, curated_by_user="olddev",
                  curated_at=datetime(2026, 1, 1, tzinfo=UTC))


@pytest.fixture
def merge_env(fake_mongo, monkeypatch, tmp_path):
    monkeypatch.setenv("MASTER_TRIAL_EXPORT_DIR", str(tmp_path))
    return fake_mongo


def test_merge_from_04_stamps_auto_provenance(merge_env):
    from ctm.mm_cli import _cmd_trials_merge

    merge_env["collections"] = {
        LLM_AUTOFILL_COLLECTION: [_trial("CHG")],
        DEFAULT_MASTER_COLLECTION: [_master("KEEP"), _master("CHG")],
        DIFF_COLLECTION: [],
    }
    _cmd_trials_merge(_merge_args())
    by_key = {t["protocol_no"]: t for t in merge_env["writes"][0]["docs"]}
    assert by_key["CHG"]["curated_by"] == CURATED_AUTO
    assert by_key["CHG"]["curated_by_user"]
    assert by_key["KEEP"]["curated_by"] == "manual-curation"   # carried forward


def test_merge_from_04_replaces_stale_curation_provenance(merge_env):
    """A backfill feeds 06 back through the LLM; the old manual stamp must not
    survive on a trial the AI just rewrote."""
    from ctm.mm_cli import _cmd_trials_merge

    merge_env["collections"] = {
        LLM_AUTOFILL_COLLECTION: [_master("CHG")],
        DEFAULT_MASTER_COLLECTION: [_master("CHG")],
        DIFF_COLLECTION: [],
    }
    _cmd_trials_merge(_merge_args())
    assert merge_env["writes"][0]["docs"][0]["curated_by"] == CURATED_AUTO


def test_merge_from_04_with_nothing_changed_carries_master_and_deletes(merge_env):
    from ctm.mm_cli import _cmd_trials_merge

    merge_env["collections"] = {
        LLM_AUTOFILL_COLLECTION: [],
        DEFAULT_MASTER_COLLECTION: [_master("KEEP"), _master("DEL")],
        DIFF_COLLECTION: [{"entity": "amc", "protocol_no": "DEL", "nct_id": None,
                           "diff_status": "deleted"}],
    }
    _cmd_trials_merge(_merge_args())
    assert [t["protocol_no"] for t in merge_env["writes"][0]["docs"]] == ["KEEP"]


def test_empty_05_is_still_an_error(merge_env, caplog):
    """An empty edits collection means the edit never landed."""
    from ctm.mm_cli import _cmd_trials_merge

    merge_env["collections"] = {MANUAL_COLLECTION: [],
                                DEFAULT_MASTER_COLLECTION: [_master("KEEP")]}
    with pytest.raises(SystemExit):
        _cmd_trials_merge(_merge_args(source_collection=MANUAL_COLLECTION))


def test_superseding_a_manual_edit_is_flagged(merge_env, caplog):
    """The source changed, so the AI re-drafted it and the hand fix is gone."""
    from ctm.mm_cli import _cmd_trials_merge

    merge_env["collections"] = {
        LLM_AUTOFILL_COLLECTION: [_trial("EDIT"), _trial("OLD")],
        DEFAULT_MASTER_COLLECTION: [_master("EDIT", CURATED_MANUAL_EDIT), _master("OLD")],
        DIFF_COLLECTION: [],
    }
    _cmd_trials_merge(_merge_args())
    flagged = [r for r in caplog.records
               if getattr(r, "event", None) == "trials_merge.manual_edit_superseded"]
    assert len(flagged) == 1 and flagged[0].levelname == "WARNING"
    assert flagged[0].trial_keys == ["EDIT"]   # the pre-2.6 "manual-curation" one is not flagged


def test_merge_from_rejects_other_collections():
    from ctm import mm_cli

    with pytest.raises(SystemExit):
        mm_cli.build_parser().parse_args(["trials-merge", "--from", "03g_genomic_trials"])
    parsed = mm_cli.build_parser().parse_args(["trials-merge"])
    assert parsed.source_collection == MANUAL_COLLECTION


# ── add-manual --curated-by ──────────────────────────────────────────────────

def test_add_manual_records_the_method(tmp_path, fake_mongo):
    from test_manual_curation_merge import _amc_curated_trial

    from ctm.mm_cli import _cmd_add_manual

    path = tmp_path / "edits.json"
    path.write_text(json.dumps([_amc_curated_trial()], default=str))
    _cmd_add_manual(argparse.Namespace(trials=str(path), db=None, run_date="2026-11-16",
                                       curated_by_user="deemer",
                                       curated_by=CURATED_MANUAL_EDIT))
    assert fake_mongo["upserted"][0]["doc"]["curated_by"] == CURATED_MANUAL_EDIT


# ── legacy entries ───────────────────────────────────────────────────────────

def _legacy_curation():
    return {
        "_ctml_suggestions": [{"text": "old", "suggested_node": {"x": 1}},
                              {"node_type": "clinical", "source": "s", "text": "new",
                               "suggested_node": {"clinical": {"age_numerical": ">=18"}}}],
        "_null_ctml_suggestions": [{"text": "old null"}],
        "biomarker_references": ["BRAF"],
        "molecular_genomic_refs": [],
    }


def test_drop_legacy_entries():
    from ctm.transformers.llm_curation import drop_legacy_entries

    curation = _legacy_curation()
    assert drop_legacy_entries(curation) == 2
    assert [e["text"] for e in curation["_ctml_suggestions"]] == ["new"]
    assert curation["_null_ctml_suggestions"] == []
    assert "biomarker_references" not in curation


def test_legacy_keys_are_always_dropped():
    """3.0.0: genomic regenerates refs, so the old keys go even before it runs."""
    from ctm.transformers.llm_curation import drop_legacy_entries

    curation = {"biomarker_references": ["BRAF"], "final_suggested_ctml": [{}]}
    drop_legacy_entries(curation)
    assert curation == {}


def test_autofill_drops_legacy_even_when_it_skips_the_match():
    from ctm.transformers.autofill_ctml import autofill_trial

    trial = {"treatment_list": {"step": [{"match": [{"clinical": {"oncotree_primary_diagnosis": "X"}}]}]},
             "_llm_curation": _legacy_curation()}
    out, report = autofill_trial(trial)
    assert report["reason"] == "match not empty"
    assert report["legacy_dropped"] == 2
    assert all(e.get("node_type") for e in out["_llm_curation"]["_ctml_suggestions"])


# ── ctm-apply-edits ──────────────────────────────────────────────────────────

def test_apply_edits_stages():
    stages = apply_edits_cli.build_stages(Path("e.json"), "2026-11-18", "deemer")
    assert [s.label for s in stages] == [
        "05 ctm-mm add-manual", "06 ctm-mm trials-merge", "07 ctm-mm trials-filter"]
    add = stages[0].argv
    assert add[add.index("--curated-by") + 1] == CURATED_MANUAL_EDIT
    assert add[add.index("--curated-by-user") + 1] == "deemer"
    assert all("2026-11-18" in s.argv for s in stages)


def test_apply_edits_rejects_bad_json_before_mongo(tmp_path, caplog):
    path = tmp_path / "edits.json"
    path.write_text('[{"nct_id": "NCT1"')
    with pytest.raises(SystemExit):
        apply_edits_cli.read_edits(path)
    assert "not valid JSON" in caplog.text


def test_apply_edits_reports_what_it_replaces(caplog):
    caplog.set_level(logging.INFO)
    edits = [_trial("A"), _trial("NEW")]
    master = [_master("A", CURATED_MANUAL_EDIT), _master("B")]
    summary = apply_edits_cli.preview(edits, master)
    assert summary == {"replaced": ["A"], "added": ["NEW"], "re_edited": ["A"]}


# ── removed in 3.0.0 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("module", ["pre_curate_cli", "post_curate_cli", "ctml_cli"])
def test_the_deprecated_entry_points_are_gone(module):
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"ctm.{module}")
    text = (REPO / "pyproject.toml").read_text()
    assert f"ctm.{module}" not in text


def test_trials_curate_and_the_file_merge_are_gone():
    from ctm import mm_cli

    with pytest.raises(SystemExit):
        mm_cli.build_parser().parse_args(["trials-curate"])
    with pytest.raises(SystemExit):
        mm_cli.build_parser().parse_args(["trials-merge", "--unchanged", "u.json"])


# ── packaging and deploy ─────────────────────────────────────────────────────

def test_entry_points_exist():
    text = (REPO / "pyproject.toml").read_text()
    assert 'ctm-pipeline = "ctm.pipeline_cli:main"' in text
    assert 'ctm-apply-edits = "ctm.apply_edits_cli:main"' in text


def test_cron_file_runs_the_pipeline_on_mondays_and_match_nightly():
    lines = [line.split() for line in (REPO / "deploy/cron.d/ctm").read_text().splitlines()
             if line and not line.startswith("#") and "=" not in line.split()[0]]
    commands = {" ".join(parts[6:]): parts[:5] for parts in lines}
    weekly = next(c for c in commands if c.startswith("ctm-pipeline --end 07_filtered_trials"))
    assert commands[weekly] == ["0", "7", "*", "*", "MON"]
    assert any("ctm-match" in c for c in commands)
    assert not any("curate" in c for c in commands)
