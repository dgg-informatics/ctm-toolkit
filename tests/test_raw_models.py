"""Tests for the Raw models — extra-column capture, id coercion, wildtype parsing."""
import pytest

from ctm.schemas.raw.models import RawFinding, RawReportMetadata


def test_finding_captures_undeclared_raw_column():
    raw = RawFinding.model_validate({
        "pt_uuid": "pt_0000001", "report_uuid": "rp_0000001", "raw_test": "Tempus xT",
    })
    assert raw.model_dump()["raw_test"] == "Tempus xT"


@pytest.mark.parametrize("value,expected", [
    (True, "true"), (False, "false"),            # Excel booleans → canonical strings
    ("TRUE", "true"), ("false", "false"),        # word strings, case-insensitive
    ("Indeterminate", "indeterminate"),          # the third valid state
    ("  TRUE  ", "true"),                         # trimmed
    (None, None), ("", None),                    # blank → unset
    ("maybe", "maybe"),                          # invalid passes through lowercased,
    (1, "1"),                                    # to be flagged at transform time —
    (0, "0"),                                    # the column must be TRUE/FALSE/INDETERMINATE
])
def test_wildtype_normalization(value, expected):
    raw = RawFinding.model_validate({
        "pt_uuid": "pt_0000001", "report_uuid": "rp_0000001", "wildtype": value,
    })
    assert raw.wildtype == expected


def test_integer_ids_are_coerced_to_str():
    raw = RawFinding.model_validate({"pt_uuid": 117, "report_uuid": 1})
    assert raw.pt_uuid == "117"
    assert raw.report_uuid == "1"


def test_report_metadata_accepts_extra_columns():
    raw = RawReportMetadata.model_validate({
        "report_uuid": "rp_0000001", "pt_uuid": "pt_0000001", "source": "tempus",
        "case_no": "C-123", "specimen_site": "lung",
    })
    dumped = raw.model_dump()
    assert dumped["case_no"] == "C-123"
    assert dumped["specimen_site"] == "lung"
