"""ctm-llm clinical: clinical-only prompt, node_type entries, parsed_sections."""
import copy

from llm_fakes import QueuedClient

from ctm.transformers.clinical_ctml import (
    CLINICAL_SYSTEM_PROMPT,
    draft_clinical,
    keep_clinical,
)
from ctm.transformers.eligibility_to_ctml import _cache_key

VALID = {"Breast", "Lung"}
REVIEWED = {"breast": {"oncotree": "Breast", "type": "solid"},
            "lung": {"oncotree": "Lung", "type": "solid"}}


def _crit(text):
    return {"text": text, "sub_criteria": []}


def _trial(**extra):
    return {"nct_id": "NCT1",
            "eligibility": {"inclusion": [_crit("Age >= 18")], "exclusion": [_crit("No brain mets")]},
            "_summary": {"long_title": "A Study in Women"}, **extra}


def _draft(trial, client, cache=None):
    return draft_clinical(trial, {} if cache is None else cache, client, VALID, REVIEWED, {})


def _all(curation):
    return curation["_ctml_suggestions"] + curation["_null_ctml_suggestions"]


def test_clinical_entries_tagged_and_partitioned():
    client = QueuedClient(['{"clinical": {"age_numerical": ">=18"}}', "null", "null"])
    cur = _draft(_trial(), client)["_llm_curation"]
    assert len(cur["_ctml_suggestions"]) == 1
    assert len(cur["_null_ctml_suggestions"]) == 2
    assert {e["node_type"] for e in _all(cur)} == {"clinical"}
    assert [e["source"] for e in _all(cur)] == ["inclusion", "exclusion", "summary"]


def test_clinical_strips_genomic_leaves():
    assert keep_clinical({"and": [{"clinical": {"gender": "Female"}},
                                  {"genomic": {"hugo_symbol": "BRCA1"}}]}) == {"clinical": {"gender": "Female"}}
    assert keep_clinical({"genomic": {"hugo_symbol": "BRCA1"}}) is None
    client = QueuedClient(['{"genomic": {"hugo_symbol": "BRCA1"}}', "null", "null"])
    cur = _draft(_trial(), client)["_llm_curation"]
    assert cur["_ctml_suggestions"] == []
    assert len(cur["_null_ctml_suggestions"]) == 3


def test_clinical_prompt_has_no_genomic_fields():
    assert "hugo_symbol" not in CLINICAL_SYSTEM_PROMPT
    assert "genomic" not in CLINICAL_SYSTEM_PROMPT


def test_clinical_includes_conditions_and_disease_site():
    trial = {"_raw": {"conditions": ["Breast Cancer"], "_ddots": {"disease_site": "Breast"}}}
    client = QueuedClient([])
    cur = _draft(trial, client)["_llm_curation"]
    by_source = {e["source"]: e for e in cur["_ctml_suggestions"]}
    assert set(by_source) == {"conditions", "disease_site"}
    assert all(e["node_type"] == "clinical" and e["mixed"] is False for e in by_source.values())
    assert by_source["conditions"]["transferred_to_match"] is True
    assert by_source["disease_site"]["mapping_source"] == "reviewed"
    assert client.call_count == 0


def test_clinical_parsed_sections_rows():
    trial = _trial(_raw={"_ddots": {"disease_site": "Breast; Lung"}, "octsu_genes_interest": "IDH1"})
    trial["eligibility"]["inclusion"].append(_crit("ECOG 0-1"))
    client = QueuedClient(["null"] * 4)
    rows = _draft(trial, client)["_llm_curation"]["parsed_sections"]["clinical"]
    assert set(rows) == {"inclusion", "exclusion", "summary", "conditions", "disease_site"}
    assert rows["inclusion"] == {"found": True, "scanned": True, "pieces": 2}
    assert rows["conditions"] == {"found": False, "scanned": False, "pieces": 0}
    assert rows["disease_site"] == {"found": True, "scanned": True, "pieces": 2}


def test_clinical_empty_trial():
    client = QueuedClient([])
    cur = _draft({}, client)["_llm_curation"]
    assert cur["_ctml_suggestions"] == [] and cur["_null_ctml_suggestions"] == []
    assert all(r == {"found": False, "scanned": False, "pieces": 0}
               for r in cur["parsed_sections"]["clinical"].values())
    assert len(cur["parsed_sections"]["clinical"]) == 5
    assert client.call_count == 0


def test_clinical_leaves_legacy_entries_alone():
    legacy = {"source": "inclusion", "text": "Age >= 18",
              "suggested_node": {"clinical": {"age_numerical": ">=18"}}, "transferred_to_match": True}
    refs = [{"trial_nct": "NCT1", "biomarker": "BRAF", "section": "eligibility"}]
    trial = _trial(_llm_curation={"_ctml_suggestions": [copy.deepcopy(legacy)],
                                  "biomarker_references": copy.deepcopy(refs)})
    cur = _draft(trial, QueuedClient(["null"] * 3))["_llm_curation"]
    assert cur["_ctml_suggestions"][0] == legacy
    assert cur["biomarker_references"] == refs


def test_clinical_cache_key_is_stage_scoped():
    from ctm.transformers.clinical_ctml import PROMPT_VERSION

    cache = {}
    trial = {"eligibility": {"inclusion": [_crit("Age >= 18")]}}
    client = QueuedClient(['{"clinical": {"age_numerical": ">=18"}}'])
    _draft(trial, client, cache)
    assert _cache_key(f"clinical:{PROMPT_VERSION()}:inclusion:Age >= 18") in cache
    _draft(trial, client, cache)
    assert client.call_count == 1


def test_draft_clinical_does_not_mutate_input():
    trial = _trial()
    before = copy.deepcopy(trial)
    _draft(trial, QueuedClient(["null"] * 3))
    assert trial == before


def test_clinical_drops_a_legacy_top_level_suggestions_key():
    trial = _trial(_ctml_suggestions=[{"source": "inclusion", "suggested_node": None}])
    drafted = _draft(trial, QueuedClient(["null"] * 3))
    assert "_ctml_suggestions" not in drafted
    assert "_ctml_suggestions" in drafted["_llm_curation"]


# ── final-review fixes ───────────────────────────────────────────────────────

def test_clinical_prompt_change_misses_the_cache(monkeypatch):
    from ctm.transformers import clinical_ctml

    cache, trial = {}, {"_summary": {"long_title": "t"}}
    client = QueuedClient(["null", "null"])
    _draft(trial, client, cache)
    monkeypatch.setattr(clinical_ctml, "CLINICAL_SYSTEM_PROMPT", clinical_ctml.CLINICAL_SYSTEM_PROMPT + " v2")
    _draft(trial, client, cache)
    assert client.call_count == 2


def test_clinical_handles_fenced_and_empty_responses():
    trial = {"eligibility": {"inclusion": [_crit("Age >= 18"), _crit("Female")]}}
    client = QueuedClient(['```json\n{"clinical": {"age_numerical": ">=18"}}\n```', None])
    cur = _draft(trial, client)["_llm_curation"]
    assert [e["text"] for e in cur["_ctml_suggestions"]] == ["Age >= 18"]
    assert [e["text"] for e in cur["_null_ctml_suggestions"]] == ["Female"]
