"""ctm-report — build a trial match report (PDF or live preview).

Usage:
  ctm-report --pts pts.json --trials trials.json --matches matches.json --sample-id ID [--preview] [--out PATH]
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


def _run_preview(pts_path: str, trials_path: str, matches_path: str, sample_id: str) -> None:
    from livereload import Server
    from ctm.reports.builder import BASE_DIR, render_html_from_pt_trials_matches

    output_dir = BASE_DIR / "output"
    output_file = output_dir / "report.html"

    def build():
        output_dir.mkdir(exist_ok=True)
        output_file.write_text(render_html_from_pt_trials_matches(pts_path, trials_path, matches_path, sample_id))

    build()
    server = Server()
    server.watch(str(BASE_DIR / "templates" / "*.html"), build)
    server.watch(str(BASE_DIR / "static" / "*.css"), build)
    server.watch(pts_path, build)
    server.watch(trials_path, build)
    server.watch(matches_path, build)
    server.serve(root=str(output_dir), port=5500, open_url_delay=1,
                 default_filename="report.html")


def main() -> None:
    _fix_macos_weasyprint_path()

    from ctm.reports.builder import BASE_DIR, render_html_from_pt_trials_matches

    parser = argparse.ArgumentParser(prog="ctm-report")
    parser.add_argument("--out", metavar="PATH", default=None,
                        help="Output PDF path (default: output/report.pdf)")
    parser.add_argument("--pts", dest="pts_path", metavar="PATH", required=True,
                        help="Patient collection JSON from ctm-mm patients")
    parser.add_argument("--trials", dest="trials_path", metavar="PATH", required=True,
                        help="Trial collection JSON from ctm-mm trials")
    parser.add_argument("--matches", dest="matches_path", metavar="PATH", required=True,
                        help="Flat trial_match collection JSON from the match engine")
    parser.add_argument("--sample-id", dest="sample_id", metavar="ID", required=True,
                        help="SAMPLE_ID of the patient to build the report for")
    parser.add_argument("--preview", action="store_true",
                        help="Spin up livereload server instead of building PDF")
    args = parser.parse_args()

    if args.preview:
        _run_preview(args.pts_path, args.trials_path, args.matches_path, args.sample_id)
        return

    from weasyprint import HTML

    html = render_html_from_pt_trials_matches(args.pts_path, args.trials_path, args.matches_path, args.sample_id)
    output_path = Path(args.out) if args.out else BASE_DIR / "output" / "report.pdf"
    output_path.parent.mkdir(exist_ok=True, parents=True)
    HTML(string=html).write_pdf(str(output_path))
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
