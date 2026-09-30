"""Watermarks and the staleness decision that drives ctm-match.

No server: the doubles below mirror what pymongo actually exposes, including
the things it *forbids* — a Database is deliberately not iterable, so a double
built from a plain dict would accept `in` where the real object raises.
"""
from datetime import UTC, datetime, timedelta

from mongo_doubles import FakeClient, FakeCollection, FakeDatabase, docs_written_at

from ctm.pipeline_state import (
    MATCH_STATE_ID,
    STATE_COLLECTION,
    PipelineStatus,
    Watermark,
    read_status,
    read_watermark,
    write_match_state,
)

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
YESTERDAY = NOW - timedelta(days=1)
LAST_WEEK = NOW - timedelta(days=7)


# ── Watermarks ───────────────────────────────────────────────────────────────

def test_watermark_reads_the_newest_object_id():
    client = FakeClient({"master": FakeDatabase({
        "07_filtered_trials": FakeCollection(
            docs_written_at(LAST_WEEK, 2) + docs_written_at(NOW, 1)),
    })})
    wm = read_watermark(client, "master", "07_filtered_trials")
    assert wm.count == 3
    assert wm.written_at == NOW.replace(microsecond=0)
    assert wm.exists


def test_missing_collection_is_reported_not_raised():
    """A stage that has never run is a legitimate state to report."""
    wm = read_watermark(FakeClient(), "master", "07_filtered_trials")
    assert wm.count == 0
    assert not wm.exists
    assert wm.describe() == "missing"


# ── Staleness ────────────────────────────────────────────────────────────────

def _status(trials_at=NOW, patients_at=NOW, last_match=None):
    return PipelineStatus(
        trials=Watermark("master", "07_filtered_trials", 300, trials_at),
        patients_clinical=Watermark("patients", "latest_clinical", 37, patients_at),
        patients_genomic=Watermark("patients", "latest_genomic", 400, patients_at),
        last_match=last_match,
    )


def _state(trials_at, patients_at):
    return {"inputs": {"trials": trials_at.isoformat(), "patients": patients_at.isoformat()}}


def test_never_matched_is_stale():
    status = _status()
    assert status.stale
    assert status.reasons() == ["no match has been run yet"]


def test_unchanged_inputs_are_not_stale():
    status = _status(last_match=_state(NOW, NOW))
    assert not status.stale
    assert status.reasons() == ["up to date"]


def test_new_trials_make_it_stale():
    status = _status(trials_at=NOW, patients_at=LAST_WEEK,
                     last_match=_state(YESTERDAY, LAST_WEEK))
    assert status.stale
    assert status.reasons() == ["trials changed since the last match"]


def test_new_patients_make_it_stale():
    """The case the chained pipeline could not express: trials unchanged this
    week, but new patient data still deserves a match."""
    status = _status(trials_at=LAST_WEEK, patients_at=NOW,
                     last_match=_state(LAST_WEEK, YESTERDAY))
    assert status.stale
    assert status.reasons() == ["patients changed since the last match"]


def test_both_changing_is_still_one_match():
    """Trials at 10:05 and patients at 11:05 produce a single stale verdict —
    the whole reason this is level-triggered rather than event-driven."""
    status = _status(trials_at=NOW, patients_at=NOW,
                     last_match=_state(LAST_WEEK, LAST_WEEK))
    assert status.stale
    assert len(status.reasons()) == 2


def test_missing_patients_is_not_ready_rather_than_stale():
    """Nothing to match against is an unbuilt pipeline, not a stale one —
    reporting it as stale would make ctm-match retry forever."""
    status = _status(patients_at=None)
    status.patients_clinical = Watermark("patients", "latest_clinical", 0, None)
    assert not status.ready
    assert not status.stale
    assert status.reasons() == ["latest_clinical is empty or missing"]


# ── Wiring ───────────────────────────────────────────────────────────────────

def test_read_status_prefers_filtered_trials(monkeypatch):
    client = FakeClient({
        "master": FakeDatabase({
            "07_filtered_trials": FakeCollection(docs_written_at(NOW)),
            "06_master_trials": FakeCollection(docs_written_at(LAST_WEEK)),
        }),
        "patients": FakeDatabase({
            "latest_clinical": FakeCollection(docs_written_at(NOW)),
            "latest_genomic": FakeCollection(docs_written_at(NOW)),
        }),
    })
    config = {"master_dbname": "master", "master_collection": "06_master_trials",
              "filtered_collection": "07_filtered_trials", "patient_dbname": "patients"}
    status = read_status(client, config)
    assert status.trials.collection == "07_filtered_trials"


def test_read_status_falls_back_to_the_master(monkeypatch):
    """match-prep falls back this way when trials-filter has not run; the
    staleness check has to watch whatever match-prep will actually read."""
    client = FakeClient({
        "master": FakeDatabase({"06_master_trials": FakeCollection(docs_written_at(NOW))}),
        "patients": FakeDatabase({
            "latest_clinical": FakeCollection(docs_written_at(NOW)),
            "latest_genomic": FakeCollection(docs_written_at(NOW)),
        }),
    })
    config = {"master_dbname": "master", "master_collection": "06_master_trials",
              "filtered_collection": "07_filtered_trials", "patient_dbname": "patients"}
    assert read_status(client, config).trials.collection == "06_master_trials"


def test_write_then_read_round_trips_to_not_stale():
    """After a successful match the same inputs must read as up to date —
    otherwise the nightly job re-matches forever."""
    client = FakeClient({"master": FakeDatabase()})
    status = _status()
    write_match_state(client, "master", status, "2026-09-29_match", reports=37,
                      export_path="/var/lib/ctm/matches/2026-09-29_trial_match.json")

    stored = client["master"][STATE_COLLECTION].find_one({"_id": MATCH_STATE_ID})
    assert stored["match_db"] == "2026-09-29_match"
    assert stored["reports"] == 37

    assert not _status(last_match=stored).stale



# ── ctm-status rendering ─────────────────────────────────────────────────────

def test_unloaded_workbook_is_flagged(tmp_path):
    """The failure the whole reconciler design opened with: trials refreshed,
    patient data not, so the match runs against a stale cohort."""
    from ctm.status_cli import _unloaded_workbook

    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "2026-09-29-patients.xlsx").touch()
    loaded_last_week = Watermark("patients", "latest_clinical", 37, LAST_WEEK)
    assert _unloaded_workbook(raw, loaded_last_week)

    loaded_just_now = Watermark("patients", "latest_clinical", 37,
                                datetime.now(tz=UTC) + timedelta(minutes=1))
    assert not _unloaded_workbook(raw, loaded_just_now)

