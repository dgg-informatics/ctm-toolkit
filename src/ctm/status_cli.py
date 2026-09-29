"""ctm-status — what the pipeline holds right now, and whether it needs a match.

Answers the questions currently answered by poking at Mongo by hand: did my
curation land, how old is the patient data, do I need to re-run anything.

Exit status is the machine-readable half, so a wrapper can branch on it:

    0  up to date (or --json, which always exits 0)
    1  a fresh match is needed
    2  not ready — trials or patients are missing entirely

Usage:
  ctm-status                 human-readable inventory
  ctm-status --json          the same as one JSON object
"""
import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from ctm.logging_config import (
    add_logging_arguments,
    configure_logging,
    fail,
    verbosity_from_args,
)
from ctm.paths import load_env, match_export_dir, patient_raw_dir, report_export_dir

log = logging.getLogger(__name__)

EXIT_UP_TO_DATE = 0
EXIT_STALE = 1
EXIT_NOT_READY = 2


def _count(directory: Path, pattern: str) -> int:
    try:
        return sum(1 for _ in directory.glob(pattern))
    except OSError:
        return 0


def _unloaded_workbook(raw_dir: Path, clinical) -> bool:
    """True when a dropped workbook is newer than the patient data in Mongo.

    Answers "did anyone forget to run ctm-mm patients?", which is the failure
    that makes a match run against a stale cohort without anything looking wrong.
    """
    newest = max(
        (p.stat().st_mtime for p in raw_dir.glob("*.xlsx")
         if not p.name.startswith("~$")),
        default=None,
    )
    if newest is None or not clinical.exists:
        return False
    return datetime.fromtimestamp(newest, tz=UTC) > clinical.written_at


def _render(status, extras: dict) -> str:
    """The human view. Deliberately fixed-width so it is scannable in a terminal
    and diffable between runs."""
    rows = [
        ("trials", status.trials),
        ("clinical", status.patients_clinical),
        ("genomic", status.patients_genomic),
    ]
    width = max(len(name) for name, _ in rows)
    lines = [
        f"{name:<{width}}  {wm.database}.{wm.collection}".ljust(58) + wm.describe()
        for name, wm in rows
    ]

    last = status.last_match
    if last:
        lines.append(
            f"{'match':<{width}}  {last.get('match_db', '?')}".ljust(58)
            + f"{last.get('reports', 0):>5} reports  ran {str(last.get('ran_at', '?'))[:10]}"
        )
    else:
        lines.append(f"{'match':<{width}}  —".ljust(58) + "never run")

    lines.append("")
    workbooks = f"workbooks          {extras['workbooks']} in {extras['raw_dir']}"
    if extras.get("workbook_unloaded"):
        # The failure this whole design opened with: trials refreshed, patient
        # data not, so the match runs against a stale cohort.
        workbooks += "  ← newer than the loaded patient data; run ctm-mm patients"
    lines.append(workbooks)
    lines.append(f"reports on disk    {extras['reports']} in {extras['report_dir']}")
    lines.append(f"match exports      {extras['exports']} in {extras['export_dir']}")
    lines.append("")
    if status.stale:
        lines.append(f"STALE: {'; '.join(status.reasons())}")
        lines.append("Run ctm-match (or wait for the nightly run).")
    elif not status.ready:
        lines.append(f"NOT READY: {'; '.join(status.reasons())}")
    else:
        lines.append("up to date")
    return "\n".join(lines)


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(
        prog="ctm-status",
        description="Pipeline inventory and whether a fresh match is needed",
    )
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit one JSON object on stdout instead of a table "
                             "(always exits 0; read 'stale' from the payload)")
    parser.add_argument("--patient-db", dest="patient_db", metavar="NAME",
                        help="Patient database (default: MONGO_PATIENT_DBNAME)")
    add_logging_arguments(parser)
    args = parser.parse_args()
    configure_logging(verbosity=verbosity_from_args(args))

    from ctm import db as ctm_db
    from ctm.pipeline_state import read_status

    config = ctm_db.mongo_config(require_dbname=False, require_master=True)
    patient_db = args.patient_db or config["patient_dbname"]
    if not patient_db:
        fail("set MONGO_PATIENT_DBNAME, or pass --patient-db")

    client = ctm_db.get_client(config)
    status = read_status(client, config, patient_db)

    raw_dir, report_dir, export_dir = (
        patient_raw_dir(), report_export_dir(), match_export_dir())
    extras = {
        "raw_dir": str(raw_dir), "workbooks": _count(raw_dir, "*.xlsx"),
        "report_dir": str(report_dir), "reports": _count(report_dir, "*.pdf"),
        "export_dir": str(export_dir), "exports": _count(export_dir, "*.json"),
        "workbook_unloaded": _unloaded_workbook(raw_dir, status.patients_clinical),
    }

    if args.as_json:
        # stdout is the data channel, as everywhere else in the toolkit.
        print(json.dumps({
            "trials": status.trials.as_dict(),
            "clinical": status.patients_clinical.as_dict(),
            "genomic": status.patients_genomic.as_dict(),
            "last_match": status.last_match,
            "stale": status.stale,
            "ready": status.ready,
            "reasons": status.reasons(),
            **extras,
        }, indent=2, default=str))
        return

    # stdout, like --json: this table *is* the command's output, not a
    # diagnostic about it. Diagnostics and errors still go through logging.
    print(_render(status, extras))

    if not status.ready:
        sys.exit(EXIT_NOT_READY)
    sys.exit(EXIT_STALE if status.stale else EXIT_UP_TO_DATE)


if __name__ == "__main__":
    main()
