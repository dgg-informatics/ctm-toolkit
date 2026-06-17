import os
from datetime import datetime, timezone

from pymongo import MongoClient


def get_db():
    uri = os.getenv("CTM_MONGO_URI", "mongodb://localhost:27017")
    db_name = os.getenv("CTM_MONGO_DB", "ctm")
    return MongoClient(uri)[db_name]


def _db(db):
    return db if db is not None else get_db()


def insert_run(
    run_id: str,
    input_path: str,
    transformer_versions: dict,
    output_path: str,
    *,
    db=None,
) -> dict:
    doc = {
        "run_id": run_id,
        "run_at": datetime.now(tz=timezone.utc),
        "input_path": input_path,
        "transformer_versions": transformer_versions,
        "output_path": output_path,
        "status": "completed",
    }
    _db(db)["runs"].insert_one(doc)
    return doc


def insert_raw_document(
    run_id: str,
    source_type: str,
    source_file: str,
    raw: dict,
    *,
    db=None,
) -> dict:
    doc = {
        "run_id": run_id,
        "source_type": source_type,
        "source_file": source_file,
        "ingested_at": datetime.now(tz=timezone.utc),
        "raw": raw,
    }
    _db(db)["raw_documents"].insert_one(doc)
    return doc


def insert_processed_document(
    run_id: str,
    source_type: str,
    transformer_version: str,
    data: dict,
    *,
    db=None,
) -> dict:
    doc = {
        "run_id": run_id,
        "source_type": source_type,
        "processed_at": datetime.now(tz=timezone.utc),
        "transformer_version": transformer_version,
        "data": data,
    }
    _db(db)["processed_documents"].insert_one(doc)
    return doc


def insert_similar_patients(
    run_id: str,
    patient_id: str,
    top_n: int,
    matches: list[dict],
    *,
    db=None,
) -> dict:
    doc = {
        "run_id": run_id,
        "patient_id": patient_id,
        "computed_at": datetime.now(tz=timezone.utc),
        "top_n": top_n,
        "matches": matches,
    }
    _db(db)["similar_patients"].insert_one(doc)
    return doc


def get_run(run_id: str, *, db=None) -> dict | None:
    return _db(db)["runs"].find_one({"run_id": run_id}, {"_id": 0})


def get_processed_documents(run_id: str, source_type: str | None = None, *, db=None) -> list[dict]:
    query: dict = {"run_id": run_id}
    if source_type:
        query["source_type"] = source_type
    return list(_db(db)["processed_documents"].find(query, {"_id": 0}))
