"""ctm-fetch --amc: fetching AMC's OnCORE feed and archiving the raw records.

Nothing here touches the network or a Mongo server. The feed is stubbed at
`amc_feed_to_raw.fetch_xml` — the module's single transport seam — and the
`fake_mongo` fixture replaces db.py's driver-touching functions and captures
what would have been written, mirroring test_mm_cli_trials_pipeline.py.
"""
import argparse
import json
import sys
import urllib.error
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
AMC_FIXTURE = FIXTURES / "test-trials-amc-v0.0.1.xml"

_TWO_TRIAL_FEED = b"""<PROTOCOL_SUMMARY>
  <PROTOCOL>
    <ID>10001</ID>
    <NO>2099.014</NO>
    <NCT_NUMBER>NCT90000014</NCT_NUMBER>
    <STATUS>OPEN TO ACCRUAL</STATUS>
    <TITLE>First Trial</TITLE>
    <AGE_GROUP>Adults</AGE_GROUP>
    <ELIGIBILITY>Inclusion Criteria||~Age &gt;= 18</ELIGIBILITY>
  </PROTOCOL>
  <PROTOCOL>
    <ID>10002</ID>
    <NO>2099.015</NO>
    <NCT_NUMBER>NCT90000015</NCT_NUMBER>
    <STATUS>CLOSED TO ACCRUAL</STATUS>
    <TITLE>Second Trial</TITLE>
    <AGE_GROUP>Children</AGE_GROUP>
    <ELIGIBILITY>Inclusion Criteria||~Age &lt; 18</ELIGIBILITY>
  </PROTOCOL>
</PROTOCOL_SUMMARY>"""


@pytest.fixture
def fake_mongo(monkeypatch):
    """Capture Mongo writes without a server. Returns the captured state."""
    captured = {"written": None, "opened": []}

    def _get_database(config, db_name=None):
        captured["opened"].append(db_name or config["dbname"])
        return f"<db {db_name or config['dbname']}>"

    def _replace_collection(db, name, docs, unique_key, lookup_keys=()):
        captured["written"] = {
            "db": db, "name": name, "docs": docs,
            "unique_key": unique_key, "lookup_keys": lookup_keys,
        }

    monkeypatch.setenv("MONGO_HOST", "localhost")
    monkeypatch.setenv("MONGO_PORT", "27018")
    monkeypatch.setenv("MONGO_DBNAME", "2026-08-19_test")
    monkeypatch.setattr("ctm.db.get_database", _get_database)
    monkeypatch.setattr("ctm.db.replace_collection", _replace_collection)
    monkeypatch.setattr("ctm.db.toolkit_version", lambda: "9.9.9")
    return captured


class _Response:
    """Minimal stand-in for what urlopen returns."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@pytest.fixture
def feed(monkeypatch):
    """Stub the HTTP transport itself, overriding conftest's network blocker.

    Patching urlopen rather than fetch_xml keeps fetch_xml's own error
    translation under test — that is where an HTTPError becomes a ValueError
    naming the URL, which is the whole reason the wrapper exists.

    Assign an Exception to state["payload"] to make the transport raise.
    """
    state = {"payload": _TWO_TRIAL_FEED, "requested": None}

    def _urlopen(request, timeout=None):
        payload = state["payload"]
        if isinstance(payload, Exception):
            raise payload
        state["requested"] = request.full_url
        state["timeout"] = timeout
        return _Response(payload)

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    # A .env on the developer's machine must not leak into these assertions.
    monkeypatch.setattr("ctm.fetch_cli.load_env", lambda: None)
    return state


def _run(output: Path, *extra: str) -> None:
    """Invoke ctm-fetch's main() through argv, as a user would."""
    from ctm import fetch_cli

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["ctm-fetch", "--amc", "--output", str(output), *extra])
        fetch_cli.main()


# --- the happy path ---------------------------------------------------------


