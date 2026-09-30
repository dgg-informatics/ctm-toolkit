"""ctm-post-curate — ingest a curated trials file and rebuild the master.

Three stages: record the curation, reconcile it into the master, derive the
deduplicated set that matching reads.

**It stops there.** Matching and reports belong to ``ctm-match``, which runs when
*either* input changes — trials here, or patients from ``ctm-mm load``. Chaining
them onto curation is wrong in both directions: curate Monday and load patients
Tuesday, and Monday's reports used a stale cohort; do both in one afternoon and
you match twice for nothing.

The run is identified by the curated file's ``YYYY-MM-DD`` name prefix, not by
today's date. Ingesting Monday's curation on Thursday belongs to Monday's run —
that is the database its upstream stages wrote.

Usage:
  ctm-post-curate                     the newest file in CURATED_DIR
  ctm-post-curate --curated PATH      a specific file
  ctm-post-curate --dry-run           list the stages, change nothing
"""
import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

from ctm.logging_config import (
    add_logging_arguments,
    command_context,
    configure_logging,
    fail,
    log_event,
    verbosity_from_args,
)
from ctm.paths import curated_dir, load_env
from ctm.pipelines import Stage, newest_file, run_stages

log = logging.getLogger(__name__)

_RUN_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def resolve_curated(explicit: str | None) -> Path:
    """The curated file to ingest: the one named, else the newest in CURATED_DIR."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file() or path.stat().st_size == 0:
            fail("missing or empty: %s", path)
        return path
    return newest_file(curated_dir(), "*.json", "curated trials file")


def run_date_from(path: Path) -> str:
    """The run this curation belongs to, read from the filename.

    Deliberately not today's date: the curated file was derived from a specific
    run's trials, and its stages wrote a specific database. Re-deriving from the
    clock would write Monday's curation into Thursday's database, where the
    upstream collections it reconciles against do not exist.
    """
    match = _RUN_DATE_PREFIX.match(path.name)
    if not match:
        fail("'%s' needs a YYYY-MM-DD name prefix — that is how this run's "
             "database is identified", path.name)
    return match.group(1)


def validate_json(path: Path) -> None:
    """Fail on a malformed file before any of it reaches Mongo.

    This file has just been hand-edited, so a truncated bracket is the single
    most likely thing to be wrong with it, and it is much cheaper to say so here
    than midway through a partially-applied master rebuild.
    """
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        fail("%s is not valid JSON — fix the edit first: %s", path, exc, code=2)
    if not payload:
        fail("%s contains no trials", path, code=2)
    log_event(log, "post_curate.input", "Curated file: %s (%d trial(s))",
              path, len(payload), path=str(path), trials=len(payload))


def build_stages(curated: Path, run_date: str, db: str, curated_by: str) -> list[Stage]:
    from ctm import mm_cli

    def mm(*argv):
        parsed = mm_cli.build_parser().parse_args(argv)
        return lambda: mm_cli.command_handlers()[parsed.command](parsed)

    return [
        Stage("ctm-mm add-manual",
              mm("add-manual", "--trials", str(curated), "--db", db,
                 "--run-date", run_date, "--curated-by-user", curated_by)),
        Stage("ctm-mm trials-merge",
              mm("trials-merge", "--db", db, "--run-date", run_date)),
        Stage("ctm-mm trials-filter", mm("trials-filter", "--db", db)),
    ]


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(
        prog="ctm-post-curate",
        description="Ingest a curated trials file and rebuild the master",
    )
    parser.add_argument("--curated", metavar="PATH",
                        help="Curated trials JSON (default: newest in CURATED_DIR)")
    parser.add_argument("--db", metavar="NAME",
                        help="Run database (default: <run-date>_dev, from the "
                             "curated file's name prefix)")
    parser.add_argument("--curated-by-user", dest="curated_by_user", metavar="NAME",
                        help="Recorded as the curator (default: the invoking user)")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="List the stages without running them")
    add_logging_arguments(parser)
    args = parser.parse_args()
    configure_logging(verbosity=verbosity_from_args(args))

    with command_context(log, "ctm-post-curate"):
        curated = resolve_curated(args.curated)
        run_date = run_date_from(curated)
        db = args.db or f"{run_date}_dev"
        # SUDO_USER first: this is usually run with sudo, and the person who
        # curated is the one who typed the command, not the account it became.
        curated_by = (args.curated_by_user or os.environ.get("SUDO_USER")
                      or os.environ.get("USER") or "unknown")

        # Pinned for every stage, so the run database is the curated file's and
        # not one derived from the clock partway through.
        os.environ["MONGO_DBNAME"] = db

        validate_json(curated)
        log_event(log, "post_curate.begin", "Ingesting %s into %s (curated by %s)",
                  curated.name, db, curated_by,
                  path=str(curated), database=db, run_date=run_date,
                  curated_by=curated_by)

        code = run_stages(build_stages(curated, run_date, db, curated_by),
                          dry_run=args.dry_run)
        if code:
            sys.exit(code)
        if not args.dry_run:
            log_event(log, "post_curate.complete",
                      "Master rebuilt for %s — ctm-match will pick it up", run_date,
                      run_date=run_date, database=db)


if __name__ == "__main__":
    main()
