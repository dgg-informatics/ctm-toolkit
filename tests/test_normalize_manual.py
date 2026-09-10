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


# ── protein_change: stored as `p.` + UPPERCASE, always ───────────────────────
# matchengine compares TRUE_PROTEIN_CHANGE as an exact string, so both the
# prefix and the casing are fixed here regardless of what the curator typed.
# A value that isn't shaped like a protein change is prefixed all the same and
# reported at transform time (see test_to_matchminer).

@pytest.mark.parametrize("value,expected", [
    # prefix and case are both canonicalized, however it arrives
    ("p.L858R", "p.L858R"),
    ("P.L858R", "p.L858R"),
    ("L858R", "p.L858R"),
    ("l858r", "p.L858R"),
    ("T790M", "p.T790M"),
    # three-letter codes are UPPERCASED, never translated to the one-letter form
    ("Leu858Arg", "p.LEU858ARG"),
    ("LEU858ARG", "p.LEU858ARG"),
    # nonsense, frameshift, indel, synonymous, predicted
    ("Q192*", "p.Q192*"),
    ("V600fs", "p.V600FS"),
    ("L747fs*12", "p.L747FS*12"),
    ("E746_A750del", "p.E746_A750DEL"),
    ("V769_D770insASV", "p.V769_D770INSASV"),
    ("L858=", "p.L858="),
    ("(L858R)", "p.(L858R)"),
    # whitespace is trimmed first
    ("  L858R  ", "p.L858R"),
    # blank → unset
    (None, None),
    ("", None),
    ("   ", None),
    # not a protein change — prefixed anyway, and reported at transform time
    ("Exon 19 deletion", "p.EXON 19 DELETION"),
    ("EXON 10 DEL", "p.EXON 10 DEL"),
    ("splice site", "p.SPLICE SITE"),
    ("MSI-High", "p.MSI-HIGH"),
    # a cDNA change pasted into the wrong column gets no carve-out either
    ("c.2573T>G", "p.C.2573T>G"),
    ("g.55259515T>G", "p.G.55259515T>G"),
])
def test_protein_change_prefix_normalization(value, expected):
    row = RawFinding.model_validate({
        "pt_uuid": "pt_0000001", "report_uuid": "rp_0000001",
        "biomarker": "EGFR", "variant_category": "MUTATION", "protein_change": value,
    })
    assert normalize_finding(row, source="tempus").protein_change == expected


def test_three_letter_amino_acids_are_never_translated():
    """Uppercasing is the only rewrite. "Leu858Arg" must not become "p.L858R" —
    the residues are recognized so the value isn't flagged, never converted."""
    row = RawFinding.model_validate({
        "pt_uuid": "pt_0000001", "report_uuid": "rp_0000001",
        "biomarker": "EGFR", "variant_category": "MUTATION",
        "protein_change": "Glu746_Ala750del",
    })
    assert normalize_finding(row, source="tempus").protein_change == "p.GLU746_ALA750DEL"


def test_raw_finding_keeps_the_cell_verbatim():
    """Normalization happens on the Finding; RawFinding stays a faithful mirror
    of the sheet."""
    row = RawFinding.model_validate({
        "pt_uuid": "pt_0000001", "report_uuid": "rp_0000001", "protein_change": "L858R",
    })
    assert row.protein_change == "L858R"
