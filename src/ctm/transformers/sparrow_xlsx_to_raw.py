"""Read Sparrow marketing trials Excel → list[RawSparrowTrial].

Column order (Sheet1):
  Study Name | Description | Contact Name | Contact Phone Number |
  Trial Category | Trial SubCategory | NCT # | Contact Email | PI

NCT IDs are cleaned (strip whitespace, remove internal spaces) and validated
against the pattern NCT followed by exactly 8 digits. Rows with missing or
malformed NCT IDs are skipped with a warning printed to stderr.
"""
import re
import sys
from pathlib import Path

import openpyxl

from ..schemas.raw.models import RawSparrowTrial

_NCT_RE = re.compile(r"^NCT\d{8}$", re.IGNORECASE)


def _clean_nct(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = re.sub(r"\s+", "", str(raw).strip()).upper()
    return cleaned if _NCT_RE.match(cleaned) else None


def load(path: str | Path) -> list[RawSparrowTrial]:
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    trials: list[RawSparrowTrial] = []

    for row in ws.iter_rows(min_row=2, values_only=True):
        study_name, description, contact_name, contact_phone, \
            trial_category, trial_subcategory, nct_raw, contact_email, pi, *_ = row

        nct_id = _clean_nct(nct_raw)
        if not nct_id:
            if nct_raw and str(nct_raw).strip():
                print(f"  Warning: skipping malformed NCT ID: {nct_raw!r}", file=sys.stderr)
            continue

        trials.append(RawSparrowTrial(
            study_name=str(study_name).strip() if study_name else None,
            description=str(description).strip() if description else None,
            contact_name=str(contact_name).strip() if contact_name else None,
            contact_phone=str(contact_phone).strip() if contact_phone else None,
            trial_category=str(trial_category).strip() if trial_category else None,
            trial_subcategory=str(trial_subcategory).strip() if trial_subcategory else None,
            nct_id=nct_id,
            contact_email=str(contact_email).strip() if contact_email else None,
            pi=str(pi).strip() if pi else None,
        ))

    return trials
