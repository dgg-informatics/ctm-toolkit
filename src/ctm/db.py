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

DIFF_COLLECTION = "03_diff_trials"
DEFAULT_MASTER_COLLECTION = "06_master_trials"

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


def mongo_config(require_master: bool = False) -> dict:
    """Resolve Mongo settings from the environment, failing fast by name.

    Mirrors ``build_client()``'s failure style: a missing required variable
    raises immediately with the variable named, rather than defaulting to
    something that silently misbehaves later.

    ``require_master`` additionally demands ``MONGO_MASTER_DBNAME``. Callers set
    it only when they actually intend to read the master from Mongo, so a run
    that passes ``--master <file>`` never fails on a variable it does not use.
    """
    host = os.environ.get("MONGO_HOST")
    port = os.environ.get("MONGO_PORT")
    dbname = os.environ.get("MONGO_DBNAME")

    for name, value in (("MONGO_HOST", host), ("MONGO_PORT", port), ("MONGO_DBNAME", dbname)):
        if not value:
            raise ValueError(f"{name} not set in environment")

    try:
        port_number = int(port)
    except ValueError:
        raise ValueError(f"MONGO_PORT must be an integer, got {port!r}") from None

    master_dbname = os.environ.get("MONGO_MASTER_DBNAME")
    if require_master and not master_dbname:
        raise ValueError(
            "MONGO_MASTER_DBNAME not set in environment "
            "(required unless --master names a file)"
        )

    return {
        "host": host,
        "port": port_number,
        "dbname": dbname,
        "master_dbname": master_dbname,
        "master_collection": (
            os.environ.get("MONGO_MASTER_COLLECTION") or DEFAULT_MASTER_COLLECTION
        ),
    }


def get_database(config: dict, db_name: str | None = None):
    """Connect and return a Database. ``db_name`` overrides ``config["dbname"]``."""
    from pymongo import MongoClient

    client = MongoClient(config["host"], config["port"])
    return client[db_name or config["dbname"]]


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
    return stamped


def read_collection(db, name: str) -> list[dict]:
    """Every document in ``name``, metadata stripped. Empty list if absent."""
    if name not in db.list_collection_names():
        return []
    return [strip_metadata(doc) for doc in db[name].find()]


def replace_collection(
    db, name: str, docs: list[dict], unique_key: str, lookup_keys=()
) -> None:
    """Replace ``name``'s contents with ``docs``, keyed uniquely on ``unique_key``.

    One database holds one run, so a stage's collection holds exactly one run's
    output — a re-run replaces rather than appends.

    Validate, drop, index, insert. Validating before the drop means a batch that
    cannot be keyed leaves the previous run's data intact instead of destroying
    it and failing. Dropping rather than ``delete_many({})`` matters too:
    delete_many leaves the collection's *indexes* in place, so a previous run's
    index definition would outlive a change to ``unique_key`` and keep enforcing
    the old constraint.
    """
    unkeyed = [doc for doc in docs if not doc.get(unique_key)]
    if unkeyed:
        raise ValueError(
            f"{len(unkeyed)} of {len(docs)} document(s) have no {unique_key!r}; "
            "refusing to store documents with no unique identity"
        )

    collection = db[name]
    collection.drop()
    collection.create_index(unique_key, unique=True)
    if lookup_keys:
        collection.create_index([(key, 1) for key in lookup_keys])
    if docs:
        # ordered=False so a rejected document does not abort the batch: every
        # valid document still lands, and the error names *all* offenders at once
        # rather than only the first, which otherwise means one round trip per
        # duplicate. It also stops a partial insert from silently truncating the
        # tail of the batch — `deleted` documents are stamped last.
        collection.insert_many(docs, ordered=False)
