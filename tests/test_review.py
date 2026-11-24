"""ctm-review's pure tree and log functions (src/ctm/review.py)."""
import copy
from datetime import UTC, datetime

import pytest

from ctm.review import (
    MARKER,
    apply_actions,
    build_review_docs,
    format_path,
    iter_leaves,
    link_source,
    parse_path,
    validate_node,
)

L_AGE = {"clinical": {"age_numerical": ">=18", MARKER: "auto"}}
L_KRAS = {"genomic": {"hugo_symbol": "KRAS", MARKER: "auto"}}
L_NRAS = {"genomic": {"hugo_symbol": "NRAS", MARKER: "auto"}}
L_DX = {"clinical": {"oncotree_primary_diagnosis_name": "Colorectal Cancer", MARKER: "auto"}}


def _tl():
    """One step: and[ age, or[kras, nras], dx ]."""
    return {"step": [{"match": [{"and": [copy.deepcopy(L_AGE),
                                         {"or": [copy.deepcopy(L_KRAS), copy.deepcopy(L_NRAS)]},
                                         copy.deepcopy(L_DX)]}]}]}


def _act(action, path, node=None, step_index=0, note=""):
    a = {"action": action, "step_index": step_index, "path": path, "note": note}
    if node is not None:
        a["node"] = node
    return a


def _match(tl):
    return tl["step"][0]["match"]


# ── paths ────────────────────────────────────────────────────────────────────

def test_parse_and_format_path_round_trip():
    segs = parse_path("match[0].and[1].or[0]")
    assert segs == ["match", 0, "and", 1, "or", 0]
    assert format_path(segs) == "match[0].and[1].or[0]"
    assert parse_path("match") == ["match"]
    with pytest.raises(ValueError):
        parse_path("and[0]")
    with pytest.raises(ValueError):
        parse_path("match[x]")


def test_iter_leaves_paths():
    paths = [p for p, _ in iter_leaves(_match(_tl()))]
    assert paths == ["match[0].and[0]", "match[0].and[1].or[0]",
                     "match[0].and[1].or[1]", "match[0].and[2]"]


# ── validate_node ────────────────────────────────────────────────────────────

def test_validate_node_accepts_and_marks_manual():
    assert validate_node('{"genomic": {"hugo_symbol": "KRAS"}}') == {
        "genomic": {"hugo_symbol": "KRAS", MARKER: "manual"}}


def test_validate_node_overwrites_user_marker():
    node = validate_node({"clinical": {"age_numerical": ">=18", MARKER: "auto"}})
    assert node["clinical"][MARKER] == "manual"


@pytest.mark.parametrize("text", [
    "not json", "[]", "{}", '{"clinical": {}}',
    '{"clinical": {"a": 1}, "genomic": {"b": 2}}',
    '{"and": [{"clinical": {"a": 1}}]}', '{"clinical": "x"}',
])
def test_validate_node_rejects(text):
    with pytest.raises(ValueError):
        validate_node(text)


# ── apply_actions ────────────────────────────────────────────────────────────

def test_verify_stamps_verified_only():
    new, resolved = apply_actions(_tl(), [_act("verified", "match[0].and[0]")])
    leaves = dict(iter_leaves(_match(new)))
    assert leaves["match[0].and[0]"]["clinical"][MARKER] == "verified"
    assert leaves["match[0].and[1].or[0]"] == L_KRAS
    assert leaves["match[0].and[2]"] == L_DX
    assert resolved[0]["node_before"] == L_AGE
    assert resolved[0]["node_after"]["clinical"][MARKER] == "verified"


def test_edit_replaces_leaf_with_manual():
    node = {"genomic": {"hugo_symbol": "BRAF"}}
    new, resolved = apply_actions(_tl(), [_act("edited", "match[0].and[1].or[0]", node)])
    assert dict(iter_leaves(_match(new)))["match[0].and[1].or[0]"] == {
        "genomic": {"hugo_symbol": "BRAF", MARKER: "manual"}}
    assert resolved[0]["node_before"] == L_KRAS
    assert resolved[0]["node_after"] == {"genomic": {"hugo_symbol": "BRAF", MARKER: "manual"}}


