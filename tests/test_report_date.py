"""report_date — required on every report, and the tie-breaker between reports.

When two or more of a patient's reports cover the same biomarker (gene +
variant_category), the report with the most recent report_date is the source of
truth: its rows are matched, the older reports' rows are kept in patient_data
but marked superseded. Two reports on the same date that disagree are never
resolved silently — both are kept and an error is logged for a person to review.
"""
import logging
from datetime import date, datetime

import openpyxl
import pytest
from pydantic import ValidationError

from ctm.schemas.raw.models import RawReportMetadata
from ctm.schemas.raw.normalized import Finding, Patient
from ctm.transformers.excel_reader import MissingReportDateError, read_and_normalize
from ctm.transformers.normalize_manual import normalize_report_metadata
from ctm.transformers.to_matchminer import select_latest_findings, to_genomic_docs

PT = "pt_1000000"


def _finding(report_uuid, report_date, biomarker="MET", category="MUTATION", **kw):
    return Finding(pt_uuid=PT, report_uuid=report_uuid, source="x",
                   report_date=report_date, biomarker=biomarker,
                   variant_category=category, **kw)


# ── Required on the report ─────────────────────────────────────────────────────

def test_report_metadata_without_report_date_is_rejected():
    with pytest.raises(ValidationError, match="report_date"):
        RawReportMetadata.model_validate(
            {"report_uuid": "rp_1", "pt_uuid": PT, "source": "tempus"})


def test_report_date_from_excel_is_stored_as_a_plain_date():
    row = RawReportMetadata.model_validate({
        "report_uuid": "rp_1", "pt_uuid": PT, "source": "tempus",
        "report_date": datetime(2026, 9, 1, 0, 0),
    })
    m = normalize_report_metadata(row)
    assert m.report_date == date(2026, 9, 1)
    assert type(m.report_date) is date
    assert "report_date" not in m.raw


def _workbook(tmp_path, report_rows):
    wb = openpyxl.Workbook()
    wb.active.title = "pt_general"
    wb["pt_general"].append(["pt_uuid"])
    wb["pt_general"].append([PT])
    rm = wb.create_sheet("report_metadata")
    rm.append(["report_uuid", "pt_uuid", "source", "report_date"])
    for r in report_rows:
        rm.append(r)
    tf = wb.create_sheet("tempus_findings")
    tf.append(["pt_uuid", "report_uuid", "biomarker", "variant_category", "wildtype"])
    for r in report_rows:
        tf.append([PT, r[0], "MET", "MUTATION", "FALSE"])
    path = tmp_path / "wb.xlsx"
    wb.save(path)
    return path


def test_reader_fails_naming_every_report_missing_a_date(tmp_path):
    path = _workbook(tmp_path, [
        ["rp_1", PT, "tempus", datetime(2026, 9, 1)],
        ["rp_2", PT, "tempus", None],
        ["rp_3", PT, "tempus", "not a date"],
    ])
    with pytest.raises(MissingReportDateError) as exc:
        read_and_normalize(path)
    assert "rp_2" in str(exc.value)
    assert "rp_3" in str(exc.value)
    assert "rp_1" not in str(exc.value)


def test_findings_carry_their_reports_date(tmp_path):
    path = _workbook(tmp_path, [
        ["rp_1", PT, "tempus", datetime(2026, 9, 1)],
        ["rp_2", PT, "tempus", datetime(2026, 6, 1)],
    ])
    _, _, findings = read_and_normalize(path)
    assert {f.report_uuid: f.report_date for f in findings} == {
        "rp_1": date(2026, 9, 1), "rp_2": date(2026, 6, 1)}


# ── Most recent report wins ────────────────────────────────────────────────────

def test_most_recent_report_wins_a_conflict():
    """The spec example: Company A (09/01/2026) says MET wildtype, Company B
    (06/01/2026) says MET detected — A is used for matching."""
    a = _finding("rp_A", date(2026, 9, 1), wildtype=True)
    b = _finding("rp_B", date(2026, 6, 1), wildtype=False)
    resolved = {f.report_uuid: f for f in select_latest_findings([b, a])}
    assert resolved["rp_A"].superseded_by is None
    assert resolved["rp_B"].superseded_by == "rp_A"


def test_newer_report_wins_even_when_results_agree_on_wildtype():
    a = _finding("rp_A", date(2026, 9, 1), wildtype=False, protein_change="p.Y1003F")
    b = _finding("rp_B", date(2026, 6, 1), wildtype=False, protein_change="p.Y1003C")
    resolved = {f.report_uuid: f for f in select_latest_findings([a, b])}
    assert resolved["rp_B"].superseded_by == "rp_A"


def test_biomarker_match_ignores_case():
    a = _finding("rp_A", date(2026, 9, 1), biomarker="MET", category="MUTATION")
    b = _finding("rp_B", date(2026, 6, 1), biomarker="met", category="Mutation")
    resolved = {f.report_uuid: f for f in select_latest_findings([a, b])}
    assert resolved["rp_B"].superseded_by == "rp_A"


