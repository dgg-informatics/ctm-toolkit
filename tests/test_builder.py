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
            "show_in_ui": True, "protocol_no": "NCT90000002", "nct_id": "NCT90000002",
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
    assert "NCT90000002" not in other_protocols
    assert "NCT99999999" in other_protocols
    m = ctx["other_matches"][0]
    assert "protocol_no" in m
    assert "nct_id" in m
    assert "source" in m
    assert m["source"] == "matchminer"


def test_match_reason_labels():
    from ctm.reports.builder import _match_reason
    assert _match_reason({"reason_type": "genomic", "genomic_alteration": "HER2"}) == "HER2"
    assert _match_reason({"reason_type": "genomic", "true_hugo_symbol": "BRAF"}) == "BRAF"
    assert _match_reason({"reason_type": "clinical", "match_type": "tmb"}) == "TMB"
    assert _match_reason({"reason_type": "clinical", "match_type": "generic_clinical",
                          "oncotree_primary_diagnosis_name": "Lymphoid"}) == "Lymphoid"
    assert _match_reason({"reason_type": "clinical", "match_type": "generic_clinical"}) == "Clinical criteria"


def test_other_matches_prefers_genomic_reason_when_trial_matched_on_both():
    # NCT90000003 case: a trial matches on BOTH age (clinical) and a gene
    # (genomic). The row should surface the gene, not generic_clinical — even
    # though the clinical doc appears first in the list.
    from ctm.reports.builder import load_context_from_flat_matches
    matches = [
        {"sample_id": "1", "match_level": "step", "reason_type": "clinical",
         "show_in_ui": True, "protocol_no": "2024.010", "nct_id": "NCT90000003",
         "match_type": "generic_clinical", "genomic_alteration": "",
         "trial_summary_status": "open", "sort_order": [1, 99, 99, 99, 99, 99], "hash": "a"},
        {"sample_id": "1", "match_level": "step", "reason_type": "genomic",
         "show_in_ui": True, "protocol_no": "2024.010", "nct_id": "NCT90000003",
         "match_type": "gene", "genomic_alteration": "HER2", "true_hugo_symbol": "HER2",
         "trial_summary_status": "open", "sort_order": [1, 99, 1, 99, 99, 99], "hash": "b"},
        # a second, unrelated trial so 2024.010 lands in other_matches, not primary
        {"sample_id": "1", "match_level": "arm", "reason_type": "genomic",
         "show_in_ui": True, "protocol_no": "2024.999", "nct_id": "NCT99999999",
         "match_type": "gene", "genomic_alteration": "BRAF", "trial_summary_status": "open",
         "sort_order": [0, 99, 1, 99, 99, 99], "hash": "c"},
    ]
    ctx = load_context_from_flat_matches(matches, "1")
    other = {m["protocol_no"]: m for m in ctx["other_matches"]}
    assert other["2024.010"]["match_reason"] == "HER2"   # gene surfaced, not generic_clinical
    assert other["2024.010"]["genomic_alteration"] == "HER2"


def test_other_matches_includes_trial_name_from_trials_by_protocol():
    from ctm.reports.builder import load_context_from_flat_matches
    matches = [
        {
            "sample_id": "1", "match_level": "arm", "reason_type": "genomic",
            "show_in_ui": True, "protocol_no": "2025.001", "nct_id": "NCT00000001",
            "match_type": "gene", "genomic_alteration": "EGFR", "trial_summary_status": "open",
            "sort_order": [1, 99, 1, 99, 99, 99], "hash": "aaa",
        },
        {
            "sample_id": "1", "match_level": "arm", "reason_type": "clinical",
            "show_in_ui": True, "protocol_no": "2025.002", "nct_id": "NCT00000002",
            "match_type": "generic_clinical", "genomic_alteration": "", "trial_summary_status": "open",
            "sort_order": [2, 99, 99, 99, 99, 99], "hash": "bbb",
        },
    ]
    trials_by_protocol = {
        "2025.001": {"_summary": {"long_title": "Primary Trial Long Title"}},
        "2025.002": {"_summary": {"short_title": "Other Trial Short Title"}},
    }
    ctx = load_context_from_flat_matches(matches, "1", trials_by_protocol)
    other = ctx["other_matches"][0]
    assert other["protocol_no"] == "2025.002"
    assert other["trial_name"] == "Other Trial Short Title"


