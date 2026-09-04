"""Tests for ctm-mm match-prep — assemble a frozen match db (match_prep.py + _cmd)."""
import argparse
import json

import pytest

from ctm.match_prep import matchengine_command, synthesize_secrets

# ── synthesize_secrets ────────────────────────────────────────────────────────

def test_synthesize_secrets_from_host_port():
    config = {"uri": None, "host": "localhost", "port": 27018}
    assert synthesize_secrets(config, "2026-09-04_match") == {
        "MONGO_HOST": "localhost", "MONGO_PORT": 27018,
        "MONGO_DBNAME": "2026-09-04_match",
    }


def test_synthesize_secrets_parses_uri_with_credentials():
    config = {"uri": "mongodb://deemer:pw@host.example:27017/?authSource=admin"}
    s = synthesize_secrets(config, "2026-09-04_match")
    assert s["MONGO_HOST"] == "host.example"
    assert s["MONGO_PORT"] == 27017
    assert s["MONGO_DBNAME"] == "2026-09-04_match"
    assert s["MONGO_USERNAME"] == s["MONGO_RO_USERNAME"] == "deemer"
    assert s["MONGO_PASSWORD"] == s["MONGO_RO_PASSWORD"] == "pw"
    assert s["MONGO_AUTH_SOURCE"] == "admin"


def test_synthesize_secrets_defaults_authsource_to_admin_when_absent():
    """A URI with credentials but no authSource/db → matchengine must auth against
    admin (pymongo's default), not the target db name."""
    config = {"uri": "mongodb://deemer:pw@localhost:27017/"}
    s = synthesize_secrets(config, "2026-09-04_match")
    assert s["MONGO_AUTH_SOURCE"] == "admin"


def test_synthesize_secrets_uses_uri_default_db_as_authsource():
    config = {"uri": "mongodb://deemer:pw@localhost:27017/somedb"}
    assert synthesize_secrets(config, "2026-09-04_match")["MONGO_AUTH_SOURCE"] == "somedb"


def test_matchengine_command():
    assert matchengine_command("2026-09-04_match") == [
        "matchengine", "match", "--db", "2026-09-04_match"]


# ── _cmd_match_prep against an in-memory fake client ──────────────────────────

class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find(self, *_):
        return list(self.docs)

    def drop(self):
        self.docs = []

    def insert_many(self, docs):
        self.docs.extend(docs)


class _FakeDB:
    def __init__(self, collections=None):
        self.collections = {k: _FakeCollection(v) for k, v in (collections or {}).items()}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _FakeCollection())


class _FakeClient:
    def __init__(self, dbs=None):
        self.dbs = {k: _FakeDB(v) for k, v in (dbs or {}).items()}

    def __getitem__(self, name):
        return self.dbs.setdefault(name, _FakeDB())


def _match_args(**over):
    d = {"match_db": None, "run_date": "2026-09-04", "trial_db": None,
         "trial_collection": None, "trials_file": None, "clinical_db": None,
         "clinical_collection": None, "genomic_db": None, "genomic_collection": None,
         "pt_data": None, "run": False}
    return argparse.Namespace(**{**d, **over})


def _base_env(monkeypatch):
    monkeypatch.setenv("MONGO_HOST", "localhost")
    monkeypatch.setenv("MONGO_PORT", "27018")
    monkeypatch.setenv("MONGO_MASTER_DBNAME", "latest_trials")
    monkeypatch.setenv("MONGO_PATIENT_DBNAME", "patients_dev")
    monkeypatch.delenv("MONGO_DBNAME", raising=False)


def test_match_prep_assembles_from_mongo_defaults(monkeypatch):
    from ctm import mm_cli

    _base_env(monkeypatch)
    # sources: master trials + latest patient collections, with _id-bearing docs
    client = _FakeClient({
        "latest_trials": {"06_master_trials": [{"_id": 1, "protocol_no": "A"},
                                               {"_id": 2, "protocol_no": "B"}]},
        "patients_dev": {
            "latest_clinical": [{"_id": 10, "SAMPLE_ID": "pt_1", "BIRTH_DATE": "1957-04-28"}],
            "latest_genomic": [{"_id": 20, "SAMPLE_ID": "pt_1", "CLINICAL_ID": 10}],
        },
    })
    monkeypatch.setattr("ctm.db.get_client", lambda config: client)

    mm_cli._cmd_match_prep(_match_args())

    match_db = client["2026-09-04_match"]
    assert [d["protocol_no"] for d in match_db["trial"].docs] == ["A", "B"]
    # clinical copied (id preserved) and healed with matchengine birthdate fields
    clin = match_db["clinical"].docs[0]
    assert clin["_id"] == 10 and clin["BIRTH_DATE_INT"] == 19570428
    # _id + CLINICAL_ID preserved through the copy → link survives
    assert match_db["genomic"].docs[0]["CLINICAL_ID"] == 10


def test_match_prep_from_files_links_pt_data(monkeypatch, tmp_path):
    from ctm import mm_cli

    _base_env(monkeypatch)
    trials_file = tmp_path / "trials.json"
    trials_file.write_text(json.dumps([{"protocol_no": "X"}]))
    pt_file = tmp_path / "pts.json"
    pt_file.write_text(json.dumps({
        "clinical": [{"SAMPLE_ID": "pt_1"}],
        "genomic": [{"SAMPLE_ID": "pt_1", "TRUE_HUGO_SYMBOL": "KRAS"}],
        "extras": {"patients": {}},
    }))

    client = _FakeClient()
    monkeypatch.setattr("ctm.db.get_client", lambda config: client)

    mm_cli._cmd_match_prep(_match_args(trials_file=str(trials_file), pt_data=str(pt_file)))

    match_db = client["2026-09-04_match"]
    assert [d["protocol_no"] for d in match_db["trial"].docs] == ["X"]
    # --pt-data path links genomic → clinical on the fly
    clinical_id = match_db["clinical"].docs[0]["_id"]
    assert match_db["genomic"].docs[0]["CLINICAL_ID"] == clinical_id


def test_match_prep_run_invokes_matchengine_with_synthesized_secrets(monkeypatch):
    from ctm import mm_cli

    _base_env(monkeypatch)
    client = _FakeClient({
        "latest_trials": {"06_master_trials": []},
        "patients_dev": {"latest_clinical": [], "latest_genomic": []},
    })
    monkeypatch.setattr("ctm.db.get_client", lambda config: client)

    calls = {}

    def _fake_run(cmd, env=None):
        calls["cmd"] = cmd
        calls["secrets"] = json.loads(env["SECRETS_JSON"])
        return argparse.Namespace(returncode=0)

    monkeypatch.setattr("subprocess.run", _fake_run)

    with pytest.raises(SystemExit) as exc:
        mm_cli._cmd_match_prep(_match_args(run=True))

    assert exc.value.code == 0
    assert calls["cmd"] == ["matchengine", "match", "--db", "2026-09-04_match"]
    assert calls["secrets"]["MONGO_DBNAME"] == "2026-09-04_match"


def test_match_prep_errors_without_master_db(monkeypatch):
    from ctm import mm_cli

    monkeypatch.setenv("MONGO_HOST", "localhost")
    monkeypatch.setenv("MONGO_PORT", "27018")
    monkeypatch.delenv("MONGO_DBNAME", raising=False)
    monkeypatch.delenv("MONGO_MASTER_DBNAME", raising=False)
    monkeypatch.setattr("ctm.db.get_client", lambda config: _FakeClient())

    with pytest.raises(SystemExit):
        mm_cli._cmd_match_prep(_match_args())
