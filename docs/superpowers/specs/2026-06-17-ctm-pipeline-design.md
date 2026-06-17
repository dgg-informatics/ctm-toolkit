# CTM Pipeline Design

**Date:** 2026-06-17  
**Status:** Draft  
**Scope:** Data ingestion, transformation, validation, MongoDB storage, and report generation for the CTM trial match workflow.

---

## Overview

This repo is a preparation and validation layer before data enters MatchMiner/TrialMatchAI. The pipeline accepts manually placed raw files, transforms them into normalized processed documents, stores everything in MongoDB (with run metadata), writes processed output to a dated run folder, and generates PDF trial match reports.

The two top-level goals:
1. **Validate data sources** — confirm raw files from heterogeneous sources conform to expected shapes before they reach downstream systems.
2. **Test report generation** — produce reports from both mock and real processed data.

---

## Repository Layout

```
ctm-report-preview/
├── src/
│   └── ctm/
│       ├── __init__.py
│       ├── cli.py                         # ctm-process entry point
│       ├── pipeline.py                    # orchestrates a full run
│       │
│       ├── transformers/
│       │   ├── __init__.py
│       │   ├── process_ct_data.py         # clinical trial data (JSON + Word)
│       │   ├── process_patient_general.py # patient demographics/clinical (JSON)
│       │   └── process_patient_genetic.py # patient genomic data (JSON)
│       │
│       ├── fetchers/
│       │   ├── __init__.py
│       │   └── fetch_similar_patients.py  # queries MongoDB, produces JSON
│       │
│       ├── schemas/
│       │   ├── __init__.py
│       │   ├── raw/
│       │   │   ├── clinical_trial.py      # expected raw CT input shape (comments)
│       │   │   ├── patient_general.py     # expected raw patient general shape
│       │   │   └── patient_genetic.py     # expected raw genomic shape
│       │   └── processed/
│       │       ├── clinical_trial.py      # Pydantic model for processed CT
│       │       ├── patient_general.py     # Pydantic model for processed patient
│       │       ├── patient_genetic.py     # Pydantic model for processed genomic
│       │       └── similar_patients.py    # Pydantic model for similarity results
│       │
│       ├── db/
│       │   ├── __init__.py
│       │   └── store.py                   # pymongo: insert/retrieve raw, processed, runs
│       │
│       └── reports/
│           ├── __init__.py
│           └── builder.py                 # report_builder.py moved here
│
├── data/
│   ├── raw/                               # drop input files here (gitignored)
│   │   ├── clinical_trials/              # JSON files or Word docs
│   │   ├── patient_general/              # JSON files
│   │   └── patient_genetic/              # JSON files
│   ├── mock/                              # existing synthetic data (kept for dev)
│   └── output/                            # gitignored
│       └── <DDMMMYYYY-XXXXXXXX>/         # one folder per run (see Run ID below)
│           ├── clinical_trials.json
│           ├── patient_general.json
│           ├── patient_genetic.json
│           ├── similar_patients.json
│           └── report.pdf
│
├── templates/                             # existing Jinja2 templates
├── static/                                # existing CSS
│
├── tests/
│   ├── conftest.py
│   ├── test_transformers.py
│   ├── test_fetchers.py
│   └── test_reports.py
│
├── preview.py                             # existing live-reload dev server
├── build_pdf.py                           # existing PDF builder
├── pyproject.toml                         # package def + ctm-process entry_point
└── requirements.txt
```

---

## Data Flow

```
data/raw/
  clinical_trials/   ─→ process_ct_data.py      ─┐
  patient_general/   ─→ process_patient_general.py─┤─→ MongoDB (raw + processed)
  patient_genetic/   ─→ process_patient_genetic.py─┤─→ data/output/<run-id>/
                                                    │
  MongoDB            ─→ fetch_similar_patients.py ─┘

data/output/<run-id>/ ─→ reports/builder.py ─→ report.pdf
```

Steps in order:
1. CLI parses `-i <path>` and generates a run ID.
2. `pipeline.py` scans the input path, dispatches each subfolder to the matching transformer or fetcher.
3. Each transformer: reads raw file(s), validates loosely against raw schema, returns a processed Pydantic model.
4. `store.py` inserts raw document + processed document + run metadata into MongoDB.
5. Processed documents are written as JSON to `data/output/<run-id>/`.
6. `reports/builder.py` renders the processed data into an HTML report and exports a PDF.

---

## CLI

```
ctm-process -i data/raw/
ctm-process -i data/raw/clinical_trials/NCT12345.json   # single file
ctm-process --mock                                        # use data/mock/ (dev mode)
ctm-process --no-report                                   # skip PDF generation
```

Defined in `pyproject.toml`:

```toml
[project.scripts]
ctm-process = "ctm.cli:main"
```

---

## Run ID

Format: `DDMMMYYYY-XXXXXXXX` where `XXXXXXXX` is 8 hex characters from `uuid4()`.

Example: `17Jun2026-a3f8c201`

The run ID:
- Names the output folder under `data/output/`
- Is stored on every MongoDB document written during that run
- Allows full reconstruction of any run's inputs and outputs

---

## MongoDB Collections

### `runs`
One document per `ctm-process` invocation.

