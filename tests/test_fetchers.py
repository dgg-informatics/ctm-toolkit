"""Similar patients fetcher tests — requires MongoDB."""
import pytest
from ctm.db.store import insert_processed_document
from ctm.fetchers.fetch_similar_patients import fetch
from ctm.schemas.processed.models import SimilarPatients

RUN_ID = "17Jun2026-fetchtest"


def test_fetch_returns_valid_schema(db):
    # Seed some patients
    for mrn in ["2000", "3001", "4002"]:
        insert_processed_document(RUN_ID, "patient_general", "0.1.0", {"mrn": mrn}, db=db)

    result = fetch("1036", top_n=3, db=db)
    model = SimilarPatients.model_validate(result)
    assert model.patient_id == "1036"
    assert model.top_n == 3
    assert len(model.matches) <= 3


def test_fetch_excludes_self(db):
    insert_processed_document(RUN_ID, "patient_general", "0.1.0", {"mrn": "1036"}, db=db)
    insert_processed_document(RUN_ID, "patient_general", "0.1.0", {"mrn": "9999"}, db=db)

    result = fetch("1036", top_n=5, db=db)
    patient_ids = [m["patient_id"] for m in result["matches"]]
    assert "1036" not in patient_ids


def test_fetch_empty_db(db):
    result = fetch("1036", top_n=5, db=db)
    assert result["patient_id"] == "1036"
    assert result["matches"] == []
