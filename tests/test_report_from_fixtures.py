"""Build and validate a report from the versioned test-pts/test-trials/test-matches
fixtures, scoped to sample_id "8" (Maria Emo, pancreatic adenocarcinoma).

Ground truth: test-matches-v0.0.1.json has 6 match docs for sample_id "8"
across 4 trials. Trial 2021.070/NCT04858334 has 3 of them — two identical
"clinical" docs and one "genomic" (BRCA2) doc. Every doc ties on
match_level="step", so the genomic doc wins outright on reason_type. This is
manually-validated data, not a guess.
"""
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_ID = "8"


@pytest.fixture(scope="module")
def matches():
    return json.loads((FIXTURES / "test-matches-v0.0.1.json").read_text())


@pytest.fixture(scope="module")
def trials():
    return json.loads((FIXTURES / "test-trials-v0.0.1.json").read_text())


@pytest.fixture(scope="module")
def patient_matches(matches):
    return [m for m in matches if m["sample_id"] == SAMPLE_ID]


@pytest.fixture(scope="module")
def trials_by_protocol(trials, patient_matches):
    referenced = {m["protocol_no"] for m in patient_matches}
    return {t["protocol_no"]: t for t in trials if t["protocol_no"] in referenced}


def test_primary_match_is_genomic_over_tied_clinical(patient_matches):
    from ctm.reports.builder import load_context_from_flat_matches
    ctx = load_context_from_flat_matches(patient_matches, SAMPLE_ID)

    assert ctx["primary_match"]["nct_id"] == "NCT04858334"
    match_detail = {r["label"]: r["value"] for r in ctx["primary_match"]["match_detail"]}
    assert match_detail["Reason Type"] == "genomic"


def test_other_matches_excludes_primary_protocol_and_dedupes(patient_matches):
    from ctm.reports.builder import load_context_from_flat_matches
    ctx = load_context_from_flat_matches(patient_matches, SAMPLE_ID)

    other_protocols = {m["protocol_no"] for m in ctx["other_matches"]}
    assert other_protocols == {"2015.063", "2019.058", "2021.045"}
    assert len(ctx["other_matches"]) == 3


def test_primary_match_trial_data_from_trials_fixture(patient_matches, trials_by_protocol):
    from ctm.reports.builder import load_context_from_flat_matches
    ctx = load_context_from_flat_matches(patient_matches, SAMPLE_ID, trials_by_protocol)

    trial_rows = {r["label"]: r["value"] for r in ctx["primary_match"]["trial"]}
    expected = trials_by_protocol["2021.070"]["_summary"]
    assert trial_rows["Trial Name"] == expected["long_title"]
    assert trial_rows["Phase"] == expected["phase"]


def test_render_html_from_pt_trials_matches_smoke():
    from ctm.reports.builder import render_html_from_pt_trials_matches
    html = render_html_from_pt_trials_matches(
        str(FIXTURES / "test-pts-v0.0.1.json"),
        str(FIXTURES / "test-trials-v0.0.1.json"),
        str(FIXTURES / "test-matches-v0.0.1.json"),
        SAMPLE_ID,
    )
    assert "<html" in html
    assert "Trial Match Report" in html
    assert "NCT04858334" in html
    assert "Pancreatic Adenocarcinoma" in html
