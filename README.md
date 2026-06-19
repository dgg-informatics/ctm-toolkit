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
data/dump/raw/               ← unmodified source files (XML, PDF, XLSX, DOCX)
data/dump/manually_processed/ ← hand-transcribed from sources that can't be exported
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

## Data sources: mock vs. real

Both scripts default to `data/mock/`, which holds synthetic data safe to
commit to git. Real patient data goes in `data/real/` (gitignored — never
committed) using the same four filenames: `sample_match.json`,
`other_matches.json`, `similar_patients.json`, `patient_detail.json`. Pass
`--real` to load from there instead:

```bash
./.venv/bin/python preview.py --real
./.venv/bin/python build_pdf.py --real
```

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

- `data/mock/` — synthetic data, safe to commit: `sample_match.json` is a
  real-shaped sample trial-match blob, the rest are placeholder data for the
  other-matches, similar-patients, and extended-patient-data sections (no
  real schema for those yet)
- `data/real/` — gitignored; drop real patient data here using the same
  filenames as `data/mock/`
- `templates/` — `report.html` is the base page, `_*.html` are the per-section
  includes
- `static/report.css` — shared styling, including the `@page` rule for PDF
  page size/margins
- `src/ctm/reports/builder.py` — loads the JSON data and renders the Jinja2 template
  to an HTML string
- `preview.py` — live-reload dev server
- `build_pdf.py` — renders the HTML and exports it to PDF via WeasyPrint
