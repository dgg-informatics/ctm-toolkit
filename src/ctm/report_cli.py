"""ctm-report — build a trial match report (PDF or live preview).

Reads from MongoDB by default; pass the three JSON files to read from disk instead.

Usage:
  ctm-report --all                                  every patient in <today>_match
  ctm-report --match-db 2026-09-15_match --all      a specific run
  ctm-report --sample-id pt_0000016                 one patient, from Mongo
  ctm-report --pts p.json --trials t.json --matches m.json --sample-id ID
"""
import argparse
import os
import platform
from pathlib import Path


def _fix_macos_weasyprint_path() -> None:
    if platform.system() == "Darwin":
        homebrew_lib = "/opt/homebrew/lib"
        if os.path.isdir(homebrew_lib):
            os.environ["DYLD_LIBRARY_PATH"] = (
                homebrew_lib + os.pathsep + os.environ.get("DYLD_LIBRARY_PATH", "")
            )


def _run_preview(pts_path: str, trials_path: str, matches_path: str, sample_id: str,
                 meaningful_only: bool = False) -> None:
    from livereload import Server

    from ctm.reports.builder import STATIC_DIR, TEMPLATES_DIR, render_html_from_pt_trials_matches

    output_dir = Path.cwd() / "output"
    output_file = output_dir / "report.html"

    def build():
        output_dir.mkdir(exist_ok=True, parents=True)
        output_file.write_text(render_html_from_pt_trials_matches(
            pts_path, trials_path, matches_path, sample_id, meaningful_only=meaningful_only))

    build()
    server = Server()
    # Watch the packaged assets — under an editable install these are the files
    # you actually edit, and they are what render_html reads.
    server.watch(str(TEMPLATES_DIR / "*.html"), build)
    server.watch(str(STATIC_DIR / "*.css"), build)
    server.watch(pts_path, build)
    server.watch(trials_path, build)
    server.watch(matches_path, build)
    server.serve(root=str(output_dir), port=5500, open_url_delay=1,
                 default_filename="report.html")


def _run_from_mongo(args, parser) -> None:
    """One report per patient, straight out of the pipeline's own databases."""
    import sys
    from datetime import date

    from ctm import db as ctm_db
    from ctm.paths import report_export_dir
    from ctm.reports.builder import patient_context_from_entry, render_html_from_docs
    from ctm.reports.sources import load_patient, load_trials, report_filename, sample_ids

    if not args.all and not args.sample_id:
        parser.error("pass --sample-id, or --all for every patient in the match db")

    run_date = args.run_date or date.today().isoformat()
    config = ctm_db.mongo_config(require_dbname=False)
    match_db_name = args.match_db or f"{run_date}_match"
    patient_db_name = args.patient_db or config.get("patient_dbname")
    if not patient_db_name:
        parser.error("set MONGO_PATIENT_DBNAME, or pass --patient-db")

    client = ctm_db.get_client(config)
    match_db, patient_db = client[match_db_name], client[patient_db_name]

    targets = sample_ids(match_db) if args.all else [args.sample_id]
    if not targets:
        print(f"Error: no patients in {match_db_name}.clinical", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir) if args.out_dir else report_export_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Loaded once: the trial collection is the same for every patient.
    trials = load_trials(match_db)
    print(f"{len(targets)} patient(s), {len(trials)} trial(s) from {match_db_name}",
          file=sys.stderr)

    # The renderer is imported here, so the macOS library shim has to be applied
    # here too — _run_from_mongo is reachable without going through main().
    _fix_macos_weasyprint_path()
    from weasyprint import HTML

    failures = []
    for sample_id in targets:
        # One patient's failure must not cost the other 36 their reports.
        try:
            docs = load_patient(match_db, patient_db, sample_id)
            html = render_html_from_docs(
                sample_id, docs["matches"], trials, docs["genomic_docs"],
                patient_context=patient_context_from_entry(docs["patient_entry"]),
            )
            out_path = (Path(args.out) if args.out and not args.all
                        else out_dir / report_filename(run_date, sample_id))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            HTML(string=html).write_pdf(str(out_path))
            print(f"  {sample_id}: {len(docs['matches'])} match doc(s) → {out_path}",
                  file=sys.stderr)
        except Exception as exc:
            failures.append((sample_id, exc))
            print(f"  {sample_id}: FAILED — {exc}", file=sys.stderr)

    print(f"Wrote {len(targets) - len(failures)}/{len(targets)} report(s) → {out_dir}",
          file=sys.stderr)
    if failures:
        sys.exit(1)


