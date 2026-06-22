# CTM Trial Match Report

A data preparation and validation layer that normalizes patient, genetic, and clinical
trial data before it enters a trial matching engine (MatchMiner or TrialMatchAI). Also
generates single-page trial-match reports (Jinja2 + WeasyPrint) from the matching
engine output, previewable live in the browser and exportable to PDF.

## Data pipeline

```
Sources (files or manual transcription)
        │
        ▼
data/test/                   ← source files (XML, PDF, XLSX, DOCX) and hand-transcribed data
        │
        │  minimal massaging
        ▼
data/normalized/             ← engine-agnostic raw schema; stored in MongoDB
        │
        │  transformer (one per engine)
        ├──→ MatchMiner format
        └──→ TrialMatchAI format
                │
                ▼
        matching engine
                │
                ▼
        match results  ──→  report.pdf
```

**Schema levels:**
- `schemas/raw/` — engine-agnostic intermediate schema: minimal massaging, covers all
  fields we want to preserve, MongoDB-friendly. This is the stable record.
- `schemas/processed/` — Pydantic models for the fully transformed, engine-ready output.
  One transformer module per engine.

**Why two layers?** Separating normalization from engine-specific transformation means new
engines can be added without re-ingesting source data. The normalized layer is the single
source of truth.

## Setup

```bash
uv pip install -r requirements.txt --python .venv/bin/python
```

On macOS, WeasyPrint also needs the native Pango library, which isn't installed
by default:

```bash
brew install pango
```

## Data directories

| Directory | Purpose |
|-----------|---------|
| `data/mock/` | Synthetic data, safe to commit — used for local development and report preview |
| `data/real/` | Real patient data (gitignored) — drop files here to normalize and ingest |
| `data/test/` | Source files and hand-transcribed data (gitignored) — input to the normalization pipeline |
| `data/normalized/` | Engine-agnostic intermediate output (gitignored) — stored in MongoDB |

Pass `--real` to load real data instead of mock:

```bash
./.venv/bin/python preview.py --real
./.venv/bin/python build_pdf.py --real
```

## Mock data files

| File | Purpose |
|------|---------|
| `patient.json` | Patient identity + clinical detail |
| `reports.json` | Lab reports with findings embedded |
| `matches.json` | Primary match + other matches (regional + CTG) |
| `similar_patients.json` | DB storage artifact |
| `methods.json` | Methods + disclaimer paragraphs |

## Spin up the live preview

```bash
./.venv/bin/python preview.py
```

This opens a browser tab at `http://localhost:5500/report.html`. Any edit to a
template in `templates/`, the stylesheet in `static/report.css`, or the active
data directory (`data/mock/` or `data/real/`, depending on the flag)
automatically re-renders the report and refreshes the page.

## Download the PDF

```bash
./.venv/bin/python build_pdf.py
```

Writes `output/report.pdf`. Pass a custom path as an argument to write
elsewhere:

```bash
./.venv/bin/python build_pdf.py ~/Desktop/report.pdf --real
```

## Project layout

- `data/` — see Data directories table above
- `templates/` — `report.html` is the base page, `_*.html` are the per-section includes
- `static/report.css` — shared styling, including the `@page` rule for PDF page size/margins
- `src/ctm/reports/builder.py` — loads JSON data and renders the Jinja2 template to HTML
- `preview.py` — live-reload dev server
- `build_pdf.py` — renders the HTML and exports it to PDF via WeasyPrint
