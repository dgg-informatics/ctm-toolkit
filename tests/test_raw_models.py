"""Tests for the Raw models — extra-column capture, id coercion, wildtype parsing."""
import pytest

from ctm.schemas.raw.models import RawFinding, RawReportMetadata
from ctm.transformers.normalize_manual import normalize_finding


def test_finding_captures_undeclared_raw_column():
    raw = RawFinding.model_validate({
        "pt_uuid": "pt_0000001", "report_uuid": "rp_0000001", "raw_test": "Tempus xT",
    })
    assert raw.model_dump()["raw_test"] == "Tempus xT"


def test_finding_raw_dict_preserves_extra_columns():
    raw = RawFinding.model_validate({
        "pt_uuid": "pt_0000001", "report_uuid": "rp_0000001",
        "biomarker": "EGFR", "variant_category": "MUTATION",
        "raw_test": "Tempus xT", "raw_title": "Appendix A",
    })
    finding = normalize_finding(raw, source="tempus")
    assert finding.raw["raw_test"] == "Tempus xT"
    assert finding.raw["raw_title"] == "Appendix A"


def test_finding_captures_unprefixed_extra_column():
    """Capture is widened to any non-canonical column, prefixed raw_ or not."""
    raw = RawFinding.model_validate({
        "pt_uuid": "pt_0000001", "report_uuid": "rp_0000001",
        "biomarker": "EGFR", "variant_category": "MUTATION",
        "some_unprefixed_note": "keep me",
    })
    finding = normalize_finding(raw, source="tempus")
    assert finding.raw["some_unprefixed_note"] == "keep me"


@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False),
    ("TRUE", True), ("false", False), ("Yes", True), ("no", False),
    (1, True), (0, False),
    (None, None), ("maybe", None),
])
def test_wildtype_coercion(value, expected):
    raw = RawFinding.model_validate({
        "pt_uuid": "pt_0000001", "report_uuid": "rp_0000001", "wildtype": value,
    })
    assert raw.wildtype is expected


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
