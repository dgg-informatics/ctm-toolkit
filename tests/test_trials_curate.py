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
        "section": "eligibility",  # default when the model omits it
        "in_kb": True,
    }]
    assert len(cache) == 1


def test_scan_biomarkers_cache_hit_skips_client():
    from ctm.transformers.trials_curate import _cache_key, _trial_scan_text, scan_biomarkers

    trial = _trial_with_eligibility()
    text = _trial_scan_text(trial)
    key = _cache_key("NCT00000001", text)
    cache = {key: [{"biomarker": "BRCA1", "type": "snv", "reference": "BRCA1 mutation"}]}
    client = _FakeClient([])  # no responses queued — a call would raise IndexError

    hits = scan_biomarkers(trial, client, cache, known_genes={"BRCA1"})

    assert client.call_count == 0
    assert hits[0]["biomarker"] == "BRCA1"


def test_scan_text_includes_titles_keywords_and_curator_genes():
    """Biomarker language lives in places the criteria never restate: a title can
    embed the requirement outright, and _raw.octsu_genes_interest is a
    curator-authored gene list nothing else in the pipeline reads."""
    from ctm.transformers.trials_curate import _trial_scan_text

    trial = {
        "nct_id": "NCT00000003",
        "protocol_no": None,
        "eligibility": {"inclusion": [{"text": "Age >= 18", "sub_criteria": []}], "exclusion": []},
        "_summary": {
            "short_title": "Olaparib in BRCA-mutant pancreatic cancer",
            "long_title": "A Study in Patients with a Pathogenic BRCA1, BRCA2 or PALB2 Mutation",
            "disease_keywords": ["Metastatic HER2-Negative Breast Carcinoma"],
        },
        "_raw": {"octsu_genes_interest": "IDH1 (R132); IDH2 (R172)"},
    }

    text = _trial_scan_text(trial)

    assert "PALB2" in text
    assert "BRCA-mutant" in text
    assert "HER2-Negative" in text
    assert "IDH1 (R132); IDH2 (R172)" in text
    assert "Age >= 18" in text
    # Labelled, so the model can attribute each hit to a section.
    for label in ("TRIAL TITLES:", "DISEASE KEYWORDS:",
                  "CURATOR GENES OF INTEREST:", "ELIGIBILITY CRITERIA:"):
        assert label in text


def test_scan_text_omits_absent_sections():
    from ctm.transformers.trials_curate import _trial_scan_text

    trial = _trial_with_eligibility()
    text = _trial_scan_text(trial)

    assert "ELIGIBILITY CRITERIA:" in text
    assert "TRIAL TITLES:" not in text
    assert "DISEASE KEYWORDS:" not in text
    assert "CURATOR GENES OF INTEREST:" not in text


def test_scan_biomarkers_scans_curator_genes_with_no_eligibility_text():
    """octsu_genes_interest alone is enough to warrant a call: previously an empty
    eligibility list short-circuited before the gene list was ever read."""
    from ctm.transformers.trials_curate import scan_biomarkers

    trial = {
        "nct_id": "NCT00000005",
        "protocol_no": None,
        "eligibility": {"inclusion": [], "exclusion": []},
        "_raw": {"octsu_genes_interest": "FLT3"},
    }
    client = _FakeClient([
        '[{"biomarker": "FLT3", "type": "snv", "reference": "FLT3", "section": "genes_of_interest"}]'
    ])

    hits = scan_biomarkers(trial, client, {}, known_genes={"FLT3"})

    assert client.call_count == 1
    assert hits[0]["section"] == "genes_of_interest"


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


def test_scan_biomarkers_content_filter_returns_empty_and_caches():
    """A provider content filter is a hard 400 that clinical eligibility text trips
    routinely; it must not crash the run. The scan returns [], and caches [] so a
    re-run does not re-trigger the same deterministic failure."""
    from ctm.transformers.trials_curate import scan_biomarkers

    class _FilteredClient:
        def __init__(self):
            self.call_count = 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kwargs):
            self.call_count += 1
            raise RuntimeError("400 content_filter: the response was filtered")

    trial = _trial_with_eligibility(text="Metastatic disease with prior grade 4 toxicity")
    client = _FilteredClient()
    cache = {}

    assert scan_biomarkers(trial, client, cache, known_genes=set()) == []
    assert client.call_count == 1
    # Cached as [] → a re-run does not call the client again.
    assert scan_biomarkers(trial, client, cache, known_genes=set()) == []
    assert client.call_count == 1


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


def test_annotate_biomarkers_writes_only_its_own_key():
    """The invariant that makes this stage safe to re-run on curated trials."""
    from ctm.transformers.trials_curate import annotate_biomarkers

    trial = _full_trial()
    client = _FakeClient(['[{"biomarker": "BRCA1", "type": "snv", "reference": "BRCA1"}]'])

    result = annotate_biomarkers(trial, client, cache={}, known_genes={"BRCA1"})

    assert result["_llm_curation"]["biomarker_references"][0]["biomarker"] == "BRCA1"
    # No match-node work: that belongs to `ctm-llm general`.
    assert "final_suggested_ctml" not in result["_llm_curation"]
    # Exactly one call — the title suggestion moved to `general`, only the scan remains.
    assert client.call_count == 1


def test_annotate_biomarkers_preserves_existing_llm_curation():
    """Re-running on an already-drafted trial must not discard `general`'s work.
    The old fused curate_trial rebuilt the whole block, which is exactly how a
    master rescan destroyed _ctml_suggestions."""
    from ctm.transformers.trials_curate import annotate_biomarkers

    trial = _full_trial()
    trial["_llm_curation"] = {
        "_ctml_suggestions": [
            {"source": "inclusion", "text": "Age >= 18",
             "suggested_node": {"clinical": {"age_numerical": ">=18"}}},
            {"source": "summary", "text": "title",
             "suggested_node": {"genomic": {"hugo_symbol": "BRCA1"}}},
        ],
    }
    client = _FakeClient(['[{"biomarker": "BRCA1", "type": "snv", "reference": "BRCA1"}]'])

    result = annotate_biomarkers(trial, client, cache={}, known_genes={"BRCA1"})

    assert len(result["_llm_curation"]["_ctml_suggestions"]) == 2
    assert len(result["_llm_curation"]["biomarker_references"]) == 1


def test_annotate_biomarkers_overwrites_only_stale_biomarkers():
    """A refresh replaces biomarker_references rather than appending to it."""
    from ctm.transformers.trials_curate import annotate_biomarkers

    trial = _full_trial()
    trial["_llm_curation"] = {
        "_ctml_suggestions": [{"source": "inclusion", "suggested_node": None}],
        "biomarker_references": [{"biomarker": "STALE", "type": "snv"}],
    }
    client = _FakeClient(['[{"biomarker": "BRCA1", "type": "snv", "reference": "BRCA1"}]'])

    refs = annotate_biomarkers(trial, client, cache={}, known_genes=set())["_llm_curation"]["biomarker_references"]

    assert [r["biomarker"] for r in refs] == ["BRCA1"]
