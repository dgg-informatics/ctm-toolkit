"""Keeps the intake template in sync with the parser.

Two drifts have already happened here and both were silent:

* mayo/henry_ford/guardant360 normalizers were added to SHEET_NORMALIZERS, but
  no matching sheet was ever added to the template — curators had nowhere to
  record those findings.
* `protein` and `nucleotide` were inserted into six column lists without
  updating the corresponding `example=` rows, so every value after `gene`
  landed one or two columns to the left and the template taught the wrong
  layout.

These tests fail on either.
"""
import ast
from pathlib import Path

import openpyxl
import pytest

from ctm.transformers.normalize_manual import SHEET_NORMALIZERS

REPO_ROOT = Path(__file__).parent.parent
GENERATOR = REPO_ROOT / "scripts" / "make_template.py"
# The committed reference workbook. Doubles as the layout a curator looks at,
# so it must cover every sheet the parser reads.
REFERENCE = REPO_ROOT / "tests" / "fixtures" / "test-pt-data-v0.0.1.xlsx"


def _add_sheet_calls():
    """Every add_sheet(...) call in the generator as (name, n_columns, n_example)."""
    tree = ast.parse(GENERATOR.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "add_sheet":
            example = next((k.value for k in node.keywords if k.arg == "example"), None)
            yield (
                node.args[1].value,
                len(node.args[2].elts),
                len(example.elts) if example is not None else 0,
            )


@pytest.mark.parametrize("sheet,n_cols,n_example", sorted(_add_sheet_calls()))
def test_example_row_has_one_value_per_column(sheet, n_cols, n_example):
    """A short example list silently shifts every later value one column left."""
    assert n_example == n_cols, (
        f"{sheet}: {n_cols} columns but {n_example} example values — "
        f"pad with None so each value lands under its own header"
    )


def test_generator_emits_every_sheet_the_parser_reads():
    generated = {name for name, _, _ in _add_sheet_calls()}
    missing = set(SHEET_NORMALIZERS) - generated
    assert not missing, f"parser reads these sheets but the template lacks them: {sorted(missing)}"


def test_reference_workbook_covers_every_sheet_the_parser_reads():
    """It is the only committed example of the layout, so a gap is invisible."""
    wb = openpyxl.load_workbook(REFERENCE, read_only=True)
    missing = set(SHEET_NORMALIZERS) - set(wb.sheetnames)
    assert not missing, f"reference workbook is missing {sorted(missing)}"


def test_findings_sheets_spell_the_canonical_columns_correctly():
    """Every Raw* model sets extra='allow', so unknown columns pass through into
    `raw` rather than erroring. That makes a typo in a canonical header silent:
    `varient_type` would be accepted and the real field left empty. Assert the
    join keys and canonical names are present and exactly spelled, without
    forbidding the extra pass-through columns the schema deliberately permits."""
    required = {"pt_uuid", "report_uuid"}
    canonical = {"gene", "variant_type", "result_summary"}
    wb = openpyxl.load_workbook(REFERENCE, read_only=True)
    for sheet in SHEET_NORMALIZERS:
        headers = {c.value for c in wb[sheet][1] if c.value is not None}
        assert required <= headers, f"{sheet}: missing join keys {sorted(required - headers)}"
        assert canonical <= headers, f"{sheet}: missing/misspelled {sorted(canonical - headers)}"


def test_reference_workbook_is_parseable_and_joined():
    """Rows must actually join — orphaned pt_uuids are dropped without warning."""
    from ctm.transformers.excel_reader import read_and_normalize

    patients, _metadata, findings = read_and_normalize(REFERENCE)
    assert patients, "no patients parsed"
    assert findings, "findings present in the workbook but none survived the join"
