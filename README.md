# CTM Trial Match Report

Generates a single-page trial-match report (Jinja2 + WeasyPrint) from a clinical
trial matching JSON blob, previewable live in the browser and exportable to PDF.

## Setup

```bash
uv pip install -r requirements.txt --python .venv/bin/python
```

On macOS, WeasyPrint also needs the native Pango library, which isn't installed
by default:

```bash
brew install pango
```

## Spin up the live preview

```bash
./.venv/bin/python preview.py
```

This opens a browser tab at `http://localhost:5500/report.html`. Any edit to a
template in `templates/`, the stylesheet in `static/report.css`, or the data
files in `data/` automatically re-renders the report and refreshes the page.

## Download the PDF

```bash
./.venv/bin/python build_pdf.py
```

Writes `output/report.pdf`. Pass a custom path as an argument to write
elsewhere:

```bash
./.venv/bin/python build_pdf.py ~/Desktop/report.pdf
```

## Project layout

- `data/` — `sample_match.json` is the real trial-match blob; the `mock_*.json`
  files are placeholder data for the other-matches, similar-patients, and
  extended-patient-data sections (no real schema for those yet)
- `templates/` — `report.html` is the base page, `_*.html` are the per-section
  includes
- `static/report.css` — shared styling, including the `@page` rule for PDF
  page size/margins
- `report_builder.py` — loads the JSON data and renders the Jinja2 template
  to an HTML string
- `preview.py` — live-reload dev server
- `build_pdf.py` — renders the HTML and exports it to PDF via WeasyPrint
