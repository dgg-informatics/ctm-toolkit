"""Tests for the real-data builder loader functions."""
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Task 2: load_context_from_flat_matches
# ---------------------------------------------------------------------------


def test_flat_matches_step_level_doc_produces_a_block():
    from ctm.reports.builder import load_context_from_flat_matches
    matches = [{
        "sample_id": "1", "match_level": "step", "reason_type": "clinical",
        "show_in_ui": True, "protocol_no": "NCT00000001", "nct_id": "NCT00000001",
        "cancer_type_match": "specific", "match_type": "generic_clinical",
        "genomic_alteration": "", "trial_summary_status": "open",
        "sort_order": [1, 99, 99, 99, 99, 99], "hash": "aaa"
    }]
    ctx = load_context_from_flat_matches(matches, "1")
    assert ctx["trial_blocks"][0]["nct_id"] == "NCT00000001"


def test_flat_matches_block_has_required_keys():
    from ctm.reports.builder import load_context_from_flat_matches
    matches = [{
        "sample_id": "1", "match_level": "arm", "reason_type": "genomic",
        "show_in_ui": True, "protocol_no": "NCT00000001", "nct_id": "NCT00000001",
        "cancer_type_match": "specific", "match_type": "gene",
        "genomic_alteration": "EGFR", "trial_summary_status": "open",
        "sort_order": [1, 99, 1, 99, 99, 99], "hash": "aaa"
    }]
    ctx = load_context_from_flat_matches(matches, "1")
    pm = ctx["trial_blocks"][0]
    assert "nct_id" in pm
    assert "trial_status" in pm
    assert isinstance(pm["trial"], list)
    assert isinstance(pm["match_detail"], list)
    assert isinstance(pm["genomic"], list)



def test_match_reason_labels():
    from ctm.reports.builder import _match_reason
    assert _match_reason({"reason_type": "genomic", "genomic_alteration": "HER2"}) == "HER2"
    assert _match_reason({"reason_type": "genomic", "true_hugo_symbol": "BRAF"}) == "BRAF"
    assert _match_reason({"reason_type": "clinical", "match_type": "tmb"}) == "TMB"
    assert _match_reason({"reason_type": "clinical", "match_type": "generic_clinical",
                          "oncotree_primary_diagnosis_name": "Lymphoid"}) == "Lymphoid"
    assert _match_reason({"reason_type": "clinical", "match_type": "generic_clinical"}) == "Clinical criteria"



def test_block_detail_uses_genomic_doc_when_trial_matched_on_both():
    """A trial matching on age (clinical) AND a gene lists both reasons, and
    takes its detail fields from the genomic doc even though the clinical doc
    appears first in the list."""
    from ctm.reports.builder import load_context_from_flat_matches
    matches = [
        {"sample_id": "1", "match_level": "step", "reason_type": "clinical",
         "show_in_ui": True, "protocol_no": "2024.010", "nct_id": "NCT90000003",
         "match_type": "generic_clinical", "genomic_alteration": "",
         "oncotree_primary_diagnosis_name": "Lymphoid",
         "trial_summary_status": "open", "sort_order": [1, 99, 99, 99, 99, 99], "hash": "a"},
        {"sample_id": "1", "match_level": "step", "reason_type": "genomic",
         "show_in_ui": True, "protocol_no": "2024.010", "nct_id": "NCT90000003",
         "match_type": "gene", "genomic_alteration": "HER2", "true_hugo_symbol": "HER2",
         "trial_summary_status": "open", "sort_order": [1, 99, 1, 99, 99, 99], "hash": "b"},
    ]
    ctx = load_context_from_flat_matches(matches, "1")
    block = ctx["trial_blocks"][0]
    assert block["match_reasons"] == ["HER2", "Lymphoid"]
    genomic = {r["label"]: r["value"] for r in block["genomic"]}
    assert genomic["Alteration"] == "HER2"



def test_block_includes_trial_name_from_trials_by_protocol():
    from ctm.reports.builder import load_context_from_flat_matches
    matches = [
        {"sample_id": "1", "match_level": "step", "reason_type": "genomic",
         "show_in_ui": True, "protocol_no": "2025.001", "nct_id": "NCT00000001",
         "match_type": "gene", "genomic_alteration": "EGFR", "trial_summary_status": "open",
         "sort_order": [1, 99, 1, 99, 99, 99], "hash": "aaa"},
        {"sample_id": "1", "match_level": "step", "reason_type": "clinical",
         "show_in_ui": True, "protocol_no": "2025.002", "nct_id": "NCT00000002",
         "match_type": "generic_clinical", "genomic_alteration": "",
         "trial_summary_status": "open", "sort_order": [2, 99, 99, 99, 99, 99], "hash": "bbb"},
    ]
    trials_by_protocol = {
        "2025.001": {"_summary": {"long_title": "Genomic Trial Long Title"}},
        "2025.002": {"_summary": {"short_title": "Clinical Trial Short Title"}},
    }
    ctx = load_context_from_flat_matches(matches, "1", trials_by_protocol)
    names = {b["nct_id"]: {r["label"]: r["value"] for r in b["trial"]}["Trial Name"]
             for b in ctx["trial_blocks"]}
    assert names["NCT00000001"] == "Genomic Trial Long Title"
    assert names["NCT00000002"] == "Clinical Trial Short Title"


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


def test_trial_block_context_disease_site_in_trial_column():
    from ctm.reports.builder import _build_trial_block_context
    match = {"nct_id": "NCT1", "protocol_no": "2025.001", "trial_summary_status": "open"}
    trial = {"_raw": {"disease_site": "Breast; Lung"}}
    ctx = _build_trial_block_context(match, trial)
    labels = {r["label"]: r["value"] for r in ctx["trial"]}
    assert labels["Disease Site"] == "Breast; Lung"


