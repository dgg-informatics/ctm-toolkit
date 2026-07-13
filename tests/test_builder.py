"""Tests for the real-data builder loader functions."""
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Task 2: load_context_from_flat_matches
# ---------------------------------------------------------------------------

def test_flat_matches_empty_returns_none_primary():
    from ctm.reports.builder import load_context_from_flat_matches
    ctx = load_context_from_flat_matches([], "1")
    assert ctx["primary_match"] is None
    assert ctx["other_matches"] == []


def test_flat_matches_no_arm_match_falls_back_to_step():
    from ctm.reports.builder import load_context_from_flat_matches
    matches = [{
        "sample_id": "1", "match_level": "step", "reason_type": "clinical",
        "show_in_ui": True, "protocol_no": "NCT00000001", "nct_id": "NCT00000001",
        "cancer_type_match": "specific", "match_type": "generic_clinical",
        "genomic_alteration": "", "trial_summary_status": "open",
        "sort_order": [1, 99, 99, 99, 99, 99], "hash": "aaa"
    }]
    ctx = load_context_from_flat_matches(matches, "1")
    assert ctx["primary_match"]["nct_id"] == "NCT00000001"


def test_flat_matches_primary_match_has_required_keys():
    from ctm.reports.builder import load_context_from_flat_matches
    matches = [{
        "sample_id": "1", "match_level": "arm", "reason_type": "genomic",
        "show_in_ui": True, "protocol_no": "NCT00000001", "nct_id": "NCT00000001",
        "cancer_type_match": "specific", "match_type": "gene",
        "genomic_alteration": "EGFR", "trial_summary_status": "open",
        "sort_order": [1, 99, 1, 99, 99, 99], "hash": "aaa"
    }]
    ctx = load_context_from_flat_matches(matches, "1")
    pm = ctx["primary_match"]
    assert "nct_id" in pm
    assert "trial_status" in pm
    assert isinstance(pm["trial"], list)
    assert isinstance(pm["match_detail"], list)
    assert isinstance(pm["genomic"], list)


def test_flat_matches_other_matches_excludes_primary_protocol():
    from ctm.reports.builder import load_context_from_flat_matches
    matches = [
        {
            "sample_id": "1", "match_level": "arm", "reason_type": "genomic",
            "show_in_ui": True, "protocol_no": "NCT02477839", "nct_id": "NCT02477839",
            "cancer_type_match": "specific", "match_type": "gene",
            "genomic_alteration": "EGFR", "trial_summary_status": "open",
            "sort_order": [1, 99, 1, 99, 99, 99], "hash": "aaa",
        },
        {
            "sample_id": "1", "match_level": "arm", "reason_type": "clinical",
            "show_in_ui": True, "protocol_no": "NCT99999999", "nct_id": "NCT99999999",
            "cancer_type_match": "broader", "match_type": "generic_clinical",
            "genomic_alteration": "", "trial_summary_status": "open",
            "sort_order": [2, 99, 99, 99, 99, 99], "hash": "bbb",
        },
    ]
    ctx = load_context_from_flat_matches(matches, "1")
    other_protocols = [m["protocol_no"] for m in ctx["other_matches"]]
    assert "NCT02477839" not in other_protocols
    assert "NCT99999999" in other_protocols
    m = ctx["other_matches"][0]
    assert "protocol_no" in m
    assert "nct_id" in m
    assert "source" in m
    assert m["source"] == "matchminer"


# ---------------------------------------------------------------------------
# Task 5: load_context_from_normalized_json
# ---------------------------------------------------------------------------

def _make_normalized_json(tmp_path):
    data = {
        "clinical": {
            "SAMPLE_ID": "302939",
            "VITAL_STATUS": "alive",
            "ONCOTREE_PRIMARY_DIAGNOSIS_NAME": "READ",
        },
        "genomic": [
            {"SAMPLE_ID": "302939", "TRUE_HUGO_SYMBOL": "ERBB2", "VARIANT_CATEGORY": "MUTATION"}
        ],
        "extras": {
            "patients": {
                "000000": {
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
            },
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
