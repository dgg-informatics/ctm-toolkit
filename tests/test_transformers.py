"""Transformer tests — no MongoDB required."""
import pytest
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
