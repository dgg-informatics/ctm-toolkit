"""End-to-end: clinical + genomic compose into one view, in either order, and survive reruns."""
import copy
import json
from types import SimpleNamespace

from ctm.transformers.clinical_ctml import draft_clinical
from ctm.transformers.genomic_ctml import draft_genomic

HER2 = "HER2-positive breast cancer"
AGE = "Age >= 18"
BREAST = {"clinical": {"oncotree_primary_diagnosis": "Breast"}}
ADULT = {"clinical": {"age_numerical": ">=18"}}
ERBB2 = {"genomic": {"hugo_symbol": "ERBB2", "variant_category": "Copy Number Variation",
                     "cnv_call": "High Amplification", "wildtype": False}}
REVIEWED = {"breast": {"oncotree": "Breast", "type": "solid"}}


class _ByText:
    """Answers by which piece of text the prompt carries."""

    def __init__(self, answers: dict[str, str]):
        self.answers = answers
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        prompt = kwargs["messages"][1]["content"]
        content = next(v for k, v in self.answers.items() if k in prompt)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _clinical_client():
    return _ByText({HER2: json.dumps(BREAST), AGE: json.dumps(ADULT)})


def _genomic_client():
    ref = {"biomarker": "HER2", "type": "ihc", "reference": "HER2-positive", "origin": "unknown"}
    return _ByText({HER2: json.dumps({"node": ERBB2, "refs": [ref]}),
                    AGE: json.dumps({"node": None, "refs": []}),
                    "Breast Cancer": json.dumps({"node": None, "refs": []})})


def _trial():
    return {"nct_id": "NCT7",
            "eligibility": {"inclusion": [{"text": HER2, "sub_criteria": []},
                                          {"text": AGE, "sub_criteria": []}], "exclusion": []},
            "_raw": {"conditions": ["Breast Cancer"], "_ddots": {"disease_site": "Breast"}}}


def _clinical(trial):
    return draft_clinical(trial, {}, _clinical_client(), {"Breast"}, REVIEWED, {})


def _genomic(trial):
    return draft_genomic(trial, {}, _genomic_client(), {"ERBB2"})


def _view(trial):
    cur = trial["_llm_curation"]
    return (sorted((e["node_type"], e["source"], e["text"], e["mixed"]) for e in cur["_ctml_suggestions"]),
            sorted((e["node_type"], e["source"], e["text"], e["mixed"]) for e in cur["_null_ctml_suggestions"]))


def test_clinical_then_genomic_one_view():
    trial = _genomic(_clinical(_trial()))
    suggestions, nulls = _view(trial)
    assert suggestions == sorted([
        ("clinical", "eligibility.inclusion", HER2, True),
        ("genomic", "eligibility.inclusion", HER2, True),
        ("clinical", "eligibility.inclusion", AGE, False),
        ("clinical", "_raw.conditions", "Breast Cancer", False),
        ("clinical", "_raw._ddots.disease_site", "Breast", False),
    ])
    assert nulls == sorted([
        ("genomic", "eligibility.inclusion", AGE, False),
        ("genomic", "_raw.conditions", "Breast Cancer", False),
    ])
    assert set(trial["_llm_curation"]["parsed_sections"]) == {"clinical", "genomic"}
    assert [r["biomarker"] for r in trial["_llm_curation"]["molecular_genomic_refs"]] == ["HER2"]


def test_rerun_clinical_after_genomic_keeps_genomic_and_mixed():
    once = _genomic(_clinical(_trial()))
    again = _clinical(copy.deepcopy(once))
    cur_once, cur_again = once["_llm_curation"], again["_llm_curation"]

    def genomic(cur):
        return [e for e in cur["_ctml_suggestions"] + cur["_null_ctml_suggestions"]
                if e["node_type"] == "genomic"]

    assert genomic(cur_again) == genomic(cur_once)
    assert cur_again["molecular_genomic_refs"] == cur_once["molecular_genomic_refs"]
    assert cur_again["parsed_sections"]["genomic"] == cur_once["parsed_sections"]["genomic"]
    assert _view(again) == _view(once)


def test_genomic_then_clinical_order_gives_same_result():
    assert _view(_clinical(_genomic(_trial()))) == _view(_genomic(_clinical(_trial())))
