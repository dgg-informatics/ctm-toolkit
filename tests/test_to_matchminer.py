"""Tests for to_matchminer.py — normalized findings → MatchMiner clinical/genomic
document shapes.

The golden tests pin the *whole* conversion of the v1 reference workbook: every
row in test-pt-data-v1.0.0.xlsx must transform to exactly the clinical/genomic
JSON in the committed fixtures. Regenerate the goldens (and eyeball the diff)
whenever the workbook or the mapping changes — see scripts note in the PR.
"""
import json
from pathlib import Path

import pytest

from ctm.schemas.raw.normalized import Finding, Patient
from ctm.transformers.excel_reader import read_and_normalize
from ctm.transformers.to_matchminer import to_clinical, to_genomic_docs

FIXTURES = Path(__file__).parent / "fixtures"
WORKBOOK = FIXTURES / "test-pt-data-v1.0.0.xlsx"


def _strip_updated(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "_updated"}


# ── Golden: the whole workbook converts exactly ───────────────────────────────

def test_v1_clinical_matches_golden():
    pts, _, finds = read_and_normalize(WORKBOOK)
    assert len(pts) == 1
    clinical = _strip_updated(to_clinical(pts[0]))
    expected = json.loads((FIXTURES / "pt-clinical-v1.0.0.json").read_text())
    assert clinical == expected


def test_v1_genomic_matches_golden():
    pts, _, finds = read_and_normalize(WORKBOOK)
    genomic = [_strip_updated(d) for d in to_genomic_docs(pts[0], finds)]
    expected = json.loads((FIXTURES / "pt-genomic-v1.0.0.json").read_text())
    assert genomic == expected


def test_v1_non_matchable_rows_are_skipped():
    """24 findings → 21 genomic docs: the 2 blank-signature rows and the 1
    OTHER row produce nothing, everything else does."""
    pts, _, finds = read_and_normalize(WORKBOOK)
    assert len(finds) == 24
    assert len(to_genomic_docs(pts[0], finds)) == 21


def test_v1_other_row_is_dropped_but_kept_in_findings():
    """The IGN / OTHER row is retained in the normalized findings (so it survives
    losslessly into patient_data) but produces no clinical/genomic doc."""
    pts, _, finds = read_and_normalize(WORKBOOK)
    assert any(
        f.biomarker == "IGN" and (f.variant_category or "").upper() == "OTHER"
        for f in finds
    ), "the OTHER fixture row is missing from the normalized findings"

    genomic = to_genomic_docs(pts[0], finds)
    assert not any(g["TRUE_HUGO_SYMBOL"] == "IGN" for g in genomic)
    assert not any(g["VARIANT_CATEGORY"] == "OTHER" for g in genomic)


def test_clinical_sample_id_is_pt_uuid_not_mrn():
    """Clinical/genomic must carry no PHI — SAMPLE_ID is the pt_uuid."""
    pts, _, _ = read_and_normalize(WORKBOOK)
    clinical = to_clinical(pts[0])
    assert clinical["SAMPLE_ID"] == "pt_0000001"
    assert pts[0].mrn not in (clinical["SAMPLE_ID"],)


# ── Targeted unit tests for each mapping ──────────────────────────────────────

def _patient(pt_uuid="pt_0000001", mrn="MRN001"):
    return Patient(pt_uuid=pt_uuid, mrn=mrn)


def _finding(**kw):
    base = {"pt_uuid": "pt_0000001", "report_uuid": "rp_0000001", "source": "tempus"}
    return Finding(**{**base, **kw})


def test_biomarker_maps_to_true_hugo_symbol():
    docs = to_genomic_docs(_patient(), [_finding(biomarker="EGFR", variant_category="MUTATION")])
    assert docs[0]["TRUE_HUGO_SYMBOL"] == "EGFR"


