"""Similar patients fetcher.

Queries MongoDB processed_documents for existing patient records,
computes similarity against a given patient, and returns the top N matches.

Similarity algorithm: placeholder — returns stub scores until a real
similarity metric (e.g. cosine on genomic vectors) is implemented.
"""
from ctm.db.store import get_db
from ctm.schemas.processed.similar_patients import SimilarPatients, SimilarPatientMatch

__version__ = "0.1.0"


def fetch(patient_id: str, top_n: int = 5, *, db=None) -> dict:
    db = db if db is not None else get_db()
    # Query all patient_general docs across all runs
    candidates = list(db["processed_documents"].find(
        {"source_type": "patient_general"}, {"_id": 0}
    ))

    # Placeholder: assign stub scores; replace with real similarity metric.
    matches = []
    for i, candidate in enumerate(candidates):
        cid = candidate.get("data", {}).get("mrn", f"unknown-{i}")
        if cid == patient_id:
            continue
        matches.append(
            SimilarPatientMatch(patient_id=cid, similarity_score=round(1.0 - i * 0.1, 2))
        )

    matches = sorted(matches, key=lambda m: m.similarity_score, reverse=True)[:top_n]
    result = SimilarPatients(patient_id=patient_id, top_n=top_n, matches=matches)
    return result.model_dump()
