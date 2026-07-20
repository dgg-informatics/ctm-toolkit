"""Tests for trials_curate.py — the LLM curation synthesis stage that runs
after ctm-ctml, adding biomarker-reference scanning, a title-derived
suggestion, and a unioned final_suggested_ctml to each trial."""
import json


def test_parse_json_array_valid_json():
    from ctm.transformers.trials_curate import _parse_json_array
    result = _parse_json_array('[{"biomarker": "BRCA1"}]')
    assert result == [{"biomarker": "BRCA1"}]


def test_parse_json_array_strips_markdown_fence():
    from ctm.transformers.trials_curate import _parse_json_array
    raw = '```json\n[{"biomarker": "BRCA1"}]\n```'
    result = _parse_json_array(raw)
    assert result == [{"biomarker": "BRCA1"}]


def test_parse_json_array_invalid_json_returns_empty():
    from ctm.transformers.trials_curate import _parse_json_array
    assert _parse_json_array("not json at all") == []


def test_parse_json_array_non_list_returns_empty():
    from ctm.transformers.trials_curate import _parse_json_array
    assert _parse_json_array('{"not": "a list"}') == []


def test_trial_id_prefers_nct_id():
    from ctm.transformers.trials_curate import _trial_id
    trial = {"nct_id": "NCT04858334", "protocol_no": "2021.070"}
    assert _trial_id(trial) == "NCT04858334"


def test_trial_id_falls_back_to_protocol_no():
    from ctm.transformers.trials_curate import _trial_id
    trial = {"nct_id": None, "protocol_no": "2021.070"}
    assert _trial_id(trial) == "2021.070"


def test_union_match_nodes_filters_null_and_flattens():
    from ctm.transformers.trials_curate import union_match_nodes
    suggestions = [
        {"source": "inclusion", "suggested_node": {"clinical": {"age_numerical": ">=18"}}},
        {"source": "exclusion", "suggested_node": None},
        {"source": "summary", "suggested_node": {"genomic": {"hugo_symbol": "BRCA1"}}},
    ]
    result = union_match_nodes(suggestions)
    assert result == [
        {"clinical": {"age_numerical": ">=18"}},
        {"genomic": {"hugo_symbol": "BRCA1"}},
    ]


def test_union_match_nodes_empty_input():
    from ctm.transformers.trials_curate import union_match_nodes
    assert union_match_nodes([]) == []


def test_load_known_genes(tmp_path):
    from ctm.transformers.trials_curate import load_known_genes
    kb_path = tmp_path / "kb.json"
    kb_path.write_text(json.dumps([{"name": "BRAF"}, {"name": "Kit"}]))
    genes = load_known_genes(kb_path)
    assert genes == {"BRAF", "KIT"}


def test_load_cache_missing_file_returns_empty(tmp_path):
    from ctm.transformers.trials_curate import load_cache
    assert load_cache(tmp_path / "does-not-exist.json") == {}


def test_save_and_load_cache_roundtrip(tmp_path):
    from ctm.transformers.trials_curate import load_cache, save_cache
    cache_path = tmp_path / "cache.json"
    save_cache({"key1": ["hit1"]}, cache_path)
    assert load_cache(cache_path) == {"key1": ["hit1"]}
