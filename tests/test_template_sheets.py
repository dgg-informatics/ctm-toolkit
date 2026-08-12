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
TEMPLATE = REPO_ROOT / "data" / "raw" / "patient_data_template.xlsx"
GENERATOR = REPO_ROOT / "scripts" / "make_template.py"


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


@pytest.mark.skipif(not TEMPLATE.exists(), reason="template not generated")
def test_committed_template_matches_the_generator():
    """Guards against editing make_template.py but forgetting to regenerate."""
    wb = openpyxl.load_workbook(TEMPLATE, read_only=True)
    on_disk = set(wb.sheetnames)
    missing = set(SHEET_NORMALIZERS) - on_disk
    assert not missing, (
        f"committed template is missing {sorted(missing)} — "
        f"re-run: python scripts/make_template.py"
    )


@pytest.mark.skipif(not TEMPLATE.exists(), reason="template not generated")
def test_committed_template_holds_no_patient_data():
    """The template is a visual reference; real data belongs in an ignored copy."""
    wb = openpyxl.load_workbook(TEMPLATE, read_only=True)
    for ws in wb.worksheets:
        if ws.title.startswith("_"):
            continue  # _legend / _conventions are documentation
        assert ws.max_row <= 2, (
            f"{ws.title} has {ws.max_row} rows — the template must hold only a "
            f"header and a single example row, never real records"
        )
