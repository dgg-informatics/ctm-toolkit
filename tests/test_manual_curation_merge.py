"""Tests for add-manual, the Mongo trials-merge, and curation provenance.

Reconciliation and provenance are pure/dict logic, so most of this runs without a
server; the CLI paths use the fake_mongo fixture from conftest.
"""
import argparse
import json
from datetime import UTC, datetime, timedelta

import pytest

# ── reconcile_master ─────────────────────────────────────────────────────────

def _t(key, entity="amc", **extra):
    field = "protocol_no" if entity == "amc" else "nct_id"
    other = "nct_id" if entity == "amc" else "protocol_no"
    return {"entity": entity, field: key, other: None, **extra}


def test_reconcile_carries_forward_supersedes_and_deletes():
    from ctm.trials_lifecycle import reconcile_master

    previous = [_t("KEEP", note="old"), _t("CHG", note="old"), _t("DEL", note="old")]
    new_curated = [_t("CHG", note="fresh"), _t("NEW", note="fresh")]

    result = reconcile_master(previous, new_curated, deleted_keys={"DEL"})
    by_key = {t.get("protocol_no"): t for t in result}

    assert set(by_key) == {"KEEP", "CHG", "NEW"}
    assert by_key["KEEP"]["note"] == "old"      # carried forward untouched
    assert by_key["CHG"]["note"] == "fresh"     # superseded by the curated version
    assert "DEL" not in by_key                  # dropped via the deleted keys


def test_reconcile_keys_on_trial_key_not_hash():
    """A changed trial's hash differs between old master row and new curation, so
    supersession must key on trial_key."""
    from ctm.trials_lifecycle import reconcile_master

    previous = [_t("CHG", trial_hash="a" * 64)]
    new_curated = [_t("CHG", trial_hash="b" * 64)]

    result = reconcile_master(previous, new_curated, deleted_keys=set())
    assert len(result) == 1
    assert result[0]["trial_hash"] == "b" * 64  # the new one won


def test_reconcile_empty_previous_master_is_just_the_curated_set():
    from ctm.trials_lifecycle import reconcile_master

    result = reconcile_master([], [_t("NEW")], deleted_keys=set())
    assert [t["protocol_no"] for t in result] == ["NEW"]


# ── curation provenance ──────────────────────────────────────────────────────

def test_stamp_curation_defaults_to_the_os_user():
    from ctm.db import stamp_curation

    d = stamp_curation({"trial_hash": "a" * 64})
    assert d["curated_by"] == "manual-curation"
    assert d["curated_by_user"]                       # from getpass, non-empty
    assert d["curated_at"].tzinfo is not None


def test_stamp_curation_user_override():
    from ctm.db import stamp_curation

    d = stamp_curation({}, curated_by_user="jcurator")
    assert d["curated_by_user"] == "jcurator"


def test_curated_fields_survive_strip_metadata():
    """They must be sticky across reads — not stripped like the run envelope."""
    from ctm.db import stamp_curation, strip_metadata

    d = stamp_curation({"trial_hash": "a" * 64, "run_date": "2026-08-21"})
    stripped = strip_metadata(d)
    assert "run_date" not in stripped               # envelope, stripped
    assert stripped["curated_by"] == "manual-curation"   # curation, kept
    assert "curated_at" in stripped


def test_validate_master_requires_curation():
    from pydantic import ValidationError

    from ctm.schemas.storage import validate_master

    base = {"trial_key": "X", "trial_hash": "a" * 64,
            "processed_with": "ctm-mm trials-merge 1.2.0", "run_date": "2026-08-21",
            "curated_by": "manual-curation", "curated_by_user": "jcurator",
            "curated_at": datetime.now(tz=UTC)}
    validate_master(base)                            # full doc: ok

    for missing in ("curated_by", "curated_by_user", "curated_at"):
        with pytest.raises(ValidationError):
            validate_master({k: v for k, v in base.items() if k != missing})


# ── add-manual CLI ───────────────────────────────────────────────────────────

def _amc_curated_trial(protocol_no="2021.070"):
    """A trial with a genuinely CtmlTreatmentList-valid body, via the real normalizer."""
    import xml.etree.ElementTree as ET

    from ctm.transformers.amc_xml_to_raw import parse
    from ctm.transformers.raw_amc_to_ctml import to_ctml_dict
    from ctm.trials_lifecycle import compute_trial_hash

    xml = f"""<PROTOCOL_SUMMARY><PROTOCOL><NO>{protocol_no}</NO>
      <NCT_NUMBER>NCT04858334</NCT_NUMBER><STATUS>OPEN TO ACCRUAL</STATUS>
      <TITLE>T</TITLE><ELIGIBILITY>Inclusion Criteria:
~Age &gt;= 18</ELIGIBILITY></PROTOCOL></PROTOCOL_SUMMARY>"""
    t = to_ctml_dict(parse(ET.fromstring(xml))[0])
    t["trial_hash"] = compute_trial_hash(t)
    t["_llm_curation"] = {"_ctml_suggestions": [], "biomarker_references": []}
    return t


def _add_manual_args(**over):
    base = {"trials": None, "db": None, "run_date": "2026-08-21", "curated_by_user": None}
    return argparse.Namespace(**{**base, **over})


def test_add_manual_stamps_provenance_and_appends(tmp_path, fake_mongo):
    from ctm.db import MANUAL_COLLECTION
    from ctm.mm_cli import _cmd_add_manual

    path = tmp_path / "curated.json"
    path.write_text(json.dumps([_amc_curated_trial()], default=str))

    _cmd_add_manual(_add_manual_args(trials=str(path), curated_by_user="jcurator"))

    # Appends via open_collection (never a drop), one upsert per trial.
    assert fake_mongo["opened_collection"]["name"] == MANUAL_COLLECTION
    assert fake_mongo["prepared"] is None, "05 must not be dropped"

    doc = fake_mongo["upserted"][0]["doc"]
    assert doc["curated_by"] == "manual-curation"
    assert doc["curated_by_user"] == "jcurator"
    assert "curated_at" in doc
    assert doc["processed_with"].startswith("ctm-mm add-manual")


