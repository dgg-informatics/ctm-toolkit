# Single Report-Building Path Consolidation
**Date:** 2026-07-13
**Status:** Approved

## Problem

`builder.py` grew four parallel ways to build report HTML, each sourcing
patient/match data differently:

| Path | Patient source | Match source | Used by |
|---|---|---|---|
| A | ad-hoc mock JSON | ad-hoc mock JSON | `render_html()` — already dead code (function doesn't exist, but `build_pdf.py`/`preview.py` still import it) |
| B | Excel workbook directly | single-patient mm_export envelope | `render_html_from_sources()` — `build_pdf.py`/`preview.py` |
| C | normalized JSON (`ctm-mm patients`) | single-patient mm_export envelope | `render_html_from_pt_and_matches()` — the real production `ctm-report` CLI |
| D | normalized JSON (`extras.patients`) | flat multi-patient `trial_match` list | `render_html_from_fixture_bundle()` — added for the fixture-driven work, fixture-only |

B and C's match data is the same underlying `trial_match` Mongo fields as D,
just packaged differently — a single-patient envelope wrapper vs. a flat
multi-patient list. There's no real reason for both shapes to exist, and
having four orchestrators invites the report to look different depending on
which one happened to get called. The goal: one input shape (patient
collection + trial collection + match collection, matching real MatchMiner
Mongo collections), one orchestrator, one CLI.

## Approach

Collapse to path D's shape, generalized beyond fixtures since it becomes the
real production path too. Delete A, B, and C outright rather than keeping
them as unconsolidated alternatives — partial consolidation would leave the
CLI implying multiple valid shapes.

### Deleted

- `load_context()`, `load_context_from_raw_excel()`,
  `load_context_from_mm_matches()`, `render_html_from_sources()`,
  `render_html_from_pt_and_matches()`, `_build_reports_context()` (only used
  by the Excel path) — all from `builder.py`.
- `build_pdf.py`, `preview.py` (repo root) — both already import the
  nonexistent `render_html`, and both are fully redundant with `ctm-report`'s
  PDF-write and `--preview` livereload behavior once it's updated.
- `tests/fixtures/mm_export_7439568.json` — envelope-shaped, incompatible
  with the flat-only model. Dependent tests in `test_builder.py` for
  `load_context_from_mm_matches`, `load_context_from_raw_excel`,
  `render_html_from_sources`, `render_html_from_pt_and_matches` are removed.

### Kept / renamed

- `load_context_from_normalized_json(pts_path, sample_id)` — unchanged.
- `load_context_from_flat_matches(matches, sample_id, trials_by_protocol)` —
  unchanged.
- `render_html_from_fixture_bundle` → renamed
  **`render_html_from_pt_trials_matches(pts_path, trials_path, matches_path, sample_id)`**
  — drops the "fixture" framing since this is now the one production path,
  not a test-only one. Behavior unchanged.

### `ctm-report` CLI

`report_cli.py` changes from:

```
ctm-report --pt PATH --matches PATH --engine ENGINE [--preview] [--out PATH]
```

to:

```
ctm-report --pts PATH --trials PATH --matches PATH --sample-id ID [--preview] [--out PATH]
```

`--engine`/`_KNOWN_ENGINES` go away — there's only one shape now, so nothing
to select between. `_run_preview` and the PDF-write path both call
`render_html_from_pt_trials_matches` instead.

### Test migration

Two `load_context_from_mm_matches` tests cover branches not exercised by
`test-matches-v0.0.1.json` (which has no `arm`-level matches) and aren't tied
to `mm_export_7439568.json`'s content — they build synthetic single-entry
data inline. These are rewritten against `load_context_from_flat_matches`
instead of retired, so `_select_primary_match`'s arm-vs-step and
empty-input branches stay covered:

- empty matches list → `primary_match is None`, `other_matches == []`
- a single step-level match, no arm-level match present → falls back to it
  as primary (this is really testing "no crash / correct fallback when no
  arm match exists," not literally "arm beats step," which patient `"8"`'s
  genomic-beats-clinical case in `test_report_from_fixtures.py` already
  covers from the reason_type angle)

## Testing

- `uv run pytest -q` — full suite green after the rewrite/removal, no drop
  in meaningful coverage (the two preserved edge cases move to
  `test_builder.py` rewritten against the flat-list API; primary-match
  selection is otherwise covered by `test_report_from_fixtures.py`'s
  patient `"8"` case).
- Manually confirm `ctm-report --pts ... --trials ... --matches ...
  --sample-id 8` produces the same HTML as the direct
  `render_html_from_pt_trials_matches()` call already verified in the prior
  round.

## Out of scope

- Deleting the now-orphaned `data/mock/`/`data/real/` static JSON files that
  only `load_context()` read — harmless leftover data, not code, left for
  the user to clean up if desired.
- Any change to `ctm-mm`, `ctm-ctml`, `ctm-fetch`, `ctm-meta` CLIs.
