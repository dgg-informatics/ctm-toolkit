"""Tests for normalize_manual.py — raw Excel finding rows -> Finding docs."""
from ctm.schemas.raw.models import RawHenryFordFinding, RawMayoFinding, RawTempusFinding
from ctm.transformers.normalize_manual import (
    SHEET_NORMALIZERS,
    normalize_henry_ford,
    normalize_mayo,
)


def test_mayo_and_henry_ford_registered_in_sheet_normalizers():
    assert "mayo_findings" in SHEET_NORMALIZERS
    assert "henry_ford_findings" in SHEET_NORMALIZERS
    assert SHEET_NORMALIZERS["mayo_findings"] == (RawMayoFinding, normalize_mayo)
    assert SHEET_NORMALIZERS["henry_ford_findings"] == (RawHenryFordFinding, normalize_henry_ford)


def test_normalize_mayo_maps_canonical_fields():
    row = RawMayoFinding(pt_uuid=3, report_uuid=1, accession_no="MA13171504",
                         gene="TP53", protein="p.Q192*", nucleotide="c.574C>T",
                         variant_type="SNV", raw_biomarker="TP53 mutation")
    finding = normalize_mayo(row)

    assert finding.pt_uuid == 3
    assert finding.report_uuid == 1
    assert finding.source == "mayo"
    assert finding.gene == "TP53"
    assert finding.protein == "p.Q192*"
    assert finding.nucleotide == "c.574C>T"
    assert finding.variant_type == "SNV"
    assert finding.raw == {"accession_no": "MA13171504", "raw_biomarker": "TP53 mutation"}


def test_normalize_henry_ford_maps_canonical_fields():
    row = RawHenryFordFinding(pt_uuid=5, report_uuid=6, accession_no="PM24-194",
                              gene="TP53", protein="p.Q192*", nucleotide="c.574C>T",
                              variant_type="SNV", raw_gene="TP53", raw_chro=17,
                              raw_protein_change="p.(Gln192*)")
    finding = normalize_henry_ford(row)

    assert finding.source == "henry_ford"
    assert finding.gene == "TP53"
    assert finding.raw["accession_no"] == "PM24-194"
    assert finding.raw["raw_gene"] == "TP53"
    assert finding.raw["raw_chro"] == 17
    assert finding.raw["raw_protein_change"] == "p.(Gln192*)"


def test_raw_fields_still_drops_none_and_ignores_non_raw_non_accession_keys():
    from ctm.transformers.normalize_manual import normalize_tempus
    row = RawTempusFinding(pt_uuid=1, report_uuid=1, gene="EGFR", variant_type="SNV",
                           raw_test="Tempus xT", raw_result=None)
    finding = normalize_tempus(row)

    assert finding.raw == {"raw_test": "Tempus xT"}
    assert "accession_no" not in finding.raw  # RawTempusFinding has no accession_no field at all
