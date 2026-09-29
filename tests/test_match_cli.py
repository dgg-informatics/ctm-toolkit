"""ctm-match: the reconcile loop.

The behaviours worth proving are the ones that make it safe on a timer —
no-op when up to date, one match when both inputs move, a fresh match database
every time, and state that refuses to advance past a failure.
"""
import argparse
import json
from datetime import UTC, datetime, timedelta

import pytest
from mongo_doubles import FakeClient, FakeCollection, FakeDatabase, docs_written_at

from ctm import match_cli
from ctm.pipeline_state import MATCH_STATE_ID, STATE_COLLECTION

NOW = datetime.now(tz=UTC)
LAST_WEEK = NOW - timedelta(days=7)


def _client(trials_at=NOW, patients_at=NOW, match_collections=None):
    return FakeClient({
        "latest_trials": FakeDatabase({
            "07_filtered_trials": FakeCollection(docs_written_at(trials_at, 300)),
            STATE_COLLECTION: FakeCollection(),
        }),
        "patients_dev": FakeDatabase({
            "latest_clinical": FakeCollection(docs_written_at(patients_at, 37)),
            "latest_genomic": FakeCollection(docs_written_at(patients_at, 400)),
        }),
        "2026-09-29_match": FakeDatabase(match_collections or {}),
    })


