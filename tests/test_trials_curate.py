"""Tests for trials_curate.py — the LLM curation synthesis stage that runs
after ctm-ctml, adding biomarker-reference scanning, a title-derived
suggestion, and a unioned final_suggested_ctml to each trial."""
import json
from types import SimpleNamespace


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


class _FakeClient:
    """Stub OpenAI-compatible client: returns queued chat-completion responses
    in call order, so a test can control exactly what "the LLM said" without
    hitting a real API."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.call_count = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.call_count += 1
        content = self._responses.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _trial_with_eligibility(nct_id="NCT00000001", text="Patients must have BRCA1 mutation"):
    return {
        "nct_id": nct_id,
        "protocol_no": None,
        "eligibility": {"inclusion": [{"text": text, "sub_criteria": []}], "exclusion": []},
    }


def test_scan_biomarkers_cache_miss_calls_client_and_caches():
    from ctm.transformers.trials_curate import scan_biomarkers

    trial = _trial_with_eligibility()
    client = _FakeClient(['[{"biomarker": "BRCA1", "type": "snv", "reference": "BRCA1 mutation"}]'])
    cache = {}

    hits = scan_biomarkers(trial, client, cache, known_genes={"BRCA1"})

    assert client.call_count == 1
    assert hits == [{
        "trial_nct": "NCT00000001",
        "reference": "BRCA1 mutation",
        "biomarker": "BRCA1",
        "type": "snv",
        "in_kb": True,
    }]
    assert len(cache) == 1


def test_scan_biomarkers_cache_hit_skips_client():
    from ctm.transformers.trials_curate import scan_biomarkers, _cache_key, _trial_full_eligibility_text

    trial = _trial_with_eligibility()
    text = _trial_full_eligibility_text(trial)
    key = _cache_key("NCT00000001", text)
    cache = {key: [{"biomarker": "BRCA1", "type": "snv", "reference": "BRCA1 mutation"}]}
    client = _FakeClient([])  # no responses queued — a call would raise IndexError

    hits = scan_biomarkers(trial, client, cache, known_genes={"BRCA1"})

    assert client.call_count == 0
    assert hits[0]["biomarker"] == "BRCA1"


def test_scan_biomarkers_empty_eligibility_returns_empty_no_call():
    from ctm.transformers.trials_curate import scan_biomarkers

    trial = {"nct_id": "NCT00000002", "protocol_no": None, "eligibility": {"inclusion": [], "exclusion": []}}
    client = _FakeClient([])

    hits = scan_biomarkers(trial, client, {}, known_genes=set())

    assert hits == []
    assert client.call_count == 0


def test_scan_biomarkers_marks_unknown_gene_not_in_kb():
    from ctm.transformers.trials_curate import scan_biomarkers

    trial = _trial_with_eligibility(text="Must have CD22 expression")
    client = _FakeClient(['[{"biomarker": "CD22", "type": "ihc", "reference": "CD22 expression"}]'])

    hits = scan_biomarkers(trial, client, {}, known_genes={"BRCA1"})

    assert hits[0]["in_kb"] is False


def _full_trial(nct_id="NCT00000003"):
    return {
        "nct_id": nct_id,
        "protocol_no": None,
        "eligibility": {
            "inclusion": [{"text": "Patients must be >= 18 years old", "sub_criteria": []}],
            "exclusion": [],
        },
        "_summary": {"long_title": "A Study of Olaparib in Patients with a BRCA1 Mutation"},
        "_ctml_suggestions": [
            {"source": "inclusion", "text": "Patients must be >= 18 years old",
             "suggested_node": {"clinical": {"age_numerical": ">=18"}}, "transferred_to_match": False},
        ],
    }


def test_curate_trial_builds_llm_curation_with_all_three_subfields():
    from ctm.transformers.trials_curate import curate_trial

    trial = _full_trial()
    # curate_trial calls suggest_node (summary) first, then scan_biomarkers —
    # two responses queued in that order.
    client = _FakeClient([
        '{"genomic": {"hugo_symbol": "BRCA1", "variant_category": "MUTATION"}}',
        '[]',
    ])

    result = curate_trial(trial, client, cache={}, known_genes={"BRCA1"}, valid_oncotree=set())

    assert "_llm_curation" in result
    curation = result["_llm_curation"]
    assert "_ctml_suggestions" in curation
    assert "biomarker_references" in curation
    assert "final_suggested_ctml" in curation


def test_curate_trial_removes_top_level_ctml_suggestions():
    from ctm.transformers.trials_curate import curate_trial

    trial = _full_trial()
    client = _FakeClient(['null', '[]'])

    result = curate_trial(trial, client, cache={}, known_genes=set(), valid_oncotree=set())

    assert "_ctml_suggestions" not in result


def test_curate_trial_summary_source_labeled_correctly():
    from ctm.transformers.trials_curate import curate_trial

    trial = _full_trial()
    client = _FakeClient([
        '{"genomic": {"hugo_symbol": "BRCA1", "variant_category": "MUTATION"}}',
        '[]',
    ])

    result = curate_trial(trial, client, cache={}, known_genes=set(), valid_oncotree=set())

    suggestions = result["_llm_curation"]["_ctml_suggestions"]
    summary_entries = [s for s in suggestions if s["source"] == "summary"]
    assert len(summary_entries) == 1
    assert summary_entries[0]["text"] == "A Study of Olaparib in Patients with a BRCA1 Mutation"
    assert summary_entries[0]["suggested_node"] == {"genomic": {"hugo_symbol": "BRCA1", "variant_category": "MUTATION"}}
    assert summary_entries[0]["transferred_to_match"] is False


def test_curate_trial_final_suggested_ctml_unions_criterion_and_summary():
    from ctm.transformers.trials_curate import curate_trial

    trial = _full_trial()
    client = _FakeClient([
        '{"genomic": {"hugo_symbol": "BRCA1", "variant_category": "MUTATION"}}',
        '[]',
    ])

    result = curate_trial(trial, client, cache={}, known_genes=set(), valid_oncotree=set())

    final = result["_llm_curation"]["final_suggested_ctml"]
    assert {"clinical": {"age_numerical": ">=18"}} in final
    assert {"genomic": {"hugo_symbol": "BRCA1", "variant_category": "MUTATION"}} in final
    assert len(final) == 2