def test_trial_block_context_omits_constant_match_level():
    from ctm.reports.builder import _build_trial_block_context
    match = {"nct_id": "NCT1", "protocol_no": "2025.001", "trial_summary_status": "open",
             "match_level": "arm", "reason_type": "clinical", "match_type": "generic_clinical"}
    ctx = _build_trial_block_context(match, trial=None)
    trial_labels = [r["label"] for r in ctx["trial"]]
    detail_labels = {r["label"]: r["value"] for r in ctx["match_detail"]}
    assert "Match Level" not in trial_labels
    assert "Match Engine" not in trial_labels
    # every trial is curated under step.match, so match_level carries no signal
    assert "Match Level" not in detail_labels
    assert detail_labels["Match Engine"] == "MatchMiner-v2"


def test_trial_block_context_known_biomarker_count_in_genomic_column():
    from ctm.reports.builder import _build_trial_block_context
    match = {"nct_id": "NCT1", "protocol_no": "2025.001", "trial_summary_status": "open",
             "reason_type": "clinical"}
    ctx = _build_trial_block_context(match, trial=None, known_biomarker_count=90)
    genomic_labels = {r["label"]: r["value"] for r in ctx["genomic"]}
    assert genomic_labels["Known Biomarkers"] == "90 on file"


def test_trial_block_context_no_known_biomarker_row_when_count_not_given():
    from ctm.reports.builder import _build_trial_block_context
    match = {"nct_id": "NCT1", "protocol_no": "2025.001", "trial_summary_status": "open"}
    ctx = _build_trial_block_context(match, trial=None)
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


# ---------------------------------------------------------------------------
# Uniform nct-keyed trial blocks
# ---------------------------------------------------------------------------

def _match(nct, *, protocol="P1", reason_type="clinical", dx="Colorectal Cancer",
           alteration="", sort_order=None, **extra):
    """A trial_match doc of the shape matchengine writes (engine.py:856-888)."""
    doc = {
        "sample_id": "1", "nct_id": nct, "protocol_no": protocol,
        "match_level": "step", "reason_type": reason_type, "show_in_ui": True,
        "oncotree_primary_diagnosis_name": dx, "genomic_alteration": alteration,
        "trial_summary_status": "open", "cancer_type_match": "specific",
        "sort_order": sort_order or [1, 99, 99, 99, 99, 99],
    }
    doc.update(extra)
    return doc


def test_trial_blocks_one_block_per_unique_nct():
    """Two protocol_no values under one NCT collapse to a single block."""
    from ctm.reports.builder import load_context_from_flat_matches
    matches = [_match("NCT01", protocol="P1"), _match("NCT01", protocol="P2")]
    ctx = load_context_from_flat_matches(matches, "1")
    assert len(ctx["trial_blocks"]) == 1
    assert ctx["trial_blocks"][0]["nct_id"] == "NCT01"


def test_trial_blocks_list_every_distinct_reason():
    """A trial matched on diagnosis AND a gene reports both, not just one."""
    from ctm.reports.builder import load_context_from_flat_matches
    matches = [
        _match("NCT01", reason_type="clinical"),
        _match("NCT01", reason_type="genomic", alteration="BRCA1 p.V600E"),
    ]
    ctx = load_context_from_flat_matches(matches, "1")
    assert ctx["trial_blocks"][0]["match_reasons"] == ["BRCA1 p.V600E", "Colorectal Cancer"]


def test_trial_blocks_dedupe_repeated_reason():
    """The same reason arriving on several docs is listed once."""
    from ctm.reports.builder import load_context_from_flat_matches
    matches = [_match("NCT01"), _match("NCT01", protocol="P2"), _match("NCT01", protocol="P3")]
    ctx = load_context_from_flat_matches(matches, "1")
    assert ctx["trial_blocks"][0]["match_reasons"] == ["Colorectal Cancer"]


def test_trial_blocks_keep_docs_with_no_protocol_no():
    """protocol_no: null docs are keyed on nct_id, not dropped (builder.py:93)."""
    from ctm.reports.builder import load_context_from_flat_matches
    matches = [_match("NCT01", protocol=None)]
    ctx = load_context_from_flat_matches(matches, "1")
    assert len(ctx["trial_blocks"]) == 1
    assert ctx["trial_blocks"][0]["nct_id"] == "NCT01"


def test_trial_blocks_order_genomic_trials_first():
    from ctm.reports.builder import load_context_from_flat_matches
    matches = [
        _match("NCT_CLINICAL", protocol="P1", reason_type="clinical"),
        _match("NCT_GENOMIC", protocol="P2", reason_type="genomic", alteration="EGFR"),
    ]
    ctx = load_context_from_flat_matches(matches, "1")
    assert [b["nct_id"] for b in ctx["trial_blocks"]] == ["NCT_GENOMIC", "NCT_CLINICAL"]


def test_trial_blocks_are_ranked_from_one():
    from ctm.reports.builder import load_context_from_flat_matches
    matches = [_match(f"NCT{i:02d}", protocol=f"P{i}") for i in range(3)]
    ctx = load_context_from_flat_matches(matches, "1")
    assert [b["rank"] for b in ctx["trial_blocks"]] == [1, 2, 3]


def test_trial_blocks_empty_for_patient_with_no_matches():
    from ctm.reports.builder import load_context_from_flat_matches
    assert load_context_from_flat_matches([], "1")["trial_blocks"] == []