def test_add_to_wrapper_and_to_root():
    braf = {"genomic": {"hugo_symbol": "BRAF"}}
    ecog = {"clinical": {"ecog_performance_status": "0"}}
    new, resolved = apply_actions(_tl(), [_act("added", "match[0].and[1]", braf),
                                          _act("added", "match", ecog)])
    m = _match(new)
    assert m[0]["and"][1]["or"][-1] == {"genomic": {"hugo_symbol": "BRAF", MARKER: "manual"}}
    assert m[-1] == {"clinical": {"ecog_performance_status": "0", MARKER: "manual"}}
    assert all(r["node_before"] is None for r in resolved)


def test_paths_refer_to_loaded_tree():
    node = {"clinical": {"oncotree_primary_diagnosis_name": "Melanoma"}}
    new, _ = apply_actions(_tl(), [_act("deleted", "match[0].and[0]"),
                                   _act("edited", "match[0].and[2]", node)])
    children = _match(new)[0]["and"]
    assert L_AGE not in children
    assert children[-1]["clinical"]["oncotree_primary_diagnosis_name"] == "Melanoma"
    assert children[0] == {"or": [L_KRAS, L_NRAS]}


def test_delete_prunes_empty_wrappers_upward():
    new, _ = apply_actions(_tl(), [_act("deleted", "match[0].and[1].or[0]"),
                                   _act("deleted", "match[0].and[1].or[1]")])
    assert _match(new) == [{"and": [L_AGE, L_DX]}]

    every = [_act("deleted", p) for p, _ in iter_leaves(_match(_tl()))]
    new, resolved = apply_actions(_tl(), every)
    assert _match(new) == []
    assert all(r["node_after"] is None for r in resolved)


def test_single_child_wrapper_kept():
    new, _ = apply_actions(_tl(), [_act("deleted", "match[0].and[1].or[0]")])
    assert _match(new)[0]["and"][1] == {"or": [L_NRAS]}


@pytest.mark.parametrize("actions", [
    [_act("approved", "match[0].and[0]")],
    [_act("verified", "match[0].and[0]", step_index=5)],
    [_act("verified", "match[0]")],
    [_act("added", "match[0].and[0]", {"clinical": {"a": 1}})],
    [_act("verified", "match[0].and[0]"), _act("deleted", "match[0].and[0]")],
    [_act("edited", "match[0].and[0]", {})],
    [_act("edited", "match[0].and[0]")],
    [_act("verified", "match[9]")],
])
def test_rejects(actions):
    with pytest.raises(ValueError):
        apply_actions(_tl(), actions)


def test_input_not_mutated():
    tl = _tl()
    before = copy.deepcopy(tl)
    apply_actions(tl, [_act("deleted", "match[0].and[0]"),
                       _act("verified", "match[0].and[2]"),
                       _act("added", "match", {"clinical": {"a": 1}})])
    assert tl == before


# ── source link and log documents ────────────────────────────────────────────

def _sugg(node, source="eligibility.inclusion", text="Age >= 18 with CRC"):
    return {"source": source, "text": text, "suggested_node": node, "node_type": "clinical"}


def test_link_source_matches_leaf_inside_tree():
    tree = {"and": [{"clinical": {"age_numerical": ">=18"}},
                    {"clinical": {"oncotree_primary_diagnosis_name": "X"}}]}
    curation = {"_ctml_suggestions": [_sugg(tree, text="Age ≥ 18 with X")]}
    assert link_source(L_AGE, curation) == ("eligibility.inclusion", "Age ≥ 18 with X")


def test_link_source_first_wins_and_none():
    leaf = {"clinical": {"age_numerical": ">=18"}}
    curation = {"_ctml_suggestions": [
        _sugg({"clinical": {"age_numerical": "<99"}}, text="other"),
        _sugg(leaf, source="_raw.minimum_age", text="first"),
        _sugg(leaf, source="eligibility.inclusion", text="second"),
    ]}
    assert link_source(L_AGE, curation) == ("_raw.minimum_age", "first")
    assert link_source(L_KRAS, curation) == (None, None)
    assert link_source(L_AGE, None) == (None, None)
    assert link_source(L_AGE, {"_ctml_suggestions": [_sugg(None)]}) == (None, None)


LOG_FIELDS = {"action", "entity", "trial_key", "nct_id", "protocol_no", "step_index", "path",
              "node_before", "node_after", "note", "reviewer", "reviewed_at", "batch_id",
              "trial_hash", "source_field", "source_text", "stage_runs", "processed_with"}


