"""Schema validation tests — no MongoDB required."""
import pytest
from ctm.schemas.processed.clinical_trial import ClinicalTrial
from ctm.schemas.processed.patient_general import PatientGeneral
from ctm.schemas.processed.patient_genetic import PatientGenetic
from ctm.schemas.processed.similar_patients import SimilarPatients, SimilarPatientMatch


def test_clinical_trial_empty():
    m = ClinicalTrial()
    assert m.nct_id is None


def test_clinical_trial_full():
    m = ClinicalTrial(nct_id="NCT12345", status="open", phase="II", match_type="gene")
    assert m.nct_id == "NCT12345"
    assert m.phase == "II"


def test_patient_general_empty():
    m = PatientGeneral()
    assert m.mrn is None
    assert m.tmb is None


def test_patient_general_full():
    m = PatientGeneral(mrn="1036", gender="Female", vital_status="alive", tmb=4.1)
    assert m.mrn == "1036"
    assert m.tmb == 4.1


def test_patient_genetic_empty():
    m = PatientGenetic()
    assert m.gene is None


def test_patient_genetic_full():
    m = PatientGenetic(
        sample_id="abc123",
        gene="EGFR",
        protein_change="p.L858G",
        allele_fraction=0.29,
        tier=2,
    )
    assert m.gene == "EGFR"
    assert m.tier == 2


def test_similar_patients():
    m = SimilarPatients(
        patient_id="1036",
        top_n=2,
        matches=[
            SimilarPatientMatch(patient_id="2000", similarity_score=0.91),
            SimilarPatientMatch(patient_id="3001", similarity_score=0.78),
        ],
    )
    assert len(m.matches) == 2
    assert m.matches[0].similarity_score == 0.91


def test_schemas_round_trip():
    """model_dump → model_validate round trip."""
    original = PatientGenetic(gene="KRAS", tier=1, allele_fraction=0.45)
    restored = PatientGenetic.model_validate(original.model_dump())
    assert restored == original
