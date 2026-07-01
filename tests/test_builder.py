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


# ---------------------------------------------------------------------------
# Task 3: load_context_from_raw_excel
# ---------------------------------------------------------------------------

def test_excel_patient_detail_contains_mrn():
    from ctm.reports.builder import load_context_from_raw_excel
    ctx = load_context_from_raw_excel(str(EXCEL))
    labels = [r["label"] for r in ctx["patient_detail"]]
    assert "MRN" in labels


def test_excel_returns_required_keys():
    from ctm.reports.builder import load_context_from_raw_excel
    ctx = load_context_from_raw_excel(str(EXCEL))
    assert "patient_header" in ctx
    assert "patient_detail" in ctx
    assert "reports" in ctx
    assert isinstance(ctx["patient_header"], list)
    assert isinstance(ctx["reports"], list)


def test_excel_reports_include_raw_fields():
    from ctm.reports.builder import load_context_from_raw_excel
    ctx = load_context_from_raw_excel(str(EXCEL))
    all_findings = [f for r in ctx["reports"] for f in r.get("findings", [])]
    raw_dicts = [f["raw"] for f in all_findings if f.get("raw")]
    assert len(raw_dicts) > 0


def test_excel_missing_file_returns_empty_context():
    from ctm.reports.builder import load_context_from_raw_excel
    ctx = load_context_from_raw_excel("/nonexistent/path.xlsx")
    assert ctx["patient_header"] == []
    assert ctx["patient_detail"] == []
    assert ctx["reports"] == []


# ---------------------------------------------------------------------------
# Task 4: render_html_from_sources
# ---------------------------------------------------------------------------

def test_render_html_from_sources_returns_html():
    from ctm.reports.builder import render_html_from_sources
    html = render_html_from_sources(str(EXCEL), str(FIXTURES / "mm_export_7439568.json"))
    assert "<html" in html
    assert "Trial Match Report" in html


# ---------------------------------------------------------------------------
# Task 5: load_context_from_normalized_json
# ---------------------------------------------------------------------------

def _make_normalized_json(tmp_path):
    data = {
        "clinical": {
            "SAMPLE_ID": "302939",
            "VITAL_STATUS": "Alive",
            "ONCOTREE_PRIMARY_DIAGNOSIS_NAME": "READ",
        },
        "genomic": [
            {"SAMPLE_ID": "302939", "TRUE_HUGO_SYMBOL": "ERBB2", "VARIANT_CATEGORY": "MUTATION"}
        ],
        "extras": {
            "patient": {
                "pt_uuid": 0,
                "mrn": "000000",
                "first_name": "Dane",
                "last_name": "Doe",
                "dob": None,
                "sex": None,
                "vital_status": None,
                "entity": "AMC",
                "primary_dx": "mid-rectal adenocarcinoma",
                "oncotree_primary_diagnosis": "READ",
                "metastasis_sites": ["liver", "bone"],
            },
            "reports": [
                {
                    "source": "tempus",
                    "test_name": "xT CDx",
                    "accession_no": "TL-26-001",
                    "physician": "Dr. Smith",
                    "date_completed": "2026-03-07",
                    "findings": [
                        {
                            "gene": "ERBB2",
                            "protein": "p.T733I",
                            "nucleotide": None,
                            "variant_type": "somatic_mutation",
                            "result_summary": "53.2% VAF",
                            "raw": {"raw_test": "ERBB2 (HER2) p.T733I", "raw_result": "53.2% VAF"},
                        }
                    ],
                }
            ],
        },
    }
    path = tmp_path / "normalized_pt.json"
    path.write_text(json.dumps(data))
    return path


def test_normalized_json_returns_required_keys(tmp_path):
    from ctm.reports.builder import load_context_from_normalized_json
    ctx = load_context_from_normalized_json(str(_make_normalized_json(tmp_path)))
    assert "patient_header" in ctx
    assert "patient_detail" in ctx
    assert "reports" in ctx
    assert isinstance(ctx["patient_header"], list)
    assert isinstance(ctx["reports"], list)


def test_normalized_json_patient_header_has_name(tmp_path):
    from ctm.reports.builder import load_context_from_normalized_json
    ctx = load_context_from_normalized_json(str(_make_normalized_json(tmp_path)))
    labels = [r["label"] for r in ctx["patient_header"]]
    assert "First Name" in labels
    assert "Last Name" in labels


def test_normalized_json_metastasis_sites_is_string(tmp_path):
    from ctm.reports.builder import load_context_from_normalized_json
    ctx = load_context_from_normalized_json(str(_make_normalized_json(tmp_path)))
    detail = {r["label"]: r["value"] for r in ctx["patient_detail"]}
    assert detail["Metastasis Sites"] == "liver, bone"


def test_normalized_json_reports_include_raw_fields(tmp_path):
    from ctm.reports.builder import load_context_from_normalized_json
    ctx = load_context_from_normalized_json(str(_make_normalized_json(tmp_path)))
    all_findings = [f for r in ctx["reports"] for f in r.get("findings", [])]
    assert any(f.get("raw") for f in all_findings)


def test_normalized_json_missing_file_returns_empty():
    from ctm.reports.builder import load_context_from_normalized_json
    ctx = load_context_from_normalized_json("/nonexistent/path.json")
    assert ctx["patient_header"] == []
    assert ctx["patient_detail"] == []
    assert ctx["reports"] == []


# ---------------------------------------------------------------------------
# Task 6: render_html_from_pt_and_matches
# ---------------------------------------------------------------------------

def test_render_html_from_pt_and_matches_returns_html(tmp_path):
    from ctm.reports.builder import render_html_from_pt_and_matches
    html = render_html_from_pt_and_matches(
        str(_make_normalized_json(tmp_path)),
        str(FIXTURES / "mm_export_7439568.json"),
        "mm",
    )
    assert "<html" in html
    assert "Trial Match Report" in html


def test_render_html_from_pt_and_matches_unknown_engine_raises(tmp_path):
    from ctm.reports.builder import render_html_from_pt_and_matches
    with pytest.raises(ValueError, match="Unknown match engine"):
        render_html_from_pt_and_matches(
            str(_make_normalized_json(tmp_path)),
            str(FIXTURES / "mm_export_7439568.json"),
            "trialmatchai",
        )
