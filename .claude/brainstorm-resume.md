# Brainstorming Resume — Real Data Pipeline

**Status:** Design in progress — approach C approved, decomposition approved, matches simplification approved.

## Approved Decisions

- **Approach C:** Two new functions in `builder.py`, mock path unchanged
- **Decoupled functions:**
  - `load_context_from_raw_excel(excel_path)` — Excel side: patient header, patient detail, reports context
  - `load_context_from_mm_matches(mm_export_path)` — matchminer side: primary_match + other_matches
  - `render_html()` calls both, merges partial dicts
- **Simplified matches structure:**
  - `primary_match` — single best match (arm-level, genomic reason, lowest sort_order)
  - `other_matches` — flat list of remaining unique protocol_nos (no regional/CTG split)
  - TODO: future — fold in regional trials + ClinicalTrials.gov into `other_matches` with a `source` field
- **Style:** Uncle Bob clean code — one function does one thing

## Key Facts

- **Join:** `trial_match.sample_id == patient.mrn`
- **Primary match selection:** prefer `match_level="arm"` + `reason_type="genomic"`; lowest `sort_order`; `combo_coord` groups related docs
- **Missing from matchminer:** `allele_fraction`, `tier`, `chromosome`, `position` — come from Excel `Finding.raw`, join by gene name
- **Data loss location:** `PatientGeneral` processed model drops `first_name`, `last_name`, `entity`, `metastasis_sites` — fix is in `load_context_from_raw_excel`
- **Matchminer export format:** `{clinical: {...}, genomic: [...], trial_match: [...]}` — from `export_matches.py`
- **Excel reader:** `read_and_normalize(excel_path)` → `(patients, metadata, findings)`
- **`Finding.raw`** preserves all `raw_*` fields from Excel

## Next Design Sections to Present

1. Data flow inside each function (reading, joining, selection logic)
2. Error handling
3. Testing approach
4. Template changes (if any — removing regional_matches/ctg_matches keys)
5. CLI wire-up in `preview.py`

## After Design Approval

Write spec to `docs/superpowers/specs/2026-06-23-real-data-pipeline-design.md`, then invoke `writing-plans` skill.
