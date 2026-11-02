"""ctm-llm genomic: per-piece genomic CTML plus molecular_genomic_refs."""
import copy
import json

import pytest
from llm_fakes import ContentFilterError, QueuedClient

from ctm.transformers.eligibility_to_ctml import _cache_key
from ctm.transformers.genomic_ctml import GENOMIC_SYSTEM_PROMPT, draft_genomic

KNOWN = {"MYCN", "BRAF"}
MYCN_NODE = {"genomic": {"hugo_symbol": "MYCN", "variant_category": "Copy Number Variation",
                         "wildtype": False}}


def _resp(node=None, refs=()):
    return json.dumps({"node": node, "refs": list(refs)})


def _crit(text):
    return {"text": text, "sub_criteria": []}


def _cur(trial, client, cache=None):
    return draft_genomic(trial, {} if cache is None else cache, client, KNOWN)["_llm_curation"]


def test_genomic_entries_and_refs_per_piece():
    trial = {"nct_id": "NCT9", "eligibility": {"inclusion": [_crit("MYCN amplified"), _crit("MYCN amp by FISH")]}}
    ref = {"biomarker": "MYCN", "type": "cnv", "reference": "MYCN amplification", "origin": "somatic"}
    cur = _cur(trial, QueuedClient([_resp(MYCN_NODE, [ref])] * 2))
    assert [e["node_type"] for e in cur["_ctml_suggestions"]] == ["genomic", "genomic"]
    assert cur["_ctml_suggestions"][0]["suggested_node"] == MYCN_NODE
    refs = cur["molecular_genomic_refs"]
    assert len(refs) == 2
    assert all(r == {"trial_nct": "NCT9", "reference": "MYCN amplification", "biomarker": "MYCN",
                     "type": "cnv", "section": "inclusion", "in_kb": True, "origin": "somatic"}
               for r in refs)


def test_ref_section_set_by_code():
    trial = {"_summary": {"long_title": "A BRAF trial"}}
    ref = {"biomarker": "BRAF", "type": "snv", "reference": "BRAF", "section": "titles"}
    cur = _cur(trial, QueuedClient([_resp(None, [ref])]))
    assert cur["molecular_genomic_refs"][0]["section"] == "summary"


def test_ref_origin_from_model_and_coerced():
    refs = [{"biomarker": "B", "type": "snv", "reference": "r", "origin": o}
            for o in ("germline", "Somatic", "blood")] + [{"biomarker": "B", "type": "snv", "reference": "r"}]
    cur = _cur({"_summary": {"long_title": "t"}}, QueuedClient([_resp(None, refs)]))
    assert [r["origin"] for r in cur["molecular_genomic_refs"]] == ["germline", "somatic", "unknown", "unknown"]


def test_refs_without_a_biomarker_are_dropped():
    refs = [{"biomarker": "", "type": "snv"}, "junk", {"type": "snv"}, {"biomarker": "BRAF"}]
    cur = _cur({"_summary": {"long_title": "t"}}, QueuedClient([_resp(None, refs)]))
    assert [r["biomarker"] for r in cur["molecular_genomic_refs"]] == ["BRAF"]
    assert cur["molecular_genomic_refs"][0]["type"] == "other"


def test_genomic_reads_conditions_and_genes_not_disease_site():
    trial = {"_raw": {"conditions": ["EGFR-Mutant NSCLC"], "octsu_genes_interest": "IDH1 (R132)",
                      "_ddots": {"disease_site": "Lung- Non-Small Cell Lung Cancer"}}}
    client = QueuedClient([_resp()] * 2)
    cur = _cur(trial, client)
    assert len(client.prompts) == 2
    assert "EGFR-Mutant NSCLC" in client.prompts[0] and "IDH1 (R132)" in client.prompts[1]
    assert not any("Lung- Non-Small" in p for p in client.prompts)
    rows = cur["parsed_sections"]["genomic"]
    assert set(rows) == {"inclusion", "exclusion", "summary", "conditions", "genes_of_interest"}
    assert rows["genes_of_interest"] == {"found": True, "scanned": True, "pieces": 1}
    assert rows["inclusion"] == {"found": False, "scanned": False, "pieces": 0}


def test_genomic_validates_nodes():
    bad = {"genomic": {"hugo_symbol": "KRAS", "variant_category": "MUTATION", "mmr_status": "bogus"}}
    cur = _cur({"_summary": {"long_title": "t"}}, QueuedClient([_resp(bad)]))
    assert cur["_ctml_suggestions"] == []
    assert cur["_null_ctml_suggestions"][0]["node_type"] == "genomic"


@pytest.mark.parametrize("raw", ["not json", "[]", '"x"', '{"refs": []}', '{"node": "x"}', '{"node": null, "refs": "x"}'])
def test_genomic_junk_responses_become_null(raw):
    cur = _cur({"_summary": {"long_title": "t"}}, QueuedClient([raw]))
    assert cur["_ctml_suggestions"] == []
    assert len(cur["_null_ctml_suggestions"]) == 1
    assert cur["_null_ctml_suggestions"][0]["node_type"] == "genomic"
    assert cur["molecular_genomic_refs"] == []


def test_genomic_content_filter_is_cached_null():
    trial = {"eligibility": {"inclusion": [_crit("first"), _crit("MYCN amplified")]}}
    cache = {}
    client = QueuedClient([ContentFilterError("blocked"), _resp(MYCN_NODE)])
    cur = _cur(trial, client, cache)
    from ctm.transformers.genomic_ctml import PROMPT_VERSION
    assert cache[_cache_key(f"genomic:{PROMPT_VERSION()}:inclusion:first")] == {"node": None, "refs": []}
    assert [e["text"] for e in cur["_null_ctml_suggestions"]] == ["first"]
    assert [e["text"] for e in cur["_ctml_suggestions"]] == ["MYCN amplified"]


