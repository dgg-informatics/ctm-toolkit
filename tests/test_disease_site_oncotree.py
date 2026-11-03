"""disease_site → oncotree_primary_diagnosis suggestion (ctm-llm general)."""
import json
from types import SimpleNamespace

import pytest

from ctm.transformers import disease_site_to_oncotree as dsm
from ctm.transformers.clinical_ctml import draft_clinical

VALID = {"Colon", "Melanoma", "Lymphoid", "Breast"}


class _Client:
    """Answers each disease-site prompt from a {site: json-string} map."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.asked = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        site = kwargs["messages"][1]["content"].removeprefix("Disease site: ")
        self.asked.append(site)
        content = self.answers.get(site, '{"oncotree": null, "type": null}')
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _amc(site, **extra):
    return {"nct_id": "NCT1", "eligibility": {}, "_summary": {}, "_raw": {"disease_site": site}, **extra}


def _node(name):
    return {"clinical": {"oncotree_primary_diagnosis": name}}


def test_reviewed_table_wins_and_lookup_ignores_case_and_spacing():
    client = _Client()
    reviewed = {"colon": {"oncotree": "Colon", "type": "solid"}}
    s = dsm.site_suggestion(_amc("  COLON "), reviewed, {}, client, VALID)
    assert client.asked == []
    assert s["suggested_node"] == _node("Colon")
    assert s["mapping_source"] == "reviewed_ref"
    assert s["source"] == "_raw.disease_site"


def test_unseen_site_is_drafted_once_and_cached():
    client = _Client({"Melanoma, Skin": '{"oncotree": "Melanoma", "type": "solid"}'})
    drafts = {}
    first = dsm.site_suggestion(_amc("Melanoma, Skin"), {}, drafts, client, VALID)
    second = dsm.site_suggestion(_amc("melanoma, skin"), {}, drafts, client, VALID)
    assert client.asked == ["Melanoma, Skin"]
    assert drafts["melanoma, skin"]["oncotree"] == "Melanoma"
    assert first["suggested_node"] == second["suggested_node"] == _node("Melanoma")
    assert first["mapping_source"] == "ai_draft"


def test_invalid_oncotree_name_from_model_is_dropped():
    client = _Client({"Colon": '{"oncotree": "Totally Made Up Cancer", "type": "solid"}'})
    s = dsm.site_suggestion(_amc("Colon"), {}, {}, client, VALID)
    assert s["suggested_node"] is None
    assert s["site_mappings"][0]["oncotree"] is None


@pytest.mark.parametrize("special", ["_SOLID_", "_LIQUID_"])
def test_special_values_survive_validation(special):
    client = _Client({"Other Hematopoietic": json.dumps({"oncotree": special, "type": None})})
    s = dsm.site_suggestion(_amc("Other Hematopoietic"), {}, {}, client, VALID)
    assert s["suggested_node"] == _node(special)


def test_several_sites_become_an_or_and_null_rows_are_skipped():
    reviewed = {
        "breast": {"oncotree": "Breast", "type": "solid"},
        "colon": {"oncotree": "Colon", "type": "solid"},
        "unknown sites": {"oncotree": None, "type": None},
    }
    s = dsm.site_suggestion(_amc("Breast; Unknown Sites; Colon"), reviewed, {}, _Client(), VALID)
    assert s["suggested_node"] == {"or": [_node("Breast"), _node("Colon")]}
    assert s["text"] == "Breast; Unknown Sites; Colon"


def test_many_sites_collapse_to_solid_and_liquid():
    sites = [f"Site {i}" for i in range(dsm.MANY_SITES - 1)] + ["Lymphoid Leukemia"]
    reviewed = {dsm._norm(s): {"oncotree": "Colon", "type": "solid"} for s in sites[:-1]}
    reviewed["lymphoid leukemia"] = {"oncotree": "Lymphoid", "type": "liquid"}
    s = dsm.site_suggestion(_amc("; ".join(sites)), reviewed, {}, _Client(), VALID)
    assert s["suggested_node"] == {"or": [_node("_SOLID_"), _node("_LIQUID_")]}


def test_many_solid_only_sites_collapse_to_solid():
    sites = [f"Site {i}" for i in range(dsm.MANY_SITES)]
    reviewed = {dsm._norm(s): {"oncotree": "Colon", "type": "solid"} for s in sites}
    s = dsm.site_suggestion(_amc("; ".join(sites)), reviewed, {}, _Client(), VALID)
    assert s["suggested_node"] == _node("_SOLID_")


def test_amc_and_ddots_sites_are_read_per_field():
    trial = {"_raw": {"disease_site": "Colon", "_ddots": {"disease_site": "colon; Breast"}}}
    assert dsm.trial_sites(trial, "_raw.disease_site") == ["Colon"]
    assert dsm.trial_sites(trial, "_raw._ddots.disease_site") == ["colon", "Breast"]
    reviewed = {"colon": {"oncotree": "Colon", "type": "solid"}, "breast": {"oncotree": "Breast", "type": "solid"}}
    ddots = dsm.site_suggestion(trial, reviewed, {}, _Client(), VALID, field="_raw._ddots.disease_site")
    assert ddots["source"] == "_raw._ddots.disease_site" and ddots["text"] == "colon; Breast"


def test_no_disease_site_means_no_suggestion():
    assert dsm.site_suggestion({"_raw": {}}, {}, {}, _Client(), VALID) is None
    assert dsm.site_suggestion({}, {}, {}, _Client(), VALID) is None




def test_drafts_round_trip(tmp_path):
    path = tmp_path / "drafts.json"
    dsm.save_drafts({"colon": {"site": "Colon", "oncotree": "Colon", "type": "solid"}}, path)
    assert dsm.load_drafts(path)["colon"]["oncotree"] == "Colon"


DDOTS_SITES = [
    "GI- Esophageal", "Lung- Small Cell Lung Cancer", "Head and Neck", "GU- Prostate",
    "Cancer Care Delivery", "GI- Colorectal", "Multiple Disease Sites ",
    "Hematology- Chronic Lymphocytic Leukemia (CLL)", "Breast", "Gynecology- Cervical",
    "Gynecology- Endometrial", "GU- Bladder", "Lung- Small Cell Lung Cancer Extensive",
    "Observational study", "GU- Renal", "Hematology- Multiple Myeloma",
    "Lung- Non-Small Cell Lung Cancer", "Cancer Control ", "GI- Metastatic Esophageal/Gastric",
    "Genomic Based Trial", "Lymphoma", "GU",
]


@pytest.mark.parametrize("site", DDOTS_SITES)
def test_reviewed_table_covers_every_known_ddots_site(site):
    """Every current DDOTS value resolves from the repo table — no model call."""
    client = _Client()
    trial = {"_raw": {"_ddots": {"disease_site": site}}}
    s = dsm.site_suggestion(trial, dsm.load_reviewed(), {}, client, VALID, field="_raw._ddots.disease_site")
    assert client.asked == []
    assert s["mapping_source"] == "reviewed_ref"


@pytest.mark.parametrize("placeholder", ["Multiple Disease Sites ", "Genomic Based Trial",
                                         "Observational study", "Cancer Control "])
def test_ddots_placeholders_produce_no_diagnosis(placeholder):
    trial = {"_raw": {"_ddots": {"disease_site": placeholder}}}
    s = dsm.site_suggestion(trial, dsm.load_reviewed(), {}, _Client(), VALID, field="_raw._ddots.disease_site")
    assert s["suggested_node"] is None


def test_a_reviewed_row_can_list_several_names():
    trial = {"_raw": {"_ddots": {"disease_site": "Lymphoma"}}}
    s = dsm.site_suggestion(trial, dsm.load_reviewed(), {}, _Client(), VALID, field="_raw._ddots.disease_site")
    assert s["suggested_node"] == {"or": [_node("Hodgkin Lymphoma"), _node("Non-Hodgkin Lymphoma")]}



# --- conditions record -------------------------------------------------------

def test_conditions_record_mirrors_the_transform_mapping():
    from ctm.transformers.eligibility_to_ctml import conditions_suggestion
    from ctm.transformers.oncotree_mapping import ctgov_conditions_to_oncotree, diagnoses_to_match_node

    conditions = ["Metastatic Breast Cancer", "Melanoma"]
    s = conditions_suggestion({"_raw": {"conditions": conditions}})

    assert s["source"] == "_raw.conditions"
    assert s["text"] == "Metastatic Breast Cancer; Melanoma"
    assert s["suggested_node"] == diagnoses_to_match_node(ctgov_conditions_to_oncotree(conditions))
    assert s["suggested_node"] is not None
    assert s["transferred_to_match"] is None     # None until autofill-ctml decides


def test_conditions_with_no_keyword_hit_are_still_recorded():
    from ctm.transformers.eligibility_to_ctml import conditions_suggestion
    s = conditions_suggestion({"_raw": {"conditions": ["Healthy Volunteers"]}})
    assert s["suggested_node"] is None
    assert s["transferred_to_match"] is None


def test_no_conditions_means_no_record():
    from ctm.transformers.eligibility_to_ctml import conditions_suggestion
    assert conditions_suggestion({"_raw": {"disease_site": "Breast"}}) is None   # AMC
    assert conditions_suggestion({}) is None


def test_clinical_stage_adds_the_site_entry():
    client = _Client({"Colon": '{"oncotree": "Colon", "type": "solid"}'})
    cur = draft_clinical(_amc("Colon"), {}, client, VALID, {}, {})["_llm_curation"]
    site = [e for e in cur["_ctml_suggestions"] if e["source"] == "_raw.disease_site"]
    assert len(site) == 1 and site[0]["node_type"] == "clinical"
    assert site[0]["suggested_node"] == _node("Colon")


def test_clinical_stage_records_conditions_without_a_model_call():
    client = _Client()
    trial = {"eligibility": {}, "_summary": {}, "_raw": {"conditions": ["Breast Cancer"]}}
    cur = draft_clinical(trial, {}, client, VALID, {}, {})["_llm_curation"]
    assert [e["source"] for e in cur["_ctml_suggestions"]] == ["_raw.conditions"]
    assert client.asked == []


# ── West disease site + missing-field notification ───────────────────────────

def test_west_disease_site_is_read_like_ddots():
    trial = {"entity": "west", "_raw": {"_west": {"Disease Site": "Breast; Lung"}}}
    reviewed = {"breast": {"oncotree": "Breast", "type": "solid"}, "lung": {"oncotree": "Lung", "type": "solid"}}
    assert dsm.trial_sites(trial, "_raw._west.Disease Site") == ["Breast", "Lung"]
    s = dsm.site_suggestion(trial, reviewed, {}, _Client(), VALID, field="_raw._west.Disease Site")
    assert s["source"] == "_raw._west.Disease Site"
    assert s["suggested_node"] == {"or": [_node("Breast"), _node("Lung")]}


def test_clinical_stage_writes_the_west_entry():
    trial = {"entity": "west", "_raw": {"_west": {"Disease Site": "Breast"}}}
    reviewed = {"breast": {"oncotree": "Breast", "type": "solid"}}
    cur = draft_clinical(trial, {}, _Client(), VALID, reviewed, {})["_llm_curation"]
    assert [e["source"] for e in cur["_ctml_suggestions"]] == ["_raw._west.Disease Site"]
    assert cur["parsed_sections"]["clinical"]["_raw._west.Disease Site"]["pieces"] == 1


@pytest.mark.parametrize("entity,raw,field", [
    ("west", {"_west": {"Status": "Open"}}, "_raw._west.Disease Site"),
    ("sparrow-api", {"_ddots": {"status": "O"}}, "_raw._ddots.disease_site"),
    ("amc", {"protocol_no": "1"}, "_raw.disease_site"),
])
def test_missing_expected_field_is_notified_not_an_error(caplog, entity, raw, field):
    import logging

    trial = {"entity": entity, "protocol_no": "P1", "_raw": raw}
    with caplog.at_level(logging.WARNING):
        cur = draft_clinical(trial, {}, _Client(), VALID, {}, {})["_llm_curation"]
    hits = [r for r in caplog.records if getattr(r, "event", None) == "llm.field_missing"]
    assert [h.field for h in hits] == [field]
    assert cur["parsed_sections"]["clinical"][field]["found"] is False


def test_other_sources_fields_are_not_notified(caplog):
    import logging

    trial = {"entity": "amc", "_raw": {"disease_site": "Breast"}}
    with caplog.at_level(logging.WARNING):
        draft_clinical(trial, {}, _Client(), VALID, {"breast": {"oncotree": "Breast", "type": "solid"}}, {})
    assert not [r for r in caplog.records if getattr(r, "event", None) == "llm.field_missing"]
