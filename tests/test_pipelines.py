"""The two multi-stage pipelines, and the step runner underneath them.

These used to be bash in /usr/local/bin, untested and unversioned. The
behaviours that matter are stop-at-first-failure, a non-zero exit (which is what
turns a failed cron run into mail), and post-curate identifying the run from the
curated file's name rather than the clock.
"""
import json
import os

import pytest

from ctm import post_curate_cli, pre_curate_cli
from ctm.pipelines import Stage, newest_file, run_stages

# ── The step runner ──────────────────────────────────────────────────────────

def test_stages_run_in_order():
    seen = []
    assert run_stages([
        Stage("one", lambda: seen.append(1)),
        Stage("two", lambda: seen.append(2)),
    ]) == 0
    assert seen == [1, 2]


def test_a_failure_stops_the_run_and_returns_its_code():
    """Every stage consumes the previous one's output, so carrying on past a
    failure produces a master built from half a run."""
    seen = []
    code = run_stages([
        Stage("one", lambda: seen.append(1)),
        Stage("two", lambda: 3),
        Stage("three", lambda: seen.append(3)),
    ])
    assert code == 3
    assert seen == [1], "the stage after the failure must not have run"


def test_dry_run_executes_nothing():
    seen = []
    assert run_stages([Stage("one", lambda: seen.append(1))], dry_run=True) == 0
    assert seen == []


def test_a_failure_is_logged_at_error_with_the_stage_name(caplog):
    """Under cron the console line is all that reaches the mail."""
    run_stages([Stage("ctm-llm general", lambda: 1)])
    failures = [r for r in caplog.records if getattr(r, "event", None) == "stage.failed"]
    assert failures and failures[0].levelname == "ERROR"
    assert failures[0].stage == "ctm-llm general"


# ── newest_file ──────────────────────────────────────────────────────────────


def test_newest_file_skips_excel_lock_files(tmp_path):
    (tmp_path / "real.xlsx").write_text("x")
    (tmp_path / "~$real.xlsx").write_text("lock")
    assert newest_file(tmp_path, "*.xlsx", "workbook").name == "real.xlsx"


def test_newest_file_fails_naming_the_directory(tmp_path, caplog):
    with pytest.raises(SystemExit):
        newest_file(tmp_path, "*.json", "curated trials file")
    assert str(tmp_path) in caplog.text
    assert "curated trials file" in caplog.text


# ── ctm-post-curate ──────────────────────────────────────────────────────────

def _curated(tmp_path, name="2026-09-21-curated.json", payload=None):
    directory = tmp_path / "curated"
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(payload if payload is not None else [{"nct_id": "NCT1"}]))
    return path


def test_run_date_comes_from_the_filename_not_today(tmp_path):
    """Ingesting Monday's curation on Thursday belongs to Monday's run — that is
    the database its upstream stages wrote."""
    assert post_curate_cli.run_date_from(_curated(tmp_path)) == "2026-09-21"


def test_a_file_without_a_date_prefix_is_rejected(tmp_path, caplog):
    with pytest.raises(SystemExit):
        post_curate_cli.run_date_from(_curated(tmp_path, name="curated.json"))
    assert "YYYY-MM-DD" in caplog.text


def test_resolve_curated_prefers_the_explicit_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CURATED_DIR", str(tmp_path / "curated"))
    explicit = _curated(tmp_path, name="2026-01-01-explicit.json")
    _curated(tmp_path, name="2026-09-28-newer.json")
    assert post_curate_cli.resolve_curated(str(explicit)) == explicit


def test_resolve_curated_falls_back_to_the_newest(tmp_path, monkeypatch):
    older = _curated(tmp_path, name="2026-09-21-curated.json")
    newer = _curated(tmp_path, name="2026-09-28-curated.json")
    os.utime(older, (1_000, 1_000))
    monkeypatch.setenv("CURATED_DIR", str(tmp_path / "curated"))
    assert post_curate_cli.resolve_curated(None) == newer


def test_an_empty_explicit_file_is_rejected(tmp_path, caplog):
    path = tmp_path / "2026-09-21-empty.json"
    path.write_text("")
    with pytest.raises(SystemExit):
        post_curate_cli.resolve_curated(str(path))
    assert "empty" in caplog.text


def test_malformed_json_fails_before_anything_reaches_mongo(tmp_path, caplog):
    """The file has just been hand-edited; a truncated bracket is the single most
    likely thing wrong with it, and much cheaper to catch here."""
    path = tmp_path / "2026-09-21-bad.json"
    path.write_text('[{"nct_id": "NCT1"')
    with pytest.raises(SystemExit) as excinfo:
        post_curate_cli.validate_json(path)
    assert excinfo.value.code == 2
    assert "not valid JSON" in caplog.text


def test_an_empty_trial_list_is_rejected(tmp_path):
    path = tmp_path / "2026-09-21-none.json"
    path.write_text("[]")
    with pytest.raises(SystemExit):
        post_curate_cli.validate_json(path)


