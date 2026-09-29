"""End-to-end: run the patient ingest and prove nothing identifying is logged.

This is the test that makes it safe to point `ctm-mm patients` at a log file on a
shared server. The redaction filter is a backstop; this asserts the actual
outcome for a real workbook, through the real CLI, into a real log file.
"""
import argparse
import json
import logging
from pathlib import Path

import openpyxl
import pytest

from ctm import logging_config as lc
from ctm.mm_cli import _cmd_raw_to_mm

FIXTURE = Path(__file__).parent / "fixtures" / "test-pt-data-v1.3.0.xlsx"


def _identifying_values() -> set[str]:
    """Every MRN, name and DOB in the fixture — what must never appear."""
    workbook = openpyxl.load_workbook(FIXTURE, data_only=True)
    sheet = workbook["pt_general"]
    headers = [cell.value for cell in sheet[1]]
    wanted = {"mrn", "first_name", "last_name", "dob"}
    values = set()
    for row in sheet.iter_rows(min_row=2, values_only=True):
        for header, value in zip(headers, row, strict=False):
            if header in wanted and value is not None:
                text = str(value).strip()
                # One- or two-character values (mrn=1 in the fixture) are not
                # searchable: "1" appears in every count and timestamp.
                if len(text) > 2:
                    values.add(text)
    return values


@pytest.fixture(autouse=True)
def _reset_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    factory = logging.getLogRecordFactory()
    yield
    root.handlers, root.level = handlers, level
    logging.setLogRecordFactory(factory)


def test_patient_ingest_writes_no_phi_to_the_log_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("CTM_RUN_ID", "phi-check")
    monkeypatch.setenv("CTM_LOG_FILE_LEVEL", "DEBUG")
    lc.configure_logging(env="staging")

    out = tmp_path / "bundle.json"
    _cmd_raw_to_mm(argparse.Namespace(
        excel=str(FIXTURE), pt_uuid=None, out=str(out),
    ))
    for handler in logging.getLogger().handlers:
        handler.flush()

    identifying = _identifying_values()
    assert identifying, "fixture should contain names/MRNs, or this test proves nothing"

    body = (tmp_path / "logs" / "phi-check.log").read_text()
    leaked = sorted(v for v in identifying if v in body)
    assert not leaked, f"PHI reached the log file: {leaked}"

    # Console is the same story — it is what cron mails and what a curator sees.
    console = capsys.readouterr().err
    assert not [v for v in identifying if v in console]

    # And the run is still genuinely described: pt_uuid is the PHI-free handle.
    records = [json.loads(line) for line in body.splitlines() if line.strip()]
    events = {r.get("event") for r in records}
    assert "patients.read" in events
    assert any(r.get("pt_uuid", "").startswith("pt_") for r in records)


def test_the_bundle_itself_still_contains_the_phi(tmp_path):
    """Guards against the opposite failure: the log is clean because the ingest
    silently dropped the data. The JSON bundle is storage, not a log, and the
    patient_data rollup is deliberately lossless."""
    out = tmp_path / "bundle.json"
    _cmd_raw_to_mm(argparse.Namespace(excel=str(FIXTURE), pt_uuid=None, out=str(out)))
    body = out.read_text()
    assert any(v in body for v in _identifying_values())
