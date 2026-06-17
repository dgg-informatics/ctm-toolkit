"""Dev preview server: re-renders the report whenever a template, data file,
or stylesheet changes, and auto-refreshes the browser. Run with:

    python preview.py [--real | --mock]
"""
import argparse

from livereload import Server

from ctm.reports.builder import BASE_DIR, render_html

OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_FILE = OUTPUT_DIR / "report.html"


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--real", action="store_true", help="load data from data/real/")
    source.add_argument("--mock", action="store_true", help="load data from data/mock/ (default)")
    args = parser.parse_args()

    data_dir = BASE_DIR / "data" / ("real" if args.real else "mock")

    def build():
        OUTPUT_DIR.mkdir(exist_ok=True)
        OUTPUT_FILE.write_text(render_html(use_real=args.real))

    build()
    server = Server()
    server.watch(str(BASE_DIR / "templates" / "*.html"), build)
    server.watch(str(data_dir / "*.json"), build)
    server.watch(str(BASE_DIR / "static" / "*.css"), build)
    server.serve(root=str(OUTPUT_DIR), port=5500, open_url_delay=1, default_filename="report.html")


if __name__ == "__main__":
    main()
