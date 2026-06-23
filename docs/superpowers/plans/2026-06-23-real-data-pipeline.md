# Real Data Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire real matchengine trial_match data and Excel patient data into the report builder, replacing mock JSON files.

**Architecture:** Two new focused functions in `builder.py` — `load_context_from_raw_excel()` and `load_context_from_mm_matches()` — each return a partial context dict that `render_html_from_sources()` merges and renders. Raw finding models gain `extra='allow'` so no Excel column is silently dropped.

**Tech Stack:** Python 3.11, Pydantic v2, openpyxl, Jinja2, pytest

## Global Constraints

- Follow Uncle Bob clean code style: one function does one (maybe two) things
- All tests use `pytest`; run from repo root with `.venv/bin/python -m pytest`
- Never modify `load_context()` or `render_html()` — mock path stays untouched
- All new code lives in `src/ctm/`; tests in `tests/`
- Pydantic v2 syntax throughout (`model_config = ConfigDict(...)`, `model_dump()`, `model_validate()`)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/ctm/transformers/process_ct_data.py` | Modify | Fix broken import (pre-existing bug) |
| `src/ctm/schemas/raw/models.py` | Modify | Add `extra='allow'`; rename mismatched fields |
| `src/ctm/reports/builder.py` | Modify | Add two loader functions + `render_html_from_sources()` |
| `templates/_other_matches.html` | Rewrite | Render flat `other_matches` list |
| `templates/report.html` | Modify | Swap regional/ctg includes for `_other_matches.html` |
| `templates/_regional_matches.html` | Delete | Replaced by `_other_matches.html` |
| `templates/_ctg_matches.html` | Delete | Replaced by `_other_matches.html` |
| `preview.py` | Modify | Add `--excel` + `--mm-export` flags |
| `tests/test_raw_models.py` | Create | Tests for extra fields + renamed fields |
| `tests/test_builder.py` | Create | Tests for the two new loader functions |

---

## Task 0: Fix pre-existing broken import

Tests are currently broken because `process_ct_data.py` imports from a module that doesn't exist.

**Files:**
- Modify: `src/ctm/transformers/process_ct_data.py:8`

- [ ] **Step 1: Confirm breakage**

```bash
cd /Users/deemer/Documents/git-repos/ctm-report-preview
.venv/bin/python -m pytest tests/test_transformers.py -q --no-header 2>&1 | head -10
```
Expected: `ModuleNotFoundError: No module named 'ctm.schemas.processed.clinical_trial'`

- [ ] **Step 2: Fix the import**

In `src/ctm/transformers/process_ct_data.py` line 8, change:
```python
from ctm.schemas.processed.clinical_trial import ClinicalTrial
```
to:
```python
from ctm.schemas.processed.models import ClinicalTrial
```

- [ ] **Step 3: Verify tests pass**

```bash
.venv/bin/python -m pytest tests/test_transformers.py -q --no-header
```
Expected: `9 passed`

- [ ] **Step 4: Commit**

```bash
git add src/ctm/transformers/process_ct_data.py
git commit -m "fix: correct broken import in process_ct_data"
```

---

## Task 1: Fix Raw model data loss

Add `extra='allow'` to all Raw finding models and rename mismatched fields in `RawTempusFinding` to match actual Excel column headers.

**Files:**
- Modify: `src/ctm/schemas/raw/models.py`
- Create: `tests/test_raw_models.py`

**Interfaces:**
- Produces: `RawTempusFinding` with `raw_test`, `raw_therapies_other_indications` fields; all Raw finding models accept unknown `raw_*` columns

- [ ] **Step 1: Write failing tests**

Create `tests/test_raw_models.py`:

```python
"""Tests for Raw model data capture — extra columns and renamed fields."""
import pytest
from ctm.schemas.raw.models import (
    RawTempusFinding,
    RawCarisFinding,
    RawAmbryFinding,
    RawAmcNgsFinding,
    RawOgmFinding,
    RawPmlRaraFinding,
    RawTumorBiomarker,
)
from ctm.transformers.normalize_manual import normalize_tempus


def test_tempus_renamed_field_raw_test():
    raw = RawTempusFinding.model_validate({
        "pt_uuid": 1, "report_uuid": 1, "raw_test": "Tempus xT"
    })
    assert raw.raw_test == "Tempus xT"


def test_tempus_renamed_field_raw_therapies_other_indications():
    raw = RawTempusFinding.model_validate({
        "pt_uuid": 1, "report_uuid": 1,
        "raw_therapies_other_indications": "Drug A"
    })
    assert raw.raw_therapies_other_indications == "Drug A"


