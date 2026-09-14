"""Read West (CRCWM) trials Excel → list[RawWestTrial].

The sheet's column order and set of columns drift between deliveries, so the
reader keys on the header literally named ``nct_id`` (case-insensitive) rather
than a fixed position, and captures every other column verbatim under its own
header name — unmodeled. Only ``nct_id`` is used downstream (to fetch the trial
from ClinicalTrials.gov); the rest rides along as provenance in ``_raw._west``.

Rows without an nct_id are skipped — nothing to fetch.

The workbook keeps a stable filename and is replaced in place rather than
versioned, so its mtime is the only record of which version fed a run. Every
row is stamped with it as ``source_modified_at`` (ISO-8601 UTC), which ends up
in ``_raw._west.source_modified_at`` — deliberately excluded from
``compute_trial_hash`` (see ``_VOLATILE_RAW_KEYS`` in trials_lifecycle.py), or
just touching the file would make every West trial look changed.
"""
from datetime import UTC, datetime
from pathlib import Path

import openpyxl

from ..schemas.raw.models import RawWestTrial


def load(path: str | Path) -> list[RawWestTrial]:
    path = Path(path)
    source_modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()

    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)

    header = next(rows, None)
    if header is None:
        return []
    headers = [str(h).strip() if h is not None else None for h in header]

    nct_col = next(
        (i for i, h in enumerate(headers) if h is not None and h.lower() == "nct_id"),
        None,
    )
    if nct_col is None:
        raise ValueError(
            "West sheet has no 'nct_id' column "
            f"(headers found: {[h for h in headers if h]})"
        )

    trials: list[RawWestTrial] = []
    for row in rows:
        nct = row[nct_col] if nct_col < len(row) else None
        if not nct:
            continue
        # Every non-nct column, keyed by its own header name, Nones dropped.
        data: dict = {"nct_id": str(nct).strip()}
        for i, value in enumerate(row):
            if i == nct_col or i >= len(headers):
                continue
            name = headers[i]
            if name is not None and value is not None:
                data[name] = value
        # Provenance, not sheet content — set after the column loop so it always
        # wins over a coincidentally-named column.
        data["source_modified_at"] = source_modified_at
        trials.append(RawWestTrial.model_validate(data))

    return trials
