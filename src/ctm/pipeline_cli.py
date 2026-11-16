"""ctm-pipeline — the weekly trial run, from the source pull to the filtered set
matchengine reads, with no one in between.

    01 ctm-mm trials → 02 trials-diff → 03c ctm-llm clinical → 03g ctm-llm genomic
      → 04 ctm-llm autofill-ctml → 06 trials-merge --from 04 → 07 trials-filter

05_manual_curated_trials is not a stage: it only holds manual edits, which
``ctm-apply-edits`` merges on any day. Every trial this run merges is stamped
``curated_by: "auto"``.

Usage:
  ctm-pipeline                                   the whole run (--end 07)
  ctm-pipeline --end 03c                         stop after a stage
  ctm-pipeline --start 03g --db 2026-11-16       resume a failed run in its database
  ctm-pipeline --start 03c --trials prod06.json --db backfill-2026-11-16
                                                 re-annotate a file of trials
  ctm-pipeline --dry-run                         list the stages, run nothing

Stages are named by their collection, or its prefix: ``07`` or
``07_filtered_trials``. A week with no changed trials skips the LLM stages and
still merges, so the diff's deletions are applied and the run exits 0.

The cron line is just ``ctm-pipeline --end 07_filtered_trials``; see
``deploy/cron.d/ctm``. ``CTM_LOG_ENV=prod`` keeps a clean run silent, so mail
means something broke.
"""
import argparse
import logging
import os
import sys
from datetime import date

from ctm.logging_config import (
    add_logging_arguments,
    command_context,
    configure_logging,
    fail,
    log_event,
    verbosity_from_args,
)
from ctm.paths import load_env
from ctm.pipelines import Stage, llm_stage, mm_stage, run_stages

log = logging.getLogger(__name__)

# Order matters: this is the run.
STAGES = {
    "01": "01_normalized_trials",
    "02": "02_diff_trials",
    "03c": "03c_clinical_trials",
    "03g": "03g_genomic_trials",
    "04": "04_autofill_trials",
    "06": "06_master_trials",
    "07": "07_filtered_trials",
}
ORDER = list(STAGES)
LLM_STAGES = ("03c", "03g", "04")


def resolve_stage(name: str) -> str:
    """A stage key from ``07`` or ``07_filtered_trials``."""
    for key, collection in STAGES.items():
        if name in (key, collection):
            return key
    if name.startswith("05"):
        fail("05_manual_curated_trials is not a pipeline stage — it holds manual "
             "edits only. Use: ctm-apply-edits --file <edits>.json")
    fail("unknown stage %r. Stages: %s", name,
         ", ".join(f"{k} ({c})" for k, c in STAGES.items()))
    raise AssertionError("unreachable")  # fail() exits


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctm-pipeline",
        description="Weekly trial run: pull, diff, AI annotation, merge into the "
                    "master, filter. Stages: " + ", ".join(STAGES.values()),
    )
    parser.add_argument("--start", default="01", metavar="STAGE",
                        help="First stage to run (default 01). Anything after 01 "
                             "needs --db, the run whose earlier stages it reads")
    parser.add_argument("--end", default="07", metavar="STAGE",
                        help="Last stage to run (default 07_filtered_trials)")
    parser.add_argument("--db", metavar="NAME",
                        help="Run database (default: today, YYYY-MM-DD)")
    parser.add_argument("--trials", metavar="JSON",
                        help="With --start 03c: annotate the trials in this file "
                             "instead of the diff's changed trials (a backfill)")
    parser.add_argument("--sources", nargs="+", metavar="FLAG",
                        help="Source flags for `ctm-mm trials` "
                             "(default: --amc --ddots --west)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Pass --overwrite to autofill-ctml: replace matches "
                             "that are not empty")
    parser.add_argument("--yes", action="store_true",
                        help="Pass --yes to the LLM stages, so a cold cache does "
                             "not stop an unattended run")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="List the stages without running them")
    add_logging_arguments(parser)
    return parser


