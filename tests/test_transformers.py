"""Transformer tests — no MongoDB required."""
from pathlib import Path
from ctm.transformers.process_ct_data import process as process_ct, __version__ as ct_version
from ctm.transformers.process_patient_general import process as process_general, __version__ as general_version
from ctm.transformers.process_patient_genetic import process as process_genetic, __version__ as genetic_version
from ctm.schemas.processed.models import ClinicalTrial, PatientGeneral, PatientGenetic

RAW_CT = {
    "nct_id": "NCT02477839",
    "protocol_no": "NCT02477839",
    "trial_summary_status": "open",
    "match_level": "step",
    "reason_type": "genomic",
    "cancer_type_match": "specific",
    "match_type": "gene",
}

RAW_PATIENT_GENERAL = {
    "mrn": "1036",
    "gender": "Female",
    "vital_status": "alive",
    "oncotree_primary_diagnosis_name": "Non-Small Cell Lung Cancer",
    "tumor_mutational_burden_per_megabase": 4.1,
}

RAW_PATIENT_GENETIC = {
    "sample_id": "5d2799d8",
    "mrn": "1036",
    "true_hugo_symbol": "EGFR",
    "true_protein_change": "p.L858G",
    "true_cdna_change": "c.2572T>G",
    "true_variant_classification": "Missense_Mutation",
    "variant_category": "MUTATION",
    "allele_fraction": 0.29,
    "tier": 2,
    "chromosome": "7",
    "position": 55259515,
    "reference_allele": "T",
    "wildtype": False,
}


def test_ct_transformer_version():
    assert ct_version == "0.1.0"


def test_ct_transformer_returns_valid_schema():
    result = process_ct(RAW_CT)
    model = ClinicalTrial.model_validate(result)
    assert model.nct_id == "NCT02477839"
    assert model.status == "open"
    assert model.match_type == "gene"


def test_ct_transformer_empty_input():
    result = process_ct({})
    model = ClinicalTrial.model_validate(result)
    assert model.nct_id is None


def test_patient_general_version():
    assert general_version == "0.1.0"


def test_patient_general_transformer_returns_valid_schema():
    result = process_general(RAW_PATIENT_GENERAL)
    model = PatientGeneral.model_validate(result)
    assert model.mrn == "1036"
    assert model.gender == "Female"
    assert model.tmb == 4.1
    assert model.diagnosis == "Non-Small Cell Lung Cancer"


def test_patient_general_transformer_empty_input():
    result = process_general({})
    model = PatientGeneral.model_validate(result)
    assert model.mrn is None


def test_patient_genetic_version():
    assert genetic_version == "0.1.0"


def test_patient_genetic_transformer_returns_valid_schema():
    result = process_genetic(RAW_PATIENT_GENETIC)
    model = PatientGenetic.model_validate(result)
    assert model.gene == "EGFR"
    assert model.allele_fraction == 0.29
    assert model.tier == 2
    assert model.wildtype is False


def test_patient_genetic_transformer_empty_input():
    result = process_genetic({})
    model = PatientGenetic.model_validate(result)
    assert model.gene is None


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