def test_amc_writes_normalized_ctml_list(feed, fake_mongo, tmp_path):
    """Output is a JSON list of CTML trials, hashed like `ctm-mm trials` output."""
    out_path = tmp_path / "out.json"

    _run(out_path)

    trials = json.loads(out_path.read_text())
    assert isinstance(trials, list)
    assert [t["protocol_no"] for t in trials] == ["2099.014", "2099.015"]
    for trial in trials:
        assert trial["entity"] == "amc"
        assert len(trial["trial_hash"]) == 64  # sha256 hex digest
        assert "_summary" in trial and "_raw" in trial


def test_amc_normalizes_the_whole_feed(feed, fake_mongo, tmp_path):
    """The feed is a full snapshot — every <PROTOCOL> must come through."""
    feed["payload"] = AMC_FIXTURE.read_bytes()
    out_path = tmp_path / "out.json"

    _run(out_path)

    trials = json.loads(out_path.read_text())
    assert len(trials) == 1
    assert trials[0]["status"] == "open to accrual"  # OPEN TO ACCRUAL normalized


def test_amc_requests_the_hardcoded_feed_url(feed, fake_mongo, tmp_path):
    """The URL is not configurable, so a drift here is a silent wrong-source bug."""
    from ctm.transformers.amc_feed_to_raw import FEED_URL

    _run(tmp_path / "out.json")

    assert feed["requested"] == FEED_URL
    assert FEED_URL.endswith(".xml")
    assert feed["timeout"] is not None, "an institutional feed must not hang forever"


# --- archiving to 00_raw_trials --------------------------------------------


def test_amc_archives_raw_records_to_the_raw_collection(feed, fake_mongo, tmp_path):
    _run(tmp_path / "out.json")

    written = fake_mongo["written"]
    assert written["name"] == "00_raw_trials"
    assert written["unique_key"] == "protocol_no"
    assert written["lookup_keys"] == ("entity", "nct_number")
    assert len(written["docs"]) == 2


def test_archived_docs_are_raw_not_normalized(feed, fake_mongo, tmp_path):
    """The point of 00_raw_trials is the pre-normalization record."""
    _run(tmp_path / "out.json")

    doc = fake_mongo["written"]["docs"][0]
    # Raw field names, not their normalized counterparts
    assert doc["nct_number"] == "NCT90000014"
    assert doc["amc_id"] == "10001"
    assert doc["status"] == "OPEN TO ACCRUAL"  # unnormalized
    assert "_summary" not in doc
    assert "treatment_list" not in doc


def test_archived_docs_carry_provenance(feed, fake_mongo, tmp_path):
    _run(tmp_path / "out.json", "--run-date", "2026-08-19")

    for doc in fake_mongo["written"]["docs"]:
        assert doc["entity"] == "amc"
        assert doc["processed_with"] == "ctm-fetch --amc 9.9.9"
        assert doc["run_date"] == "2026-08-19"


def test_every_archived_doc_has_the_unique_key(feed, fake_mongo, tmp_path):
    """replace_collection refuses unkeyed documents; don't hand it any."""
    _run(tmp_path / "out.json")

    written = fake_mongo["written"]
    assert all(doc.get(written["unique_key"]) for doc in written["docs"])


def test_amc_writes_to_the_run_database(feed, fake_mongo, tmp_path):
    _run(tmp_path / "out.json")

    assert fake_mongo["opened"] == ["2026-08-19_test"]


def test_db_flag_overrides_the_run_database(feed, fake_mongo, tmp_path):
    _run(tmp_path / "out.json", "--db", "override_db")

    assert fake_mongo["opened"] == ["override_db"]


def test_no_store_skips_mongo_but_still_writes_json(feed, fake_mongo, tmp_path):
    out_path = tmp_path / "out.json"

    _run(out_path, "--no-store")

    assert fake_mongo["written"] is None
    assert len(json.loads(out_path.read_text())) == 2