def test_tempus_captures_undeclared_raw_column():
    raw = RawTempusFinding.model_validate({
        "pt_uuid": 1, "report_uuid": 1, "raw_title": "Some Title"
    })
    assert raw.model_dump()["raw_title"] == "Some Title"


def test_finding_raw_dict_preserves_extra_column():
    raw = RawTempusFinding.model_validate({
        "pt_uuid": 1, "report_uuid": 1,
        "gene": "EGFR", "variant_type": "SNV",
        "raw_test": "Tempus xT", "raw_title": "Appendix A",
    })
    finding = normalize_tempus(raw, source="tempus")
    assert finding.raw["raw_test"] == "Tempus xT"
    assert finding.raw["raw_title"] == "Appendix A"


@pytest.mark.parametrize("model_cls", [
    RawCarisFinding,
    RawAmbryFinding,
    RawAmcNgsFinding,
    RawOgmFinding,
    RawPmlRaraFinding,
    RawTumorBiomarker,
])
def test_raw_finding_models_accept_extra_columns(model_cls):
    row = {"pt_uuid": 1, "report_uuid": 1, "raw_unexpected_column": "value"}
    instance = model_cls.model_validate(row)
    assert instance.model_dump()["raw_unexpected_column"] == "value"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_raw_models.py -q --no-header
```
Expected: multiple failures — `ValidationError` or `KeyError`

- [ ] **Step 3: Add `extra='allow'` and rename fields in `models.py`**

Replace the entire `src/ctm/schemas/raw/models.py` content. Key changes:
- Add `from pydantic import BaseModel, ConfigDict, field_validator`
- Add `model_config = ConfigDict(extra='allow')` to every Raw finding model (not `RawPatientGeneral` or `RawReportMetadata` — those are fine as-is)
- Rename `raw_biomarker` → `raw_test` and `raw_therapies_other` → `raw_therapies_other_indications` in `RawTempusFinding`

```python
"""Raw Pydantic models — one per Excel sheet row, directly from manual entry."""
from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, field_validator


def _to_date(v: object) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%d-%b-%y", "%m/%d/%Y"):
            try:
                return datetime.strptime(v, fmt).date()
            except ValueError:
                continue
    return None


class RawPatientGeneral(BaseModel):
    pt_uuid: int
    mrn: str | int | None = None
    first_name: str | None = None
    last_name: str | None = None
    dob: date | datetime | str | None = None
    sex: str | None = None
    vital_status: str | None = None
    entity: str | None = None
    primary_dx: str | None = None
    oncotree_primary_diagnosis: str | None = None
    metastasis_sites: str | None = None

    @field_validator("dob", mode="before")
    @classmethod
    def coerce_dob(cls, v: object) -> date | None:
        return _to_date(v)


class RawReportMetadata(BaseModel):
    report_uuid: int
    pt_uuid: int
    source: str
    test_name: str | None = None
    accession_no: str | None = None
    physician: str | None = None
    specimen_type: str | None = None
    date_collected: date | datetime | str | None = None
    date_received: date | datetime | str | None = None
    date_completed: date | datetime | str | None = None
    obtained_from: str | None = None
    link: str | None = None
    notes: str | None = None

    @field_validator("date_collected", "date_received", "date_completed", mode="before")
    @classmethod
    def coerce_dates(cls, v: object) -> date | None:
        return _to_date(v)


class RawTempusFinding(BaseModel):
    model_config = ConfigDict(extra='allow')
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    protein: str | None = None
    nucleotide: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    raw_test: str | None = None
    raw_result: str | None = None
    raw_category: str | None = None
    raw_nucleotide_type: str | None = None
    raw_therapies_current_dx: str | None = None
    raw_therapies_other_indications: str | None = None
    raw_trials: str | None = None


class RawCarisFinding(BaseModel):
    model_config = ConfigDict(extra='allow')
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    protein: str | None = None
    nucleotide: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    raw_specimen_id: str | None = None
    raw_primary_tumor_site: str | None = None
    raw_specimen_site: str | None = None
    raw_specimen_collected: date | datetime | str | None = None
    raw_test_report_date: date | datetime | str | None = None
    raw_completion_of_addendum: date | datetime | str | None = None
    raw_ordered_by_location: str | None = None
    raw_section: str | None = None
    raw_biomarker: str | None = None
    raw_method: str | None = None
    raw_analyte: str | None = None
    raw_result: str | None = None
    raw_benefit: str | None = None
    raw_therapy_assoc: str | None = None
    raw_biomarker_level: str | None = None
    raw_protein_alteration: str | None = None
    raw_exon: str | int | None = None
    raw_dna_alteration: str | None = None
    raw_frequency_pct: str | float | None = None
    raw_genotype: str | None = None
    raw_hla_class: str | None = None

    @field_validator(
        "raw_specimen_collected", "raw_test_report_date", "raw_completion_of_addendum",
        mode="before",
    )
    @classmethod
    def coerce_dates(cls, v: object) -> date | None:
        return _to_date(v)