def test_build_review_docs_fields():
    stage_runs = {"genomic": {"ran_at": "2026-11-20T07:00:00Z", "processed_with": "ctm-llm 3.0.0"}}
    trial = {"entity": "sparrow-api", "nct_id": "NCT01234567", "protocol_no": None,
             "trial_hash": "a" * 64, "treatment_list": _tl(),
             "_llm_curation": {"_ctml_suggestions": [_sugg({"genomic": {"hugo_symbol": "KRAS"}},
                                                           text="KRAS mutant")],
                               "stage_runs": stage_runs}}
    _, resolved = apply_actions(trial["treatment_list"], [
        _act("verified", "match[0].and[0]"),
        _act("edited", "match[0].and[1].or[0]", {"genomic": {"hugo_symbol": "BRAF"}}, note="wrong gene"),
        _act("deleted", "match[0].and[2]"),
        _act("added", "match", {"clinical": {"ecog_performance_status": "0"}}),
    ])
    when = datetime(2026, 11, 23, tzinfo=UTC)
    docs = build_review_docs(trial, resolved, reviewer="dane", reviewed_at=when,
                             batch_id="b-1", processed_with="ctm-review 3.0.0")
    assert [d["action"] for d in docs] == ["verified", "edited", "deleted", "added"]
    for d in docs:
        assert set(d) == LOG_FIELDS
        assert (d["trial_key"], d["nct_id"], d["protocol_no"]) == ("NCT01234567", "NCT01234567", None)
        assert (d["reviewer"], d["reviewed_at"], d["batch_id"]) == ("dane", when, "b-1")
        assert d["trial_hash"] == "a" * 64 and d["processed_with"] == "ctm-review 3.0.0"
        assert d["stage_runs"] == stage_runs and d["stage_runs"] is not stage_runs
    verified, edited, deleted, added = docs
    assert verified["note"] == "" and edited["note"] == "wrong gene"
    assert edited["source_field"] == "eligibility.inclusion" and edited["source_text"] == "KRAS mutant"
    assert verified["source_text"] is None
    assert deleted["node_after"] is None and deleted["node_before"] == L_DX
    assert added["node_before"] is None and added["source_text"] is None
    assert added["path"] == "match" and added["step_index"] == 0


# ── adding and/or groups ─────────────────────────────────────────────────────

GROUP = {"and": [{"clinical": {"oncotree_primary_diagnosis": "Breast"}},
                 {"or": [{"clinical": {"age_numerical": ">=18"}},
                         {"genomic": {"hugo_symbol": "ERBB2", MARKER: "auto"}}]}]}


def test_validate_group_marks_every_leaf_manual():
    node = validate_node(GROUP, allow_group=True)
    assert [leaf for _, leaf in iter_leaves([node])] == [
        {"clinical": {"oncotree_primary_diagnosis": "Breast", MARKER: "manual"}},
        {"clinical": {"age_numerical": ">=18", MARKER: "manual"}},
        {"genomic": {"hugo_symbol": "ERBB2", MARKER: "manual"}},
    ]


def test_validate_group_from_text():
    text = '{"or": [{"clinical": {"age_numerical": ">=18"}}]}'
    assert validate_node(text, allow_group=True) == {
        "or": [{"clinical": {"age_numerical": ">=18", MARKER: "manual"}}]}


def test_validate_node_rejects_group_unless_allowed():
    with pytest.raises(ValueError, match="leaf"):
        validate_node(GROUP)


@pytest.mark.parametrize("node, msg", [
    ({"and": []}, "at least one"),
    ({"AND": [{"clinical": {"age_numerical": ">=18"}}]}, "'and' or 'or'"),
    ({"and": {"clinical": {"age_numerical": ">=18"}}}, "list"),
    ({"and": [{"clinical": {}}]}, "non-empty"),
    ({"or": [{"and": []}]}, "at least one"),
    ({"and": [{"clinical": {"age_numerical": ">=18"}}], "or": []}, "exactly one key"),
])
def test_validate_group_rejects(node, msg):
    with pytest.raises(ValueError, match=msg):
        validate_node(node, allow_group=True)


def test_add_group_under_wrapper():
    new, resolved = apply_actions(_tl(), [_act("added", "match[0]", GROUP)])
    added = _match(new)[0]["and"][3]
    assert added == validate_node(GROUP, allow_group=True)
    assert resolved[0]["node_after"] == added and resolved[0]["node_before"] is None


def test_edit_rejects_group():
    with pytest.raises(ValueError, match="leaf"):
        apply_actions(_tl(), [_act("edited", "match[0].and[0]", GROUP)])
