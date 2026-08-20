"""Tests for the AMC feed fetch and the pull timestamps.

`--amc` mirrors `--ddots`: a bare flag pulls from the source, a path reads a local
export, and omitting it skips the source entirely so a forgotten flag can never
trigger a live pull.

conftest's no_network fixture makes a real urlopen a hard failure, so the fetch
tests stub it explicitly.
"""
import io
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import pytest

XML = """<PROTOCOL_SUMMARY>
  <PROTOCOL>
    <ID>1</ID>
    <NO>2021.070</NO>
    <NCT_NUMBER>NCT04858334</NCT_NUMBER>
    <STATUS>OPEN TO ACCRUAL</STATUS>
    <TITLE>First Trial</TITLE>
    <ELIGIBILITY>Inclusion Criteria:
~Age &gt;= 18</ELIGIBILITY>
  </PROTOCOL>
  <PROTOCOL>
    <ID>2</ID>
    <NO>2021.071</NO>
    <NCT_NUMBER>NCT04858335</NCT_NUMBER>
    <STATUS>OPEN TO ACCRUAL</STATUS>
    <TITLE>Second Trial</TITLE>
    <ELIGIBILITY>Inclusion Criteria:
~Age &gt;= 21</ELIGIBILITY>
  </PROTOCOL>
</PROTOCOL_SUMMARY>"""


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub_feed(monkeypatch, body: str, urls: list | None = None):
    from ctm.transformers import amc_xml_to_raw

    def _urlopen(url, timeout=None):
        if urls is not None:
            urls.append(url)
        return _Response(body.encode())

    monkeypatch.setattr(amc_xml_to_raw.urllib.request, "urlopen", _urlopen)


def test_parse_shares_one_timestamp_across_the_whole_pull():
    """One pull is one moment; per-trial microseconds would imply otherwise."""
    from ctm.transformers.amc_xml_to_raw import parse

    trials = parse(ET.fromstring(XML))

    assert len(trials) == 2
    assert trials[0].fetched_at == trials[1].fetched_at
    assert trials[0].fetched_at.tzinfo is not None, "must be timezone-aware"


def test_parse_accepts_an_explicit_timestamp():
    """A local export's real age is not knowable from its contents, so a caller
    that does know can supply it."""
    from ctm.transformers.amc_xml_to_raw import parse

    when = datetime(2026, 8, 3, 14, 36, tzinfo=UTC)
    assert all(t.fetched_at == when for t in parse(ET.fromstring(XML), fetched_at=when))


def test_load_stamps_fetched_at(tmp_path):
    from ctm.transformers.amc_xml_to_raw import load

    path = tmp_path / "amc.xml"
    path.write_text(XML)

    before = datetime.now(tz=UTC)
    trials = load(path)

    assert trials[0].fetched_at >= before


def test_fetch_uses_the_octsu_feed_by_default(monkeypatch):
    from ctm.transformers.amc_xml_to_raw import FEED_URL, fetch

    urls = []
    _stub_feed(monkeypatch, XML, urls)
    monkeypatch.delenv("AMC_FEED_URL", raising=False)

    trials = fetch()

    assert urls == [FEED_URL]
    assert FEED_URL.startswith("https://octsu.med.umich.edu/xmlfeed/")
    assert [t.protocol_no for t in trials] == ["2021.070", "2021.071"]
    assert trials[0].fetched_at == trials[1].fetched_at


def test_fetch_honours_the_url_override(monkeypatch):
    """Needed to point at a staging copy without editing code."""
    from ctm.transformers.amc_xml_to_raw import fetch

    urls = []
    _stub_feed(monkeypatch, XML, urls)

    monkeypatch.setenv("AMC_FEED_URL", "https://example.test/feed.xml")
    fetch()
    assert urls[-1] == "https://example.test/feed.xml"

    fetch(url="https://explicit.test/feed.xml")
    assert urls[-1] == "https://explicit.test/feed.xml", "argument beats the env var"


def test_fetch_reports_a_transport_failure_with_the_url(monkeypatch):
    """Unauthenticated feed, so unlike DDOTS the URL is safe to name — and naming
    it is the difference between a usable error and a mystery."""
    from ctm.transformers import amc_xml_to_raw

    monkeypatch.delenv("AMC_FEED_URL", raising=False)
    monkeypatch.setattr(amc_xml_to_raw.urllib.request, "urlopen",
                        lambda url, timeout=None: (_ for _ in ()).throw(OSError("refused")))

    with pytest.raises(RuntimeError, match=re.escape("octsu.med.umich.edu")):
        amc_xml_to_raw.fetch()


