"""Structured-field clinical entries replace ctm-mm trials' match pre-fill.

ctm-mm trials used to write age, gender and a diagnosis straight into
treatment_list.step[0].match. It now leaves the match empty; the same values are
recorded by ctm-llm clinical as entries (no model call), and only
ctm-llm autofill-ctml writes the match clause.
"""
from llm_fakes import QueuedClient

from ctm.schemas.raw.models import RawAMCTrial, RawCTGovTrial
from ctm.transformers import raw_amc_to_ctml, raw_ctgov_to_ctml
from ctm.transformers.clinical_ctml import draft_clinical
from ctm.transformers.structured_ctml import amc_categories_suggestion, structured_suggestion


def _ctgov_raw(**fields):
    return {"nct_id": "NCT1", "minimum_age": "18 Years", "sex": "FEMALE",
            "conditions": ["Breast Cancer"], **fields}


def _amc_raw(**fields):
    return {"amc_id": "1", "protocol_no": "2021.070", "age_group": "Adults",
            "categorys": [{"cat1": "Breast", "cat2": "Her2 Positive", "cat3": ""}], **fields}


def test_ctm_mm_trials_no_longer_prefills_the_match_clause():
    ctgov = raw_ctgov_to_ctml.to_ctml_dict(RawCTGovTrial(**_ctgov_raw()))
    amc = raw_amc_to_ctml.to_ctml_dict(RawAMCTrial(**_amc_raw()))
    for trial in (ctgov, amc):
        assert trial["treatment_list"]["step"][0]["match"] == []


def test_structured_ctgov_age_and_gender():
    s = structured_suggestion({"_raw": _ctgov_raw()})
    assert s["source"] == "structured"
    assert s["suggested_node"] == {"clinical": {"age_numerical": ">=18", "gender": "Female"}}
    assert s["text"] == "minimum_age: 18 Years; sex: FEMALE"
    assert s["mapping_source"] == "structured" and s["transferred_to_match"] is False


def test_structured_ctgov_all_sexes_and_month_ages_give_no_node():
    s = structured_suggestion({"_raw": _ctgov_raw(minimum_age="6 Months", sex="ALL")})
    assert s["suggested_node"] is None
    assert structured_suggestion({"_raw": {"nct_id": "NCT1"}}) is None


def test_structured_amc_age_group():
    s = structured_suggestion({"_raw": _amc_raw()})
    assert s["suggested_node"] == {"clinical": {"age_numerical": ">=18"}}
    assert s["text"] == "age_group: Adults"
    assert structured_suggestion({"_raw": _amc_raw(age_group="Both")})["suggested_node"] is None


def test_amc_categories_diagnosis():
    s = amc_categories_suggestion({"_raw": _amc_raw()})
    assert s["source"] == "amc_categories"
    assert s["text"] == "Breast > Her2 Positive"
    assert s["suggested_node"] == {"clinical": {"oncotree_primary_diagnosis": "Breast"}}
    assert s["transferred_to_match"] is False
    assert amc_categories_suggestion({"_raw": _ctgov_raw()}) is None


def test_conditions_record_is_no_longer_transferred():
    from ctm.transformers.eligibility_to_ctml import conditions_suggestion
    assert conditions_suggestion({"_raw": _ctgov_raw()})["transferred_to_match"] is False


def test_clinical_stage_records_the_structured_entries_without_model_calls():
    client = QueuedClient([])
    for raw, expected in ((_ctgov_raw(), {"structured", "conditions"}),
                          (_amc_raw(), {"structured", "amc_categories"})):
        cur = draft_clinical({"_raw": raw}, {}, client, {"Breast"}, {}, {})["_llm_curation"]
        assert {e["source"] for e in cur["_ctml_suggestions"]} == expected
        rows = cur["parsed_sections"]["clinical"]
        assert set(rows) == {"inclusion", "exclusion", "summary", "structured", "conditions",
                             "amc_categories", "disease_site"}
    assert client.call_count == 0