def test_add_manual_rejects_broken_structure(tmp_path, fake_mongo):
    """A curator who mangles the match-clause shape is caught, not silently stored."""
    from ctm.mm_cli import _cmd_add_manual

    trial = _amc_curated_trial()
    trial["treatment_list"] = {"step": [{"match": "should-be-a-list-of-steps"}]}
    path = tmp_path / "broken.json"
    path.write_text(json.dumps([trial], default=str))

    with pytest.raises(SystemExit):
        _cmd_add_manual(_add_manual_args(trials=str(path)))
    assert fake_mongo["upserted"] == [], "nothing stored when structure is broken"


# ── trials-merge CLI ─────────────────────────────────────────────────────────

def _merge_args(**over):
    base = {"db": None, "master_db": None, "master_collection": None,
            "run_date": "2026-08-21", "out": None, "allow_empty_master": False,
            "unchanged": None, "changed": None}
    return argparse.Namespace(**{**base, **over})


def _master_row(key, curated_by_user, days_ago):
    return {"entity": "amc", "protocol_no": key, "nct_id": None,
            "trial_hash": {"KEEP": "a", "CHG": "b", "DEL": "d"}[key] * 64,
            "eligibility": {"inclusion": [], "exclusion": []},
            "treatment_list": {"step": []},
            "curated_by": "manual-curation", "curated_by_user": curated_by_user,
            "curated_at": datetime.now(tz=UTC) - timedelta(days=days_ago)}


def _curated_row(key):
    row = _amc_curated_trial(protocol_no=key)
    return ctm_db_stamp_curation(row)


def ctm_db_stamp_curation(row):
    from ctm.db import stamp_curation
    return stamp_curation(row, curated_by_user="jcurator")


def test_trials_merge_reconciles_and_guards(fake_mongo, monkeypatch, tmp_path):
    from ctm.db import DEFAULT_MASTER_COLLECTION, DIFF_COLLECTION, MANUAL_COLLECTION
    from ctm.mm_cli import _cmd_trials_merge

    # v2 writes a default master backup; keep it inside the tmp dir.
    monkeypatch.setenv("MASTER_TRIAL_EXPORT_DIR", str(tmp_path))

    fake_mongo["collections"] = {
        MANUAL_COLLECTION: [_curated_row("CHG"), _curated_row("NEW")],
        DEFAULT_MASTER_COLLECTION: [
            _master_row("KEEP", "olddev", 200),
            _master_row("CHG", "olddev", 30),
            _master_row("DEL", "olddev", 30),
        ],
        DIFF_COLLECTION: [{"entity": "amc", "protocol_no": "DEL", "nct_id": None,
                           "diff_status": "deleted"}],
    }

    _cmd_trials_merge(_merge_args())

    written = fake_mongo["written"]
    assert written["name"] == DEFAULT_MASTER_COLLECTION
    keys = {t.get("protocol_no") for t in written["docs"]}
    assert keys == {"KEEP", "CHG", "NEW"}          # DEL removed

    by_key = {t.get("protocol_no"): t for t in written["docs"]}
    # KEEP carried forward with its original curator; envelope re-stamped.
    assert by_key["KEEP"]["curated_by_user"] == "olddev"
    assert by_key["KEEP"]["processed_with"].startswith("ctm-mm trials-merge")
    # CHG superseded by the freshly curated version.
    assert by_key["CHG"]["curated_by_user"] == "jcurator"


def test_trials_merge_rejects_a_master_row_missing_curation(fake_mongo):
    from ctm.db import DEFAULT_MASTER_COLLECTION, DIFF_COLLECTION, MANUAL_COLLECTION
    from ctm.mm_cli import _cmd_trials_merge

    uncurated = _amc_curated_trial(protocol_no="BAD")   # no curated_* fields
    fake_mongo["collections"] = {
        MANUAL_COLLECTION: [uncurated],
        DEFAULT_MASTER_COLLECTION: [_master_row("KEEP", "olddev", 10)],
        DIFF_COLLECTION: [],
    }

    with pytest.raises(SystemExit):
        _cmd_trials_merge(_merge_args())
    assert fake_mongo["written"] is None, "master left untouched when a row fails the guard"


def test_trials_merge_empty_manual_is_an_error(fake_mongo, capsys):
    from ctm.db import MANUAL_COLLECTION
    from ctm.mm_cli import _cmd_trials_merge

    fake_mongo["collections"] = {MANUAL_COLLECTION: []}
    with pytest.raises(SystemExit):
        _cmd_trials_merge(_merge_args())
    assert "add-manual first" in capsys.readouterr().err


def test_trials_merge_legacy_file_flow_still_works(tmp_path):
    from ctm.mm_cli import _cmd_trials_merge

    unchanged = tmp_path / "u.json"
    unchanged.write_text(json.dumps([{"entity": "amc", "protocol_no": "2015.063"}]))
    changed = tmp_path / "c.json"
    changed.write_text(json.dumps([{"entity": "amc", "protocol_no": "2021.070"}]))
    out = tmp_path / "master.json"

    _cmd_trials_merge(_merge_args(unchanged=str(unchanged), changed=str(changed), out=str(out)))

    master = json.loads(out.read_text())
    assert [t["protocol_no"] for t in master] == ["2015.063", "2021.070"]
