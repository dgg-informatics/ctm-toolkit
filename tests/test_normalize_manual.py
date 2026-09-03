"""Tests for normalize_manual.py — raw Excel rows → normalized docs."""
from pathlib import Path

from ctm.schemas.raw.models import RawFinding, RawPatientGeneral, RawReportMetadata
from ctm.transformers.excel_reader import read_and_normalize
from ctm.transformers.normalize_manual import (
    normalize_finding,
    normalize_patient,
    normalize_report_metadata,
)

FIXTURE = Path(__file__).parent / "fixtures" / "test-pt-data-v1.0.0.xlsx"


def test_normalize_finding_maps_canonical_fields():
    row = RawFinding.model_validate({
        "pt_uuid": "pt_0000001", "report_uuid": "rp_0000001",
        "biomarker": "EGFR", "variant_category": "MUTATION",
        "protein_change": "p.L858R", "nucleotide_change": "c.2573T>G", "wildtype": False,
    })
    f = normalize_finding(row, source="tempus")
    assert (f.pt_uuid, f.report_uuid, f.source) == ("pt_0000001", "rp_0000001", "tempus")
    assert f.biomarker == "EGFR"
    assert f.variant_category == "MUTATION"
    assert f.protein_change == "p.L858R"
    assert f.nucleotide_change == "c.2573T>G"
    assert f.wildtype == "false"


def test_normalize_finding_captures_all_noncanonical_columns_dropping_none():
    row = RawFinding.model_validate({
        "pt_uuid": "pt_0000001", "report_uuid": "rp_0000001",
        "biomarker": "EGFR", "variant_category": "MUTATION",
        "raw_test": "Tempus xT", "raw_result": None, "unprefixed": "kept",
    })
    f = normalize_finding(row, source="tempus")
    assert f.raw == {"raw_test": "Tempus xT", "unprefixed": "kept"}


def test_normalize_report_metadata_promotes_identity_and_raws_the_rest():
    row = RawReportMetadata.model_validate({
        "report_uuid": "rp_0000001", "pt_uuid": "pt_0000001", "source": "tempus",
        "test_name": "xT CDx", "unique_test_id": "TL-123",
        "unique_test_id_source": "accession_no", "ordering_physician": "Dr. Doe",
        "case_no": "C-9", "specimen_site": "lung",
    })
    m = normalize_report_metadata(row)
    assert m.unique_test_id == "TL-123"
    assert m.unique_test_id_source == "accession_no"
    assert m.ordering_physician == "Dr. Doe"
    assert m.raw == {"case_no": "C-9", "specimen_site": "lung"}


def test_normalize_patient_splits_metastasis_sites_and_keeps_new_fields():
    row = RawPatientGeneral.model_validate({
        "pt_uuid": "pt_0000001", "metastasis_sites": "bone, liver ,breast",
        "referring_clinician": "Dr. Seuss", "source": "manual",
    })
    p = normalize_patient(row)
    assert p.metastasis_sites == ["bone", "liver", "breast"]
    assert p.referring_clinician == "Dr. Seuss"
    assert p.source == "manual"


def test_normalize_patient_promotes_trb_date_and_raws_other_columns():
    row = RawPatientGeneral.model_validate({
        "pt_uuid": "pt_0000001", "trb_date": "2026-08-15", "some_future_col": "kept",
    })
    p = normalize_patient(row)
    assert p.trb_date.isoformat() == "2026-08-15"       # optional, date-coerced
    assert p.raw == {"some_future_col": "kept"}          # any other pt_general column, lossless


def test_reference_workbook_parses_and_joins():
    patients, metadata, findings = read_and_normalize(FIXTURE)
    assert len(patients) == 1
    assert len(metadata) == 1
    assert len(findings) == 24
    assert patients[0].pt_uuid == "pt_0000001"