def _args(**kwargs):
    base = {"force": False, "dry_run": False, "run_date": "2026-09-29",
            "match_db": None, "patient_db": "patients_dev", "min_match_level": 0,
            "out": None, "out_dir": None}
    return argparse.Namespace(**{**base, **kwargs})


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Patch the two commands ctm-match drives, and the paths it writes to."""
    calls = {"match_prep": [], "reports": []}

    monkeypatch.setattr("ctm.db.mongo_config", lambda **kw: {
        "master_dbname": "latest_trials", "master_collection": "06_master_trials",
        "filtered_collection": "07_filtered_trials", "patient_dbname": "patients_dev",
        "uri": None, "host": "localhost", "port": 27017, "dbname": None,
    })
    def _fake_match_prep(a):
        # Stand in for matchengine: it repopulates trial_match *after*
        # reset_match_database has emptied it, which is the order the export
        # depends on.
        calls["match_prep"].append(a)
        calls["client"]["2026-09-29_match"]["trial_match"].docs = list(
            calls.get("produces", []))
        return 0

    monkeypatch.setattr("ctm.mm_cli._cmd_match_prep", _fake_match_prep)
    monkeypatch.setattr("ctm.report_cli._run_from_mongo",
                        lambda a, p: calls["reports"].append(a))
    monkeypatch.setenv("MATCH_EXPORT_DIR", str(tmp_path / "matches"))
    monkeypatch.setenv("REPORT_EXPORT_DIR", str(tmp_path / "reports"))
    (tmp_path / "reports").mkdir()
    calls["tmp_path"] = tmp_path
    return calls


def _run(client, args, monkeypatch, wired=None):
    monkeypatch.setattr("ctm.db.get_client", lambda config: client)
    if wired is not None:
        wired["client"] = client
    return match_cli._run(args)


# ── The reconcile decision ───────────────────────────────────────────────────

def test_up_to_date_does_nothing(wired, monkeypatch):
    client = _client()
    client["latest_trials"][STATE_COLLECTION].docs = [{
        "_id": MATCH_STATE_ID,
        "inputs": {"trials": NOW.replace(microsecond=0).isoformat(),
                   "patients": NOW.replace(microsecond=0).isoformat()},
    }]
    assert _run(client, _args(), monkeypatch, wired) == 0
    assert wired["match_prep"] == []
    assert wired["reports"] == []


def test_stale_inputs_trigger_one_match(wired, monkeypatch):
    """Both inputs newer than the last match still means a single match — the
    property that makes 'trials at 10:05, patients at 11:05' safe."""
    client = _client()
    client["latest_trials"][STATE_COLLECTION].docs = [{
        "_id": MATCH_STATE_ID,
        "inputs": {"trials": LAST_WEEK.isoformat(), "patients": LAST_WEEK.isoformat()},
    }]
    assert _run(client, _args(), monkeypatch, wired) == 0
    assert len(wired["match_prep"]) == 1
    assert len(wired["reports"]) == 1


def test_force_matches_even_when_up_to_date(wired, monkeypatch):
    client = _client()
    client["latest_trials"][STATE_COLLECTION].docs = [{
        "_id": MATCH_STATE_ID,
        "inputs": {"trials": NOW.replace(microsecond=0).isoformat(),
                   "patients": NOW.replace(microsecond=0).isoformat()},
    }]
    _run(client, _args(force=True), monkeypatch, wired)
    assert len(wired["match_prep"]) == 1


def test_missing_patients_exits_zero_without_matching(wired, monkeypatch, caplog):
    """An unbuilt pipeline must not mail an error every night."""
    client = _client()
    client["patients_dev"]["latest_clinical"].docs = []
    assert _run(client, _args(), monkeypatch, wired) == 0
    assert wired["match_prep"] == []
    assert "nothing to match" in caplog.text


def test_dry_run_changes_nothing(wired, monkeypatch):
    client = _client(match_collections={"trial_match": FakeCollection([{"_id": 1}])})
    assert _run(client, _args(dry_run=True), monkeypatch, wired) == 0
    assert wired["match_prep"] == []
    assert client["2026-09-29_match"]["trial_match"].docs, "must not have dropped"


# ── A fresh match database ───────────────────────────────────────────────────

def test_every_collection_in_the_match_db_is_dropped(wired, monkeypatch):
    """A leftover trial_match is worse than an error: copy_collection preserves
    _id, so stale matches resolve against fresh clinical docs and the database
    looks consistent while mixing two generations of results."""
    client = _client(match_collections={
        "trial": FakeCollection([{"_id": 1}]),
        "trial_match": FakeCollection([{"_id": 2}]),
        "run_log_trial_match": FakeCollection([{"_id": 3}]),
        "clinical_run_history_trial_match": FakeCollection([{"_id": 4}]),
    })
    _run(client, _args(), monkeypatch, wired)
    assert client["2026-09-29_match"].dropped == [
        "clinical_run_history_trial_match", "run_log_trial_match", "trial", "trial_match",
    ]


def test_reset_refuses_a_database_that_is_not_a_match_db():
    """The name arrives from a cron environment; a mistyped --match-db must not
    be able to drop the trial master."""
    client = _client()
    with pytest.raises(SystemExit):
        match_cli.reset_match_database(client, "latest_trials")


@pytest.mark.parametrize("name", ["2026-09-29_match", "custom_match"])
def test_reset_accepts_match_suffixed_names(name):
    client = FakeClient({name: FakeDatabase({"trial_match": FakeCollection()})})
    assert match_cli.reset_match_database(client, name) == ["trial_match"]


# ── Durable record ───────────────────────────────────────────────────────────

def test_matches_are_exported_to_disk(wired, monkeypatch):
    """The disk copy is what lets the <date>_match databases be pruned."""
    client = _client()
    wired["produces"] = [{"_id": "a", "sample_id": "pt_1"},
                         {"_id": "b", "sample_id": "pt_2"}]
    _run(client, _args(), monkeypatch, wired)
    export = wired["tmp_path"] / "matches" / "2026-09-29_trial_match.json"
    exported = json.loads(export.read_text())
    assert [d["sample_id"] for d in exported] == ["pt_1", "pt_2"]


def test_export_happens_after_the_reset_not_before(wired, monkeypatch):
    """Guards the ordering: a pre-existing trial_match is dropped, so anything
    exported is this run's output and never last run's."""
    client = _client(match_collections={
        "trial_match": FakeCollection([{"_id": "stale", "sample_id": "OLD"}]),
    })
    wired["produces"] = [{"_id": "fresh", "sample_id": "pt_1"}]
    _run(client, _args(), monkeypatch, wired)
    exported = json.loads(
        (wired["tmp_path"] / "matches" / "2026-09-29_trial_match.json").read_text())
    assert [d["sample_id"] for d in exported] == ["pt_1"]


# ── State only advances on full success ──────────────────────────────────────

def test_state_is_written_after_a_successful_run(wired, monkeypatch):
    client = _client()
    _run(client, _args(), monkeypatch, wired)
    state = client["latest_trials"][STATE_COLLECTION].find_one({"_id": MATCH_STATE_ID})
    assert state["match_db"] == "2026-09-29_match"


def test_a_matchengine_failure_leaves_state_untouched(wired, monkeypatch):
    """Otherwise the watermark says done while no usable match exists, and the
    next run would skip instead of retrying."""
    monkeypatch.setattr("ctm.mm_cli._cmd_match_prep", lambda a: 3)
    client = _client()
    with pytest.raises(SystemExit) as excinfo:
        _run(client, _args(), monkeypatch, wired)
    assert excinfo.value.code == 3
    assert client["latest_trials"][STATE_COLLECTION].find_one({"_id": MATCH_STATE_ID}) is None


def test_a_report_failure_leaves_state_untouched(wired, monkeypatch):
    def _boom(a, p):
        raise SystemExit(1)

    monkeypatch.setattr("ctm.report_cli._run_from_mongo", _boom)
    client = _client()
    with pytest.raises(SystemExit):
        _run(client, _args(), monkeypatch, wired)
    assert client["latest_trials"][STATE_COLLECTION].find_one({"_id": MATCH_STATE_ID}) is None


def test_a_second_run_after_success_is_a_no_op(wired, monkeypatch):
    """The property that makes this safe to schedule: run it twice, the second
    does nothing."""
    client = _client()
    _run(client, _args(), monkeypatch, wired)
    assert len(wired["match_prep"]) == 1
    _run(client, _args(), monkeypatch, wired)
    assert len(wired["match_prep"]) == 1, "second run should have been a no-op"
