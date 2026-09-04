"""MongoDB access for the pipeline's per-stage collections.

The only module that imports pymongo, so the dependency stays behind the `db`
extra and every other module remains importable without it. Callers import this
module from inside their command function, following the same lazy-import
pattern the rest of `mm_cli` uses for optional dependencies.

Two different databases are in play, and the distinction is the whole design:

* The **per-run** database (``MONGO_DBNAME``, e.g. ``2026-08-17_dev``) holds one
  run's stage outputs. A fresh one per run gives structural isolation — a stray
  query cannot mix runs, and dropping a bad run is dropping a database.
* The **master** database (``MONGO_MASTER_DBNAME``) is deliberately *not*
  per-run. The master trial list is a rolling current-state artifact, so it gets
  a fixed address. Reading it out of the per-run database would query a database
  that is empty at the start of every run — routing every trial to `changed` and
  re-running full LLM curation over the entire trial list.
"""
import os
from importlib.metadata import version

# The pipeline's collections, frozen. Ordinal prefixes encode stage position, so
# inserting a stage renumbers everything after it and renames every reader — this
# map exists so that stops happening. They also sort the pipeline's collections
# together in Compass alongside MatchMiner's own `clinical` / `genomic` / `trial` /
# `trial_match` in the same dated database.
#
#   00_raw_trials         ctm-mm trials — verbatim source records
#   01_normalized_trials  ctm-mm trials
#   02_diff_trials        ctm-mm trials-diff
#   03_llm_general_trials  ctm-llm general
#   04_llm_biomarker_trials ctm-llm biomarkers
#   05_manual_curated_trials  human curation — see MACHINE_WRITTEN below
#   06_master_trials      ctm-mm trials-merge
#
# Named by *stage*, not by what the LLM extracts. Content-based names were
# considered and rejected: ctm-ctml's SYSTEM_PROMPT already emits eight fields
# across clinical and genomic, so a name like "dx_age" is wrong on arrival and
# drifts further every time a prompt changes.
# Verbatim source records (AMC XML, West/Sparrow XLSX, DDOTS API, CTGov), stored
# alongside the normalization they produced. Keyed on the same trial_hash as
# 01_normalized_trials, so the two join exactly — which is why that hash must be
# stable across pulls; see compute_trial_hash.
RAW_COLLECTION = "00_raw_trials"
NORMALIZED_COLLECTION = "01_normalized_trials"
DIFF_COLLECTION = "02_diff_trials"
LLM_GENERAL_COLLECTION = "03_llm_general_trials"
LLM_BIOMARKER_COLLECTION = "04_llm_biomarker_trials"
MANUAL_COLLECTION = "05_manual_curated_trials"
DEFAULT_MASTER_COLLECTION = "06_master_trials"

# Collections a pipeline stage owns and may therefore destroy and rewrite.
# 05_manual_curated_trials is deliberately absent: it holds hand-curated work, and
# a stage that dropped it would silently discard days of a curator's effort with
# no warning and no recovery beyond the JSON files. Machine-generated collections
# are regenerable; human-edited ones are not.
MACHINE_WRITTEN = frozenset({
    RAW_COLLECTION,
    NORMALIZED_COLLECTION,
    DIFF_COLLECTION,
    LLM_GENERAL_COLLECTION,
    LLM_BIOMARKER_COLLECTION,
    # trials-merge owns the master: it recomputes the whole snapshot each run and
    # replaces it. 05_manual_curated_trials stays absent — a curator accumulates
    # into it and add-manual appends, so nothing may drop it. A master under a
    # non-default MONGO_MASTER_COLLECTION name is not droppable via this gate.
    DEFAULT_MASTER_COLLECTION,
})

# Document identity is trial_hash — the sha256 of a trial's _raw blob that
# compute_trial_hash() already stamps for audit. Neither trial_key nor
# (entity, trial_key) is unique in real data; measured over the 443-trial
# 03aug26 normalization:
#
#   trial_key            412 unique, 30 collisions  (sparrow/west share NCT ids;
#                                                    neither has a protocol_no)
#   (entity, trial_key)  442 unique,  1 collision   (UMH-West lists the EAY191
#                                                    ComboMATCH umbrella and its
#                                                    EAY191-A3 sub-study as
#                                                    separate rows under one NCT)
#   trial_hash           443 unique,  0 collisions
#
# Those collisions are legitimately distinct source records rather than
# corruption, so all of them are stored. trial_hash separates them because each
# carries its own _raw. Two documents sharing a trial_hash would mean identical
# source data appearing twice, which is a real upstream fault worth failing on.
DIFF_UNIQUE_KEY = "trial_hash"