def main() -> None:
    _fix_macos_weasyprint_path()

    from ctm.reports.builder import render_html_from_pt_trials_matches

    parser = argparse.ArgumentParser(prog="ctm-report")
    parser.add_argument("--out", metavar="PATH", default=None,
                        help="Output PDF path (default: ./output/report.pdf)")
    parser.add_argument("--pts", dest="pts_path", metavar="PATH",
                        help="Patient collection JSON from ctm-mm patients "
                             "(file mode; requires --trials and --matches)")
    parser.add_argument("--trials", dest="trials_path", metavar="PATH",
                        help="Trial collection JSON from ctm-mm trials (file mode)")
    parser.add_argument("--matches", dest="matches_path", metavar="PATH",
                        help="Flat trial_match collection JSON from the match engine (file mode)")
    parser.add_argument("--sample-id", dest="sample_id", metavar="ID",
                        help="SAMPLE_ID to build the report for (omit with --all)")
    parser.add_argument("--all", action="store_true",
                        help="Mongo mode: one report per patient in the match db's "
                             "clinical collection, including patients with no matches")
    parser.add_argument("--match-db", dest="match_db", metavar="NAME",
                        help="Match database assembled by ctm-mm match-prep "
                             "(default: <run-date>_match)")
    parser.add_argument("--patient-db", dest="patient_db", metavar="NAME",
                        help="Patient database written by ctm-mm load "
                             "(default: MONGO_PATIENT_DBNAME) — holds latest_patient_data, "
                             "which match-prep does not copy into the match db")
    parser.add_argument("--run-date", dest="run_date", metavar="YYYY-MM-DD",
                        help="Names the match db and the output files (default: today)")
    parser.add_argument("--out-dir", dest="out_dir", metavar="DIR",
                        help="Directory for --all output (default: REPORT_EXPORT_DIR, "
                             "else /var/lib/ctm/reports)")
    parser.add_argument("--meaningful-only", dest="meaningful_only", action="store_true",
                        help="Keep only matches whose trial has an oncotree diagnosis or genomic "
                             "criterion (drops age/gender-only trials that match everyone)")
    parser.add_argument("--preview", action="store_true",
                        help="Spin up livereload server instead of building PDF")
    args = parser.parse_args()

    file_mode = any((args.pts_path, args.trials_path, args.matches_path))
    if file_mode and not all((args.pts_path, args.trials_path, args.matches_path)):
        parser.error("file mode needs all three of --pts, --trials and --matches")
    if not file_mode:
        _run_from_mongo(args, parser)
        return
    if not args.sample_id:
        parser.error("--sample-id is required in file mode")

    if args.preview:
        _run_preview(args.pts_path, args.trials_path, args.matches_path, args.sample_id,
                     meaningful_only=args.meaningful_only)
        return

    from weasyprint import HTML

    html = render_html_from_pt_trials_matches(args.pts_path, args.trials_path, args.matches_path,
                                              args.sample_id, meaningful_only=args.meaningful_only)
    output_path = Path(args.out) if args.out else Path.cwd() / "output" / "report.pdf"
    output_path.parent.mkdir(exist_ok=True, parents=True)
    HTML(string=html).write_pdf(str(output_path))
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
