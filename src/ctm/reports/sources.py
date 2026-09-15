"""Mongo-backed inputs for ``ctm-report``.

``ctm-report`` reads three JSON files when they are given. When they are not, it
reads the same documents straight out of the databases the pipeline already
builds: the ``<date>_match`` database assembled by ``ctm-mm match-prep``, and the
patient database written by ``ctm-mm load``.

Two databases, not one, because ``match-prep`` copies only ``clinical`` and
``genomic`` into the match db. The report's patient header and detail block come
from ``patient_data``, which stays behind in ``MONGO_PATIENT_DBNAME``.

The functions take Database-like objects and index them by collection name, so
callers can inject plain mappings and the suite stays offline.
"""

CLINICAL_COLLECTION = "clinical"
GENOMIC_COLLECTION = "genomic"
TRIAL_COLLECTION = "trial"
TRIAL_MATCH_COLLECTION = "trial_match"
PATIENT_DATA_COLLECTION = "latest_patient_data"


def sample_ids(match_db) -> list[str]:
    """Every SAMPLE_ID in the match db's ``clinical`` collection, sorted.

    Read from ``clinical`` rather than ``trial_match`` on purpose: a patient with
    no matches still gets a report, and would be invisible in ``trial_match``.
    """
    return sorted({doc["SAMPLE_ID"] for doc in match_db[CLINICAL_COLLECTION].find({})
                   if doc.get("SAMPLE_ID")})


def load_trials(match_db) -> list[dict]:
    """Every trial in the match db. Loaded once and shared across patients —
    the collection is the same for all of them."""
    return list(match_db[TRIAL_COLLECTION].find({}))


def load_patient(match_db, patient_db, sample_id: str) -> dict:
    """``{matches, genomic_docs, patient_entry}`` for one patient.

    ``patient_entry`` is the ``{patient, reports}`` shape
    ``load_context_from_normalized_json`` reads out of a pts file, so a report
    built from Mongo and one built from exported JSON agree. A patient present in
    ``clinical`` but absent from ``patient_data`` yields empty values rather than
    failing — the trial matches are still worth rendering.
    """
    # No "collection in db" guard: pymongo's Database is deliberately not
    # iterable, so membership raises TypeError. A missing collection already
    # yields an empty cursor, which is exactly the behaviour wanted here.
    entry = next(iter(patient_db[PATIENT_DATA_COLLECTION].find({"SAMPLE_ID": sample_id})), None)
    return {
        "matches": list(match_db[TRIAL_MATCH_COLLECTION].find({"sample_id": sample_id})),
        "genomic_docs": list(match_db[GENOMIC_COLLECTION].find({"SAMPLE_ID": sample_id})),
        "patient_entry": {
            "patient": (entry or {}).get("patient") or {},
            "reports": (entry or {}).get("reports") or [],
        },
    }


def report_filename(run_date: str, sample_id: str) -> str:
    """``<YYYY-MM-DD>_<sample_id>-report.pdf``."""
    return f"{run_date}_{sample_id}-report.pdf"