def test_older_report_still_counts_for_biomarkers_the_newer_one_lacks():
    a = _finding("rp_A", date(2026, 9, 1), biomarker="MET")
    b_met = _finding("rp_B", date(2026, 6, 1), biomarker="MET")
    b_kras = _finding("rp_B", date(2026, 6, 1), biomarker="KRAS")
    resolved = select_latest_findings([a, b_met, b_kras])
    kras = next(f for f in resolved if f.biomarker == "KRAS")
    assert kras.superseded_by is None


def test_every_row_from_the_winning_report_is_kept():
    """One report can list several variants of the same gene."""
    a1 = _finding("rp_A", date(2026, 9, 1), protein_change="p.Y1003F")
    a2 = _finding("rp_A", date(2026, 9, 1), protein_change="p.D1228N")
    b = _finding("rp_B", date(2026, 6, 1), protein_change="p.Y1003F")
    resolved = select_latest_findings([a1, a2, b])
    assert [f.superseded_by for f in resolved] == [None, None, "rp_A"]


def test_different_variant_categories_do_not_compete():
    a = _finding("rp_A", date(2026, 9, 1), category="CNV", cnv_call="High Amplification")
    b = _finding("rp_B", date(2026, 6, 1), category="MUTATION")
    assert all(f.superseded_by is None for f in select_latest_findings([a, b]))


def test_different_patients_do_not_compete():
    a = _finding("rp_A", date(2026, 9, 1))
    b = _finding("rp_B", date(2026, 6, 1)).model_copy(update={"pt_uuid": "pt_other"})
    assert all(f.superseded_by is None for f in select_latest_findings([a, b]))


def test_superseded_findings_produce_no_genomic_doc():
    a = _finding("rp_A", date(2026, 9, 1), wildtype=True)
    b = _finding("rp_B", date(2026, 6, 1), wildtype=False)
    docs = to_genomic_docs(Patient(pt_uuid=PT), select_latest_findings([a, b]))
    assert [d["WILDTYPE"] for d in docs] == [True]


# ── Same-date ties go to a person ──────────────────────────────────────────────

def test_same_date_disagreement_keeps_both_and_logs_an_error(caplog):
    a = _finding("rp_A", date(2026, 9, 1), wildtype=True)
    b = _finding("rp_B", date(2026, 9, 1), wildtype=False)
    with caplog.at_level(logging.ERROR):
        resolved = select_latest_findings([a, b])
    assert all(f.superseded_by is None for f in resolved)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "rp_A" in errors[0].getMessage() and "rp_B" in errors[0].getMessage()
    assert "MET" in errors[0].getMessage()


def test_same_date_agreement_is_not_an_error(caplog):
    a = _finding("rp_A", date(2026, 9, 1), wildtype=False)
    b = _finding("rp_B", date(2026, 9, 1), wildtype=False)
    with caplog.at_level(logging.ERROR):
        resolved = select_latest_findings([a, b])
    assert all(f.superseded_by is None for f in resolved)
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]


def test_same_date_tie_still_beats_an_older_report():
    a = _finding("rp_A", date(2026, 9, 1), wildtype=True)
    b = _finding("rp_B", date(2026, 9, 1), wildtype=False)
    c = _finding("rp_C", date(2026, 1, 1), wildtype=False)
    resolved = {f.report_uuid: f for f in select_latest_findings([a, b, c])}
    assert resolved["rp_A"].superseded_by is None
    assert resolved["rp_B"].superseded_by is None
    assert resolved["rp_C"].superseded_by == "rp_A, rp_B"


# ── Wired through `ctm-mm patients` ────────────────────────────────────────────

def _run_patients(path, out):
    import argparse

    from ctm.mm_cli import _cmd_raw_to_mm
    _cmd_raw_to_mm(argparse.Namespace(excel=str(path), pt_uuid=None, out=str(out)))
    import json
    return json.loads(out.read_text())


def test_patients_command_matches_only_the_newest_report(tmp_path):
    path = _workbook(tmp_path, [
        ["rp_A", PT, "tempus", datetime(2026, 9, 1)],
        ["rp_B", PT, "tempus", datetime(2026, 6, 1)],
    ])
    out = _run_patients(path, tmp_path / "out.json")
    assert len(out["genomic"]) == 1
    assert out["clinical"][0]["REPORT_DATE"] == "2026-09-01"
    reports = {r["report_uuid"]: r for r in out["extras"]["patients"][PT]["reports"]}
    assert reports["rp_A"]["report_date"] == "2026-09-01"
    assert reports["rp_A"]["findings"][0]["superseded_by"] is None
    assert reports["rp_B"]["findings"][0]["superseded_by"] == "rp_A"


def test_patients_command_stops_on_a_missing_report_date(tmp_path):
    path = _workbook(tmp_path, [["rp_A", PT, "tempus", None]])
    with pytest.raises(SystemExit):
        _run_patients(path, tmp_path / "out.json")
    assert not (tmp_path / "out.json").exists()
