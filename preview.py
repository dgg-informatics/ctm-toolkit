"""Dev preview server: re-renders the report whenever a template, data file,
or stylesheet changes, and auto-refreshes the browser. Run with:

    python preview.py
"""
from livereload import Server

from report_builder import BASE_DIR, render_html

OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_FILE = OUTPUT_DIR / "report.html"


def build():
    OUTPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(render_html())


if __name__ == "__main__":
    build()
    server = Server()
    server.watch(str(BASE_DIR / "templates" / "*.html"), build)
    server.watch(str(BASE_DIR / "data" / "*.json"), build)
    server.watch(str(BASE_DIR / "static" / "*.css"), build)
    server.serve(root=str(OUTPUT_DIR), port=5500, open_url_delay=1)
