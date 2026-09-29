"""Read the patient-data workbook → normalized Patient, ReportMetadata, Finding instances."""
import logging
from pathlib import Path

import openpyxl

from ..schemas.raw.models import RawPatientGeneral, RawReportMetadata, _to_date
from ..schemas.raw.normalized import Finding, Patient, ReportMetadata
from .normalize_manual import (
    SHEET_NORMALIZERS,
    normalize_patient,
    normalize_report_metadata,
)

log = logging.getLogger(__name__)


class MissingReportDateError(ValueError):
    """One or more report_metadata rows have a blank or unparseable report_date.

    Raised rather than skipping the row: report_date decides which report wins a
    biomarker conflict, and a skipped report would leave its findings flowing to
    matching with nothing to compare them by."""


def _sheet_rows(ws) -> list[dict]:
    headers = [cell.value for cell in ws[1]]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if all(v is None for v in row):
            continue
        rows.append({k: v for k, v in zip(headers, row, strict=False) if k is not None})
    return rows


def read_and_normalize(
    path: Path,
    pt_uuid_filter: str | set[str] | None = None,
) -> tuple[list[Patient], list[ReportMetadata], list[Finding]]:
    """Read Excel workbook → (patients, report_metadata, findings), all normalized.

    pt_uuid_filter: if set, only rows for the given pt_uuid(s) are returned.
    Accepts a single pt_uuid string or a set of them. Rows that fail validation
    are skipped with a printed warning, except a missing report_date, which
    raises MissingReportDateError naming every such report.
    """
    if isinstance(pt_uuid_filter, str):
        pt_uuid_filter = {pt_uuid_filter}
    wb = openpyxl.load_workbook(path, data_only=True)

    # ── Patients ──────────────────────────────────────────────────────────────
    patients: list[Patient] = []
    if "pt_general" in wb.sheetnames:
        for row in _sheet_rows(wb["pt_general"]):
            if row.get("pt_uuid") is None:
                continue
            if pt_uuid_filter is not None and row["pt_uuid"] not in pt_uuid_filter:
                continue
            try:
                patients.append(normalize_patient(RawPatientGeneral.model_validate(row)))
            except Exception as exc:
                log.warning("  pt_general row skipped — %s", exc,
                            extra={"event": "patients.row_skipped", "sheet": "pt_general"})

    valid_pt_uuids = {p.pt_uuid for p in patients}

    # ── Report metadata ────────────────────────────────────────────────────────
    metadata: list[ReportMetadata] = []
    undated: list[str] = []
    if "report_metadata" in wb.sheetnames:
        for row in _sheet_rows(wb["report_metadata"]):
            if row.get("report_uuid") is None:
                continue
            if row.get("pt_uuid") not in valid_pt_uuids:
                continue
            if _to_date(row.get("report_date")) is None:
                undated.append(str(row["report_uuid"]))
                continue
            try:
                metadata.append(
                    normalize_report_metadata(RawReportMetadata.model_validate(row))
                )
            except Exception as exc:
                log.warning("  report_metadata row skipped — %s", exc,
                            extra={"event": "patients.row_skipped",
                                   "sheet": "report_metadata"})

    if undated:
        raise MissingReportDateError(
            f"report_metadata: report_date is blank or not a date for "
            f"{len(undated)} report(s): {', '.join(undated)}"
        )

    report_source: dict[str, str] = {m.report_uuid: m.source for m in metadata}
    report_date = {m.report_uuid: m.report_date for m in metadata}

    # ── Findings (all source sheets) ──────────────────────────────────────────
    findings: list[Finding] = []
    for sheet_name, (raw_cls, norm_fn) in SHEET_NORMALIZERS.items():
        if sheet_name not in wb.sheetnames:
            continue
        for row in _sheet_rows(wb[sheet_name]):
            if row.get("pt_uuid") is None:
                continue
            if row.get("pt_uuid") not in valid_pt_uuids:
                continue
            try:
                raw = raw_cls.model_validate(row)
                source = report_source.get(
                    raw.report_uuid,
                    sheet_name.replace("_findings", ""),
                )
                findings.append(norm_fn(raw, source=source,
                                        report_date=report_date.get(raw.report_uuid)))
            except Exception as exc:
                log.warning("  %s row skipped — %s", sheet_name, exc,
                            extra={"event": "patients.row_skipped", "sheet": sheet_name})

    return patients, metadata, findings