# Non-unique, for the natural lookups ("what happened to this trial this run").
DIFF_LOOKUP_KEYS = ("entity", "trial_key")

# Provenance this module stamps onto stored documents. Stripped again on read so
# a trial carried forward from an earlier stage never inherits that stage's
# provenance — `unchanged` and `deleted` copy master fields forward, so an
# inherited _id would collide on the next write and leak into the JSON files.
# Note `trial_hash` is NOT here: that is real trial data stamped by
# `ctm-mm trials`, not storage metadata.
METADATA_FIELDS = ("_id", "processed_with", "run_date", "diff_status", "trial_key")


def toolkit_version() -> str:
    """Installed ctm-toolkit version.

    Read from package metadata rather than a module constant so `processed_with`
    cannot drift out of sync with pyproject.toml on a version bump.
    """
    return version("ctm-toolkit")


def mongo_config(require_master: bool = False, require_dbname: bool = True) -> dict:
    """Resolve Mongo settings from the environment, failing fast by name.

    Mirrors ``build_client()``'s failure style: a missing required variable
    raises immediately with the variable named, rather than defaulting to
    something that silently misbehaves later.

    ``require_master`` additionally demands ``MONGO_MASTER_DBNAME``. Callers set
    it only when they actually intend to read the master from Mongo, so a run
    that passes ``--master <file>`` never fails on a variable it does not use.

    ``require_dbname`` is on for the per-run trial stages; ``ctm-mm load`` sets it
    off — it is a patient-only command that touches ``MONGO_PATIENT_DBNAME``, never
    the per-run ``MONGO_DBNAME``.
    """
    dbname = os.environ.get("MONGO_DBNAME")
    if require_dbname and not dbname:
        raise ValueError("MONGO_DBNAME not set in environment")

    # Two ways to point at a server. MONGO_URI wins and is the only form that
    # carries credentials — a bare host/port cannot authenticate. The auth
    # database rides in the URI (append ?authSource=admin if the user is defined
    # outside the target db). MONGO_HOST/MONGO_PORT remain for the local,
    # unauthenticated case (Docker, tests).
    uri = os.environ.get("MONGO_URI")
    host = os.environ.get("MONGO_HOST")
    port = os.environ.get("MONGO_PORT")

    if not uri:
        for name, value in (("MONGO_HOST", host), ("MONGO_PORT", port)):
            if not value:
                raise ValueError(
                    f"set MONGO_URI (for an authenticated server), or {name} "
                    "(for a local unauthenticated one)"
                )
        try:
            port = int(port)
        except ValueError:
            raise ValueError(f"MONGO_PORT must be an integer, got {port!r}") from None

    master_dbname = os.environ.get("MONGO_MASTER_DBNAME")
    if require_master and not master_dbname:
        raise ValueError(
            "MONGO_MASTER_DBNAME not set in environment "
            "(required unless --master names a file)"
        )

    return {
        "uri": uri,
        "host": host,
        "port": port,
        "dbname": dbname,
        "master_dbname": master_dbname,
        "master_collection": (
            os.environ.get("MONGO_MASTER_COLLECTION") or DEFAULT_MASTER_COLLECTION
        ),
        "patient_dbname": os.environ.get("MONGO_PATIENT_DBNAME"),
    }


def get_client(config: dict):
    """The MongoClient for this config. A ``MONGO_URI`` (which carries credentials)
    is passed as the sole argument; otherwise a bare host/port. Callers that touch
    more than one database in a run (e.g. match-prep) share a single client."""
    from pymongo import MongoClient

    return MongoClient(config["uri"]) if config.get("uri") else \
        MongoClient(config["host"], config["port"])


def get_database(config: dict, db_name: str | None = None):
    """Connect and return a Database. ``db_name`` overrides ``config["dbname"]``."""
    return get_client(config)[db_name or config["dbname"]]