def test_no_store_does_not_need_the_mongo_variables(feed, tmp_path, monkeypatch):
    """--no-store must work with no Mongo configuration at all."""
    for name in ("MONGO_HOST", "MONGO_PORT", "MONGO_DBNAME"):
        monkeypatch.delenv(name, raising=False)
    out_path = tmp_path / "out.json"

    _run(out_path, "--no-store")

    assert len(json.loads(out_path.read_text())) == 2


def test_missing_mongo_config_names_the_variable_and_the_escape_hatch(
    feed, tmp_path, monkeypatch, capsys
):
    for name in ("MONGO_HOST", "MONGO_PORT", "MONGO_DBNAME"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit) as exc:
        _run(tmp_path / "out.json")

    assert exc.value.code == 1
    stderr = capsys.readouterr().err
    assert "MONGO_HOST" in stderr
    assert "--no-store" in stderr


# --- feed failures ----------------------------------------------------------


def test_http_error_names_the_status_and_url(feed, fake_mongo, tmp_path, capsys):
    from ctm.transformers.amc_feed_to_raw import FEED_URL

    feed["payload"] = urllib.error.HTTPError(FEED_URL, 404, "Not Found", {}, None)
    out_path = tmp_path / "out.json"

    with pytest.raises(SystemExit) as exc:
        _run(out_path)

    assert exc.value.code == 1
    stderr = capsys.readouterr().err
    assert "404" in stderr
    assert not out_path.exists()


def test_unreachable_feed_mentions_network_access(feed, fake_mongo, tmp_path, capsys):
    feed["payload"] = urllib.error.URLError("Connection refused")

    with pytest.raises(SystemExit) as exc:
        _run(tmp_path / "out.json")

    assert exc.value.code == 1
    assert "octsu.med.umich.edu" in capsys.readouterr().err


def test_a_login_page_is_not_reported_as_an_empty_feed(feed, fake_mongo, tmp_path, capsys):
    """A login redirect arrives as HTTP 200 and is often well-formed markup.

    It therefore parses cleanly with zero <PROTOCOL> elements. Reporting that as
    "the feed is empty" would send someone hunting upstream at AMC for what is
    really an auth or URL problem, so the root tag is what gets checked.
    """
    feed["payload"] = b"<html><body>Sign in to continue</body></html>"
    out_path = tmp_path / "out.json"

    with pytest.raises(SystemExit) as exc:
        _run(out_path)

    assert exc.value.code == 1
    stderr = capsys.readouterr().err
    assert "<html>" in stderr and "PROTOCOL_SUMMARY" in stderr
    assert "Sign in to continue" in stderr  # opening bytes, for diagnosis
    assert "empty" not in stderr
    assert not out_path.exists()


def test_malformed_xml_is_reported_with_its_opening_bytes(feed, fake_mongo, tmp_path, capsys):
    feed["payload"] = b"{\"error\": \"not xml at all\"}"
    out_path = tmp_path / "out.json"

    with pytest.raises(SystemExit) as exc:
        _run(out_path)

    assert exc.value.code == 1
    stderr = capsys.readouterr().err
    assert "parseable XML" in stderr
    assert "not xml at all" in stderr
    assert not out_path.exists()


def test_empty_feed_is_an_error_not_an_empty_list(feed, fake_mongo, tmp_path, capsys):
    """Writing [] would reach trials-diff and route every AMC trial to 'deleted'."""
    feed["payload"] = b"<PROTOCOL_SUMMARY></PROTOCOL_SUMMARY>"
    out_path = tmp_path / "out.json"

    with pytest.raises(SystemExit) as exc:
        _run(out_path)

    assert exc.value.code == 1
    assert "no <PROTOCOL> elements" in capsys.readouterr().err
    assert not out_path.exists()
    assert fake_mongo["written"] is None


def test_a_failed_fetch_archives_nothing(feed, fake_mongo, tmp_path):
    feed["payload"] = urllib.error.URLError("Connection refused")

    with pytest.raises(SystemExit):
        _run(tmp_path / "out.json")

    assert fake_mongo["written"] is None


