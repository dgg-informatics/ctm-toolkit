"""ctm-load-patients — workbook in, patient database updated, bundle on disk.

Two stages that were always run together: normalize the workbook to a
``{clinical, genomic, extras}`` bundle, then ingest that bundle into the patient
database. Splitting them meant copying a generated path from the first command
into the second by hand, which is the kind of step that gets skipped — and a
skipped patient load is exactly the failure the whole reconcile design exists to
catch, since the next match then runs against a stale cohort.

The bundle is written to disk *before* it is loaded, deliberately. It is the
lossless record of the workbook, so a patient database that is dropped or
re-loaded can be rebuilt from it without going back to whoever assembled the
spreadsheet.

Usage:
  ctm-load-patients                    the newest .xlsx in PATIENT_RAW_DIR
  ctm-load-patients --workbook PATH    a specific workbook
  ctm-load-patients --dry-run          list the stages, change nothing
"""
import argparse
import logging
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
from ctm.paths import load_env, patient_export_dir, patient_raw_dir
from ctm.pipelines import Stage, newest_file, run_stages

log = logging.getLogger(__name__)


def resolve_workbook(explicit: str | None) -> Path:
    """The workbook to ingest: the one named, else the newest in PATIENT_RAW_DIR.

    "Newest" is modification time, not the date in the filename — the filename
    carries no meaning here, unlike a curated trials file whose prefix names its
    run. Note that a plain ``cp`` sets mtime to now, so copying in an older
    workbook makes it the newest; ``cp -p`` preserves the original.
    """
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            fail("file not found: %s", path)
        return path
    return newest_file(patient_raw_dir(), "*.xlsx", "patient workbook (.xlsx)")


def build_stages(workbook: Path, bundle: Path, args) -> list[Stage]:
    """Normalize, then load. The bundle path is computed here and passed to both,
    so the second stage reads exactly what the first wrote rather than guessing
    at a generated name."""
    from ctm import mm_cli

    def mm(*argv):
        parsed = mm_cli.build_parser().parse_args(argv)
        return lambda: mm_cli.command_handlers()[parsed.command](parsed)

    normalize = ["patients", str(workbook), "--out", str(bundle)]
    if args.pt_uuid:
        normalize += ["--pt-uuid", args.pt_uuid]

    load = ["load", "--pt-data", str(bundle)]
    if args.patient_db:
        load += ["--patient-db", args.patient_db]
    if args.run_date:
        load += ["--run-date", args.run_date]

    return [
        Stage(f"ctm-mm patients {workbook.name}", mm(*normalize)),
        Stage(f"ctm-mm load {bundle.name}", mm(*load)),
    ]


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(
        prog="ctm-load-patients",
        description="Normalize a patient workbook and load it into the patient database",
    )
    parser.add_argument("--workbook", metavar="PATH",
                        help="Patient workbook (default: newest .xlsx in PATIENT_RAW_DIR)")
    parser.add_argument("--out", metavar="PATH",
                        help="Normalized bundle path "
                             "(default: PATIENT_EXPORT_DIR/<run-date>_patients.json)")
    parser.add_argument("--pt-uuid", dest="pt_uuid", metavar="ID[,ID...]",
                        help="Only these patients, comma-separated")
    parser.add_argument("--patient-db", dest="patient_db", metavar="NAME",
                        help="Patient database (default: MONGO_PATIENT_DBNAME)")
    parser.add_argument("--run-date", dest="run_date", metavar="YYYY-MM-DD",
                        help="Dates the bundle and the snapshot collections "
                             "(default: today)")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="List the stages without running them")
    add_logging_arguments(parser)
    args = parser.parse_args()
    configure_logging(verbosity=verbosity_from_args(args))

    with command_context(log, "ctm-load-patients"):
        workbook = resolve_workbook(args.workbook)
        run_date = args.run_date or date.today().isoformat()
        bundle = (Path(args.out).expanduser() if args.out
                  else patient_export_dir() / f"{run_date}_patients.json")

        log_event(log, "patients.begin", "Loading %s → %s", workbook.name, bundle,
                  workbook=str(workbook), bundle=str(bundle), run_date=run_date)

        code = run_stages(build_stages(workbook, bundle, args), dry_run=args.dry_run)
        if code:
            sys.exit(code)
        if not args.dry_run:
            log_event(log, "patients.complete",
                      "Patients loaded — ctm-match will pick them up. Bundle: %s",
                      bundle, bundle=str(bundle), run_date=run_date)


if __name__ == "__main__":
    main()
