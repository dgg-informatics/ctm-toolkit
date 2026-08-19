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
