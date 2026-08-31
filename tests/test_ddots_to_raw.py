"""Tests for ddots_to_raw.py — DDOTS /protocol payload → RawDdotsTrial.

Offline: conftest's no_network fixture makes any real urlopen a hard failure, and
the fixture payload is a redacted copy of a real response.
"""
import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "ddots-protocol-response.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text())


def test_rows_zips_columns_onto_data_and_lowercases_keys():
    """The payload is columnar, and COLUMNS come back UPPERCASE while every field
    name elsewhere is lowercase."""
    from ctm.transformers.ddots_to_raw import rows

    result = rows(_payload())

    assert len(result) == 3
    assert result[0]["protocol_title"].startswith("A Phase II Study")
    assert result[0]["nct_number"] == "04871542"
    assert not any(k != k.lower() for k in result[0]), "keys should be lowercase"


def test_rows_handles_an_empty_or_malformed_payload():
    from ctm.transformers.ddots_to_raw import rows

    assert rows({}) == []
    assert rows({"COLUMNS": [], "DATA": [[1, 2]]}) == []  # 0 columns but 2 data return []
    assert rows({"COLUMNS": ["A"], "DATA": []}) == []


@pytest.mark.parametrize("raw,expected", [
    ("04871542", "NCT04871542"),        # DDOTS returns the digits unprefixed
    ("NCT04871542", "NCT04871542"),     # already prefixed
    ("nct04871542", "NCT04871542"),     # lowercase
    (" 04871542 ", "NCT04871542"),      # surrounding whitespace
    ("NCT 04871542", "NCT04871542"),    # internal whitespace
    (4871542, None),                    # too few digits
    ("048715421", None),                # too many digits
    ("not-an-nct", None),
    ("", None),
    (None, None),
])
def test_normalize_nct(raw, expected):
    """The Excel loader's ^NCT\\d{8}$ check would reject every DDOTS value."""
    from ctm.transformers.ddots_to_raw import normalize_nct

    assert normalize_nct(raw) == expected


def test_parse_documents_unwraps_json_inside_json():
    from ctm.transformers.ddots_to_raw import parse_documents

    raw = _payload()["DATA"][0][-1]
    assert isinstance(raw, str)

    parsed = parse_documents(raw)
    assert parsed["protocol_documents"][0]["DOCUMENTTITLE"] == "0424 Protocol (v 12/26/13)"
    assert parsed["consent_documents"] == []


@pytest.mark.parametrize("raw", [None, "not json"])
def test_parse_documents_tolerates_junk(raw):
    """A malformed DOCUMENTS blob must not take down the whole ingest."""
    from ctm.transformers.ddots_to_raw import parse_documents

    assert parse_documents(raw) == {}


def test_to_raw_trials_skips_rows_without_a_usable_nct(capsys):
    """nct_id is the trial's identity and the CTGov lookup key, so a row without
    one is dropped — with a warning, not silently."""
    from ctm.transformers.ddots_to_raw import to_raw_trials

    trials = to_raw_trials(_payload())

    assert [t.nct_id for t in trials] == ["NCT04871542", "NCT05334069"]
    err = capsys.readouterr().err
    assert "no usable NCT number" in err
    assert "0999" in err, "the warning should name the skipped protocol"


def test_to_raw_trials_is_a_thin_verbatim_mapping():
    """Field names pass through as the API spells them (lowercased), so the only
    transformations are NCT prefixing and the DOCUMENTS parse."""
    from ctm.transformers.ddots_to_raw import to_raw_trials

    open_trial = next(t for t in to_raw_trials(_payload()) if t.nct_id == "NCT05334069")

    assert open_trial.protocol_title_short == "OptimICE-PCR"       # renamed field maps
    assert open_trial.disease_site_list == "Breast,Chest Wall"     # comma value kept verbatim, not split
    assert open_trial.investigator == "Doe MD, Jane"               # degree-in-surname quirk survives


def test_to_raw_trials_keeps_the_verbatim_nct_number_alongside_the_normalized_one():
    """Useful for audit: the API's unprefixed value and what we made of it."""
    from ctm.transformers.ddots_to_raw import to_raw_trials

    closed = next(t for t in to_raw_trials(_payload()) if t.nct_id == "NCT04871542")
    assert closed.nct_number == "04871542"
    assert closed.nct_id == "NCT04871542"