# ---------------------------------------------------------------------------
# Task 5: load_context_from_normalized_json
# ---------------------------------------------------------------------------

def _make_normalized_json(tmp_path):
    data = {
        "clinical": {
            "SAMPLE_ID": "900001",
            "VITAL_STATUS": "alive",
            "ONCOTREE_PRIMARY_DIAGNOSIS_NAME": "READ",
        },
        "genomic": [
            {"SAMPLE_ID": "900001", "TRUE_HUGO_SYMBOL": "ERBB2", "VARIANT_CATEGORY": "MUTATION"}
        ],
        "extras": {
            "patients": {
                "pt_0000000": {
                    "patient": {
                        "pt_uuid": "pt_0000000",
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
                        "referring_clinician": "Dr. Seuss",
                        "source": "manual",
                    },
                    "reports": [
                        {
                            "report_uuid": "rp_0000000",
                            "pt_uuid": "pt_0000000",
                            "source": "tempus",
                            "test_name": "xT CDx",
                            "unique_test_id": "TL-26-001",
                            "unique_test_id_source": "accession_no",
                            "ordering_physician": "Dr. Smith",
                            "raw": {"test_report_date": "2026-03-07"},
                            "findings": [
                                {
                                    "pt_uuid": "pt_0000000",
                                    "report_uuid": "rp_0000000",
                                    "source": "tempus",
                                    "biomarker": "ERBB2",
                                    "variant_category": "MUTATION",
                                    "protein_change": "p.T733I",
                                    "cnv_call": None,
                                    "signature_level": None,
                                    "wildtype": False,
                                    "nucleotide_change": None,
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


# ---------------------------------------------------------------------------
# _build_genetic_profile
# ---------------------------------------------------------------------------

def test_genetic_profile_includes_only_wildtype_false():
    from ctm.reports.builder import _build_genetic_profile
    genomic_docs = [
        {"TRUE_HUGO_SYMBOL": "TP53", "VARIANT_CATEGORY": "MUTATION", "WILDTYPE": False,
         "TRUE_PROTEIN_CHANGE": "p.Q192*", "TRUE_CDNA_CHANGE": "c.574C>T"},
        {"TRUE_HUGO_SYMBOL": "EGFR", "VARIANT_CATEGORY": "MUTATION", "WILDTYPE": True},
        {"TRUE_HUGO_SYMBOL": "FGF4", "VARIANT_CATEGORY": "CNV", "WILDTYPE": False, "CNV_CALL": "Gain"},
    ]
    profile = _build_genetic_profile(genomic_docs)
    genes = {row["gene"] for row in profile}
    assert genes == {"TP53", "FGF4"}


def test_genetic_profile_row_shape():
    from ctm.reports.builder import _build_genetic_profile
    genomic_docs = [
        {"TRUE_HUGO_SYMBOL": "TP53", "VARIANT_CATEGORY": "MUTATION", "WILDTYPE": False,
         "TRUE_PROTEIN_CHANGE": "p.Q192*", "TRUE_CDNA_CHANGE": "c.574C>T"},
    ]
    profile = _build_genetic_profile(genomic_docs)
    assert profile == [{
        "gene": "TP53", "variant_category": "MUTATION",
        "protein_change": "p.Q192*", "cdna_change": "c.574C>T", "cnv_call": None,
    }]


def test_genetic_profile_empty_input_returns_empty():
    from ctm.reports.builder import _build_genetic_profile
    assert _build_genetic_profile([]) == []


def test_primary_match_context_disease_site_in_trial_column():
    from ctm.reports.builder import _build_primary_match_context
    match = {"nct_id": "NCT1", "protocol_no": "2025.001", "trial_summary_status": "open"}
    trial = {"_raw": {"disease_site": "Breast; Lung"}}
    ctx = _build_primary_match_context(match, trial)
    labels = {r["label"]: r["value"] for r in ctx["trial"]}
    assert labels["Disease Site"] == "Breast; Lung"


def test_primary_match_context_match_level_and_engine_moved_to_match_detail():
    from ctm.reports.builder import _build_primary_match_context
    match = {"nct_id": "NCT1", "protocol_no": "2025.001", "trial_summary_status": "open",
             "match_level": "arm", "reason_type": "clinical", "match_type": "generic_clinical"}
    ctx = _build_primary_match_context(match, trial=None)
    trial_labels = [r["label"] for r in ctx["trial"]]
    detail_labels = {r["label"]: r["value"] for r in ctx["match_detail"]}
    assert "Match Level" not in trial_labels
    assert "Match Engine" not in trial_labels
    assert detail_labels["Match Level"] == "arm"
    assert detail_labels["Match Engine"] == "MatchMiner-v2"


def test_primary_match_context_known_biomarker_count_in_genomic_column():
    from ctm.reports.builder import _build_primary_match_context
    match = {"nct_id": "NCT1", "protocol_no": "2025.001", "trial_summary_status": "open",
             "reason_type": "clinical"}
    ctx = _build_primary_match_context(match, trial=None, known_biomarker_count=90)
    genomic_labels = {r["label"]: r["value"] for r in ctx["genomic"]}
    assert genomic_labels["Known Biomarkers"] == "90 on file"


def test_primary_match_context_no_known_biomarker_row_when_count_not_given():
    from ctm.reports.builder import _build_primary_match_context
    match = {"nct_id": "NCT1", "protocol_no": "2025.001", "trial_summary_status": "open"}
    ctx = _build_primary_match_context(match, trial=None)
    genomic_labels = [r["label"] for r in ctx["genomic"]]
    assert "Known Biomarkers" not in genomic_labels


def test_genetic_profile_sorted_by_gene():
    from ctm.reports.builder import _build_genetic_profile
    genomic_docs = [
        {"TRUE_HUGO_SYMBOL": "TP53", "VARIANT_CATEGORY": "MUTATION", "WILDTYPE": False},
        {"TRUE_HUGO_SYMBOL": "ATM", "VARIANT_CATEGORY": "MUTATION", "WILDTYPE": False},
        {"TRUE_HUGO_SYMBOL": "CDKN2A", "VARIANT_CATEGORY": "MUTATION", "WILDTYPE": False},
    ]
    profile = _build_genetic_profile(genomic_docs)
    assert [row["gene"] for row in profile] == ["ATM", "CDKN2A", "TP53"]


def test_render_html_from_pt_trials_matches_populates_genetic_profile(tmp_path):
    from ctm.reports.builder import render_html_from_pt_trials_matches

    pts_path = _make_normalized_json(tmp_path)

    trials_path = tmp_path / "trials.json"
    trials_path.write_text(json.dumps([]))

    matches_path = tmp_path / "matches.json"
    matches_path.write_text(json.dumps({
        "clinical": {"SAMPLE_ID": "000000"},
        "genomic": [
            {"SAMPLE_ID": "000000", "TRUE_HUGO_SYMBOL": "TP53",
             "VARIANT_CATEGORY": "MUTATION", "WILDTYPE": False,
             "TRUE_PROTEIN_CHANGE": "p.Q192*"},
            {"SAMPLE_ID": "000000", "TRUE_HUGO_SYMBOL": "AKT1",
             "VARIANT_CATEGORY": "MUTATION", "WILDTYPE": True},
        ],
        "trial_match": [],
    }))

    html = render_html_from_pt_trials_matches(str(pts_path), str(trials_path), str(matches_path), "000000")
    assert "Patient Genetic Profile" in html
    assert "TP53" in html
    assert "p.Q192*" in html
    assert "AKT1" not in html


def test_render_html_from_pt_trials_matches_shows_known_biomarker_count(tmp_path):
    from ctm.reports.builder import render_html_from_pt_trials_matches

    pts_path = _make_normalized_json(tmp_path)

    trials_path = tmp_path / "trials.json"
    trials_path.write_text(json.dumps([]))

    matches_path = tmp_path / "matches.json"
    matches_path.write_text(json.dumps({
        "clinical": {"SAMPLE_ID": "000000"},
        "genomic": [
            {"SAMPLE_ID": "000000", "TRUE_HUGO_SYMBOL": "TP53",
             "VARIANT_CATEGORY": "MUTATION", "WILDTYPE": False},
            {"SAMPLE_ID": "000000", "TRUE_HUGO_SYMBOL": "AKT1",
             "VARIANT_CATEGORY": "MUTATION", "WILDTYPE": True},
        ],
        "trial_match": [{
            "sample_id": "000000", "match_level": "step", "reason_type": "clinical",
            "show_in_ui": True, "protocol_no": "2025.001", "nct_id": "NCT00000001",
            "match_type": "generic_clinical", "trial_summary_status": "open",
            "sort_order": [1, 99, 99, 99, 99, 99], "hash": "aaa",
        }],
    }))

    html = render_html_from_pt_trials_matches(str(pts_path), str(trials_path), str(matches_path), "000000")
    assert "Known Biomarkers" in html
    assert "2 on file" in html


def test_trial_is_meaningful():
    from ctm.reports.builder import _trial_is_meaningful
    dx = {"treatment_list": {"step": [{"match": [
        {"clinical": {"oncotree_primary_diagnosis": "Melanoma"}}]}]}}
    gen = {"treatment_list": {"step": [{"match": [
        {"or": [{"genomic": {"hugo_symbol": "BRAF"}}]}]}]}}
    age_only = {"treatment_list": {"step": [{"match": [
        {"clinical": {"age_numerical": ">=18"}}]}]}}
    empty = {"treatment_list": {"step": [{"match": []}]}}
    assert _trial_is_meaningful(dx) is True
    assert _trial_is_meaningful(gen) is True
    assert _trial_is_meaningful(age_only) is False
    assert _trial_is_meaningful(empty) is False


def test_render_meaningful_only_drops_age_only_trial_matches(tmp_path):
    from ctm.reports.builder import render_html_from_pt_trials_matches

    pts_path = _make_normalized_json(tmp_path)

    trials_path = tmp_path / "trials.json"
    trials_path.write_text(json.dumps([
        {"protocol_no": "2025.001", "nct_id": "NCT1",
         "_summary": {"long_title": "Age-Only Trial"},
         "treatment_list": {"step": [{"match": [{"clinical": {"age_numerical": ">=18"}}]}]}},
        {"protocol_no": "2025.002", "nct_id": "NCT2",
         "_summary": {"long_title": "Melanoma Trial"},
         "treatment_list": {"step": [{"match": [{"clinical": {"oncotree_primary_diagnosis": "Melanoma"}}]}]}},
    ]))

    matches_path = tmp_path / "matches.json"
    matches_path.write_text(json.dumps({
        "clinical": {"SAMPLE_ID": "000000"}, "genomic": [],
        "trial_match": [
            {"sample_id": "000000", "match_level": "step", "reason_type": "clinical",
             "show_in_ui": True, "protocol_no": "2025.001", "nct_id": "NCT1",
             "match_type": "generic_clinical", "trial_summary_status": "open",
             "sort_order": [1, 99, 99, 99, 99, 99], "hash": "a"},
            {"sample_id": "000000", "match_level": "step", "reason_type": "clinical",
             "show_in_ui": True, "protocol_no": "2025.002", "nct_id": "NCT2",
             "match_type": "generic_clinical", "trial_summary_status": "open",
             "sort_order": [1, 99, 99, 99, 99, 99], "hash": "b"},
        ],
    }))

    full = render_html_from_pt_trials_matches(str(pts_path), str(trials_path), str(matches_path), "000000")
    assert "NCT1" in full and "NCT2" in full          # both present without the flag

    filtered = render_html_from_pt_trials_matches(
        str(pts_path), str(trials_path), str(matches_path), "000000", meaningful_only=True)
    assert "NCT2" in filtered                          # meaningful (diagnosis) kept
    assert "NCT1" not in filtered                      # age-only dropped
