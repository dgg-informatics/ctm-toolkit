"""ctm-match — bring matches and reports up to date with trials and patients.

The pipeline has two independent human triggers: a curator publishing curated
trials, and someone dropping a new patient workbook. Chaining matching onto
either one is wrong — curate on Monday and load patients on Tuesday, and Monday's
match is against a stale cohort; do both in one afternoon and you match twice.

So this reconciles rather than reacts. It asks whether the current inputs differ
from the ones the last match consumed, and acts only if they do. Two inputs
changing five minutes apart produce one match, a missed or crashed run is
repaired by the next invocation, and running it twice in a row is a no-op. That
makes it safe on a timer and safe by hand, with no debounce to get wrong.

Usage:
  ctm-match                  match + report if stale, else do nothing
  ctm-match --force          match regardless
  ctm-match --dry-run        say what would happen, change nothing
"""
import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from ctm.logging_config import (
    add_logging_arguments,
    command_context,
    configure_logging,
    fail,
    log_event,
    verbosity_from_args,
)
from ctm.paths import load_env, match_export_dir

log = logging.getLogger(__name__)

#: Collections matchengine creates in the match database. ``match-prep`` rebuilds
#: ``trial``/``clinical``/``genomic`` itself, but nothing owns these — and a
#: leftover ``trial_match`` is worse than an error: ``copy_collection`` preserves
#: ``_id``, so stale match documents still resolve against freshly-copied
#: clinical ones and the database looks consistent while mixing two generations.
MATCHENGINE_COLLECTIONS = (
    "trial_match",
    "run_log_trial_match",
    "clinical_run_history_trial_match",
)

MATCH_DB_SUFFIX = "_match"


def reset_match_database(client, name: str, dry_run: bool = False) -> list[str]:
    """Empty the match database so matchengine starts from nothing.

    Guarded on the name: this drops every collection it finds, and the database
    to operate on arrives from an environment a cron job assembled. Refusing
    anything not ending in ``_match`` is what stops a mistyped ``--match-db``
    from taking the trial master with it.

    Dropping is safe because the match database is derived in full — trials from
    ``07_filtered_trials``, patients from ``latest_*``, matches from those. There
    is nothing here that is not reproducible from somewhere else.
    """
    if not name.endswith(MATCH_DB_SUFFIX):
        fail("refusing to reset %r: a match database name must end in %r",
             name, MATCH_DB_SUFFIX)

    database = client[name]
    existing = sorted(database.list_collection_names())
    if dry_run:
        return existing
    for collection in existing:
        database.drop_collection(collection)
    if existing:
        log_event(log, "match.reset", "Reset %s (dropped %d collection(s))",
                  name, len(existing), match_db=name, dropped=existing)
    return existing


def export_trial_match(client, match_db: str, out_path: Path) -> int:
    """Write the run's matches to disk, returning the document count.

    This is what makes the ``<date>_match`` databases disposable: the matches
    themselves are kept indefinitely as files, so Mongo only has to hold however
    many recent runs are convenient to keep.
    """
    docs = list(client[match_db]["trial_match"].find({}))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(docs, indent=2, default=str))
    log_event(log, "match.exported", "Exported %d match(es) → %s", len(docs), out_path,
              count=len(docs), path=str(out_path), match_db=match_db)
    return len(docs)


def _match_prep_args(match_db: str, run_date: str, min_match_level: int) -> argparse.Namespace:
    """The Namespace `ctm-mm match-prep --run` would have built from its flags.

    Assembled here rather than shelling out so the whole reconcile is one
    process: one run id, one log file, and state that can be written only after
    every part of it succeeded.
    """
    return argparse.Namespace(
        match_db=match_db, run_date=run_date, min_match_level=min_match_level,
        run=True,
        trial_db=None, trial_collection=None, trials_file=None,
        clinical_db=None, clinical_collection=None,
        genomic_db=None, genomic_collection=None, pt_data=None,
    )


def _report_args(run_date: str, out_dir: str | None) -> argparse.Namespace:
    return argparse.Namespace(
        all=True, sample_id=None, run_date=run_date,
        match_db=None, patient_db=None, out_dir=out_dir, out=None,
        pts_path=None, trials_path=None, matches_path=None,
        meaningful_only=False, preview=False,
    )


