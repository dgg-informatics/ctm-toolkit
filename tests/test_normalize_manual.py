"""Tests for normalize_manual.py — raw Excel rows → normalized docs."""
from pathlib import Path

import pytest

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


# ── protein_change: the `p.` prefix is added when a curator omits it ──────────
# matchengine matches TRUE_PROTEIN_CHANGE as an exact string, so a bare "L858R"
# silently never matches. Only HGVS-shaped values are prefixed; free text and
# other HGVS prefixes ride through untouched (and are warned about at transform
# time, see test_to_matchminer).

@pytest.mark.parametrize("value,expected", [
    # already prefixed — idempotent, and a stray capital is fixed
    ("p.L858R", "p.L858R"),
    ("P.L858R", "p.L858R"),
    # one- and three-letter substitutions
    ("L858R", "p.L858R"),
    ("T790M", "p.T790M"),
    ("Leu858Arg", "p.Leu858Arg"),
    # nonsense, frameshift, indel, synonymous, predicted
    ("Q192*", "p.Q192*"),
    ("V600fs", "p.V600fs"),
    ("L747fs*12", "p.L747fs*12"),
    ("E746_A750del", "p.E746_A750del"),
    ("V769_D770insASV", "p.V769_D770insASV"),
    ("L858=", "p.L858="),
    ("(L858R)", "p.(L858R)"),
    # whitespace is trimmed before the check
    ("  L858R  ", "p.L858R"),
    # blank → unset
    (None, None),
    ("", None),
    ("   ", None),
    # not a protein change — left exactly as typed
    ("c.2573T>G", "c.2573T>G"),
    ("g.55259515T>G", "g.55259515T>G"),
    ("Exon 19 deletion", "Exon 19 deletion"),
    ("splice site", "splice site"),
    ("MSI-High", "MSI-High"),
    ("l858r", "l858r"),
])
def test_protein_change_prefix_normalization(value, expected):
    row = RawFinding.model_validate({
        "pt_uuid": "pt_0000001", "report_uuid": "rp_0000001",
        "biomarker": "EGFR", "variant_category": "MUTATION", "protein_change": value,
    })
    assert normalize_finding(row, source="tempus").protein_change == expected


def test_raw_finding_keeps_the_cell_verbatim():
    """The prefix is added on the normalized Finding; RawFinding stays a faithful
    mirror of the sheet."""
    row = RawFinding.model_validate({
        "pt_uuid": "pt_0000001", "report_uuid": "rp_0000001", "protein_change": "L858R",
    })
    assert row.protein_change == "L858R"