def test_mutation_wildtype_true_false_and_blank_default_false():
    finds = [
        _finding(biomarker="A", variant_category="MUTATION", wildtype=True),
        _finding(biomarker="B", variant_category="MUTATION", wildtype=False),
        _finding(biomarker="C", variant_category="MUTATION", wildtype=None),
    ]
    docs = to_genomic_docs(_patient(), finds)
    assert [d["WILDTYPE"] for d in docs] == [True, False, False]


def test_protein_and_nucleotide_change_map_through():
    docs = to_genomic_docs(_patient(), [
        _finding(biomarker="EGFR", variant_category="MUTATION",
                 protein_change="p.L858R", nucleotide_change="c.2573T>G"),
    ])
    assert docs[0]["TRUE_PROTEIN_CHANGE"] == "p.L858R"
    assert docs[0]["TRUE_CDNA_CHANGE"] == "c.2573T>G"


def test_cnv_call_remaps_to_matchengine_stored_values():
    cases = {
        "High Amplification": "High level amplification",
        "Low Amplification": "Gain",              # the non-obvious one
        "Homozygous Deletion": "Homozygous deletion",
        "Heterozygous Deletion": "Heterozygous deletion",
    }
    for friendly, stored in cases.items():
        docs = to_genomic_docs(_patient(), [
            _finding(biomarker="ERBB2", variant_category="CNV", cnv_call=friendly),
        ])
        assert docs[0]["CNV_CALL"] == stored, friendly


def test_signature_level_remaps_and_collapses_proficient_stable():
    cases = {
        "Deficient": "Deficient (MMR-D / MSI-H)",
        "Proficient": "Proficient (MMR-P / MSS)",
        "Stable": "Proficient (MMR-P / MSS)",     # collapses onto MSS
    }
    for level, stored in cases.items():
        docs = to_genomic_docs(_patient(), [
            _finding(biomarker="MSI", variant_category="SIGNATURE", signature_level=level),
        ])
        assert docs[0]["MMR_STATUS"] == stored, level


@pytest.mark.parametrize("typed", ["Homozygous Deletion", "homozygous deletion", "HOMOZYGOUS DELETION"])
def test_cnv_call_lookup_is_case_insensitive(typed):
    """Input casing is normalized; the stored value keeps matchengine's exact form."""
    docs = to_genomic_docs(_patient(), [
        _finding(biomarker="ERBB2", variant_category="CNV", cnv_call=typed),
    ])
    assert docs[0]["CNV_CALL"] == "Homozygous deletion"


@pytest.mark.parametrize("typed", ["Deficient", "deficient", "DEFICIENT"])
def test_signature_level_lookup_is_case_insensitive(typed):
    docs = to_genomic_docs(_patient(), [
        _finding(biomarker="MSI", variant_category="SIGNATURE", signature_level=typed),
    ])
    assert docs[0]["MMR_STATUS"] == "Deficient (MMR-D / MSI-H)"


def test_blank_signature_level_is_skipped():
    docs = to_genomic_docs(_patient(), [
        _finding(biomarker="MSI", variant_category="SIGNATURE", signature_level=None),
    ])
    assert docs == []


def test_sv_splits_partner_genes_and_has_no_wildtype():
    docs = to_genomic_docs(_patient(), [
        _finding(biomarker="EML4::ALK", variant_category="SV", wildtype=False),
    ])
    doc = docs[0]
    assert doc["TRUE_HUGO_SYMBOL"] == "EML4"
    assert doc["LEFT_PARTNER_GENE"] == "EML4"
    assert doc["RIGHT_PARTNER_GENE"] == "ALK"
    assert doc["VARIANT_CATEGORY"] == "SV"
    assert "WILDTYPE" not in doc          # wildtype applies only to MUTATION/CNV


@pytest.mark.parametrize("value", ["Other", "OTHER", "other"])
def test_other_category_produces_no_genomic_doc(value):
    """Skip is case-insensitive — a curator may type any casing of 'Other'."""
    docs = to_genomic_docs(_patient(), [
        _finding(biomarker="Foo", variant_category=value),
    ])
    assert docs == []