def test_error_envelope_raises_instead_of_looking_like_no_results():
    """DDOTS reports faults in a 200 body. Unchecked, the envelope parses into one
    row with no nct_number, gets dropped as "no usable NCT number", and surfaces as
    zero trials — indistinguishable from an empty result set."""
    from ctm.transformers.ddots_to_raw import DdotsApiError, to_raw_trials

    envelope = {"COLUMNS": ["CALLDSN", "ERRORTEXT"],
                "DATA": [["429", "Too Many Requests"]]}

    with pytest.raises(DdotsApiError) as excinfo:
        to_raw_trials(envelope)

    assert excinfo.value.code == "429"
    assert excinfo.value.text == "Too Many Requests"
    assert excinfo.value.is_rate_limited is True
    assert "429" in str(excinfo.value)


def test_error_envelope_detected_regardless_of_column_order():
    from ctm.transformers.ddots_to_raw import DdotsApiError, raise_for_api_error

    with pytest.raises(DdotsApiError) as excinfo:
        raise_for_api_error({"COLUMNS": ["ERRORTEXT", "CALLDSN"],
                             "DATA": [["Unauthorized", "401"]]})

    # Values must follow the payload's own order, not a guessed one.
    assert excinfo.value.code == "401"
    assert excinfo.value.text == "Unauthorized"
    assert excinfo.value.is_rate_limited is False


def test_a_real_payload_is_not_mistaken_for_an_error():
    from ctm.transformers.ddots_to_raw import raise_for_api_error

    raise_for_api_error(_payload())          # must not raise
    raise_for_api_error({})                  # empty is not an error envelope
    raise_for_api_error({"COLUMNS": ["CALLDSN", "ERRORTEXT", "PROTOCOL"],
                         "DATA": [["a", "b", "c"]]})  # superset is data, not the envelope


def test_fetch_raises_the_api_error_rather_than_returning_it(monkeypatch):
    import io

    from ctm.transformers import ddots_to_raw

    monkeypatch.setenv("DDOTS_API_KEY", "k")
    monkeypatch.setenv("DDOTS_SECRET_KEY", "s")

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    body = json.dumps({"COLUMNS": ["CALLDSN", "ERRORTEXT"],
                       "DATA": [["429", "Too Many Requests"]]}).encode()
    monkeypatch.setattr(ddots_to_raw.urllib.request, "urlopen",
                        lambda url, timeout=None: _Response(body))

    with pytest.raises(ddots_to_raw.DdotsApiError) as excinfo:
        ddots_to_raw.fetch()
    assert excinfo.value.is_rate_limited


def test_load_reads_a_saved_dump():
    from ctm.transformers.ddots_to_raw import load

    trials = load(FIXTURE)
    assert [t.nct_id for t in trials] == ["NCT04871542", "NCT05334069"]


def test_load_accepts_a_list_of_payloads(tmp_path):
    """A paginated pull is naturally saved as a list of responses."""
    from ctm.transformers.ddots_to_raw import load

    path = tmp_path / "dump.json"
    path.write_text(json.dumps([_payload(), _payload()]))

    assert len(load(path)) == 4


def test_build_url_names_every_return_field_because_defaults_are_not_additive():
    """return_fields replaces most of the default column set rather than adding to
    it, so a query that omits status/disease_site loses them even though a bare
    call would have included them."""
    from ctm.transformers.ddots_to_raw import DEFAULT_RETURN_FIELDS, build_url

    url = build_url("KEY", "SECRET")

    for field in ("status", "status_short", "disease_site", "disease_category",
                  "investigator", "eligibility"):
        assert field in DEFAULT_RETURN_FIELDS
    assert "status_short=O" in url
    assert "api_key=KEY" in url