class RawAmbryFinding(BaseModel):
    model_config = ConfigDict(extra='allow')
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    protein: str | None = None
    nucleotide: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    raw_pathogenic_mutations: str | None = None
    raw_vus: str | None = None
    raw_gross_deletions_dups: str | None = None
    raw_summary: str | None = None


class RawAmcNgsFinding(BaseModel):
    model_config = ConfigDict(extra='allow')
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    protein: str | None = None
    nucleotide: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    raw_specimen_id: str | None = None
    raw_block_id: str | None = None
    raw_body_site: str | None = None
    raw_finding_level: str | None = None
    raw_variant_name: str | None = None
    raw_dna_change: str | None = None
    raw_amino_acid_change: str | None = None
    raw_transcript: str | None = None
    raw_interpretation: str | None = None
    raw_therapeutic_implications: str | None = None
    raw_pertinent_negatives: str | None = None


class RawOgmFinding(BaseModel):
    model_config = ConfigDict(extra='allow')
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    protein: str | None = None
    nucleotide: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    raw_selected_results: str | None = None
    raw_interpretation: str | None = None
    raw_iscn_karyotype: str | None = None
    raw_additional_results: str | None = None


class RawPmlRaraFinding(BaseModel):
    model_config = ConfigDict(extra='allow')
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    protein: str | None = None
    nucleotide: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    raw_test_result: str | None = None
    raw_interpretation: str | None = None


class RawTumorBiomarker(BaseModel):
    model_config = ConfigDict(extra='allow')
    pt_uuid: int
    report_uuid: int
    gene: str | None = None
    variant_type: str | None = None
    result_summary: str | None = None
    raw_tmb: str | None = None
    raw_msi: str | None = None
    raw_pd_l1: str | None = None
    raw_loh: str | None = None
    raw_hrd: str | None = None
    raw_mmr: str | None = None
    raw_tumor_fraction: str | float | None = None
    raw_tumor_normal: str | None = None
    raw_rna_expression: str | None = None
    raw_rna_fusion: str | None = None
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_raw_models.py -q --no-header
```
Expected: `7 passed`

- [ ] **Step 5: Confirm existing tests still pass**

```bash
.venv/bin/python -m pytest tests/ -q --no-header
```
Expected: all previously passing tests still pass

- [ ] **Step 6: Commit**

```bash
git add src/ctm/schemas/raw/models.py tests/test_raw_models.py
git commit -m "feat: add extra='allow' to raw finding models; rename mismatched Tempus fields"
```

---

## Task 2: `load_context_from_mm_matches()`

Add the matchminer loader to `builder.py`. Takes a path to the JSON exported by `export_matches.py` and returns `{primary_match, other_matches}`.

**Files:**
- Modify: `src/ctm/reports/builder.py`
- Create: `tests/test_builder.py`

**Interfaces:**
- Consumes: JSON file with shape `{clinical: {...}, genomic: [...], trial_match: [...]}`
- Produces: `load_context_from_mm_matches(path: str) -> dict` with keys `primary_match` (dict or None) and `other_matches` (list of dicts)

- [ ] **Step 1: Create test fixture file**

Create `tests/fixtures/mm_export_7439568.json` — a minimal matchminer export with two protocols so `other_matches` is non-empty. Copy the structure from `delme/7439568.json` but use plain values (no `$oid`/`$date`):

```json
{
  "clinical": {
    "SAMPLE_ID": "7439568",
    "ONCOTREE_PRIMARY_DIAGNOSIS_NAME": "Lung Adenocarcinoma",
    "VITAL_STATUS": "alive",
    "GENDER": "Male"
  },
  "genomic": [
    {
      "SAMPLE_ID": "7439568",
      "TRUE_HUGO_SYMBOL": "EGFR",
      "VARIANT_CATEGORY": "MUTATION",
      "WILDTYPE": false,
      "TRUE_PROTEIN_CHANGE": "E746_A750del"
    }
  ],
  "trial_match": [
    {
      "sample_id": "7439568",
      "match_level": "step",
      "reason_type": "clinical",
      "show_in_ui": true,
      "protocol_no": "NCT02477839",
      "nct_id": "NCT02477839",
      "cancer_type_match": "specific",
      "match_type": "generic_clinical",
      "genomic_alteration": "",
      "trial_summary_status": "open",
      "sort_order": [1, 99, 99, 99, 99, 99],
      "hash": "abc123"
    },
    {
      "sample_id": "7439568",
      "match_level": "arm",
      "reason_type": "genomic",
      "show_in_ui": true,
      "protocol_no": "NCT02477839",
      "nct_id": "NCT02477839",
      "cancer_type_match": "specific",
      "match_type": "gene",
      "genomic_alteration": "EGFR E746_A750del",
      "trial_summary_status": "open",
      "code": "ARM B",
      "true_hugo_symbol": "EGFR",
      "true_protein_change": "E746_A750del",
      "sort_order": [1, 99, 1, 99, 99, 99],
      "hash": "def456"
    },
    {
      "sample_id": "7439568",
      "match_level": "arm",
      "reason_type": "clinical",
      "show_in_ui": true,
      "protocol_no": "NCT99999999",
      "nct_id": "NCT99999999",
      "cancer_type_match": "broader",
      "match_type": "generic_clinical",
      "genomic_alteration": "",
      "trial_summary_status": "open",
      "sort_order": [2, 99, 99, 99, 99, 99],
      "hash": "ghi789"
    }
  ]
}
```

- [ ] **Step 2: Write failing tests**

Create `tests/test_builder.py`:

```python
"""Tests for the real-data builder loader functions."""
import json
import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_mm_primary_is_arm_level_genomic():
    from ctm.reports.builder import load_context_from_mm_matches
    ctx = load_context_from_mm_matches(str(FIXTURES / "mm_export_7439568.json"))
    assert ctx["primary_match"] is not None
    assert ctx["primary_match"]["nct_id"] == "NCT02477839"


