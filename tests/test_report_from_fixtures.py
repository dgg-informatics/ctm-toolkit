"""Build and validate a report from the versioned test-pts/test-trials/test-matches
fixtures, scoped to sample_id "8" (Maria Emo, pancreatic adenocarcinoma).

Ground truth: test-matches-v0.0.1.json has 6 match docs for sample_id "8"
across 4 trials, which is 4 unique NCT ids. Trial 2099.015/NCT90000014 has 3
of them — two identical "clinical" docs and one "genomic" (BRCA2) doc. Every
doc ties on match_level="step", so the genomic reason wins ordering and that
trial ranks first. This is manually-validated data, not a guess.
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



def test_genomic_trial_ranks_first(patient_matches):
    from ctm.reports.builder import load_context_from_flat_matches
    ctx = load_context_from_flat_matches(patient_matches, SAMPLE_ID)

    first = ctx["trial_blocks"][0]
    assert first["rank"] == 1
    assert first["nct_id"] == "NCT90000014"



def test_one_block_per_unique_nct(patient_matches):
    """6 match docs across 4 NCTs collapse to 4 blocks, ranked 1..4."""
    from ctm.reports.builder import load_context_from_flat_matches
    ctx = load_context_from_flat_matches(patient_matches, SAMPLE_ID)

    blocks = ctx["trial_blocks"]
    assert len(patient_matches) == 6
    assert [b["nct_id"] for b in blocks] == [
        "NCT90000014", "NCT90000002", "NCT90000010", "NCT90000012"]
    assert [b["rank"] for b in blocks] == [1, 2, 3, 4]


def test_duplicate_clinical_docs_collapse_into_one_reason(patient_matches):
    """NCT90000014's two identical clinical docs and one BRCA2 genomic doc
    become two reasons, genomic first — not three rows."""
    from ctm.reports.builder import load_context_from_flat_matches
    ctx = load_context_from_flat_matches(patient_matches, SAMPLE_ID)

    assert ctx["trial_blocks"][0]["match_reasons"] == ["BRCA2", "Pancreatic Adenocarcinoma"]



def test_block_trial_data_from_trials_fixture(patient_matches, trials_by_protocol):
    from ctm.reports.builder import load_context_from_flat_matches
    ctx = load_context_from_flat_matches(patient_matches, SAMPLE_ID, trials_by_protocol)

    trial_rows = {r["label"]: r["value"] for r in ctx["trial_blocks"][0]["trial"]}
    expected = trials_by_protocol["2099.015"]["_summary"]
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
    assert "Michigan Medicine Trial Match" in html
    assert "NCT90000014" in html
    assert "Pancreatic Adenocarcinoma" in html


def test_render_html_from_pt_trials_matches_accepts_export_matches_envelope(patient_matches, tmp_path):
    """matchengine-V2's export_matches.py outputs a single-patient
    {"clinical": ..., "genomic": ..., "trial_match": [...]} envelope, not a
    flat list. render_html_from_pt_trials_matches must accept both shapes.
    """
    from ctm.reports.builder import render_html_from_pt_trials_matches

    envelope = {
        "clinical": {"SAMPLE_ID": SAMPLE_ID},
        "genomic": [],
        "trial_match": patient_matches,
    }
    envelope_path = tmp_path / "export_matches_output.json"
    envelope_path.write_text(json.dumps(envelope))

    html = render_html_from_pt_trials_matches(
        str(FIXTURES / "test-pts-v0.0.1.json"),
        str(FIXTURES / "test-trials-v0.0.1.json"),
        str(envelope_path),
        SAMPLE_ID,
    )
    assert "<html" in html
    assert "NCT90000014" in html
