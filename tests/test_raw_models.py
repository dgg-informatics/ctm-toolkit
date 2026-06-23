"""Tests for Raw model data capture — extra columns and renamed fields."""
import pytest
from ctm.schemas.raw.models import (
    RawTempusFinding,
    RawCarisFinding,
    RawAmbryFinding,
    RawAmcNgsFinding,
    RawOgmFinding,
    RawPmlRaraFinding,
    RawTumorBiomarker,
)
from ctm.transformers.normalize_manual import normalize_tempus


def test_tempus_renamed_field_raw_test():
    raw = RawTempusFinding.model_validate({
        "pt_uuid": 1, "report_uuid": 1, "raw_test": "Tempus xT"
    })
    assert raw.raw_test == "Tempus xT"


def test_tempus_renamed_field_raw_therapies_other_indications():
    raw = RawTempusFinding.model_validate({
        "pt_uuid": 1, "report_uuid": 1,
        "raw_therapies_other_indications": "Drug A"
    })
    assert raw.raw_therapies_other_indications == "Drug A"


def test_tempus_captures_undeclared_raw_column():
    raw = RawTempusFinding.model_validate({
        "pt_uuid": 1, "report_uuid": 1, "raw_title": "Some Title"
    })
    assert raw.model_dump()["raw_title"] == "Some Title"


def test_finding_raw_dict_preserves_extra_column():
    raw = RawTempusFinding.model_validate({
        "pt_uuid": 1, "report_uuid": 1,
        "gene": "EGFR", "variant_type": "SNV",
        "raw_test": "Tempus xT", "raw_title": "Appendix A",
    })
    finding = normalize_tempus(raw, source="tempus")
    assert finding.raw["raw_test"] == "Tempus xT"
    assert finding.raw["raw_title"] == "Appendix A"


@pytest.mark.parametrize("model_cls", [
    RawCarisFinding,
    RawAmbryFinding,
    RawAmcNgsFinding,
    RawOgmFinding,
    RawPmlRaraFinding,
    RawTumorBiomarker,
])
def test_raw_finding_models_accept_extra_columns(model_cls):
    row = {"pt_uuid": 1, "report_uuid": 1, "raw_unexpected_column": "value"}
    instance = model_cls.model_validate(row)
    assert instance.model_dump()["raw_unexpected_column"] == "value"
