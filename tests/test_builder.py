"""Tests for the real-data builder loader functions."""
import json
import os
import tempfile
import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
EXCEL = Path(__file__).parent.parent / "data" / "raw" / "patient_data_template.xlsx"


# ---------------------------------------------------------------------------
# Task 2: load_context_from_mm_matches
# ---------------------------------------------------------------------------

def test_mm_primary_is_arm_level_genomic():
    from ctm.reports.builder import load_context_from_mm_matches
    ctx = load_context_from_mm_matches(str(FIXTURES / "mm_export_7439568.json"))
    assert ctx["primary_match"] is not None
    assert ctx["primary_match"]["nct_id"] == "NCT02477839"


def test_mm_primary_match_has_required_keys():
    from ctm.reports.builder import load_context_from_mm_matches
    ctx = load_context_from_mm_matches(str(FIXTURES / "mm_export_7439568.json"))
    pm = ctx["primary_match"]
    assert "nct_id" in pm
    assert "trial_status" in pm
    assert isinstance(pm["trial"], list)
    assert isinstance(pm["match_detail"], list)
    assert isinstance(pm["genomic"], list)


def test_mm_other_matches_excludes_primary_protocol():
    from ctm.reports.builder import load_context_from_mm_matches
    ctx = load_context_from_mm_matches(str(FIXTURES / "mm_export_7439568.json"))
    other_protocols = [m["protocol_no"] for m in ctx["other_matches"]]
    assert "NCT02477839" not in other_protocols
    assert "NCT99999999" in other_protocols


def test_mm_other_matches_shape():
    from ctm.reports.builder import load_context_from_mm_matches
    ctx = load_context_from_mm_matches(str(FIXTURES / "mm_export_7439568.json"))
    m = ctx["other_matches"][0]
    assert "protocol_no" in m
    assert "nct_id" in m
    assert "source" in m
    assert m["source"] == "matchminer"


def test_mm_empty_trial_match_returns_none_primary():
    from ctm.reports.builder import load_context_from_mm_matches
    data = {"clinical": {}, "genomic": [], "trial_match": []}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        tmp = f.name
    try:
        ctx = load_context_from_mm_matches(tmp)
        assert ctx["primary_match"] is None
        assert ctx["other_matches"] == []
    finally:
        os.unlink(tmp)


def test_mm_no_arm_match_falls_back_to_step():
    from ctm.reports.builder import load_context_from_mm_matches
    data = {
        "clinical": {}, "genomic": [],
        "trial_match": [{
            "sample_id": "1", "match_level": "step", "reason_type": "clinical",
            "show_in_ui": True, "protocol_no": "NCT00000001", "nct_id": "NCT00000001",
            "cancer_type_match": "specific", "match_type": "generic_clinical",
            "genomic_alteration": "", "trial_summary_status": "open",
            "sort_order": [1, 99, 99, 99, 99, 99], "hash": "aaa"
        }]
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        tmp = f.name
    try:
        ctx = load_context_from_mm_matches(tmp)
        assert ctx["primary_match"]["nct_id"] == "NCT00000001"
    finally:
        os.unlink(tmp)
