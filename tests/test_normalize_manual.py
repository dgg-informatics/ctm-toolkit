"""Tests for normalize_manual.py — raw Excel finding rows -> Finding docs."""
from ctm.schemas.raw.models import (
    RawFoundationFinding,
    RawGuardant360Finding,
    RawHenryFordFinding,
    RawMayoFinding,
    RawTempusFinding,
)
from ctm.transformers.normalize_manual import (
    SHEET_NORMALIZERS,
    normalize_foundation,
    normalize_guardant360,
    normalize_henry_ford,
    normalize_mayo,
)


def test_mayo_and_henry_ford_registered_in_sheet_normalizers():
    assert "mayo_findings" in SHEET_NORMALIZERS
    assert "henry_ford_findings" in SHEET_NORMALIZERS
    assert SHEET_NORMALIZERS["mayo_findings"] == (RawMayoFinding, normalize_mayo)
    assert SHEET_NORMALIZERS["henry_ford_findings"] == (RawHenryFordFinding, normalize_henry_ford)


def test_guardant360_registered_in_sheet_normalizers():
    assert "guardant360_findings" in SHEET_NORMALIZERS
    assert SHEET_NORMALIZERS["guardant360_findings"] == (RawGuardant360Finding, normalize_guardant360)


def test_normalize_guardant360_maps_canonical_fields():
    row = RawGuardant360Finding(pt_uuid=8, report_uuid=1, accession_no="G360-001",
                                gene="PIK3CA", protein="p.E545K", variant_type="SNV",
                                result_summary="1.2% cfDNA or Amplification",
                                raw_percent_cfdna_or_amp=0.012,
                                raw_alteration_trend="rising")
    finding = normalize_guardant360(row)

    assert finding.pt_uuid == 8
    assert finding.report_uuid == 1
    assert finding.source == "guardant360"
    assert finding.gene == "PIK3CA"
    assert finding.protein == "p.E545K"
    assert finding.variant_type == "SNV"
    assert finding.raw == {
        "accession_no": "G360-001",
        "raw_percent_cfdna_or_amp": 0.012,
        "raw_alteration_trend": "rising",
    }


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


def test_foundation_registered_in_sheet_normalizers():
    assert "foundation_findings" in SHEET_NORMALIZERS
    assert SHEET_NORMALIZERS["foundation_findings"] == (RawFoundationFinding, normalize_foundation)


def test_normalize_foundation_maps_canonical_fields():
    row = RawFoundationFinding(
        pt_uuid=117, report_uuid=1, accession_no="FMI-000001",
        gene="EGFR", protein="p.L858R", nucleotide="c.2573T>G",
        variant_type="mutation", result_summary="detected",
        raw_section="Genomic Findings", raw_biomarker="EGFR L858R",
        raw_method="NGS", raw_analyte="DNA", raw_result="Detected",
        raw_benefit="Yes", raw_therapy_assoc="osimertinib",
        raw_variant_interpretation="Known short variant",
        raw_protein_alteration="L858R", raw_exon=21,
        raw_dna_alteration="2573T>G", raw_frequency_pct=42.1,
    )
    finding = normalize_foundation(row)

    assert finding.pt_uuid == 117
    assert finding.report_uuid == 1
    assert finding.source == "foundation"
    assert finding.gene == "EGFR"
    assert finding.protein == "p.L858R"
    assert finding.nucleotide == "c.2573T>G"
    assert finding.variant_type == "mutation"
    assert finding.result_summary == "detected"


def test_normalize_foundation_collects_every_raw_column():
    """raw_variant_interpretation is a plain raw_* pass-through like the rest, so
    _raw_fields() picks it up without a special case."""
    row = RawFoundationFinding(
        pt_uuid=117, report_uuid=1, accession_no="FMI-000001", gene="EGFR",
        raw_section="Genomic Findings", raw_biomarker="EGFR L858R",
        raw_method="NGS", raw_analyte="DNA", raw_result="Detected",
        raw_benefit="Yes", raw_therapy_assoc="osimertinib",
        raw_biomarker_level="high", raw_variant_interpretation="Known short variant",
        raw_protein_alteration="L858R", raw_exon=21, raw_dna_alteration="2573T>G",
        raw_frequency_pct=42.1, raw_genotype="A*02:01", raw_hla_class="Class I",
    )
    finding = normalize_foundation(row)

    assert finding.raw == {
        "accession_no": "FMI-000001",
        "raw_section": "Genomic Findings",
        "raw_biomarker": "EGFR L858R",
        "raw_method": "NGS",
        "raw_analyte": "DNA",
        "raw_result": "Detected",
        "raw_benefit": "Yes",
        "raw_therapy_assoc": "osimertinib",
        "raw_biomarker_level": "high",
        "raw_variant_interpretation": "Known short variant",
        "raw_protein_alteration": "L858R",
        "raw_exon": 21,
        "raw_dna_alteration": "2573T>G",
        "raw_frequency_pct": 42.1,
        "raw_genotype": "A*02:01",
        "raw_hla_class": "Class I",
    }


def test_normalize_foundation_drops_none_raw_fields():
    row = RawFoundationFinding(pt_uuid=117, report_uuid=1, gene="HLA-A",
                               variant_type="genotype", raw_genotype="A*02:01")
    finding = normalize_foundation(row)

    assert finding.raw == {"raw_genotype": "A*02:01"}


def test_foundation_findings_reach_findings_from_the_reference_workbook():
    """End-to-end through the Excel reader: a row that fails the pt_general /
    report_metadata join is dropped with no warning, so registration alone is not
    evidence the sheet works.

    Identified by accession prefix rather than `source`, because excel_reader
    takes source from the matching report_metadata row (the fixture uses "x"/"y")
    and only falls back to the sheet name when no metadata row exists.
    """
    from pathlib import Path

    from ctm.transformers.excel_reader import read_and_normalize

    fixture = Path(__file__).parent / "fixtures" / "test-pt-data-v0.0.1.xlsx"
    _patients, _metadata, findings = read_and_normalize(fixture)

    foundation = [f for f in findings if str(f.raw.get("accession_no", "")).startswith("FMI-")]
    assert len(foundation) == 2, "foundation_findings rows did not survive the join"
    assert {f.gene for f in foundation} == {"EGFR", "HLA-A"}

    egfr = next(f for f in foundation if f.gene == "EGFR")
    assert egfr.raw["raw_variant_interpretation"] == "Known short variant"
    assert egfr.raw["raw_exon"] == 21
    assert egfr.protein == "p.L858R"

    # The HLA row exercises columns the EGFR row leaves empty, and Nones are dropped.
    hla = next(f for f in foundation if f.gene == "HLA-A")
    assert hla.raw["raw_hla_class"] == "Class I"
    assert "raw_protein_alteration" not in hla.raw