def test_build_url_omits_the_status_filter_when_asked():
    """Checked as a query parameter, not a substring: `status_short` is also one of
    the names inside return_fields, so a substring test always passes."""
    import urllib.parse

    from ctm.transformers.ddots_to_raw import build_url

    def params(url):
        return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    assert "status_short" not in params(build_url("K", "S", status_short=None))
    assert params(build_url("K", "S"))["status_short"] == ["O"]
    # Still requested as a returned column either way.
    assert "status_short" in params(build_url("K", "S", status_short=None))["return_fields"][0]


def test_build_url_hospital_scoping():
    """The DDOTS instance is shared across institutions, so an unscoped query
    returns other hospitals' protocols — which this pipeline would then stamp
    entity="sparrow-api". Defaults to one hospital; can be deliberately unscoped."""
    import urllib.parse

    from ctm.transformers.ddots_to_raw import DEFAULT_HOSPITAL_ID, build_url

    def params(url):
        return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    # Default: scoped to the Sparrow hospital, and requested back as a column.
    scoped = params(build_url("K", "S"))
    assert scoped["hospital_id"] == [DEFAULT_HOSPITAL_ID] == ["18"]
    assert "hospital_id" in scoped["return_fields"][0]

    # hospital_id=None omits the scope entirely.
    assert "hospital_id" not in params(build_url("K", "S", hospital_id=None))


def test_fetch_hospital_id_resolution_order(monkeypatch):
    """Explicit argument, then DDOTS_HOSPITAL_ID, then the Sparrow default — so a
    fetch is never accidentally unscoped."""
    import urllib.parse

    from ctm.transformers import ddots_to_raw

    monkeypatch.setenv("DDOTS_API_KEY", "k")
    monkeypatch.setenv("DDOTS_SECRET_KEY", "s")
    seen = []
    monkeypatch.setattr(ddots_to_raw.urllib.request, "urlopen",
                        lambda url, timeout=None: seen.append(url) or (_ for _ in ()).throw(
                            RuntimeError("stop")))

    def hospital_of(url):
        return urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("hospital_id")

    monkeypatch.delenv("DDOTS_HOSPITAL_ID", raising=False)
    with pytest.raises(RuntimeError):
        ddots_to_raw.fetch()
    assert hospital_of(seen[-1]) == ["18"], "falls back to the Sparrow default"

    monkeypatch.setenv("DDOTS_HOSPITAL_ID", "42")
    with pytest.raises(RuntimeError):
        ddots_to_raw.fetch()
    assert hospital_of(seen[-1]) == ["42"], "DDOTS_HOSPITAL_ID wins over the default"

    with pytest.raises(RuntimeError):
        ddots_to_raw.fetch(hospital_id="99")
    assert hospital_of(seen[-1]) == ["99"], "an explicit argument wins over the env"


def test_cmd_trials_passes_the_hospital_id_flag_through(monkeypatch, tmp_path, fake_mongo):
    from ctm.mm_cli import _cmd_trials
    from ctm.transformers import ddots_to_raw

    captured = {}
    monkeypatch.setattr(ddots_to_raw, "fetch",
                        lambda **kw: captured.update(kw) or _payload())
    monkeypatch.setattr("ctm.transformers.raw_ddots_to_ctml.fetch",
                        lambda nct_id: (_ for _ in ()).throw(ValueError("no ctgov in this test")))

    args = _trials_args("--out", str(tmp_path / "o.json"), "--ddots",
                        "--ddots-hospital-id", "7", "--ddots-status-short", "C")
    with pytest.raises(SystemExit):   # every trial skipped, so no trials normalized
        _cmd_trials(args)

    assert captured == {"hospital_id": "7", "status_short": "C"}


def test_fetch_requires_credentials(monkeypatch):
    from ctm.transformers.ddots_to_raw import fetch

    monkeypatch.delenv("DDOTS_API_KEY", raising=False)
    monkeypatch.delenv("DDOTS_SECRET_KEY", raising=False)

    with pytest.raises(ValueError, match="DDOTS_API_KEY"):
        fetch()

    monkeypatch.setenv("DDOTS_API_KEY", "k")
    with pytest.raises(ValueError, match="DDOTS_SECRET_KEY"):
        fetch()