```python
{
    "run_id": "17Jun2026-a3f8c201",
    "run_at": datetime,
    "input_path": str,
    "transformer_versions": {
        "process_ct_data": "0.1.0",
        "process_patient_general": "0.1.0",
        "process_patient_genetic": "0.1.0",
    },
    "status": "completed" | "failed",
    "output_path": str,
}
```

### `raw_documents`
One document per input file, before transformation.

```python
{
    "run_id": str,
    "source_type": "clinical_trial" | "patient_general" | "patient_genetic",
    "source_file": str,       # original filename
    "ingested_at": datetime,
    "raw": dict,              # the raw JSON as-is
}
```

### `processed_documents`
One document per transformed output.

```python
{
    "run_id": str,
    "source_type": str,
    "processed_at": datetime,
    "transformer_version": str,
    "data": dict,             # serialized Pydantic processed model
}
```

### `similar_patients`
Produced by `fetch_similar_patients` rather than a file transformer.

```python
{
    "run_id": str,
    "patient_id": str,
    "computed_at": datetime,
    "top_n": int,
    "matches": [{"patient_id": str, "similarity_score": float}],
}
```

---

## Transformers

Each transformer exports a `__version__` string and a `process()` function.

### `process_ct_data.py`

```python
__version__ = "0.1.0"

# Raw input (JSON form):
# {
#   "nct_id": str,
#   "title": str,
#   "phase": str,
#   "status": str,
#   "eligibility": { "criteria": str, ... },
#   "sponsor": str,
#   ...
# }
#
# Raw input (Word doc form): not yet handled — placeholder only.
#
# Processed output: see schemas/processed/clinical_trial.py

def process(raw: dict) -> dict:
    return {}  # TODO
```

### `process_patient_general.py`

```python
__version__ = "0.1.0"

# Raw input:
# {
#   "mrn": str,
#   "gender": str,
#   "dob": str,
#   "vital_status": str,
#   "oncotree_primary_diagnosis_name": str,
#   "tumor_mutational_burden_per_megabase": float,
#   ...
# }
#
# Processed output: see schemas/processed/patient_general.py

def process(raw: dict) -> dict:
    return {}  # TODO
```

### `process_patient_genetic.py`

```python
__version__ = "0.1.0"

# Raw input:
# {
#   "sample_id": str,
#   "mrn": str,
#   "true_hugo_symbol": str,
#   "true_protein_change": str,
#   "true_cdna_change": str,
#   "true_variant_classification": str,
#   "variant_category": str,
#   "allele_fraction": float,
#   "tier": int,
#   "chromosome": str,
#   "position": int,
#   ...
# }
#
# Processed output: see schemas/processed/patient_genetic.py

def process(raw: dict) -> dict:
    return {}  # TODO
```

---

## Fetchers

### `fetch_similar_patients.py`

```python
# Queries MongoDB processed_documents for existing patients,
# computes similarity (placeholder: returns top N by stub score),
# returns a list of SimilarPatient records.
#
# Output: see schemas/processed/similar_patients.py

def fetch(patient_id: str, top_n: int = 5) -> dict:
    return {}  # TODO
```

---

## Schemas

### Processed (Pydantic stubs — all fields optional for now)

```python
# schemas/processed/clinical_trial.py
from pydantic import BaseModel

class ClinicalTrial(BaseModel):
    nct_id: str | None = None
    title: str | None = None
    phase: str | None = None
    status: str | None = None
    # ... expand as fields are confirmed

# schemas/processed/patient_general.py
class PatientGeneral(BaseModel):
    mrn: str | None = None
    gender: str | None = None
    vital_status: str | None = None
    diagnosis: str | None = None
    tmb: float | None = None

# schemas/processed/patient_genetic.py
class PatientGenetic(BaseModel):
    sample_id: str | None = None
    mrn: str | None = None
    gene: str | None = None
    protein_change: str | None = None
    variant_category: str | None = None
    allele_fraction: float | None = None
    tier: int | None = None

# schemas/processed/similar_patients.py
class SimilarPatientMatch(BaseModel):
    patient_id: str
    similarity_score: float

class SimilarPatients(BaseModel):
    patient_id: str
    top_n: int
    matches: list[SimilarPatientMatch]
```

---

## Testing

Tests use `pytest`. MongoDB interactions use a real local MongoDB instance (not mocked) to catch schema/query issues early.

- `test_transformers.py` — feed each transformer a sample raw dict, assert output matches processed schema shape
- `test_fetchers.py` — insert stub processed docs, call `fetch_similar_patients`, assert output structure
- `test_reports.py` — feed processed mock data to `reports/builder.py`, assert HTML renders without error

---

## Configuration

MongoDB connection string is read from the `CTM_MONGO_URI` environment variable, defaulting to `mongodb://localhost:27017` for local dev. Database name: `ctm`. Set in a `.env` file (gitignored) and loaded at CLI startup.

---

## Tech Stack

| Concern | Choice |
|---|---|
| Language | Python 3.11+ |
| Packaging | `pyproject.toml` + `src/` layout |
| Schema/validation | Pydantic v2 |
| MongoDB driver | pymongo |
| Report rendering | Jinja2 + WeasyPrint |
| Testing | pytest |
| Live preview | livereload (existing) |

---

## Not in Scope (this phase)

- Automated data fetching (all ingestion is manual file placement)
- Word doc parsing (stubbed, not implemented)
- Similarity algorithm (fetch_similar_patients returns a stub)
- Authentication / access control on MongoDB
- Report template changes (existing templates remain as-is)
