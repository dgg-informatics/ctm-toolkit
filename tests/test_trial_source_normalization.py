"""End-to-end normalization for each raw trial source, without a network call.

Covers the two layers that used to be untested:

* xlsx/xml -> Raw* model: pure parsing of a human-maintained spreadsheet.
* Raw* model -> CTML dict: entity tagging and the _summary/_raw key surgery,
  which needs a ClinicalTrials.gov response for West and Sparrow. That comes
  from tests/fixtures/clinicaltrial_gov/ via the stub_ctgov fixture.
"""
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
WEST = FIXTURES / "test-trials-west-v0.0.1.xlsx"
SPARROW = FIXTURES / "test-trials-sparrow-v0.0.1.xlsx"
AMC = FIXTURES / "test-trials-amc-v0.0.1.xml"


# ── raw parsing (no network involved at all) ─────────────────────────────────

def test_west_xlsx_parses_both_rows():
    from ctm.transformers.west_xlsx_to_raw import load

    trials = load(WEST)
    assert [t.nct_id for t in trials] == ["NCT04314401", "NCT03560752"]
    # non-nct columns are captured verbatim under their own header names,
    # regardless of column order (this fixture has nct_id in the last column)
    assert trials[0].model_dump()["Sponsor"] == "NCI"
    assert trials[1].model_dump()["ID"] == "18007"


def test_west_xlsx_stamps_source_modified_at_from_the_file_mtime(tmp_path):
    """The workbook keeps a stable filename and is replaced in place, so its mtime
    is the only record of which version fed a run — captured on every row."""
    import os
    import shutil
    from datetime import UTC, datetime

    from ctm.transformers.west_xlsx_to_raw import load

    copy = tmp_path / "west.xlsx"
    shutil.copy(WEST, copy)
    os.utime(copy, (1700000000, 1700000000))

    trials = load(copy)
    expected = datetime.fromtimestamp(1700000000, tz=UTC).isoformat()

    assert len(trials) == 2
    assert all(t.model_dump()["source_modified_at"] == expected for t in trials)


def test_sparrow_xlsx_parses_both_rows():
    from ctm.transformers.sparrow_xlsx_to_raw import load

    trials = load(SPARROW)
    assert [t.nct_id for t in trials] == ["NCT04871542", "NCT05334069"]
    assert trials[0].study_name == "S2013"


def test_amc_xml_parses():
    from ctm.transformers.amc_xml_to_raw import load

    trials = load(AMC)
    assert len(trials) == 1
    # AMC keeps the raw XML tag names, so it is nct_number here, not nct_id
    assert trials[0].nct_number == "NCT90000014"
    assert trials[0].protocol_no == "2099.014"


# ── raw -> CTML, using canned ClinicalTrials.gov responses ──────────────────

@pytest.mark.parametrize(
    "source,path,entity,raw_key",
    [
        ("west", WEST, "west", "_west"),
        ("sparrow", SPARROW, "sparrow", "_sparrow"),
    ],
)
def test_source_normalizes_to_ctml(stub_ctgov, source, path, entity, raw_key):
    """Each trial is tagged with its entity and keeps its vendor metadata."""
    if source == "west":
        from ctm.transformers.raw_west_to_ctml import to_ctml_dict
        from ctm.transformers.west_xlsx_to_raw import load
    else:
        from ctm.transformers.raw_sparrow_to_ctml import to_ctml_dict
        from ctm.transformers.sparrow_xlsx_to_raw import load

    for raw in load(path):
        d = to_ctml_dict(raw)

        assert d["entity"] == entity
        assert d["nct_id"] == raw.nct_id
        # summary/raw are renamed to the MatchMiner underscore convention
        assert "_summary" in d and "summary" not in d
        assert "_raw" in d and "raw" not in d
        # vendor metadata is merged in, minus the nct_id it was keyed by
        assert raw_key in d["_raw"]
        assert "nct_id" not in d["_raw"][raw_key]
        # content actually came from the canned CTGov response
        assert d["_summary"]["short_title"] or d["_summary"]["long_title"]


def test_ctml_dict_carries_source_modified_at_as_iso8601_utc(stub_ctgov, tmp_path):
    """`_raw._west.source_modified_at` must survive into the CTML dict and parse
    as an ISO-8601 UTC timestamp — it's the provenance compute_trial_hash relies
    on being present (and volatile) for a stable-filename workbook."""
    import os
    import shutil
    from datetime import UTC, datetime

    from ctm.transformers.raw_west_to_ctml import to_ctml_dict
    from ctm.transformers.west_xlsx_to_raw import load

    copy = tmp_path / "west.xlsx"
    shutil.copy(WEST, copy)
    os.utime(copy, (1700000000, 1700000000))

    raw = load(copy)[0]
    d = to_ctml_dict(raw)

    source_modified_at = d["_raw"]["_west"]["source_modified_at"]
    assert isinstance(source_modified_at, str)
    assert datetime.fromisoformat(source_modified_at) == datetime.fromtimestamp(1700000000, tz=UTC)


def test_ctgov_content_reaches_the_ctml_dict(stub_ctgov):
    """A field only present in the canned response must survive the transform."""
    from ctm.schemas.raw.models import RawWestTrial
    from ctm.transformers.raw_west_to_ctml import to_ctml_dict

    d = to_ctml_dict(RawWestTrial(nct_id="NCT04314401", sponsor="NCI"))
    assert "Moonshot" in (d["_summary"]["long_title"] or d["_summary"]["short_title"])
    assert d["_summary"]["investigator"]["full_name"] == "Richard Roe"


def test_study_chair_is_not_treated_as_principal_investigator(stub_ctgov):
    """NCT05334069's only official has role STUDY_CHAIR, so there is no PI."""
    from ctm.schemas.raw.models import RawSparrowTrial
    from ctm.transformers.raw_sparrow_to_ctml import to_ctml_dict

    d = to_ctml_dict(RawSparrowTrial(nct_id="NCT05334069", study_name="A212102"))
    assert not (d["_summary"]["investigator"] or {}).get("full_name")


def test_unknown_nct_raises_like_a_404(stub_ctgov):
    from ctm.schemas.raw.models import RawWestTrial
    from ctm.transformers.raw_west_to_ctml import to_ctml_dict

    with pytest.raises(ValueError, match="not found"):
        to_ctml_dict(RawWestTrial(nct_id="NCT00000000", sponsor="NCI"))


# ── the guard itself ────────────────────────────────────────────────────────

def test_network_is_blocked_without_the_stub():
    """Proves the autouse guard works: no stub, so the real fetch must fail."""
    from ctm.schemas.raw.models import RawWestTrial
    from ctm.transformers.raw_west_to_ctml import to_ctml_dict

    with pytest.raises(RuntimeError, match="real network call"):
        to_ctml_dict(RawWestTrial(nct_id="NCT04314401", sponsor="NCI"))
