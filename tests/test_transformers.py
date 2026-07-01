"""Transformer tests — no MongoDB required."""
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures"


def test_amc_xml_to_normalized():
    from ctm.transformers.amc_xml_to_raw import load
    from ctm.transformers.raw_amc_to_ctml import to_ctml_dict
    from ctm.schemas.matchminer.clinical_trial import ClinicalTrialNormalized

    raw_trials = load(FIXTURES / "amc_trials_sample.xml")
    assert len(raw_trials) == 1

    d = to_ctml_dict(raw_trials[0])
    trial = ClinicalTrialNormalized.model_validate({**d, "summary": d.get("_summary", {}), "raw": d.get("_raw", {})})

    assert trial.protocol_no == "2021.045"
    assert trial.nct_id == "NCT03715933"
    assert trial.status == "open to accrual"
    assert trial.entity == "amc"

    # treatment_list stub is present
    assert len(trial.treatment_list.step) == 1
    step = trial.treatment_list.step[0]
    assert step.step_type == "Registration"
    # Adults → age match criterion
    assert step.match == [{"clinical": {"age_numerical": ">=18"}}]

    # eligibility hierarchy preserved
    inclusion = trial.eligibility.inclusion
    assert len(inclusion) == 3
    assert inclusion[1].text == "Measurable disease per RECISTv1.1."
    assert inclusion[1].sub_criteria[0].text == "Modified RECIST for mesothelioma."

    # _raw has full source fields
    assert d["_raw"]["octsu_genes_interest"] == "IDH1, IDH2"
    assert d["_raw"]["secondary_protocol_no"] == "HUM00202966"
    assert d["_raw"]["management_group"] == "CTSU - Oncology"