def test_post_curate_stops_before_matching(tmp_path):
    """Matching belongs to ctm-match, which runs when *either* trials or
    patients change — chaining it here re-introduces the stale-cohort bug."""
    stages = post_curate_cli.build_stages(
        _curated(tmp_path), "2026-09-21", "2026-09-21_dev", "deemer")
    assert [s.label for s in stages] == [
        "ctm-mm add-manual", "ctm-mm trials-merge", "ctm-mm trials-filter"]



# ── ctm-pre-curate ───────────────────────────────────────────────────────────

def test_pre_curate_runs_the_four_stages():
    import argparse

    args = argparse.Namespace(sources=None, yes=False, dry_run=True)
    assert [s.label for s in pre_curate_cli.build_stages(args)] == [
        "ctm-mm trials --amc --ddots --west",
        "ctm-mm trials-diff",
        "ctm-llm general",
        "ctm-llm biomarkers",
    ]



# ── CTM_ENV_FILE ─────────────────────────────────────────────────────────────

def test_ctm_env_file_is_loaded_explicitly(tmp_path, monkeypatch):
    """cron runs with a home directory as cwd, so find_dotenv can never reach
    /etc/ctm/.env — naming it is what lets the entry points replace the shell
    wrapper that used to source it."""
    from ctm.paths import load_env

    env_file = tmp_path / "server.env"
    env_file.write_text("CTM_TEST_SENTINEL=from-the-file\n")
    monkeypatch.setenv("CTM_ENV_FILE", str(env_file))
    monkeypatch.delenv("CTM_TEST_SENTINEL", raising=False)

    assert load_env() == env_file
    assert os.environ["CTM_TEST_SENTINEL"] == "from-the-file"


def test_an_exported_value_still_wins_over_the_file(tmp_path, monkeypatch):
    from ctm.paths import load_env

    env_file = tmp_path / "server.env"
    env_file.write_text("CTM_TEST_SENTINEL=from-the-file\n")
    monkeypatch.setenv("CTM_ENV_FILE", str(env_file))
    monkeypatch.setenv("CTM_TEST_SENTINEL", "exported")
    load_env()
    assert os.environ["CTM_TEST_SENTINEL"] == "exported"


def test_a_missing_ctm_env_file_is_loud(tmp_path, monkeypatch):
    """Silently ignoring it would make every variable missing, and each command
    would fail separately on whichever one it happened to need first."""
    from ctm.paths import load_env

    monkeypatch.setenv("CTM_ENV_FILE", str(tmp_path / "nope.env"))
    with pytest.raises(ValueError, match="not a file"):
        load_env()


# ── ctm-load-patients ────────────────────────────────────────────────────────

def _patient_args(**kwargs):
    import argparse

    base = {"workbook": None, "out": None, "pt_uuid": None, "patient_db": None,
            "run_date": None, "dry_run": False}
    return argparse.Namespace(**{**base, **kwargs})


def test_load_patients_threads_one_bundle_path_through_both_stages(tmp_path):
    """The bug this pipeline exists to remove: the load stage reading a path it
    guessed rather than the one the normalize stage actually wrote."""
    from ctm import load_patients_cli, mm_cli

    workbook = tmp_path / "2026-10-05-patients.xlsx"
    workbook.touch()
    bundle = tmp_path / "2026-10-05_patients.json"

    stages = load_patients_cli.build_stages(workbook, bundle, _patient_args())
    assert [s.label for s in stages] == [
        f"ctm-mm patients {workbook.name}", f"ctm-mm load {bundle.name}"]

    normalize = mm_cli.build_parser().parse_args(
        ["patients", str(workbook), "--out", str(bundle)])
    load = mm_cli.build_parser().parse_args(["load", "--pt-data", str(bundle)])
    assert normalize.out == load.pt_data == str(bundle)


def test_load_patients_passes_optional_flags_on(tmp_path):
    from ctm import load_patients_cli, mm_cli

    workbook = tmp_path / "wb.xlsx"
    workbook.touch()
    load_patients_cli.build_stages(
        workbook, tmp_path / "b.json",
        _patient_args(pt_uuid="pt_1,pt_2", patient_db="patients_test",
                      run_date="2026-10-05"))
    parsed = mm_cli.build_parser().parse_args(
        ["load", "--pt-data", "b.json", "--patient-db", "patients_test",
         "--run-date", "2026-10-05"])
    assert parsed.patient_db == "patients_test"
    assert parsed.run_date == "2026-10-05"


def test_load_patients_rejects_a_missing_explicit_workbook(tmp_path, caplog):
    from ctm import load_patients_cli

    with pytest.raises(SystemExit):
        load_patients_cli.resolve_workbook(str(tmp_path / "nope.xlsx"))
    assert "not found" in caplog.text