def copy_collection(source, dest) -> int:
    """Copy every document (preserving ``_id``) from the ``source`` collection into
    ``dest``, replacing dest's contents. Returns the count.

    Preserving ``_id`` is the whole point: a genomic doc's ``CLINICAL_ID`` points at
    its clinical doc's ``_id`` (set by ``ctm-mm load``), so both must be copied with
    their ids intact for the link to survive into the assembled match database.
    """
    docs = list(source.find({}))
    dest.drop()
    if docs:
        dest.insert_many(docs)
    return len(docs)


def strip_metadata(doc: dict) -> dict:
    """Copy of ``doc`` without storage metadata — see METADATA_FIELDS."""
    return {k: v for k, v in doc.items() if k not in METADATA_FIELDS}


def stamp(doc: dict, stage: str, run_date: str, **extra) -> dict:
    """Storage-ready copy of a trial: metadata re-stamped, never inherited.

    Pure. The returned dict is what goes to Mongo; the JSON files keep the
    unstamped trial, which is what makes the file outputs byte-identical to
    those from before Mongo integration.
    """
    from ctm.trials_lifecycle import compute_trial_hash, trial_key

    stamped = strip_metadata(doc)
    stamped["trial_key"] = trial_key(doc)
    # trial_hash is normally stamped upstream by `ctm-mm trials`, but a master
    # predating that stamping — or a hand-trimmed one — may not carry it, and it
    # is this collection's unique key. Recomputing is a derivation, not an
    # invention: compute_trial_hash() is a pure function of _raw, so it produces
    # the same value the upstream stage would have.
    if not stamped.get("trial_hash"):
        stamped["trial_hash"] = compute_trial_hash(doc)
    stamped["processed_with"] = f"{stage} {toolkit_version()}"
    stamped["run_date"] = run_date
    stamped.update(extra)

    # Validate what crosses the Mongo boundary at the point it is assembled, so a
    # malformed write fails at the stage that caused it rather than downstream.
    # Checks the envelope and _llm_curation only; the trial body is already typed
    # by ClinicalTrialNormalized. See ctm.schemas.storage.
    from ctm.schemas.storage import validate_storage
    validate_storage(stamped)

    return stamped


def inherited_run_date(docs: list[dict], fallback: str | None = None) -> str:
    """The run_date carried by ``docs``, so a stage stays on its run's timeline.

    A stage must not read the clock. The weekly cycle spans days — diff Monday,
    LLM Monday, manual curation midweek, merge Friday — so ``date.today()`` at
    each stage stamps a different value and nothing correlates one run's
    documents across collections. Only the first stage in the chain has any
    business asking what today is.

    Raises when the input carries more than one run_date, which means two runs
    have been mixed and any single answer would be a lie.
    """
    dates = {doc.get("run_date") for doc in docs if doc.get("run_date")}
    if len(dates) > 1:
        raise ValueError(
            f"source documents span multiple run_dates ({', '.join(sorted(dates))}); "
            "pass --run-date to choose one explicitly"
        )
    if dates:
        return dates.pop()
    if fallback:
        return fallback
    raise ValueError(
        "source documents carry no run_date and no fallback was given; "
        "pass --run-date"
    )


def stamp_curation(doc: dict, *, curated_by: str = "manual-curation",
                   curated_by_user: str | None = None, curated_at=None) -> dict:
    """Attach sticky curation provenance to a copy of ``doc``.

    Set once by ``add-manual`` and carried forward unchanged thereafter — unlike
    the run envelope, which every stage re-stamps. ``curated_by_user`` defaults to
    the OS account via ``getpass.getuser()`` (reliable on macOS and RHEL alike);
    in production that is the service account unless a curator overrides it.
    ``curated_at`` is a timezone-aware UTC datetime so "curated in the last N
    months" is a Mongo range query.
    """
    import getpass
    from datetime import UTC, datetime

    result = dict(doc)
    result["curated_by"] = curated_by
    result["curated_by_user"] = curated_by_user or getpass.getuser()
    result["curated_at"] = curated_at or datetime.now(tz=UTC)
    return result


def read_collection(db, name: str, query: dict | None = None,
                    keep_metadata: bool = False) -> list[dict]:
    """Documents in ``name``, metadata stripped. Empty list if the collection is absent.

    ``keep_metadata=True`` retains the stamped fields, which callers need when
    they have to read ``run_date`` off the source documents before stripping it.
    """
    if name not in db.list_collection_names():
        return []
    docs = db[name].find(query or {})
    if keep_metadata:
        return [{k: v for k, v in doc.items() if k != "_id"} for doc in docs]
    return [strip_metadata(doc) for doc in docs]


