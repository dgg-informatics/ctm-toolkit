# Fixture-Driven Report Design
**Date:** 2026-07-13
**Status:** Approved

## Problem

We want to build (and test) a report directly from the versioned test fixtures —
`test-pts-v0.0.1.json`, `test-trials-v0.0.1.json`, `test-matches-v0.0.1.json` —
instead of the single-patient mock/Excel/mm-export paths `builder.py` currently
supports. This is the first step toward a growing corpus (eventually ~100
patients × ~100 trials) that exercises real, manually-validated match data.

Two things block this today:

1. **`extras` is single-patient, but not because the data is missing.**
   `load_context_from_normalized_json` reads `extras.patient` — a single
   object. It's populated by `mm_cli.py`'s `_build_extras()`, which does
   `patient = patients[0]` — hardcoded to the first row — before building
   `extras`. The source Excel this fixture was generated from
   (`data/dump/08Jul26/test-pts-v0.0.1.xlsx`, gitignored) has a `pt_general`
   sheet with full name/entity/metastasis-site data for all 9 patients (e.g.
   `SAMPLE_ID "8"` is "Maria Emo", AMC, pancreatic adenocarcinoma). The
   `clinical`/`genomic` collections in `test-pts-v0.0.1.json` correctly loop
   over all patients when generated; `extras` doesn't, and silently drops
   everyone but patient `"1"` ("Spider Man"). This is a real bug in
   `_build_extras()`, not a fixture gap — fixed and regenerated below.
2. **No trial-record input.** `render_html_from_pt_and_matches` never reads a
   trials file. Trial name/therapy in the rendered report are hardcoded mock
   strings (`_row("Trial Name", "EGFR-TKI Resistance Combination Study")`).
   `test-trials-v0.0.1.json` has real `_summary` data per trial (e.g. for
   `2021.070`/`NCT04858334`: `long_title` "APOLLO: A Randomized Phase II
   Double-Blind Study of Olaparib versus Placebo...", `phase` "Phase II",
   `investigator.full_name` "Enzler, Thomas") that should replace those mocks.

## Approach

Add a new, parallel code path in `builder.py` for the fixture-driven case.
Existing paths (`load_context`, `load_context_from_raw_excel`,
`load_context_from_mm_matches`, `render_html_from_sources`,
`render_html_from_pt_and_matches`) are untouched in behavior, aside from the
`_build_extras`/`extras` shape fix below (which they consume unchanged, via a
new optional parameter with a backward-compatible default).

### Fix `_build_extras()` and regenerate the pts fixture

`_build_extras(patients, metadata, findings)` in `mm_cli.py` currently
returns `{"patient": {...}, "reports": [...]}` for `patients[0]` only. Change
it to loop over every patient and key by `SAMPLE_ID` (`patient.mrn or
str(patient.pt_uuid)`, the same join key documented in the June 23 pipeline
spec):

```
{"patients": {"<SAMPLE_ID>": {"patient": {...}, "reports": [...]}, ...}}
```

`load_context_from_normalized_json(pt_path, sample_id=None)` gains an
optional `sample_id` param: if given, look up `extras["patients"][sample_id]`;
if omitted, fall back to the first entry (preserves today's behavior for the
existing single-patient callers, which never pass one).

