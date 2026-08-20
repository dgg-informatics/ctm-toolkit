"""Shared fixtures. Keeps the suite offline and deterministic.

The West and Sparrow transformers only get an NCT number from their Excel sheet;
the trial content comes from a live ClinicalTrials.gov call. Tests replace that
call with canned responses from tests/fixtures/clinicaltrial_gov/.

Two things are patched, and both matter:

* Each transformer's own ``fetch`` name. They do a module-level
  ``from .ctgov_to_raw import fetch``, which copies the function object into
  their namespace — so patching ``ctgov_to_raw.fetch`` would leave them calling
  the real one.
* ``urllib.request.urlopen``, so a seam we forgot fails loudly instead of
  quietly reaching the network and passing.
"""
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
CTGOV_DIR = FIXTURES / "clinicaltrial_gov"


def load_ctgov_study(nct_id: str) -> dict:
    """Canned ClinicalTrials.gov API response, in the shape fetch() receives."""
    path = CTGOV_DIR / f"{nct_id}.json"
    if not path.exists():
        raise ValueError(f"NCT ID not found: {nct_id}")  # mirrors fetch()'s 404
    return json.loads(path.read_text())


@pytest.fixture(autouse=True)
def no_real_env(monkeypatch):
    """Never ingest the developer's real .env, and never inherit exported config.

    ``load_env()`` mutates ``os.environ`` for the whole pytest process, so one
    test reaching ``main()`` leaks real MONGO_* / UMGPT_* values into every test
    that runs after it. That is not theoretical: it let a trials-curate test
    write a live ``04_curated_trials`` collection into a real dated database.

    Patched where the name is *used*, for the same reason as ``fetch`` below —
    both CLIs do a module-level ``from ctm.paths import load_env``, so patching
    ``ctm.paths.load_env`` would leave them calling the real one.
    """
    for module in ("ctm.mm_cli", "ctm.llm_cli"):
        monkeypatch.setattr(f"{module}.load_env", lambda: None)
    for name in ("MONGO_HOST", "MONGO_PORT", "MONGO_DBNAME",
                 "MONGO_MASTER_DBNAME", "MONGO_MASTER_COLLECTION"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any real HTTP call is a test bug — fail with a message that says so."""
    def _blocked(*args, **kwargs):
        raise RuntimeError(
            "test attempted a real network call; add a canned response under "
            "tests/fixtures/clinicaltrial_gov/ and patch the relevant fetch seam"
        )
    monkeypatch.setattr("urllib.request.urlopen", _blocked)


@pytest.fixture
def stub_ctgov(monkeypatch):
    """Point both transformers at the canned responses."""
    from ctm.transformers.ctgov_to_raw import from_study

    def _fetch(nct_id: str):
        return from_study(load_ctgov_study(nct_id))

    # patch where the name is *used*, not where it is defined. Every module that
    # does `from .ctgov_to_raw import fetch` needs its own entry here — a missing
    # one shows up as no_network's "attempted a real network call".
    monkeypatch.setattr("ctm.transformers.raw_west_to_ctml.fetch", _fetch)
    monkeypatch.setattr("ctm.transformers.raw_sparrow_to_ctml.fetch", _fetch)
    monkeypatch.setattr("ctm.transformers.raw_ddots_to_ctml.fetch", _fetch)
    return _fetch


@pytest.fixture
def fake_mongo(monkeypatch):
    """Capture Mongo reads/writes without a server. Returns the captured state."""
    captured = {"master": [], "written": None, "opened": []}

    def _get_database(config, db_name=None):
        captured["opened"].append(db_name or config["dbname"])
        return f"<db {db_name or config['dbname']}>"

    def _read_collection(db, name, query=None, keep_metadata=False):
        captured["read_from"] = (db, name)
        captured.setdefault("queries", []).append({"name": name, "query": query})
        # Per-collection sources for the chained stages; `master` is the default
        # so the trials-diff tests keep reading the way they always did.
        return captured.get("collections", {}).get(name, captured["master"])

    def _replace_collection(db, name, docs, unique_key, lookup_keys=()):
        captured["written"] = {
            "db": db, "name": name, "docs": docs,
            "unique_key": unique_key, "lookup_keys": lookup_keys,
        }

    def _prepare_collection(db, name, unique_key, lookup_keys=()):
        captured["prepared"] = {"db": db, "name": name, "unique_key": unique_key}
        return f"<collection {name}>"

    def _upsert_doc(collection, doc, unique_key):
        captured.setdefault("upserted", []).append({"collection": collection, "doc": doc})

    monkeypatch.setenv("MONGO_HOST", "localhost")
    monkeypatch.setenv("MONGO_PORT", "27018")
    monkeypatch.setenv("MONGO_DBNAME", "2026-08-17_test")
    monkeypatch.setenv("MONGO_MASTER_DBNAME", "ctm_master_test")
    monkeypatch.delenv("MONGO_MASTER_COLLECTION", raising=False)
    monkeypatch.setattr("ctm.db.get_database", _get_database)
    monkeypatch.setattr("ctm.db.read_collection", _read_collection)
    monkeypatch.setattr("ctm.db.replace_collection", _replace_collection)
    monkeypatch.setattr("ctm.db.prepare_collection", _prepare_collection)
    monkeypatch.setattr("ctm.db.upsert_doc", _upsert_doc)
    return captured


def _trials_args(*argv):
    """A `trials` Namespace built by the real parser.

    Hand-rolling argparse.Namespace here meant every new source flag broke this
    test with an AttributeError; going through the parser keeps defaults in one
    place and makes a missing flag impossible.
    """
    import sys as _sys
    from unittest.mock import patch

    from ctm import mm_cli

    captured = {}
    with patch.object(mm_cli, "_cmd_trials", lambda a: captured.setdefault("args", a)), \
         patch.object(_sys, "argv", ["ctm-mm", "trials", *argv]):
        mm_cli.main()
    return captured["args"]