def prepare_collection(db, name: str, unique_key: str, lookup_keys=()):
    """Empty ``name`` and (re)create its indexes, returning the collection.

    Refuses to touch a collection no pipeline stage owns — see MACHINE_WRITTEN.
    Dropping rather than ``delete_many({})`` matters: delete_many leaves the
    collection's *indexes* in place, so a previous run's index definition would
    outlive a change to ``unique_key`` and keep enforcing the old constraint.
    """
    if name not in MACHINE_WRITTEN:
        raise ValueError(
            f"refusing to clear {name!r}: not a machine-written collection. "
            f"Stages may only drop what they own ({', '.join(sorted(MACHINE_WRITTEN))})"
        )

    collection = db[name]
    collection.drop()
    collection.create_index(unique_key, unique=True)
    if lookup_keys:
        collection.create_index([(key, 1) for key in lookup_keys])
    return collection


def open_collection(db, name: str, unique_key: str, lookup_keys=()):
    """Return ``name`` with its indexes ensured, **without dropping** it.

    For collections that accumulate rather than being rebuilt per run —
    05_manual_curated_trials, where a curator adds trials across the week and a
    drop would discard earlier work. That collection is deliberately absent from
    MACHINE_WRITTEN, so ``prepare_collection`` refuses it; this is the writable
    path that never clears.
    """
    collection = db[name]
    collection.create_index(unique_key, unique=True)
    if lookup_keys:
        collection.create_index([(key, 1) for key in lookup_keys])
    return collection


def overwrite_collection(db, name: str, docs: list[dict]) -> int:
    """Drop ``name`` and insert ``docs`` fresh, returning the count written.

    For the patient snapshots that ``ctm-mm load`` writes — a full replacement of a
    dated or ``latest_*`` collection. No MACHINE_WRITTEN guard: patient data lives
    in its own database and is regenerable from the source workbook, so a drop is
    always safe. Unlike ``replace_collection`` it needs no unique key — patient
    genomic docs have several rows per SAMPLE_ID, so there is none.
    """
    db[name].drop()
    if docs:
        db[name].insert_many(docs)
    return len(docs)


def upsert_doc(collection, doc: dict, unique_key: str) -> None:
    """Write one document, replacing any existing one with the same key.

    Used by the LLM stages instead of one batch insert at the end. Those stages
    make a model call per criterion over hundreds of trials, so a crash partway
    through a batch write loses every completed trial; writing as each trial
    finishes keeps that work. This mirrors what `_cmd_trials_curate` already does
    with its response cache, which it saves after every trial "so progress
    survives interruption".
    """
    if not doc.get(unique_key):
        raise ValueError(f"document has no {unique_key!r}; refusing to store it")
    collection.replace_one({unique_key: doc[unique_key]}, doc, upsert=True)


def replace_collection(
    db, name: str, docs: list[dict], unique_key: str, lookup_keys=()
) -> None:
    """Replace ``name``'s contents with ``docs``, keyed uniquely on ``unique_key``.

    One database holds one run, so a stage's collection holds exactly one run's
    output — a re-run replaces rather than appends.

    Validating before the drop means a batch that cannot be keyed leaves the
    previous run's data intact instead of destroying it and failing.

    Suitable for cheap deterministic stages. The LLM stages use
    prepare_collection() + upsert_doc() instead, so an interrupted run keeps the
    trials it already paid for.
    """
    unkeyed = [doc for doc in docs if not doc.get(unique_key)]
    if unkeyed:
        raise ValueError(
            f"{len(unkeyed)} of {len(docs)} document(s) have no {unique_key!r}; "
            "refusing to store documents with no unique identity"
        )

    collection = prepare_collection(db, name, unique_key, lookup_keys)
    if docs:
        # ordered=False so a rejected document does not abort the batch: every
        # valid document still lands, and the error names *all* offenders at once
        # rather than only the first, which otherwise means one round trip per
        # duplicate. It also stops a partial insert from silently truncating the
        # tail of the batch — `deleted` documents are stamped last.
        collection.insert_many(docs, ordered=False)
