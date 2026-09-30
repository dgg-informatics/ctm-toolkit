"""`ctm-mm patients` resolving its workbook and its export location.

The point of both defaults is that a scheduled run needs no arguments: a
workbook is dropped in PATIENT_RAW_DIR, and the normalized bundle lands in
PATIENT_EXPORT_DIR as the disk copy the database can be rebuilt from.
"""
import argparse
import json
import shutil
from pathlib import Path

import pytest

from ctm.mm_cli import _cmd_raw_to_mm, _resolve_patient_workbook

FIXTURE = Path(__file__).parent / "fixtures" / "test-pt-data-v1.2.0.xlsx"


def _args(**kwargs):
    base = {"excel": None, "pt_uuid": None, "out": None, "disk": None}
    return argparse.Namespace(**{**base, **kwargs})



def test_missing_explicit_path_fails_loudly(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        _resolve_patient_workbook(str(tmp_path / "nope.xlsx"))
    assert excinfo.value.code == 1


def test_bare_invocation_picks_the_newest_workbook(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    for name, mtime in (("2026-09-21-patients.xlsx", 1_000), ("2026-09-28-patients.xlsx", 2_000)):
        shutil.copy(FIXTURE, raw / name)
        (raw / name).touch()
        import os
        os.utime(raw / name, (mtime, mtime))
    monkeypatch.setenv("PATIENT_RAW_DIR", str(raw))
    assert _resolve_patient_workbook(None).name == "2026-09-28-patients.xlsx"




def test_bundle_lands_in_the_canonical_export_dir(tmp_path, monkeypatch):
    """No --out: the dated bundle goes to PATIENT_EXPORT_DIR, not the cwd."""
    export = tmp_path / "normalized"
    monkeypatch.setenv("PATIENT_EXPORT_DIR", str(export))
    _cmd_raw_to_mm(_args(excel=str(FIXTURE)))
    written = list(export.glob("*_patients.json"))
    assert len(written) == 1
    bundle = json.loads(written[0].read_text())
    assert bundle["clinical"] and bundle["genomic"] and bundle["extras"]


def test_out_overrides_the_canonical_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATIENT_EXPORT_DIR", str(tmp_path / "unused"))
    out = tmp_path / "somewhere" / "bundle.json"
    _cmd_raw_to_mm(_args(excel=str(FIXTURE), out=str(out)))
    assert out.exists()
    assert not (tmp_path / "unused").exists()


def test_no_disk_prints_to_stdout(tmp_path, monkeypatch, capsys):
    """stdout stays a data channel — this is the pipe case."""
    monkeypatch.setenv("PATIENT_EXPORT_DIR", str(tmp_path / "unused"))
    _cmd_raw_to_mm(_args(excel=str(FIXTURE), disk=False))
    assert json.loads(capsys.readouterr().out)["clinical"]
    assert not (tmp_path / "unused").exists()