def test_fetch_reports_unparseable_xml_distinctly(monkeypatch):
    """Malformed markup would otherwise surface as a bare ParseError with no
    indication of where it came from."""
    from ctm.transformers import amc_xml_to_raw

    # Genuinely malformed: unclosed <br>, as a real HTML error page tends to be.
    _stub_feed(monkeypatch, "<html><body>503 Service Unavailable<br></body></html>")

    with pytest.raises(RuntimeError, match="unparseable XML"):
        amc_xml_to_raw.fetch()


def test_fetch_rejects_well_formed_xml_that_is_not_the_feed(monkeypatch):
    """The dangerous case: an error page can be *valid* XML. It parses, contains no
    <PROTOCOL>, and would yield zero trials indistinguishably from an empty feed."""
    from ctm.transformers import amc_xml_to_raw

    _stub_feed(monkeypatch, "<html><body>503 Service Unavailable</body></html>")

    with pytest.raises(RuntimeError, match="no <PROTOCOL> elements") as excinfo:
        amc_xml_to_raw.fetch()
    assert "<html>" in str(excinfo.value), "name the root element that arrived"


def test_load_stays_tolerant_of_an_empty_local_export(tmp_path):
    """An empty file is the caller's own, unlike whatever a server chose to send."""
    from ctm.transformers.amc_xml_to_raw import load

    path = tmp_path / "empty.xml"
    path.write_text("<PROTOCOL_SUMMARY></PROTOCOL_SUMMARY>")

    assert load(path) == []


def test_fetch_timestamp_precedes_the_parse(monkeypatch):
    """Stamped before the request, so it marks when data was asked for rather than
    how long parsing took."""
    from ctm.transformers.amc_xml_to_raw import fetch

    _stub_feed(monkeypatch, XML)
    before = datetime.now(tz=UTC)
    trials = fetch()
    after = datetime.now(tz=UTC)

    assert before <= trials[0].fetched_at <= after


# ── CLI wiring ────────────────────────────────────────────────────────────────

def _trials_args(*argv):
    import sys as _sys
    from unittest.mock import patch

    from ctm import mm_cli

    captured = {}
    with patch.object(mm_cli, "_cmd_trials", lambda a: captured.setdefault("args", a)), \
         patch.object(_sys, "argv", ["ctm-mm", "trials", *argv]):
        mm_cli.main()
    return captured["args"]


def test_bare_amc_means_fetch_and_a_path_means_read():
    from ctm.mm_cli import _AMC_FETCH

    assert _trials_args("--out", "o.json", "--amc").amc == _AMC_FETCH
    assert _trials_args("--out", "o.json", "--amc", "amc.xml").amc == "amc.xml"


def test_omitting_amc_does_not_fetch():
    """Omission means "no AMC trials this run" — what makes --ddots-only or
    --west-only runs work."""
    assert _trials_args("--out", "o.json", "--ddots").amc is None


def test_cmd_trials_bare_amc_fetches_the_feed(tmp_path, monkeypatch, fake_mongo):
    import json

    from ctm.mm_cli import _cmd_trials

    _stub_feed(monkeypatch, XML)
    out = tmp_path / "o.json"
    _cmd_trials(_trials_args("--out", str(out), "--amc"))

    trials = json.loads(out.read_text())
    assert [t["protocol_no"] for t in trials] == ["2021.070", "2021.071"]
    assert {t["entity"] for t in trials} == {"amc"}
    # The pull timestamp survives into the stored raw blob.
    assert trials[0]["_raw"]["fetched_at"]


def test_cmd_trials_amc_path_still_reads_a_local_export(tmp_path, fake_mongo):
    """The existing invocation must keep working unchanged."""
    import json

    from ctm.mm_cli import _cmd_trials

    path = tmp_path / "amc.xml"
    path.write_text(XML)
    out = tmp_path / "o.json"

    _cmd_trials(_trials_args("--out", str(out), "--amc", str(path)))

    assert len(json.loads(out.read_text())) == 2


def test_ddots_pull_is_timestamped_independently_of_clinicaltrials_gov():
    """The two are separate calls to separate APIs; dating one from the other
    would be a guess."""
    from pathlib import Path

    from ctm.transformers.ddots_to_raw import load

    fixture = Path(__file__).parent / "fixtures" / "ddots-protocol-response.json"
    trials = load(fixture)

    assert trials[0].fetched_at is not None
    assert trials[0].fetched_at.tzinfo is not None
    assert trials[0].fetched_at == trials[1].fetched_at