def check_args(args) -> tuple[str, str]:
    """(start, end) stage keys, or a clean failure naming the problem."""
    start, end = resolve_stage(args.start), resolve_stage(args.end)
    if ORDER.index(start) > ORDER.index(end):
        fail("--start %s comes after --end %s", STAGES[start], STAGES[end])
    if start != "01" and not args.db:
        fail("--start %s reads collections an earlier stage wrote: pass --db with "
             "that run's database (e.g. --db %s)", start, date.today().isoformat())
    if args.trials and start != "03c":
        fail("--trials only feeds the clinical stage: use it with --start 03c")
    return start, end


class ChangedGuard:
    """Skips an LLM stage when the diff found no changed trials.

    Checked when the stage runs, not when the pipeline is built: 02 does not
    exist until the diff stage has written it. Logged once per run.
    """

    def __init__(self, db: str):
        self.db = db
        self._changed: int | None = None

    def changed(self) -> int:
        if self._changed is None:
            from ctm import db as ctm_db

            database = ctm_db.get_database(ctm_db.mongo_config(), self.db)
            self._changed = len(ctm_db.read_collection(
                database, ctm_db.DIFF_COLLECTION, {"diff_status": "changed"}))
            if not self._changed:
                log_event(log, "pipeline.no_changes",
                          "No changed trials in %s.%s — skipping the LLM stages; "
                          "the master is carried forward", self.db,
                          ctm_db.DIFF_COLLECTION, database=self.db)
        return self._changed

    def wrap(self, run):
        def guarded():
            if not self.changed():
                return 0
            return run()
        return guarded


def build_stages(args, start: str, end: str, db: str) -> list[Stage]:
    sources = args.sources or ["--amc", "--ddots", "--west"]
    yes = ["--yes"] if args.yes else []
    clinical_in = ["--trials", args.trials] if args.trials else []

    every = {
        "01": lambda: mm_stage(f"01 ctm-mm trials {' '.join(sources)}",
                               "trials", *sources, "--db", db),
        "02": lambda: mm_stage("02 ctm-mm trials-diff", "trials-diff", "--db", db),
        "03c": lambda: llm_stage("03c ctm-llm clinical", "clinical", "--db", db,
                                 *yes, *clinical_in),
        "03g": lambda: llm_stage("03g ctm-llm genomic", "genomic", "--db", db, *yes),
        "04": lambda: llm_stage("04 ctm-llm autofill-ctml", "autofill-ctml", "--db", db,
                                *yes, *(["--overwrite"] if args.overwrite else [])),
        "06": lambda: mm_stage("06 ctm-mm trials-merge --from 04_autofill_trials",
                               "trials-merge", "--db", db, "--from", STAGES["04"]),
        "07": lambda: mm_stage("07 ctm-mm trials-filter", "trials-filter", "--db", db),
    }
    selected = ORDER[ORDER.index(start):ORDER.index(end) + 1]
    stages = [every[key]() for key in selected]

    # Only the diff's changed trials go to the LLM; with --trials there is no
    # diff to consult, so nothing is skipped.
    if not args.trials:
        guard = ChangedGuard(db)
        for stage in stages:
            if stage.label.split()[0] in LLM_STAGES:
                stage.run = guard.wrap(stage.run)
    return stages


def main(argv: list[str] | None = None) -> None:
    load_env()
    args = build_parser().parse_args(argv)
    configure_logging(verbosity=verbosity_from_args(args))

    with command_context(log, "ctm-pipeline"):
        start, end = check_args(args)
        from ctm.db import run_dbname
        db = args.db or run_dbname(date.today().isoformat())
        # Pinned for every stage, so a run that crosses midnight stays in one database.
        os.environ["MONGO_DBNAME"] = db
        log_event(log, "pipeline.begin", "Run database %s, stages %s → %s",
                  db, STAGES[start], STAGES[end], database=db, start=start, end=end)
        code = run_stages(build_stages(args, start, end, db), dry_run=args.dry_run)
        if code:
            sys.exit(code)


if __name__ == "__main__":
    main()