def test_mm_primary_match_has_required_keys():
    from ctm.reports.builder import load_context_from_mm_matches
    ctx = load_context_from_mm_matches(str(FIXTURES / "mm_export_7439568.json"))
    pm = ctx["primary_match"]
    assert "nct_id" in pm
    assert "trial_status" in pm
    assert isinstance(pm["trial"], list)
    assert isinstance(pm["match_detail"], list)
    assert isinstance(pm["genomic"], list)


def test_mm_other_matches_excludes_primary_protocol():
    from ctm.reports.builder import load_context_from_mm_matches
    ctx = load_context_from_mm_matches(str(FIXTURES / "mm_export_7439568.json"))
    other_protocols = [m["protocol_no"] for m in ctx["other_matches"]]
    assert "NCT02477839" not in other_protocols
    assert "NCT99999999" in other_protocols


def test_mm_other_matches_shape():
    from ctm.reports.builder import load_context_from_mm_matches
    ctx = load_context_from_mm_matches(str(FIXTURES / "mm_export_7439568.json"))
    m = ctx["other_matches"][0]
    assert "protocol_no" in m
    assert "nct_id" in m
    assert "source" in m
    assert m["source"] == "matchminer"


def test_mm_empty_trial_match_returns_none_primary():
    from ctm.reports.builder import load_context_from_mm_matches
    import tempfile, os
    data = {"clinical": {}, "genomic": [], "trial_match": []}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        tmp = f.name
    try:
        ctx = load_context_from_mm_matches(tmp)
        assert ctx["primary_match"] is None
        assert ctx["other_matches"] == []
    finally:
        os.unlink(tmp)