def test_genomic_cache_hit_skips_client():
    cache = {}
    trial = {"_summary": {"long_title": "t"}}
    client = QueuedClient([_resp(MYCN_NODE)])
    _cur(trial, client, cache)
    _cur(trial, client, cache)
    assert client.call_count == 1


def test_genomic_empty_trial():
    client = QueuedClient([])
    cur = _cur({}, client)
    assert client.call_count == 0
    assert cur["molecular_genomic_refs"] == []
    assert all(r["found"] is False for r in cur["parsed_sections"]["genomic"].values())
    assert len(cur["parsed_sections"]["genomic"]) == 5


def test_genomic_keeps_legacy_biomarker_references_and_clinical_entries():
    legacy = [{"trial_nct": "NCT1", "biomarker": "BRCA1", "section": "eligibility"}]
    clinical = {"source": "summary", "text": "t", "suggested_node": {"clinical": {"gender": "Female"}},
                "transferred_to_match": False, "node_type": "clinical", "mixed": False}
    trial = {"_summary": {"long_title": "t"},
             "_llm_curation": {"biomarker_references": copy.deepcopy(legacy),
                               "_ctml_suggestions": [copy.deepcopy(clinical)]}}
    cur = _cur(trial, QueuedClient([_resp(MYCN_NODE)]))
    assert cur["biomarker_references"] == legacy
    by_type = {e["node_type"]: e for e in cur["_ctml_suggestions"]}
    assert by_type["clinical"]["suggested_node"] == clinical["suggested_node"]
    assert by_type["clinical"]["mixed"] is True and by_type["genomic"]["mixed"] is True


def test_draft_genomic_does_not_mutate_input():
    trial = {"_summary": {"long_title": "t"}, "_llm_curation": {}}
    before = copy.deepcopy(trial)
    draft_genomic(trial, {}, QueuedClient([_resp()]), KNOWN)
    assert trial == before


def test_genomic_prompt_pins_vocabulary():
    for term in ("MMR-Deficient", "MMR-Proficient", "MSI-H", "MSI-L", "MSS", "Copy Number Variation",
                 "Structural Variation", "wildcard_protein_change", "variant_classification", "exon",
                 "germline", "somatic"):
        assert term in GENOMIC_SYSTEM_PROMPT, term
    for term in ("MUTATION", "(MMR-D / MSI-H)", '"!Copy Number Variation"', '"!Structural Variation"'):
        assert term not in GENOMIC_SYSTEM_PROMPT, term


def test_genomic_prompt_documents_the_wildtype_table():
    for term in ('"!Mutation"', "untested", "never \"!\"", "ms_status", "no wildtype"):
        assert term in GENOMIC_SYSTEM_PROMPT, term
    assert "no documented mutation" in GENOMIC_SYSTEM_PROMPT.lower()
    assert '"!Mutation", wildtype: false' in GENOMIC_SYSTEM_PROMPT


# ── final-review fixes ───────────────────────────────────────────────────────

def test_malformed_ref_fields_are_coerced_so_stamp_never_fails():
    from ctm.schemas.storage import MolecularGenomicRef

    refs = [{"biomarker": "BRAF", "reference": ["x"], "type": ["snv"]},
            {"biomarker": "MYCN", "reference": 5, "type": "weird"},
            {"biomarker": "HRD", "reference": "HRD+", "type": "hrd"}]
    cur = _cur({"_summary": {"long_title": "t"}}, QueuedClient([_resp(None, refs)]))
    stored = cur["molecular_genomic_refs"]
    assert [(r["reference"], r["type"]) for r in stored] == [("", "other"), ("", "other"), ("HRD+", "hrd")]
    for r in stored:
        MolecularGenomicRef.model_validate(r)


def test_exclusion_no_known_mutation_node_is_kept():
    trial = {"eligibility": {"exclusion": [_crit("Known KRAS mutation")]}}
    node = {"genomic": {"hugo_symbol": "KRAS", "variant_category": "!Mutation"}}
    cur = _cur(trial, QueuedClient([_resp(node)]))
    assert cur["_ctml_suggestions"][0]["suggested_node"] == {
        "genomic": {"hugo_symbol": "KRAS", "variant_category": "!Mutation", "wildtype": False}}


def test_prompt_change_misses_the_cache(monkeypatch):
    from ctm.transformers import genomic_ctml

    cache, trial = {}, {"_summary": {"long_title": "t"}}
    client = QueuedClient([_resp(MYCN_NODE), _resp(MYCN_NODE)])
    _cur(trial, client, cache)
    monkeypatch.setattr(genomic_ctml, "GENOMIC_SYSTEM_PROMPT", genomic_ctml.GENOMIC_SYSTEM_PROMPT + " v2")
    _cur(trial, client, cache)
    assert client.call_count == 2


def test_cached_answers_are_revalidated_on_read():
    """A validator fix reaches answers cached before it."""
    cache, trial = {}, {"_summary": {"long_title": "t"}}
    _cur(trial, QueuedClient([_resp(MYCN_NODE)]), cache)
    (key,) = cache
    cache[key] = {"node": {"genomic": {"hugo_symbol": "!KRAS"}}, "refs": [{"biomarker": "KRAS", "type": ["x"]}]}
    cur = _cur(trial, QueuedClient([]), cache)
    assert cur["_ctml_suggestions"] == []
    assert cur["molecular_genomic_refs"][0]["type"] == "other"