def test_fetch_never_leaks_the_secret_in_an_error(monkeypatch):
    """Credentials travel as query parameters, so an error carrying the URL would
    put the secret into logs and tracebacks."""
    from ctm.transformers import ddots_to_raw

    monkeypatch.setenv("DDOTS_API_KEY", "PUBLICKEY")
    monkeypatch.setenv("DDOTS_SECRET_KEY", "TOPSECRET")

    # conftest's no_network fixture already makes urlopen raise.
    with pytest.raises(RuntimeError) as excinfo:
        ddots_to_raw.fetch()

    message = str(excinfo.value)
    assert "TOPSECRET" not in message
    assert "PUBLICKEY" not in message
    assert "api_secret_key" not in message


# ── CLI wiring ────────────────────────────────────────────────────────────────

def _trials_args(*argv):
    """A `trials` Namespace built by the real parser, so flag defaults stay in one place."""
    import sys as _sys
    from unittest.mock import patch

    from ctm import mm_cli

    captured = {}
    with patch.object(mm_cli, "_cmd_trials", lambda a: captured.setdefault("args", a)), \
         patch.object(_sys, "argv", ["ctm-mm", "trials", *argv]):
        mm_cli.main()
    return captured["args"]


def test_bare_ddots_means_fetch_and_a_path_means_replay():
    """Optional-value flag: `--ddots` queries the API, `--ddots FILE` replays a
    saved response."""
    from ctm.mm_cli import _DDOTS_FETCH

    assert _trials_args("--out", "o.json", "--ddots").ddots == _DDOTS_FETCH
    assert _trials_args("--out", "o.json", "--ddots", "dump.json").ddots == "dump.json"


def test_omitting_ddots_does_not_fetch():
    """Omission means "no sparrow-api trials this run" — the meaning that makes
    --west-only or --amc-only runs work. It must not imply a live pull."""
    args = _trials_args("--out", "o.json", "--amc", "x.xml")
    assert args.ddots is None
    assert args.ddots_status_short == "O"


def test_cmd_trials_ddots_dump_produces_sparrow_api_trials(tmp_path, stub_ctgov, fake_mongo):
    """End to end: DDOTS supplies the trial list, ClinicalTrials.gov supplies the
    clinical content, and the DDOTS payload is kept under its own _raw key.

    Uses conftest's stub_ctgov seam, which patches `fetch` where
    raw_ddots_to_ctml *uses* it — patching ctgov_to_raw.fetch would miss, since the
    module copies the function object in at import.
    """
    import json as _json

    from ctm.mm_cli import _cmd_trials

    out = tmp_path / "o.json"
    _cmd_trials(_trials_args("--out", str(out), "--ddots", str(FIXTURE)))

    trials = _json.loads(out.read_text())
    assert len(trials) == 2, "both NCT-bearing DDOTS rows should normalize"
    assert {t["entity"] for t in trials} == {"sparrow-api"}

    ddots_blob = trials[0]["_raw"]["_ddots"]
    assert ddots_blob["protocol_id"] in (1189, 2201)
    assert ddots_blob["status"] in ("PERMANENTLY CLOSED", "OPEN TO ACCRUAL")
    # Kept apart from the legacy Excel blob so provenance is unambiguous.
    assert "_sparrow" not in trials[0]["_raw"]


def test_ddots_eligibility_is_stored_but_not_used_for_the_normalized_structure(
        tmp_path, stub_ctgov, fake_mongo):
    """The deliberate boundary of this PR: eligibility still comes from
    ClinicalTrials.gov, so the diff key and the LLM stage are unaffected. The DDOTS
    text is retained so the LLM can be taught to read it later without re-pulling.
    """
    import json as _json

    from ctm.mm_cli import _cmd_trials

    out = tmp_path / "o.json"
    _cmd_trials(_trials_args("--out", str(out), "--ddots", str(FIXTURE)))
    trial = _json.loads(out.read_text())[0]

    # Normalized eligibility came from CTGov: nested, split, free of DDOTS markup.
    assert isinstance(trial["eligibility"]["inclusion"], list)
    assert "<br />" not in _json.dumps(trial["eligibility"])

    # The DDOTS text survives only in the raw blob.
    assert "<br />" in trial["_raw"]["_ddots"]["eligibility"]
