"""What the pipeline currently holds, and whether the match is stale.

Two consumers, one body of logic:

* ``ctm-status`` prints it for a human — "did my curation land?", "how old is
  the patient data?", "do I need to re-match?"
* ``ctm-match`` acts on it — the staleness check is the whole trigger condition.

**Watermarks.** A collection's version is the timestamp embedded in its highest
``ObjectId``. Every stage that writes a collection here drops and re-inserts it
(``replace_collection``/``overwrite_collection``), and ``stamp()`` strips ``_id``
before writing, so a full rewrite always mints fresh ids — the maximum is
therefore "when this collection was last written". No field to add, none for a
future stage to forget to set, and it works identically for trial documents
(which carry a day-granularity ``run_date``) and patient documents (which carry
an ISO ``_updated``).

**Level-triggered, not edge-triggered.** Nothing here reacts to an event. It
compares current inputs against what the last match consumed. Two inputs
changing five minutes apart therefore produce one match, not two, and a missed
or crashed run is repaired by the next tick rather than stranding the pipeline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ctm import db as ctm_db
from ctm.match_prep import DEFAULT_CLINICAL_COLLECTION, DEFAULT_GENOMIC_COLLECTION

log = logging.getLogger(__name__)

#: One document per pipeline concern, in the master database — the only database
#: that is neither per-run nor dropped, so state survives what it describes.
STATE_COLLECTION = "pipeline_state"
MATCH_STATE_ID = "match"

PATIENT_DATA_COLLECTION = "latest_patient_data"


@dataclass
class Watermark:
    """When a collection was last written, and how much is in it."""

    database: str
    collection: str
    count: int
    written_at: datetime | None

    @property
    def exists(self) -> bool:
        return self.written_at is not None

    def as_dict(self) -> dict:
        return {
            "database": self.database,
            "collection": self.collection,
            "count": self.count,
            "written_at": self.written_at.isoformat() if self.written_at else None,
        }

    def describe(self) -> str:
        if not self.exists:
            return "missing"
        age = (datetime.now(tz=UTC) - self.written_at).days
        when = self.written_at.date().isoformat()
        return f"{self.count:>5} docs  written {when}" + (f"  ({age}d ago)" if age else "")


def read_watermark(client, database: str, collection: str) -> Watermark:
    """The collection's current version, or an empty watermark if it has none.

    A missing database or collection is not an error — it is the honest state of
    a pipeline that has not run that stage yet, and the caller reports it as
    such rather than crashing.
    """
    coll = client[database][collection]
    newest = next(iter(coll.find({}, {"_id": 1}).sort("_id", -1).limit(1)), None)
    written_at = None
    if newest is not None and hasattr(newest.get("_id"), "generation_time"):
        written_at = newest["_id"].generation_time
    return Watermark(
        database=database,
        collection=collection,
        count=coll.count_documents({}),
        written_at=written_at,
    )


@dataclass
class PipelineStatus:
    """Every watermark that decides whether a fresh match is needed."""

    trials: Watermark
    patients_clinical: Watermark
    patients_genomic: Watermark
    last_match: dict | None = None
    _now: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    @property
    def inputs(self) -> dict[str, str | None]:
        """What a match would consume right now, as comparable strings."""
        return {
            "trials": self.trials.written_at.isoformat() if self.trials.exists else None,
            "patients": (self.patients_genomic.written_at.isoformat()
                         if self.patients_genomic.exists else None),
        }

    @property
    def ready(self) -> bool:
        """Both inputs present. Matching with neither trials nor patients is not
        a stale state, it is an unbuilt one."""
        return self.trials.exists and self.patients_clinical.exists

    @property
    def stale(self) -> bool:
        """True when a match would produce something different from last time."""
        if not self.ready:
            return False
        if not self.last_match:
            return True
        return self.inputs != self.last_match.get("inputs")

    def reasons(self) -> list[str]:
        """Why :attr:`stale` is what it is, in words, for the human reading
        ``ctm-status`` and for the run log."""
        if not self.ready:
            missing = [w.collection for w in (self.trials, self.patients_clinical)
                       if not w.exists]
            # No "not ready:" prefix — the caller supplies the verdict, and
            # duplicating it reads as "NOT READY: not ready: ...".
            return [f"{', '.join(missing)} is empty or missing"]
        if not self.last_match:
            return ["no match has been run yet"]
        seen = self.last_match.get("inputs") or {}
        out = [f"{name} changed since the last match"
               for name, value in self.inputs.items() if seen.get(name) != value]
        return out or ["up to date"]


def read_status(client, config: dict, patient_db: str | None = None) -> PipelineStatus:
    """Gather every watermark plus the last recorded match."""
    master_db = config["master_dbname"]
    filtered = config["filtered_collection"]
    patient_db = patient_db or config["patient_dbname"]

    # trials-filter is the authoritative input to matching; trials-merge's master
    # is the fallback for a deployment that has not run it yet, mirroring what
    # match-prep itself does.
    trials = read_watermark(client, master_db, filtered)
    if not trials.exists:
        trials = read_watermark(client, master_db, config["master_collection"])

    return PipelineStatus(
        trials=trials,
        patients_clinical=read_watermark(client, patient_db, DEFAULT_CLINICAL_COLLECTION),
        patients_genomic=read_watermark(client, patient_db, DEFAULT_GENOMIC_COLLECTION),
        last_match=read_match_state(client, master_db),
    )


def read_match_state(client, master_db: str) -> dict | None:
    return client[master_db][STATE_COLLECTION].find_one({"_id": MATCH_STATE_ID})


def write_match_state(client, master_db: str, status: PipelineStatus,
                      match_db: str, reports: int, export_path: str | None) -> None:
    """Record what this match consumed — written only after the whole run
    succeeded, so a half-finished run is retried rather than marked done."""
    client[master_db][STATE_COLLECTION].replace_one(
        {"_id": MATCH_STATE_ID},
        {
            "_id": MATCH_STATE_ID,
            "inputs": status.inputs,
            "match_db": match_db,
            "reports": reports,
            "export_path": export_path,
            "ran_at": datetime.now(tz=UTC).isoformat(),
            "toolkit": ctm_db.toolkit_version(),
        },
        upsert=True,
    )
