"""MongoDB store tests — requires a running local MongoDB instance."""
import pytest
from ctm.db.store import (
    insert_run,
    insert_raw_document,
    insert_processed_document,
    insert_similar_patients,
    get_run,
    get_processed_documents,
)

RUN_ID = "17Jun2026-testabcd"


def test_insert_and_get_run(db):
    insert_run(RUN_ID, "/data/normalized", {"ct": "0.1.0"}, "/data/output/x", db=db)
    doc = get_run(RUN_ID, db=db)
    assert doc is not None
    assert doc["run_id"] == RUN_ID
    assert doc["status"] == "completed"
    assert "_id" not in doc


def test_insert_raw_document(db):
    raw = {"nct_id": "NCT12345"}
    result = insert_raw_document(RUN_ID, "clinical_trials", "trial.json", raw, db=db)
    assert result["source_type"] == "clinical_trials"
    assert result["raw"]["nct_id"] == "NCT12345"


def test_insert_processed_document(db):
    data = {"nct_id": "NCT12345", "status": "open"}
    result = insert_processed_document(RUN_ID, "clinical_trials", "0.1.0", data, db=db)
    assert result["transformer_version"] == "0.1.0"
    assert result["data"]["status"] == "open"


def test_get_processed_documents(db):
    insert_processed_document(RUN_ID, "patient_general", "0.1.0", {"mrn": "1036"}, db=db)
    insert_processed_document(RUN_ID, "patient_genetic", "0.1.0", {"gene": "EGFR"}, db=db)

    all_docs = get_processed_documents(RUN_ID, db=db)
    assert len(all_docs) == 2

    general_docs = get_processed_documents(RUN_ID, source_type="patient_general", db=db)
    assert len(general_docs) == 1
    assert general_docs[0]["data"]["mrn"] == "1036"


def test_insert_similar_patients(db):
    matches = [{"patient_id": "2000", "similarity_score": 0.91}]
    result = insert_similar_patients(RUN_ID, "1036", 5, matches, db=db)
    assert result["patient_id"] == "1036"
    assert result["matches"][0]["patient_id"] == "2000"


def test_get_run_missing(db):
    assert get_run("nonexistent-run-id", db=db) is None
