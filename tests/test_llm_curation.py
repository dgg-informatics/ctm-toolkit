"""Shared _llm_curation model: pieces, stage-owned merge, mixed, parsed_sections."""
import copy

import pytest

from ctm.transformers.llm_curation import (
    CLINICAL,
    GENOMIC,
    make_entry,
    merge_stage,
    piece_texts,
    section_row,
)


def _crit(text, *subs):
    return {"text": text, "sub_criteria": [{"text": s, "sub_criteria": []} for s in subs]}


def _e(source, text, node, node_type, **extra):
    return make_entry(source, text, node, node_type, **extra)


CLIN = {"clinical": {"gender": "Female"}}
GEN = {"genomic": {"hugo_symbol": "ERBB2"}}


def test_piece_texts_criteria_one_per_top_level_with_subs():
    trial = {"eligibility": {"inclusion": [_crit("Has cancer", "Breast"), _crit("Age >= 18")]}}
    texts = piece_texts(trial, "inclusion")
    assert len(texts) == 2
    assert texts[0].splitlines() == ["Has cancer", "  Breast"]
    assert piece_texts(trial, "exclusion") == []


def test_piece_texts_summary_conditions_genes():
    trial = {"_summary": {"long_title": "T"},
             "_raw": {"conditions": ["A", "", "B"], "octsu_genes_interest": "IDH1 (R132)"}}
    assert piece_texts(trial, "summary") == ["T"]
    assert piece_texts(trial, "conditions") == ["A; B"]
    assert piece_texts(trial, "genes_of_interest") == ["IDH1 (R132)"]
    for source in ("summary", "conditions", "genes_of_interest", "inclusion"):
        assert piece_texts({}, source) == []


def test_piece_texts_rejects_disease_site():
    with pytest.raises(ValueError):
        piece_texts({}, "disease_site")


def test_make_entry_shape_and_override():
    e = make_entry("inclusion", "x", None, CLINICAL)
    assert e == {"source": "inclusion", "text": "x", "suggested_node": None,
                 "transferred_to_match": False, "node_type": "clinical", "mixed": False}
    assert make_entry("conditions", "x", CLIN, CLINICAL, transferred_to_match=True)["transferred_to_match"]


def test_merge_partitions_null_and_non_null():
    out = merge_stage(None, CLINICAL, [_e("inclusion", "a", CLIN, CLINICAL),
                                       _e("inclusion", "b", None, CLINICAL)], {})
    assert [e["text"] for e in out["_ctml_suggestions"]] == ["a"]
    assert [e["text"] for e in out["_null_ctml_suggestions"]] == ["b"]


def test_merge_replaces_only_own_node_type():
    g1, g2 = _e("inclusion", "g", GEN, GENOMIC), _e("exclusion", "h", None, GENOMIC)
    cur = {"_ctml_suggestions": [g1, _e("inclusion", "old", CLIN, CLINICAL)],
           "_null_ctml_suggestions": [g2]}
    out = merge_stage(cur, CLINICAL, [_e("inclusion", "new", CLIN, CLINICAL)], {})
    assert [e["text"] for e in out["_ctml_suggestions"]] == ["g", "new"]
    assert out["_null_ctml_suggestions"] == [g2]


def test_merge_never_touches_legacy_entries():
    legacy = {"source": "inclusion", "text": "Age >= 18", "suggested_node": CLIN,
              "transferred_to_match": True}
    refs = [{"biomarker": "X"}]
    cur = {"_ctml_suggestions": [copy.deepcopy(legacy)], "biomarker_references": copy.deepcopy(refs)}
    out = merge_stage(cur, CLINICAL, [_e("inclusion", "Age >= 18", CLIN, CLINICAL)], {})
    assert out["_ctml_suggestions"][0] == legacy
    assert "mixed" not in out["_ctml_suggestions"][0]
    assert out["biomarker_references"] == refs


def test_merge_replace_sources_limits_removal():
    cur = {"_ctml_suggestions": [_e("inclusion", "i", CLIN, CLINICAL),
                                 _e("conditions", "c-old", CLIN, CLINICAL)]}
    out = merge_stage(cur, CLINICAL, [_e("conditions", "c-new", CLIN, CLINICAL)], {},
                      replace_sources={"conditions"})
    assert [e["text"] for e in out["_ctml_suggestions"]] == ["i", "c-new"]


def test_mixed_true_only_when_both_types_non_null_same_piece():
    cur = merge_stage(None, CLINICAL, [_e("inclusion", "HER2+ breast", CLIN, CLINICAL),
                                       _e("exclusion", "HER2+ breast", CLIN, CLINICAL),
                                       _e("inclusion", "null one", None, CLINICAL)], {})
    out = merge_stage(cur, GENOMIC, [_e("inclusion", "HER2+ breast", GEN, GENOMIC),
                                     _e("inclusion", "null one", None, GENOMIC)], {})
    mixed = {(e["node_type"], e["source"]): e["mixed"] for e in out["_ctml_suggestions"]}
    assert mixed == {("clinical", "inclusion"): True, ("clinical", "exclusion"): False,
                     ("genomic", "inclusion"): True}
    assert all(e["mixed"] is False for e in out["_null_ctml_suggestions"])


def test_mixed_recomputed_when_partner_disappears():
    cur = merge_stage(None, CLINICAL, [_e("inclusion", "t", CLIN, CLINICAL)], {})
    cur = merge_stage(cur, GENOMIC, [_e("inclusion", "t", GEN, GENOMIC)], {})
    assert all(e["mixed"] for e in cur["_ctml_suggestions"])
    out = merge_stage(cur, GENOMIC, [_e("inclusion", "t", None, GENOMIC)], {})
    assert out["_ctml_suggestions"][0]["mixed"] is False
    assert out["_null_ctml_suggestions"][0]["mixed"] is False


def test_parsed_sections_merge_per_source():
    cur = {"parsed_sections": {"clinical": {"inclusion": section_row(True, True, 2),
                                            "conditions": section_row(False, False, 0)},
                               "genomic": {"summary": section_row(True, True, 1)}}}
    out = merge_stage(cur, CLINICAL, [], {"conditions": section_row(True, True, 1)})
    assert out["parsed_sections"]["clinical"] == {
        "inclusion": {"found": True, "scanned": True, "pieces": 2},
        "conditions": {"found": True, "scanned": True, "pieces": 1}}
    assert out["parsed_sections"]["genomic"] == cur["parsed_sections"]["genomic"]


def test_merge_does_not_mutate_input():
    cur = {"_ctml_suggestions": [_e("inclusion", "t", CLIN, CLINICAL)], "parsed_sections": {}}
    before = copy.deepcopy(cur)
    merge_stage(cur, GENOMIC, [_e("inclusion", "t", GEN, GENOMIC)], {"inclusion": section_row(True, True, 1)})
    assert cur == before
