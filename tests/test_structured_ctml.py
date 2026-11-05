"""Field-derived clinical entries replace ctm-mm trials' match pre-fill.

ctm-mm trials used to write age, gender and a diagnosis straight into
treatment_list.step[0].match. It now leaves the match empty; the same values are
recorded by ctm-llm clinical as one entry per source field (no model call), and
only ctm-llm autofill-ctml writes the match clause.
"""
from llm_fakes import QueuedClient

from ctm.schemas.raw.models import RawAMCTrial, RawCTGovTrial
from ctm.transformers import raw_amc_to_ctml, raw_ctgov_to_ctml
from ctm.transformers.clinical_ctml import draft_clinical
from ctm.transformers.structured_ctml import age_sex_suggestions, amc_categories_suggestion

CLINICAL_ROWS = {"eligibility.inclusion", "eligibility.exclusion", "_summary.long_title",
                 "_raw.minimum_age", "_raw.sex", "_raw.age_group", "_raw.conditions",
                 "_raw.categorys", "_raw.disease_site", "_raw._ddots.disease_site",
                 "_raw._west.Disease Site"}


def _ctgov_raw(**fields):
    return {"nct_id": "NCT1", "minimum_age": "18 Years", "sex": "FEMALE",
            "conditions": ["Breast Cancer"], **fields}


def _amc_raw(**fields):
    return {"amc_id": "1", "protocol_no": "2021.070", "age_group": "Adults",
            "categorys": [{"cat1": "Breast", "cat2": "Her2 Positive", "cat3": ""}], **fields}


def _by_source(entries):
    return {e["source"]: e for e in entries}


def test_ctm_mm_trials_no_longer_prefills_the_match_clause():
    ctgov = raw_ctgov_to_ctml.to_ctml_dict(RawCTGovTrial(**_ctgov_raw()))
    amc = raw_amc_to_ctml.to_ctml_dict(RawAMCTrial(**_amc_raw()))
    for trial in (ctgov, amc):
        assert trial["treatment_list"]["step"][0]["match"] == []


def test_ctgov_minimum_age_and_sex_are_separate_entries():
    by = _by_source(age_sex_suggestions({"_raw": _ctgov_raw()}))
    assert set(by) == {"_raw.minimum_age", "_raw.sex"}
    assert by["_raw.minimum_age"]["suggested_node"] == {"clinical": {"age_numerical": ">=18"}}
    assert by["_raw.minimum_age"]["text"] == "18 Years"
    assert by["_raw.sex"]["suggested_node"] == {"clinical": {"gender": "Female"}}
    assert by["_raw.sex"]["text"] == "FEMALE"
    for e in by.values():
        assert e["mapping_source"] == "field_value_map"
        assert e["transferred_to_match"] is None


def test_unmappable_values_still_record_a_null_entry():
    by = _by_source(age_sex_suggestions({"_raw": _ctgov_raw(minimum_age="6 Months", sex="ALL")}))
    assert by["_raw.minimum_age"]["suggested_node"] is None
    assert by["_raw.sex"]["suggested_node"] is None
    assert age_sex_suggestions({"_raw": {"nct_id": "NCT1"}}) == []


def test_amc_age_group():
    (e,) = age_sex_suggestions({"_raw": _amc_raw()})
    assert e["source"] == "_raw.age_group"
    assert e["suggested_node"] == {"clinical": {"age_numerical": ">=18"}}
    assert e["text"] == "Adults"
    (both,) = age_sex_suggestions({"_raw": _amc_raw(age_group="Both")})
    assert both["suggested_node"] is None


def test_amc_categories_diagnosis():
    s = amc_categories_suggestion({"_raw": _amc_raw()})
    assert s["source"] == "_raw.categorys"
    assert s["text"] == "Breast > Her2 Positive"
    assert s["suggested_node"] == {"clinical": {"oncotree_primary_diagnosis": "Breast"}}
    assert s["mapping_source"] == "amc_category_oncotree_map"
    assert s["transferred_to_match"] is None
    assert amc_categories_suggestion({"_raw": _ctgov_raw()}) is None


def test_conditions_record():
    from ctm.transformers.eligibility_to_ctml import conditions_suggestion
    s = conditions_suggestion({"_raw": _ctgov_raw()})
    assert s["source"] == "_raw.conditions"
    assert s["mapping_source"] == "keyword_oncotree_map"
    assert s["transferred_to_match"] is None


def test_clinical_stage_records_the_field_entries_without_model_calls():
    client = QueuedClient([])
    for raw, expected in ((_ctgov_raw(), {"_raw.minimum_age", "_raw.sex", "_raw.conditions"}),
                          (_amc_raw(), {"_raw.age_group", "_raw.categorys"})):
        cur = draft_clinical({"_raw": raw}, {}, client, {"Breast"}, {}, {})["_llm_curation"]
        assert {e["source"] for e in cur["_ctml_suggestions"]} == expected
        assert set(cur["parsed_sections"]["clinical"]) == CLINICAL_ROWS
    assert client.call_count == 0