def test_mm_no_arm_match_falls_back_to_step():
    from ctm.reports.builder import load_context_from_mm_matches
    import tempfile, os
    data = {
        "clinical": {}, "genomic": [],
        "trial_match": [{
            "sample_id": "1", "match_level": "step", "reason_type": "clinical",
            "show_in_ui": True, "protocol_no": "NCT00000001", "nct_id": "NCT00000001",
            "cancer_type_match": "specific", "match_type": "generic_clinical",
            "genomic_alteration": "", "trial_summary_status": "open",
            "sort_order": [1, 99, 99, 99, 99, 99], "hash": "aaa"
        }]
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        tmp = f.name
    try:
        ctx = load_context_from_mm_matches(tmp)
        assert ctx["primary_match"]["nct_id"] == "NCT00000001"
    finally:
        os.unlink(tmp)
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_builder.py -q --no-header
```
Expected: `ImportError` or `AttributeError` — `load_context_from_mm_matches` not defined yet

- [ ] **Step 4: Implement in `builder.py`**

Add the following to `src/ctm/reports/builder.py` after the existing `_extract` function. Do not modify any existing functions.

```python
# ---------------------------------------------------------------------------
# Primary-match selection helpers
# ---------------------------------------------------------------------------

def _select_primary_match(trial_matches: list[dict]) -> dict | None:
    if not trial_matches:
        return None

    def _priority(m: dict) -> tuple:
        level = 0 if m.get("match_level") == "arm" else 1
        reason = 0 if m.get("reason_type") == "genomic" else 1
        sort = m.get("sort_order") or [99] * 6
        return (level, reason, sort)

    return min(trial_matches, key=_priority)


def _build_other_matches(trial_matches: list[dict], primary: dict | None) -> list[dict]:
    primary_protocol = primary.get("protocol_no") if primary else None
    seen: set[str] = set()
    others = []
    for m in trial_matches:
        protocol = m.get("protocol_no")
        if not protocol or protocol == primary_protocol or protocol in seen:
            continue
        seen.add(protocol)
        others.append({
            "protocol_no": protocol,
            "nct_id": m.get("nct_id"),
            "match_level": m.get("match_level"),
            "match_type": m.get("match_type"),
            "genomic_alteration": m.get("genomic_alteration", ""),
            "source": "matchminer",
        })
    return others


_GENOMIC_MATCH_FIELDS = {
    "true_hugo_symbol": "Gene",
    "true_protein_change": "Protein Change",
    "true_cdna_change": "cDNA Change",
    "variant_category": "Variant Category",
    "genomic_alteration": "Alteration",
}


def _build_primary_match_context(match: dict) -> dict:
    trial_rows = [
        _row("NCT ID", match.get("nct_id")),
        _row("Protocol No.", match.get("protocol_no")),
        _row("Match Level", match.get("match_level")),
        _row("Trial Status", (match.get("trial_summary_status") or "").capitalize()),
        _row("Match Engine", "MatchMiner-v2"),
    ]
    match_detail_rows = [
        _row("Cancer Type Match", match.get("cancer_type_match")),
        _row("Reason Type", match.get("reason_type")),
        _row("Match Type", match.get("match_type")),
    ]
    if match.get("code"):
        match_detail_rows.append(_row("Arm", match["code"]))

    return {
        "nct_id": match.get("nct_id"),
        "trial_status": (match.get("trial_summary_status") or "").capitalize(),
        "trial": [r for r in trial_rows if r["value"] not in (None, "")],
        "match_detail": [r for r in match_detail_rows if r["value"] not in (None, "")],
        "genomic": _extract(match, _GENOMIC_MATCH_FIELDS),
    }


# ---------------------------------------------------------------------------
# Public loader: matchminer export
# ---------------------------------------------------------------------------

def load_context_from_mm_matches(mm_export_path: str) -> dict:
    with open(mm_export_path) as f:
        data = json.load(f)

    visible = [m for m in data.get("trial_match", []) if m.get("show_in_ui")]
    primary = _select_primary_match(visible)

    return {
        "primary_match": _build_primary_match_context(primary) if primary else None,
        "other_matches": _build_other_matches(visible, primary),
    }
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/python -m pytest tests/test_builder.py -q --no-header
```
Expected: `6 passed`

- [ ] **Step 6: Commit**

```bash
git add src/ctm/reports/builder.py tests/test_builder.py tests/fixtures/mm_export_7439568.json
git commit -m "feat: add load_context_from_mm_matches to builder"
```

---

## Task 3: `load_context_from_raw_excel()`

Add the Excel loader to `builder.py`. Takes a path to the Excel workbook and returns `{patient_header, patient_detail, reports}`.

**Files:**
- Modify: `src/ctm/reports/builder.py`
- Modify: `tests/test_builder.py`

**Interfaces:**
- Consumes: `read_and_normalize(path)` from `ctm.transformers.excel_reader` → `(patients, metadata, findings)`
- Produces: `load_context_from_raw_excel(path: str) -> dict` with keys `patient_header` (list), `patient_detail` (list), `reports` (list)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_builder.py`:

```python
def test_excel_patient_header_contains_mrn():
    from ctm.reports.builder import load_context_from_raw_excel
    excel = str(Path(__file__).parent.parent / "data" / "raw" / "patient_data_template.xlsx")
    ctx = load_context_from_raw_excel(excel)
    labels = [r["label"] for r in ctx["patient_header"]]
    assert "MRN" in labels


def test_excel_returns_required_keys():
    from ctm.reports.builder import load_context_from_raw_excel
    excel = str(Path(__file__).parent.parent / "data" / "raw" / "patient_data_template.xlsx")
    ctx = load_context_from_raw_excel(excel)
    assert "patient_header" in ctx
    assert "patient_detail" in ctx
    assert "reports" in ctx
    assert isinstance(ctx["patient_header"], list)
    assert isinstance(ctx["reports"], list)


def test_excel_reports_include_raw_fields():
    from ctm.reports.builder import load_context_from_raw_excel
    excel = str(Path(__file__).parent.parent / "data" / "raw" / "patient_data_template.xlsx")
    ctx = load_context_from_raw_excel(excel)
    # At least one finding should have a non-empty raw dict
    all_findings = [f for r in ctx["reports"] for f in r.get("findings", [])]
    raw_dicts = [f["raw"] for f in all_findings if f.get("raw")]
    assert len(raw_dicts) > 0


def test_excel_missing_file_returns_empty_context():
    from ctm.reports.builder import load_context_from_raw_excel
    ctx = load_context_from_raw_excel("/nonexistent/path.xlsx")
    assert ctx["patient_header"] == []
    assert ctx["patient_detail"] == []
    assert ctx["reports"] == []
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_builder.py::test_excel_patient_header_contains_mrn -q --no-header
```
Expected: `AttributeError` — `load_context_from_raw_excel` not defined

- [ ] **Step 3: Replace `PATIENT_HEADER_FIELDS` and `PATIENT_DETAIL_FIELDS` at top of `builder.py`**

Replace the existing `PATIENT_HEADER_FIELDS` and `PATIENT_DETAIL_FIELDS` dicts:

```python
PATIENT_HEADER_FIELDS = {
    "mrn": "MRN",
    "first_name": "First Name",
    "last_name": "Last Name",
    "sex": "Gender",
    "dob": "Date of Birth",
    "vital_status": "Vital Status",
    "entity": "Institution",
    "oncotree_primary_diagnosis": "Diagnosis (OncoTree)",
}

PATIENT_DETAIL_FIELDS = {
    "primary_dx": "Primary Diagnosis",
    "metastasis_sites": "Metastasis Sites",
}
```

- [ ] **Step 4: Add `_build_reports_context` helper and `load_context_from_raw_excel` to `builder.py`**

Add after `load_context_from_mm_matches`:

```python
def _build_reports_context(metadata: list, findings: list) -> list[dict]:
    findings_by_report: dict[int, list] = {}
    for f in findings:
        findings_by_report.setdefault(f.report_uuid, []).append({
            "gene": f.gene,
            "protein": f.protein,
            "variant_type": f.variant_type,
            "result_summary": f.result_summary,
            "raw": f.raw,
        })

    return [
        {
            "source": m.source,
            "test_name": m.test_name,
            "accession_no": m.accession_no,
            "physician": m.physician,
            "date_completed": m.date_completed.isoformat() if m.date_completed else None,
            "findings": findings_by_report.get(m.report_uuid, []),
        }
        for m in metadata
    ]


def load_context_from_raw_excel(excel_path: str) -> dict:
    _empty = {"patient_header": [], "patient_detail": [], "reports": []}
    try:
        from ctm.transformers.excel_reader import read_and_normalize
        patients, metadata, findings = read_and_normalize(Path(excel_path))
    except FileNotFoundError:
        return _empty

    if not patients:
        return _empty

    patient_dict = patients[0].model_dump()

    return {
        "patient_header": _extract(patient_dict, PATIENT_HEADER_FIELDS),
        "patient_detail": _extract(patient_dict, PATIENT_DETAIL_FIELDS),
        "reports": _build_reports_context(metadata, findings),
    }
```

Note: `Path` is already imported at the top of `builder.py` via `from pathlib import Path` — if not present, add it.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/python -m pytest tests/test_builder.py -q --no-header
```
Expected: `10 passed`

- [ ] **Step 6: Commit**

```bash
git add src/ctm/reports/builder.py tests/test_builder.py
git commit -m "feat: add load_context_from_raw_excel to builder"
```

---

## Task 4: Wire loaders into `render_html_from_sources()`

Add the thin orchestration function that merges both contexts and renders.

**Files:**
- Modify: `src/ctm/reports/builder.py`
- Modify: `tests/test_builder.py`

**Interfaces:**
- Consumes: `load_context_from_raw_excel()` and `load_context_from_mm_matches()` from Tasks 2–3
- Produces: `render_html_from_sources(excel_path: str, mm_export_path: str) -> str`

- [ ] **Step 1: Write failing test**

Append to `tests/test_builder.py`:

```python
def test_render_html_from_sources_returns_html():
    from ctm.reports.builder import render_html_from_sources
    excel = str(Path(__file__).parent.parent / "data" / "raw" / "patient_data_template.xlsx")
    mm = str(Path(__file__).parent / "fixtures" / "mm_export_7439568.json")
    html = render_html_from_sources(excel, mm)
    assert "<html" in html
    assert "Trial Match Report" in html
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
.venv/bin/python -m pytest tests/test_builder.py::test_render_html_from_sources_returns_html -q --no-header
```
Expected: `AttributeError` — `render_html_from_sources` not defined

- [ ] **Step 3: Add `render_html_from_sources` to `builder.py`**

Add after `load_context_from_raw_excel`:

```python
def render_html_from_sources(excel_path: str, mm_export_path: str) -> str:
    excel_ctx = load_context_from_raw_excel(excel_path)
    mm_ctx = load_context_from_mm_matches(mm_export_path)
    ctx = {**excel_ctx, **mm_ctx}
    ctx["methods"] = []
    ctx["provenance"] = {
        "generated_on": datetime.now().strftime("%d%b%Y"),
        "data_source": DATA_SOURCE_VERSION,
        "sample_id": (mm_ctx.get("primary_match") or {}).get("nct_id", ""),
        "record_hash": "",
    }
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("report.html")
    css = (STATIC_DIR / "report.css").read_text()
    return template.render(css=css, **ctx)
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_builder.py -q --no-header
```
Expected: `11 passed`

- [ ] **Step 5: Commit**

```bash
git add src/ctm/reports/builder.py tests/test_builder.py
git commit -m "feat: add render_html_from_sources orchestration function"
```

---

## Task 5: Template cleanup

Replace the two separate match template partials with a single `_other_matches.html`, and update `report.html`.

**Files:**
- Rewrite: `templates/_other_matches.html`
- Modify: `templates/report.html`
- Delete: `templates/_regional_matches.html`
- Delete: `templates/_ctg_matches.html`

- [ ] **Step 1: Rewrite `templates/_other_matches.html`**

```html
{% if other_matches %}
<section class="section section-other">
  <h2>Additional Trial Matches</h2>
  <table class="data-table">
    <thead>
      <tr>
        <th>NCT ID</th>
        <th>Match Level</th>
        <th>Match Type</th>
        <th>Alteration</th>
      </tr>
    </thead>
    <tbody>
      {% for m in other_matches %}
      <tr>
        <td>{{ m.nct_id or m.protocol_no }}</td>
        <td>{{ m.match_level or "—" }}</td>
        <td>{{ m.match_type or "—" }}</td>
        <td>{{ m.genomic_alteration or "—" }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>
{% endif %}
```

- [ ] **Step 2: Update `templates/report.html`**

Replace:
```html
  {% include "_regional_matches.html" %}
  {% include "_ctg_matches.html" %}
```
with:
```html
  {% include "_other_matches.html" %}
```

- [ ] **Step 3: Delete old templates**

```bash
rm templates/_regional_matches.html templates/_ctg_matches.html
```

- [ ] **Step 4: Verify report renders without error**

```bash
cd /Users/deemer/Documents/git-repos/ctm-report-preview
.venv/bin/python -c "
from ctm.reports.builder import render_html_from_sources
html = render_html_from_sources(
    'data/raw/patient_data_template.xlsx',
    'tests/fixtures/mm_export_7439568.json'
)
print('OK — rendered', len(html), 'chars')
"
```
Expected: `OK — rendered NNNNN chars` (no exceptions)

- [ ] **Step 5: Verify mock path still works**

```bash
.venv/bin/python -c "
from ctm.reports.builder import render_html
html = render_html(use_real=False)
print('mock OK — rendered', len(html), 'chars')
"
```
Expected: exception from Jinja2 about `regional_matches` — fix by also removing those references from `load_context()` in `builder.py`. In `load_context()`, change:
```python
    regional_matches = [m for m in others if m.get("source") == "regional"]
    ctg_matches = [m for m in others if m.get("source") == "clinicaltrials_gov"]
    ...
    return {
        ...
        "regional_matches": regional_matches,
        "ctg_matches": ctg_matches,
        ...
    }
```
to:
```python
    other_matches = [{"protocol_no": m.get("trial_id"), "nct_id": m.get("trial_id"),
                      "match_level": None, "match_type": None,
                      "genomic_alteration": "", "source": m.get("source", "")}
                     for m in others]
    ...
    return {
        ...
        "other_matches": other_matches,
        ...
    }
```

Then re-run:
```bash
.venv/bin/python -c "
from ctm.reports.builder import render_html
html = render_html(use_real=False)
print('mock OK — rendered', len(html), 'chars')
"
```
Expected: `mock OK — rendered NNNNN chars`

- [ ] **Step 6: Run full test suite**

```bash
.venv/bin/python -m pytest tests/ -q --no-header
```
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add templates/_other_matches.html templates/report.html src/ctm/reports/builder.py
git rm templates/_regional_matches.html templates/_ctg_matches.html
git commit -m "feat: replace regional/ctg match templates with unified other_matches"
```

---

## Task 6: CLI wire-up in `preview.py`

Add `--excel` and `--mm-export` flags so the dev server can render with real data.

**Files:**
- Modify: `preview.py`

- [ ] **Step 1: Update `preview.py`**

Replace the entire file:

```python
"""Dev preview server — re-renders the report on file changes.

Usage:
    python preview.py [--mock]
    python preview.py --excel PATH --mm-export PATH
"""
import argparse
from pathlib import Path

from livereload import Server
from ctm.reports.builder import BASE_DIR, render_html, render_html_from_sources

OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_FILE = OUTPUT_DIR / "report.html"


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--mock", action="store_true", help="load data from data/mock/ (default)")
    parser.add_argument("--excel", metavar="PATH", help="Path to patient_data_template.xlsx")
    parser.add_argument("--mm-export", dest="mm_export", metavar="PATH",
                        help="Path to matchminer export JSON")
    args = parser.parse_args()

    use_real_sources = bool(args.excel and args.mm_export)

    def build():
        OUTPUT_DIR.mkdir(exist_ok=True)
        if use_real_sources:
            OUTPUT_FILE.write_text(render_html_from_sources(args.excel, args.mm_export))
        else:
            OUTPUT_FILE.write_text(render_html(use_real=False))

    build()
    server = Server()
    server.watch(str(BASE_DIR / "templates" / "*.html"), build)
    server.watch(str(BASE_DIR / "static" / "*.css"), build)
    if use_real_sources:
        server.watch(args.mm_export, build)
        server.watch(args.excel, build)
    else:
        server.watch(str(BASE_DIR / "data" / "mock" / "*.json"), build)
    server.serve(root=str(OUTPUT_DIR), port=5500, open_url_delay=1,
                 default_filename="report.html")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify mock mode still launches**

```bash
.venv/bin/python preview.py --mock --help
```
Expected: help text prints, no import errors

- [ ] **Step 3: Verify real-data mode launches a build**

```bash
.venv/bin/python -c "
import sys
sys.argv = ['preview.py',
    '--excel', 'data/raw/patient_data_template.xlsx',
    '--mm-export', 'tests/fixtures/mm_export_7439568.json']
# Just test the build() path without starting the server
from pathlib import Path
from ctm.reports.builder import render_html_from_sources
html = render_html_from_sources(
    'data/raw/patient_data_template.xlsx',
    'tests/fixtures/mm_export_7439568.json'
)
Path('output').mkdir(exist_ok=True)
Path('output/report.html').write_text(html)
print('Written to output/report.html')
"
```
Expected: `Written to output/report.html`

- [ ] **Step 4: Run full test suite one last time**

```bash
.venv/bin/python -m pytest tests/ -q --no-header
```
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add preview.py
git commit -m "feat: add --excel and --mm-export flags to preview.py"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| `load_context_from_raw_excel()` | Task 3 |
| `load_context_from_mm_matches()` | Task 2 |
| `render_html_from_sources()` | Task 4 |
| `extra='allow'` on all Raw finding models | Task 1 |
| Rename mismatched fields in `RawTempusFinding` | Task 1 |
| Primary match: arm > step, genomic > clinical, sort_order tiebreak | Task 2 |
| `other_matches` flat list with `source` field | Task 2 |
| Remove `_regional_matches.html` / `_ctg_matches.html` | Task 5 |
| Add `_other_matches.html` | Task 5 |
| Update `report.html` | Task 5 |
| Update `PATIENT_HEADER_FIELDS` / `PATIENT_DETAIL_FIELDS` with new Patient fields | Task 3 |
| `--excel` + `--mm-export` CLI flags | Task 6 |
| Mock path untouched | Task 5 (Step 5 fixes `load_context`) |
| All Patient fields pass through to context | Task 3 |
| `finding.raw` preserved in reports context | Task 3 |
| Tests for each feature | Tasks 1–4 |
