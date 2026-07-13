"""Structural validation for the versioned test-pts/test-trials/test-matches fixtures.

These don't run matchengine or check match correctness — they only catch
malformed fixtures and referential drift (a match pointing at a sample_id or
protocol_no that no longer exists in the paired pts/trials file).
"""
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(scope="module")
def pts():
    return _load("test-pts-v0.0.1.json")


@pytest.fixture(scope="module")
def trials():
    return _load("test-trials-v0.0.1.json")


@pytest.fixture(scope="module")
def matches():
    return _load("test-matches-v0.0.1.json")


# ---------------------------------------------------------------------------
# test-pts-v0.0.1.json
# ---------------------------------------------------------------------------

def test_pts_fixture_shape(pts):
    from ctm.schemas.matchminer.patient import MMClinical, MMGenomic

    assert set(pts.keys()) >= {"clinical", "genomic", "extras"}

    sample_ids = [c["SAMPLE_ID"] for c in pts["clinical"]]
    assert len(sample_ids) == len(set(sample_ids)), "duplicate SAMPLE_ID in clinical"

    for entry in pts["clinical"]:
        MMClinical.model_validate(entry)

    for entry in pts["genomic"]:
        MMGenomic.model_validate(entry)
        assert entry["SAMPLE_ID"] in sample_ids, (
            f"genomic entry references unknown SAMPLE_ID {entry['SAMPLE_ID']!r}"
        )

    patients_extras = pts["extras"].get("patients", {})
    assert patients_extras, "extras.patients is empty"
    for sample_id, entry in patients_extras.items():
        assert sample_id in sample_ids, f"extras.patients has unknown SAMPLE_ID {sample_id!r}"
        assert "patient" in entry
        assert "reports" in entry


# ---------------------------------------------------------------------------
# test-trials-v0.0.1.json
# ---------------------------------------------------------------------------

def test_trials_fixture_shape(trials):
    from ctm.schemas.matchminer.clinical_trial import ClinicalTrialNormalized

    assert isinstance(trials, list) and trials

    protocol_nos = [t.get("protocol_no") for t in trials]
    assert all(protocol_nos), "trial missing protocol_no"
    assert len(protocol_nos) == len(set(protocol_nos)), "duplicate protocol_no"

    for entry in trials:
        ClinicalTrialNormalized.model_validate({
            **entry,
            "summary": entry.get("_summary", {}),
            "raw": entry.get("_raw", {}),
        })


# ---------------------------------------------------------------------------
# test-matches-v0.0.1.json
# ---------------------------------------------------------------------------

def test_matches_fixture_shape(matches, pts, trials):
    assert isinstance(matches, list) and matches

    sample_ids = {c["SAMPLE_ID"] for c in pts["clinical"]}
    protocol_nos = {t.get("protocol_no") for t in trials}

    for m in matches:
        for key in ("sample_id", "protocol_no", "nct_id", "match_level", "reason_type", "show_in_ui", "sort_order"):
            assert key in m, f"match missing {key!r}: {m}"

        assert m["match_level"] in ("step", "arm")
        assert m["reason_type"] in ("clinical", "genomic")
        assert isinstance(m["show_in_ui"], bool)
        assert isinstance(m["sort_order"], list) and len(m["sort_order"]) == 6
        assert all(isinstance(v, int) for v in m["sort_order"])

        assert m["sample_id"] in sample_ids, (
            f"match references unknown sample_id {m['sample_id']!r}"
        )
        assert m["protocol_no"] in protocol_nos, (
            f"match references unknown protocol_no {m['protocol_no']!r}"
        )
