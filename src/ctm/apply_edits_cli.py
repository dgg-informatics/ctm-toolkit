"""ctm-apply-edits — merge hand-edited trials into the master, any day of the week.

    05 ctm-mm add-manual → 06 trials-merge → 07 trials-filter

Start from the latest ``06_master_trials``, not ``04_autofill_trials``: 06
already holds earlier manual edits, and editing an older copy would undo them.
Each trial is stamped ``curated_by: "manual-edit"`` with the account that ran
this, so later merges can tell a hand fix from the AI's draft and warn when a
changed source replaces one.

The edits land in the run database for today (or ``--db``). A day without a
weekly run has no 02 diff there, so nothing is deleted.

Matching is not chained: ``ctm-match`` picks up the new 07 on its next run.

Usage:
  ctm-apply-edits --file 2026-11-18-germ-cell-fix.json
  ctm-apply-edits --file edits.json --dry-run     show what would be replaced
"""
import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

from ctm.logging_config import (
    add_logging_arguments,
    command_context,
    configure_logging,
    fail,
    log_event,
    verbosity_from_args,
)
from ctm.paths import load_env
from ctm.pipelines import Stage, mm_stage, run_stages

log = logging.getLogger(__name__)


def read_edits(path: Path) -> list[dict]:
    """The edited trials, or a clean failure before anything reaches Mongo.

    The file was just edited by hand, so a truncated bracket is the most likely
    thing wrong with it.
    """
    if not path.is_file():
        fail("not found: %s", path)
    try:
        trials = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        fail("%s is not valid JSON — fix the edit first: %s", path, exc, code=2)
    if not isinstance(trials, list) or not trials:
        fail("%s must be a non-empty JSON array of trials", path, code=2)
    return trials


def preview(edits: list[dict], master: list[dict]) -> dict:
    """Which trial keys the edits replace, add, and re-edit (a trial already
    hand-edited, whose earlier fix this one supersedes)."""
    from ctm.db import CURATED_MANUAL_EDIT
    from ctm.trials_lifecycle import trial_key

    by_key = {trial_key(t): t for t in master}
    keys = [trial_key(t) for t in edits]
    return {
        "replaced": [k for k in keys if k in by_key],
        "added": [k for k in keys if k not in by_key],
        "re_edited": [k for k in keys
                      if by_key.get(k, {}).get("curated_by") == CURATED_MANUAL_EDIT],
    }


def build_stages(edits: Path, db: str, curated_by_user: str) -> list[Stage]:
    from ctm.db import CURATED_MANUAL_EDIT

    return [
        mm_stage("05 ctm-mm add-manual", "add-manual", "--trials", str(edits),
                 "--db", db, "--curated-by", CURATED_MANUAL_EDIT,
                 "--curated-by-user", curated_by_user),
        mm_stage("06 ctm-mm trials-merge", "trials-merge", "--db", db),
        mm_stage("07 ctm-mm trials-filter", "trials-filter", "--db", db),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctm-apply-edits",
        description="Merge a file of hand-edited trials into 06_master_trials "
                    "and rebuild 07_filtered_trials",
    )
    parser.add_argument("--file", required=True, metavar="JSON",
                        help="JSON array of edited trials (start from the latest 06)")
    parser.add_argument("--db", metavar="NAME",
                        help="Run database for 05 (default: today, YYYY-MM-DD)")
    parser.add_argument("--curated-by-user", dest="curated_by_user", metavar="NAME",
                        help="Recorded as the editor (default: the invoking user)")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="Show which trials would be replaced; change nothing")
    add_logging_arguments(parser)
    return parser


def main(argv: list[str] | None = None) -> None:
    load_env()
    args = build_parser().parse_args(argv)
    configure_logging(verbosity=verbosity_from_args(args))

    with command_context(log, "ctm-apply-edits"):
        from ctm import db as ctm_db

        path = Path(args.file).expanduser()
        edits = read_edits(path)
        db = args.db or ctm_db.run_dbname(date.today().isoformat())
        # SUDO_USER first: under sudo, the editor is who typed the command.
        editor = (args.curated_by_user or os.environ.get("SUDO_USER")
                  or os.environ.get("USER") or "unknown")
        os.environ["MONGO_DBNAME"] = db

        config = ctm_db.mongo_config(require_master=True)
        master = ctm_db.read_collection(
            ctm_db.get_database(config, config["master_dbname"]),
            config["master_collection"], keep_metadata=True)
        summary = preview(edits, master)
        log_event(log, "apply_edits.preview",
                  "%s: %d trial(s) — replaces %d, adds %d, by %s → %s",
                  path.name, len(edits), len(summary["replaced"]), len(summary["added"]),
                  editor, db, path=str(path), database=db, editor=editor, **summary)
        if summary["re_edited"]:
            log.warning("These were already hand-edited; this file replaces that edit: %s",
                        ", ".join(summary["re_edited"]))

        code = run_stages(build_stages(path, db, editor), dry_run=args.dry_run)
        if code:
            sys.exit(code)


if __name__ == "__main__":
    main()
