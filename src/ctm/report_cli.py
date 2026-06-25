"""ctm-report — build a trial match report (PDF or live preview).

Usage:
  ctm-report --pt normalized.json --matches matches.json --engine mm [--preview] [--db NAME] [--out PATH]
"""
import argparse
import json
import os
import platform
import sys
from pathlib import Path

_KNOWN_ENGINES = ("mm",)


def _fix_macos_weasyprint_path() -> None:
    if platform.system() == "Darwin":
        homebrew_lib = "/opt/homebrew/lib"
        if os.path.isdir(homebrew_lib):
            os.environ["DYLD_LIBRARY_PATH"] = (
                homebrew_lib + os.pathsep + os.environ.get("DYLD_LIBRARY_PATH", "")
            )


def _write_to_mongo(pt_path: str, matches_path: str, db_name: str) -> None:
    try:
        from pymongo import MongoClient
    except ImportError:
        print("Error: pymongo not installed.", file=sys.stderr)
        sys.exit(1)

    pt_data = json.loads(Path(pt_path).read_text())
    matches_data = json.loads(Path(matches_path).read_text())
    uri = os.getenv("CTM_MONGO_URI", "mongodb://localhost:27017")
    db = MongoClient(uri)[db_name]

    # clinical may be a list (ctm-mm output) or a single dict (mm export)
    clinical_docs = pt_data.get("clinical", [])
    if isinstance(clinical_docs, dict):
        clinical_docs = [clinical_docs]
    genomic_all = pt_data.get("genomic", [])

    sample_id_to_clinical_id: dict = {}
    for clinical in clinical_docs:
        sample_id = clinical.get("SAMPLE_ID")
        db["clinical"].replace_one({"SAMPLE_ID": sample_id}, clinical, upsert=True)
        doc = db["clinical"].find_one({"SAMPLE_ID": sample_id}, {"_id": 1})
        sample_id_to_clinical_id[sample_id] = doc["_id"] if doc else None
        print(f"  clinical: upserted SAMPLE_ID={sample_id}")

    for sample_id, clinical_id in sample_id_to_clinical_id.items():
        pt_genomic = [g for g in genomic_all if g.get("SAMPLE_ID") == sample_id]
        if pt_genomic:
            db["genomic"].delete_many({"SAMPLE_ID": sample_id})
            if clinical_id:
                for g in pt_genomic:
                    g["CLINICAL_ID"] = clinical_id
            db["genomic"].insert_many(pt_genomic)
            print(f"  genomic: inserted {len(pt_genomic)} docs for {sample_id}")

    trial_matches = matches_data.get("trial_match", [])
    if trial_matches:
        match_sample_ids = {m.get("sample_id") for m in trial_matches if m.get("sample_id")}
        for sid in match_sample_ids:
            db["trial_match"].delete_many({"sample_id": sid})
        db["trial_match"].insert_many(trial_matches)
        print(f"  trial_match: inserted {len(trial_matches)} docs")

    print(f"  → {uri}/{db_name}")


def _run_preview(pt_path: str, matches_path: str, engine: str) -> None:
    from livereload import Server
    from ctm.reports.builder import BASE_DIR, render_html_from_pt_and_matches

    output_dir = BASE_DIR / "output"
    output_file = output_dir / "report.html"

    def build():
        output_dir.mkdir(exist_ok=True)
        output_file.write_text(render_html_from_pt_and_matches(pt_path, matches_path, engine))

    build()
    server = Server()
    server.watch(str(BASE_DIR / "templates" / "*.html"), build)
    server.watch(str(BASE_DIR / "static" / "*.css"), build)
    server.watch(pt_path, build)
    server.watch(matches_path, build)
    server.serve(root=str(output_dir), port=5500, open_url_delay=1,
                 default_filename="report.html")


def main() -> None:
    _fix_macos_weasyprint_path()

    from ctm.reports.builder import BASE_DIR, render_html_from_pt_and_matches

    parser = argparse.ArgumentParser(prog="ctm-report")
    parser.add_argument("--out", metavar="PATH", default=None,
                        help="Output PDF path (default: output/report.pdf)")
    parser.add_argument("--pt", dest="pt_path", metavar="PATH", required=True,
                        help="Normalized patient JSON from ctm-mm patients")
    parser.add_argument("--matches", metavar="PATH", required=True,
                        help="Match results JSON from the match engine")
    parser.add_argument("--engine", metavar="ENGINE", required=True,
                        help=f"Match engine that produced --matches (one of: {', '.join(_KNOWN_ENGINES)})")
    parser.add_argument("--preview", action="store_true",
                        help="Spin up livereload server instead of building PDF")
    parser.add_argument("--db", metavar="NAME",
                        help="Also write patient + match data to MongoDB database NAME")
    args = parser.parse_args()

    if args.engine not in _KNOWN_ENGINES:
        parser.error(f"--engine must be one of: {', '.join(_KNOWN_ENGINES)}")

    if args.db:
        print(f"Writing to MongoDB ({args.db}) ...")
        _write_to_mongo(args.pt_path, args.matches, args.db)

    if args.preview:
        _run_preview(args.pt_path, args.matches, args.engine)
        return

    from weasyprint import HTML

    html = render_html_from_pt_and_matches(args.pt_path, args.matches, args.engine)
    output_path = Path(args.out) if args.out else BASE_DIR / "output" / "report.pdf"
    output_path.parent.mkdir(exist_ok=True, parents=True)
    HTML(string=html).write_pdf(str(output_path))
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