def _run(args) -> int:
    from ctm import db as ctm_db
    from ctm.mm_cli import _cmd_match_prep
    from ctm.pipeline_state import read_status, write_match_state
    from ctm.report_cli import _run_from_mongo

    config = ctm_db.mongo_config(require_dbname=False, require_master=True)
    patient_db = args.patient_db or config["patient_dbname"]
    if not patient_db:
        fail("set MONGO_PATIENT_DBNAME, or pass --patient-db")

    client = ctm_db.get_client(config)
    status = read_status(client, config, patient_db)

    if not status.ready:
        # Not an error and not a retry loop: a pipeline missing an input has not
        # gone stale, it has not been built yet. Exit 0 so a nightly timer does
        # not mail every morning until someone loads patients.
        log.warning("nothing to match — %s", "; ".join(status.reasons()))
        return 0

    if not status.stale and not args.force:
        log_event(log, "match.skipped", "Up to date — nothing to do",
                  reasons=status.reasons())
        return 0

    run_date = args.run_date or date.today().isoformat()
    match_db = args.match_db or f"{run_date}{MATCH_DB_SUFFIX}"
    export_path = Path(args.out) if args.out else (
        match_export_dir() / f"{run_date}_trial_match.json")

    log_event(log, "match.begin", "Matching: %s", "; ".join(status.reasons()),
              match_db=match_db, reasons=status.reasons(),
              trials=status.trials.count, patients=status.patients_clinical.count)

    if args.dry_run:
        would_drop = reset_match_database(client, match_db, dry_run=True)
        log.warning("dry run — would reset %s (%d collection(s)), match, "
                    "export to %s and rebuild reports",
                    match_db, len(would_drop), export_path)
        return 0

    reset_match_database(client, match_db)

    code = _cmd_match_prep(_match_prep_args(match_db, run_date, args.min_match_level))
    if code:
        fail("matchengine exited %d — state not advanced, so the next run retries",
             code, code=code)

    exported = export_trial_match(client, match_db, export_path)

    # Reports last, and state after them: a run that matched but failed to render
    # must look unfinished, or the watermark says done while no reports exist.
    try:
        _run_from_mongo(_report_args(run_date, args.out_dir), argparse.ArgumentParser())
    except SystemExit as exc:
        if exc.code:
            fail("report generation exited %s — state not advanced", exc.code,
                 code=exc.code)

    from ctm.paths import report_export_dir
    out_dir = Path(args.out_dir) if args.out_dir else report_export_dir()
    reports = sum(1 for _ in out_dir.glob(f"{run_date}_*.pdf"))

    write_match_state(client, config["master_dbname"], status, match_db,
                      reports=reports, export_path=str(export_path))
    log_event(log, "match.complete",
              "Matched %d trial(s) against %d patient(s) → %s; %d match(es), %d report(s)",
              status.trials.count, status.patients_clinical.count, match_db,
              exported, reports,
              match_db=match_db, matches=exported, reports=reports,
              completed_at=datetime.now(tz=UTC).isoformat())
    return 0


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(
        prog="ctm-match",
        description="Match and report, but only if trials or patients have changed",
    )
    parser.add_argument("--force", action="store_true",
                        help="Match even when nothing has changed")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="Report what would happen; change nothing")
    parser.add_argument("--run-date", dest="run_date", metavar="YYYY-MM-DD",
                        help="Names the match db, the export and the reports "
                             "(default: today)")
    parser.add_argument("--match-db", dest="match_db", metavar="NAME",
                        help="Match database to rebuild (default: <run-date>_match). "
                             "Must end in _match")
    parser.add_argument("--patient-db", dest="patient_db", metavar="NAME",
                        help="Patient database (default: MONGO_PATIENT_DBNAME)")
    parser.add_argument("--min-match-level", dest="min_match_level", type=int,
                        choices=[0, 1, 2, 3], default=0, metavar="N",
                        help="Passed to match-prep: only match trials whose match "
                             "clause is at least this specific")
    parser.add_argument("--out", metavar="PATH",
                        help="trial_match export path (default: MATCH_EXPORT_DIR)")
    parser.add_argument("--out-dir", dest="out_dir", metavar="DIR",
                        help="Report directory (default: REPORT_EXPORT_DIR)")
    add_logging_arguments(parser)
    args = parser.parse_args()
    configure_logging(verbosity=verbosity_from_args(args))

    with command_context(log, "ctm-match"):
        code = _run(args)
        if code:
            sys.exit(code)


if __name__ == "__main__":
    main()