# --- parser wiring ----------------------------------------------------------


@pytest.mark.parametrize("argv", [
    ["--output", "x.json"],                                   # no source
    ["--nct", "NCT03067181", "--amc", "--output", "x.json"],  # both sources
    ["--amc"],                                                # no --output
])
def test_invalid_source_combinations_are_rejected(argv, monkeypatch):
    from ctm import fetch_cli

    monkeypatch.setattr("ctm.fetch_cli.load_env", lambda: None)
    monkeypatch.setattr(sys, "argv", ["ctm-fetch", *argv])

    with pytest.raises(SystemExit) as exc:
        fetch_cli.main()

    assert exc.value.code == 2  # argparse usage error


def test_fmt_mm_with_amc_is_rejected(feed, fake_mongo, tmp_path, capsys):
    """--amc output is always CTML, so --fmt-mm is a no-op worth naming."""
    out_path = tmp_path / "out.json"

    with pytest.raises(SystemExit) as exc:
        _run(out_path, "--fmt-mm")

    assert exc.value.code == 1
    assert "--fmt-mm" in capsys.readouterr().err
    assert not out_path.exists()


@pytest.mark.parametrize("extra", [
    ["--db", "somedb"],
    ["--run-date", "2026-08-19"],
    ["--no-store"],
])
def test_amc_only_flags_are_rejected_with_nct(extra, monkeypatch, capsys, tmp_path):
    """Silently ignoring them would make the command line lie about the run."""
    from ctm import fetch_cli

    monkeypatch.setattr("ctm.fetch_cli.load_env", lambda: None)
    monkeypatch.setattr(sys, "argv", [
        "ctm-fetch", "--nct", "NCT03067181",
        "--output", str(tmp_path / "out.json"), *extra,
    ])

    with pytest.raises(SystemExit) as exc:
        fetch_cli.main()

    assert exc.value.code == 1
    assert "--amc only" in capsys.readouterr().err


# --- handoff into ctm-mm trials --------------------------------------------


def test_ctm_mm_trials_accepts_the_normalized_json(feed, fake_mongo, tmp_path):
    """`ctm-fetch --amc` output feeds back into --amc, matching the XML path."""
    from ctm.mm_cli import _cmd_trials

    feed["payload"] = AMC_FIXTURE.read_bytes()
    fetched = tmp_path / "amc-normalized.json"
    _run(fetched)

    from_json = tmp_path / "from-json.json"
    from_xml = tmp_path / "from-xml.json"
    for amc_input, out in ((fetched, from_json), (AMC_FIXTURE, from_xml)):
        _cmd_trials(argparse.Namespace(
            amc=str(amc_input), ct=None, sparrow=None, west=None, out=str(out),
        ))

    assert json.loads(from_json.read_text()) == json.loads(from_xml.read_text())


def test_ctm_mm_trials_rejects_an_unknown_amc_suffix(tmp_path, capsys):
    from ctm.mm_cli import _cmd_trials

    bogus = tmp_path / "amc.csv"
    bogus.write_text("protocol_no\n2099.014\n")

    with pytest.raises(SystemExit) as exc:
        _cmd_trials(argparse.Namespace(
            amc=str(bogus), ct=None, sparrow=None, west=None,
            out=str(tmp_path / "out.json"),
        ))

    assert exc.value.code == 1
    assert ".xml or .json" in capsys.readouterr().err


# --- the in-memory parser used by the feed ---------------------------------


def test_from_root_matches_load(tmp_path):
    """The feed and file paths share one parser; they must not drift apart."""
    import xml.etree.ElementTree as ET

    from ctm.transformers.amc_xml_to_raw import from_root, load

    from_bytes = from_root(ET.fromstring(AMC_FIXTURE.read_bytes()))
    from_path = load(AMC_FIXTURE)

    assert [t.model_dump() for t in from_bytes] == [t.model_dump() for t in from_path]
    assert from_bytes[0].protocol_no == "2099.014"
