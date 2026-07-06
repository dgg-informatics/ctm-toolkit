"""Read West (CRCWM) trials Excel → list[RawWestTrial].

Only rows with a populated NCT Number column are returned — rows without
one are skipped with a warning (no NCT means nothing to fetch from CTGov).

Expected columns (row 1 header):
  Group | Disease Category | Sponsor | Title | ID | nct_id
"""
import sys
from pathlib import Path

import openpyxl

from ..schemas.raw.models import RawWestTrial


def load(path: str | Path) -> list[RawWestTrial]:
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    trials: list[RawWestTrial] = []

    for row in ws.iter_rows(min_row=2, values_only=True):
        group, disease_category, sponsor, title, protocol_id, nct_id = (
            row[0], row[1], row[2], row[3], row[4], row[5] if len(row) > 5 else None
        )
        if not nct_id:
            continue
        trials.append(RawWestTrial(
            group=str(group).strip() if group else None,
            disease_category=str(disease_category).strip() if disease_category else None,
            sponsor=str(sponsor).strip() if sponsor else None,
            title=str(title).strip() if title else None,
            protocol_id=str(protocol_id).strip() if protocol_id else None,
            nct_id=str(nct_id).strip(),
        ))

    return trials
