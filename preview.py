"""Dev preview server: re-renders the report whenever a template, data file,
or stylesheet changes, and auto-refreshes the browser.

Usage:
    python preview.py [--mock]
    python preview.py --excel PATH --mm-export PATH
"""
import argparse

from livereload import Server

from ctm.reports.builder import BASE_DIR, render_html, render_html_from_sources

OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_FILE = OUTPUT_DIR / "report.html"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="load data from data/mock/ (default)")
    parser.add_argument("--excel", metavar="PATH", help="Path to patient_data_template.xlsx")
    parser.add_argument("--mm-export", dest="mm_export", metavar="PATH",
                        help="Path to matchminer export JSON")
    args = parser.parse_args()

    use_real_sources = bool(args.excel and args.mm_export)

    def build():
        OUTPUT_DIR.mkdir(exist_ok=True)
        if use_real_sources:
            OUTPUT_FILE.write_text(render_html_from_sources(args.excel, args.mm_export))
        else:
            OUTPUT_FILE.write_text(render_html(use_real=False))

    build()
    server = Server()
    server.watch(str(BASE_DIR / "templates" / "*.html"), build)
    server.watch(str(BASE_DIR / "static" / "*.css"), build)
    if use_real_sources:
        server.watch(args.mm_export, build)
        server.watch(args.excel, build)
    else:
        server.watch(str(BASE_DIR / "data" / "mock" / "*.json"), build)
    server.serve(root=str(OUTPUT_DIR), port=5500, open_url_delay=1,
                 default_filename="report.html")


if __name__ == "__main__":
    main()
