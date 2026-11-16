"""ctm-trials-report — the Monday summary of the weekly trial run.

Prints what the run changed: totals and match levels in 07, new / changed /
deleted trials, whether the AI built a match clause for each, and what needs a
look (manual edits replaced, level-0 trials, clauses not built, an unfinished
run). Read-only: no AI calls, no writes to Mongo.

Cron runs it right after the pipeline, so its output is Monday's mail — and it
still runs, and says so, when the pipeline failed:

    0 7 * * MON  dgg-mipro  ctm-pipeline --end 07_filtered_trials; ctm-trials-report

A copy is written to ``MASTER_TRIAL_EXPORT_DIR/trials_report-<db>.txt``.

Usage:
  ctm-trials-report                   today's run
  ctm-trials-report --db 2026-11-23   any past run
"""
import argparse
import logging
import re
import sys
from datetime import date
from pathlib import Path

from ctm.logging_config import add_logging_arguments, configure_logging, verbosity_from_args
from ctm.paths import load_env, master_trial_export_dir
from ctm.trials_report import build_report, render

log = logging.getLogger(__name__)

_RUN_DB = re.compile(r"\d{4}-\d{2}-\d{2}")
_MASTER = "06_master_trials"
_FILTERED = "07_filtered_trials"


def _read(database, name: str) -> list[dict]:
    from ctm import db as ctm_db

    return ctm_db.read_collection(database, name, keep_metadata=True)


def gather(client, db: str) -> dict:
    """Everything :func:`build_report` needs, from the run database and the
    latest earlier dated database holding a master snapshot (a weekly run, or a
    mid-week ``ctm-apply-edits``)."""
    from ctm import db as ctm_db

    names = client.list_database_names()
    data = {"db": db, "collections": None, "diff": [], "autofill": [], "filtered": [],
            "previous_db": None, "previous_master": [], "previous_filtered": None}

    if db in names:
        database = client[db]
        data["collections"] = sorted(database.list_collection_names())
        data["diff"] = _read(database, ctm_db.DIFF_COLLECTION)
        data["autofill"] = _read(database, ctm_db.LLM_AUTOFILL_COLLECTION)
        data["filtered"] = _read(database, _FILTERED)

    earlier = sorted(n for n in names
                     if _RUN_DB.fullmatch(n) and n < db
                     and _MASTER in client[n].list_collection_names())
    if earlier:
        previous = client[earlier[-1]]
        data["previous_db"] = earlier[-1]
        data["previous_master"] = _read(previous, _MASTER)
        if _FILTERED in previous.list_collection_names():
            data["previous_filtered"] = len(_read(previous, _FILTERED))
    return data


def _client():
    from ctm import db as ctm_db

    return ctm_db.get_client(ctm_db.mongo_config(require_dbname=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctm-trials-report",
        description="Summarize what a weekly ctm-pipeline run changed",
    )
    parser.add_argument("--db", metavar="NAME",
                        help="Run database to report on (default: today, YYYY-MM-DD)")
    parser.add_argument("--out", metavar="PATH",
                        help="Also write the report here (default: "
                             "MASTER_TRIAL_EXPORT_DIR/trials_report-<db>.txt)")
    parser.add_argument("--no-file", dest="no_file", action="store_true",
                        help="Print only; write no file")
    add_logging_arguments(parser)
    return parser


def main(argv: list[str] | None = None) -> None:
    load_env()
    args = build_parser().parse_args(argv)
    configure_logging(verbosity=verbosity_from_args(args))

    from ctm.db import run_dbname
    db = args.db or run_dbname(date.today().isoformat())
    text = render(build_report(gather(_client(), db)))
    # stdout, not logging: under cron this is the mail body, and prod logging
    # keeps the console at WARNING.
    sys.stdout.write(text)

    if args.no_file:
        return
    path = Path(args.out) if args.out else master_trial_export_dir() / f"trials_report-{db}.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    except OSError as exc:
        # The mail already has the report; a missing copy is not worth a failure.
        log.warning("could not write %s: %s", path, exc)


if __name__ == "__main__":
    main()
