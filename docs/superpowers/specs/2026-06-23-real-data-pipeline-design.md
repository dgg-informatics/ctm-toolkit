# Real Data Pipeline Design
**Date:** 2026-06-23
**Status:** Approved

## Problem

Three gaps prevent generating reports from real data:

1. **No real match data** — `data/mock/matches.json` is hand-crafted. Real trial_match docs live in MongoDB and are exported via `matchengine-V2/matchengine/tests/data/version1/export_matches.py`.
2. **Data loss in Raw → normalized path** — Raw finding models use `extra='ignore'` (Pydantic default), silently dropping Excel columns that aren't declared. Field name mismatches (e.g. `raw_test` in Excel vs `raw_biomarker` in model) compound this.
3. **`PatientGeneral` processed model drops fields** — `first_name`, `last_name`, `entity`, `metastasis_sites` from the Excel `Patient` model never reach the report context.

## Approach

Approach C: two new focused functions in `builder.py`. The existing mock path (`load_context()`) is untouched. No changes to `pipeline.py`.

## Architecture

### New functions in `builder.py`

```
load_context_from_raw_excel(excel_path) -> dict
    Owns the Excel side.
    Returns partial context: patient_header, patient_detail, reports.

load_context_from_mm_matches(mm_export_path) -> dict
    Owns the matchminer side.
    Returns partial context: primary_match, other_matches.

render_html(excel_path, mm_export_path) [new overload]
    Calls both, merges the two partial dicts, renders template.
```

Each function does one thing. Neither raises — they return partial/empty dicts on failure.

### Join key

`trial_match["sample_id"] == patient.mrn`

Established by `mm_cli.py`: `SAMPLE_ID = patient.mrn or str(pt_uuid)`.

## Data Flow

### `load_context_from_raw_excel(excel_path)`

1. Call `read_and_normalize(excel_path)` → `(patients, metadata, findings)`
2. Map all `Patient` fields → `patient_header` rows (MRN, name, DOB, sex, vital status, entity, diagnosis)
3. Map remaining `Patient` fields → `patient_detail` rows (any additional columns added to `pt_general` sheet in future flow through automatically via the field map)
4. Map `ReportMetadata` + `Finding` list → `reports` context; pass `finding.raw` through intact so all source-specific fields are available to the template

No `clinical_detail` sub-object. All patient data comes from the `Patient` model. New columns added to `pt_general` in Excel → add to `RawPatientGeneral` + `Patient` → update field map in `builder.py` to surface in report.

### `load_context_from_mm_matches(mm_export_path)`

1. Load JSON → `{clinical, genomic, trial_match}`
2. Filter `trial_match` to `show_in_ui=True`
3. Select primary match:
   - Prefer `match_level="arm"` over `"step"`
   - Among arm matches, prefer `reason_type="genomic"` over `"clinical"`
   - Tiebreak: lowest `sort_order`
   - Fall back to step-level if no arm match exists
4. Build `other_matches` — flat list of dicts `{protocol_no, nct_id, match_level, match_type, genomic_alteration, source}`, one entry per unique `protocol_no` excluding the primary
5. Return `{primary_match, other_matches}`

`other_matches` carries a `source` field (initially `"matchminer"`) so future regional and ClinicalTrials.gov entries can be folded in and optionally distinguished by the template.

## Raw Model Fix (Data Loss Root Cause)

### 1. Add `extra='allow'` to all Raw finding models

```python
from pydantic import BaseModel, ConfigDict

class RawTempusFinding(BaseModel):
    model_config = ConfigDict(extra='allow')
    ...
```

Apply to: `RawTempusFinding`, `RawCarisFinding`, `RawAmbryFinding`, `RawAmcNgsFinding`, `RawOgmFinding`, `RawPmlRaraFinding`, `RawTumorBiomarker`.

With `extra='allow'`, `model_dump()` includes undeclared Excel columns. `_raw_fields()` in `normalize_manual.py` already filters for `raw_*` prefix — no change needed there.

### 2. Reconcile mismatched field names

Audit each Raw model against the actual Excel column headers and rename model fields to match. Known mismatches in `RawTempusFinding`:

| Excel column | Current model field | Action |
|---|---|---|
| `raw_test` | `raw_biomarker` | Rename model field → `raw_test` |
| `raw_therapies_other_indications` | `raw_therapies_other` | Rename model field |
| `raw_title` | _(not declared)_ | Will be captured via `extra='allow'` — no rename needed |

Audit all other finding models for similar mismatches before implementing.

## Template Changes

- Remove `_regional_matches.html` and `_ctg_matches.html` partials
- Add `_other_matches.html` — renders `other_matches` as a single flat list
- Update `report.html` to include `_other_matches.html` in place of the two removed partials
- Update `PATIENT_HEADER_FIELDS` and `PATIENT_DETAIL_FIELDS` in `builder.py` to include `first_name`, `last_name`, `entity`

## CLI Wire-up

Add `--excel` and `--mm-export` flags to `preview.py`:

```
python preview.py --excel data/raw/patient_data_template.xlsx \
                  --mm-export path/to/7439568.json
```

When both flags are present, call `load_context_from_raw_excel()` + `load_context_from_mm_matches()`. When absent, fall back to existing `load_context()` (mock).

## Error Handling

- Row validation failures in `excel_reader.py` — already print warning and skip; no change needed
- `load_context_from_mm_matches()` — empty `trial_match` returns `{primary_match: None, other_matches: []}`, report renders with no matches section
- No arm-level match → fall back to best step-level match
- Neither loader raises — partial context is always returned

## Testing

| Test | What it covers |
|---|---|
| `test_load_context_from_raw_excel` | Fixture Excel → assert all Patient fields present, `finding.raw` contains raw_ columns |
| `test_load_context_from_mm_matches` | Fixture export JSON → assert primary is arm-level genomic, other_matches is remainder |
| `test_load_context_from_mm_matches_no_arm` | Export with only step-level matches → assert step-level match selected as primary |
| `test_raw_models_extra_allow` | `RawTempusFinding.model_validate({..., "raw_test": "x"})` → `raw_test` in `model_dump()` |
| `test_raw_fields_no_loss` | Excel row with extra raw_ column → appears in `Finding.raw` after full normalize pass |

## TODO (out of scope)

- Fold regional institutional trials into `other_matches` (add `source="regional"`)
- Fold ClinicalTrials.gov matches into `other_matches` (add `source="clinicaltrials_gov"`)
- Enrich primary match genomic fields (allele_fraction, tier, chromosome) by joining with `Finding.raw` on gene name — matchminer doesn't store these