Then regenerate `tests/fixtures/test-pts-v0.0.1.json` by rerunning `ctm-mm
patients` against `data/dump/08Jul26/test-pts-v0.0.1.xlsx` with the fixed
code, and merge in the hand-added `SAMPLE_ID "10"` all-null edge-case row
(not present in the source xlsx, used only for null-handling shape tests —
not referenced by any match, so dropping it isn't a matching-behavior risk,
but it's kept to preserve existing test coverage).

### Trimming

Given a `sample_id`, the report is built from a bundle trimmed to just that
patient's data:

- **Patient**: `extras.patients[sample_id]` (name, entity, diagnosis,
  metastasis sites, reports) via the fixed `load_context_from_normalized_json`.
- **Matches**: every match doc in `test-matches` for that `sample_id` — no
  pre-dedup. (`test-matches` can contain genuine duplicate docs, e.g. patient
  `"8"`/trial `2021.070` has two byte-identical `clinical` match docs plus one
  `genomic` doc; dedup already happens downstream in `_build_other_matches`
  by `protocol_no`, so we don't hide that reality by filtering earlier.)
- **Trials**: only the trial records referenced by those matches'
  `protocol_no`s (e.g. 4 of the 15 trials for patient `"8"`).

## Architecture

### New/changed functions in `builder.py`

```
load_context_from_normalized_json(pt_path: str, sample_id: str | None = None) -> dict
    CHANGED: new optional sample_id param, reads extras["patients"][sample_id]
    (falls back to the first patient if omitted — existing callers unaffected).

load_context_from_flat_matches(matches: list[dict], sample_id: str, trials_by_protocol: dict) -> dict
    Filters test-matches to sample_id + show_in_ui=True.
    Reuses existing _select_primary_match / _build_other_matches /
    _build_primary_match_context — their field expectations already match
    test-matches-v0.0.1.json's shape.
    NEW: _build_primary_match_context and _build_other_matches take an
    optional trial lookup (protocol_no -> trial dict) and pull
    short_title/phase/investigator/disease_keywords from trial["_summary"]
    to replace the hardcoded mock trial-name/therapy strings.

render_html_from_fixture_bundle(pts_path: str, trials_path: str, matches_path: str, sample_id: str) -> str
    Loads all three files, trims to sample_id, builds full context via the
    two functions above, renders through the existing Jinja template/CSS
    (same pattern as the other render_html_from_* orchestrators).
```

### Primary-match selection worked example (patient `"8"`)

`test-matches-v0.0.1.json` has 6 docs for `sample_id "8"` across 4 trials.
Trial `2021.070`/`NCT04858334` has 3 of them: two identical `clinical` docs
(`sort_order [1,99,99,99,99,99]`) and one `genomic` doc (`BRCA2`,
`sort_order [1,99,1,99,99,99]`). Running the existing `_select_primary_match`
priority — `(match_level, reason_type, sort_order)` — by hand: every doc ties
on `match_level="step"`, but the genomic doc wins outright on `reason_type`
regardless of `sort_order`. So:

- **Primary match**: `2021.070` / `NCT04858334`, via the genomic `BRCA2` reason.
- **Other matches** (deduped by `protocol_no`, primary's own protocol
  excluded): `2015.063`, `2019.058`, `2021.045` — exactly 3.

This is a real, known-correct assertion pulled from manually-validated data,
not a guess — it's what the test in the next section checks.

## Testing

New file `tests/test_report_from_fixtures.py`, scoped to `sample_id "8"`:

1. **Unit-level**: `load_context_from_flat_matches` on the real fixture data
   asserts `primary_match["nct_id"] == "NCT04858334"` and
   `primary_match["reason_type"] == "genomic"`, and `other_matches` contains
   exactly `{"2015.063", "2019.058", "2021.045"}`.
2. **Trial-data threading**: primary match's trial-name/phase fields reflect
   `test-trials-v0.0.1.json`'s `NCT04858334` record's `_summary`, not the old
   mock strings.
3. **Full-render smoke check**: `render_html_from_fixture_bundle(...)` doesn't
   raise, returns non-empty HTML, and contains a few expected strings
   (diagnosis, primary NCT ID).

## Out of scope

- The `render_html` import in `build_pdf.py`/`preview.py` pointing at a
  function that no longer exists in `builder.py` — pre-existing dead code,
  unrelated to this change.
- Versioning contract / semver rules across fixture files, and an opt-in
  live-matchengine integration tier — both discussed and deliberately
  deferred in earlier rounds.
