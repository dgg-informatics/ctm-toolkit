"""Tests for confidence_split.py — [BETA] confidence tiering that runs after
ctm-mm trials-curate. Split logic and thresholds are still being tuned; see
module docstring."""
from types import SimpleNamespace


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


def _trial(match, biomarker_types=None, nct_id="NCT00000001", full_title="", summary_obj=""):
    return {
        "nct_id": nct_id,
        "protocol_no": None,
        "treatment_list": {"step": [{"match": match}]},
        "_llm_curation": {
            "biomarker_references": [{"type": t} for t in (biomarker_types or [])],
        },
        "_raw": {"full_title": full_title, "summary_obj": summary_obj},
    }


def test_has_oncotree_diagnosis_top_level_clinical():
    from ctm.transformers.confidence_split import has_oncotree_diagnosis
    match = [{"clinical": {"oncotree_primary_diagnosis": "Melanoma"}}]
    assert has_oncotree_diagnosis(match) is True


def test_has_oncotree_diagnosis_nested_in_or():
    from ctm.transformers.confidence_split import has_oncotree_diagnosis
    match = [{"or": [{"clinical": {"oncotree_primary_diagnosis": "Melanoma"}}]}]
    assert has_oncotree_diagnosis(match) is True


def test_has_oncotree_diagnosis_nested_in_and():
    from ctm.transformers.confidence_split import has_oncotree_diagnosis
    match = [{"and": [{"clinical": {"age_numerical": ">=18"}},
                       {"clinical": {"oncotree_primary_diagnosis": "Melanoma"}}]}]
    assert has_oncotree_diagnosis(match) is True


def test_has_oncotree_diagnosis_absent():
    from ctm.transformers.confidence_split import has_oncotree_diagnosis
    match = [{"clinical": {"age_numerical": ">=18"}}]
    assert has_oncotree_diagnosis(match) is False


def test_is_high_confidence_diagnosis_and_no_biomarkers():
    from ctm.transformers.confidence_split import is_high_confidence
    trial = _trial([{"clinical": {"oncotree_primary_diagnosis": "Melanoma"}}])
    assert is_high_confidence(trial, allowed_biomarker_types=set()) is True


def test_is_high_confidence_diagnosis_and_only_allowed_types():
    from ctm.transformers.confidence_split import is_high_confidence
    trial = _trial([{"clinical": {"oncotree_primary_diagnosis": "Melanoma"}}],
                    biomarker_types=["ihc", "other"])
    assert is_high_confidence(trial, allowed_biomarker_types={"ihc", "other"}) is True


def test_is_high_confidence_false_when_disallowed_type_present():
    from ctm.transformers.confidence_split import is_high_confidence
    trial = _trial([{"clinical": {"oncotree_primary_diagnosis": "Melanoma"}}],
                    biomarker_types=["ihc", "snv"])
    assert is_high_confidence(trial, allowed_biomarker_types={"ihc", "other"}) is False


def test_is_high_confidence_false_when_no_diagnosis():
    from ctm.transformers.confidence_split import is_high_confidence
    trial = _trial([{"clinical": {"age_numerical": ">=18"}}])
    assert is_high_confidence(trial, allowed_biomarker_types=set()) is False


def test_extract_diagnoses_liberal_multi_diagnosis():
    from ctm.transformers.confidence_split import extract_diagnoses
    trial = _trial([], full_title="glioma and medulloblastoma trial")
    client = _FakeClient(['["Diffuse Glioma", "Medulloblastoma"]'])
    cache = {}
    result = extract_diagnoses(trial, client, cache, valid_oncotree={"Diffuse Glioma", "Medulloblastoma"})
    assert result == ["Diffuse Glioma", "Medulloblastoma"]
    assert client.call_count == 1


def test_extract_diagnoses_keeps_solid_sentinel():
    from ctm.transformers.confidence_split import extract_diagnoses
    trial = _trial([], full_title="advanced solid tumors")
    client = _FakeClient(['["_SOLID_"]'])
    result = extract_diagnoses(trial, client, cache={}, valid_oncotree=set())
    assert result == ["_SOLID_"]


def test_extract_diagnoses_cache_hit_skips_client():
    from ctm.transformers.confidence_split import extract_diagnoses
    trial = _trial([], full_title="melanoma trial", nct_id="NCT1")
    text = "melanoma trial"
    from ctm.transformers.confidence_split import _cache_key
    key = _cache_key("NCT1", text)
    cache = {key: ["Melanoma"]}
    client = _FakeClient([])  # a call would raise IndexError
    result = extract_diagnoses(trial, client, cache, valid_oncotree={"Melanoma"})
    assert result == ["Melanoma"]
    assert client.call_count == 0


def test_extract_diagnoses_empty_text_returns_empty_no_call():
    from ctm.transformers.confidence_split import extract_diagnoses
    trial = _trial([], full_title="", summary_obj="")
    client = _FakeClient([])
    result = extract_diagnoses(trial, client, cache={}, valid_oncotree=set())
    assert result == []
    assert client.call_count == 0


def test_split_by_confidence_without_recovery():
    from ctm.transformers.confidence_split import split_by_confidence
    high = _trial([{"clinical": {"oncotree_primary_diagnosis": "Melanoma"}}])
    low = _trial([{"clinical": {"age_numerical": ">=18"}}], nct_id="NCT2")
    high_confidence, needs_curation = split_by_confidence([high, low], allowed_biomarker_types=set())
    assert high_confidence == [high]
    assert needs_curation == [low]


def test_split_by_confidence_recovers_diagnosis_and_injects_match_node():
    from ctm.transformers.confidence_split import split_by_confidence
    trial = _trial([{"clinical": {"age_numerical": ">=18"}}],
                    nct_id="NCT3", full_title="melanoma trial")
    client = _FakeClient(['["Melanoma"]'])
    cache = {}
    high_confidence, needs_curation = split_by_confidence(
        [trial], allowed_biomarker_types=set(),
        recover_diagnosis=True, client=client, cache=cache, valid_oncotree={"Melanoma"},
    )
    assert high_confidence == [trial]
    assert needs_curation == []
    injected = trial["treatment_list"]["step"][0]["match"][-1]
    assert injected == {"clinical": {"oncotree_primary_diagnosis": "Melanoma"}}


def test_split_by_confidence_stays_needs_curation_when_recovery_finds_nothing():
    from ctm.transformers.confidence_split import split_by_confidence
    trial = _trial([{"clinical": {"age_numerical": ">=18"}}],
                    nct_id="NCT4", full_title="a study of medication adherence coaching")
    client = _FakeClient(["[]"])
    cache = {}
    high_confidence, needs_curation = split_by_confidence(
        [trial], allowed_biomarker_types=set(),
        recover_diagnosis=True, client=client, cache=cache, valid_oncotree=set(),
    )
    assert high_confidence == []
    assert needs_curation == [trial]
