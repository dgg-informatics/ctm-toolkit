"""Genomic CTML is checked against matchengine's trial-side vocabulary in code."""
import logging

import pytest

from ctm.transformers.genomic_ctml import validate_genomic_node


def g(**fields):
    return {"genomic": fields}


def test_valid_mutation_passes():
    node = g(hugo_symbol="EGFR", variant_category="Mutation", wildtype=False)
    assert validate_genomic_node(node) == node


def test_patient_side_category_dropped():
    assert validate_genomic_node(g(hugo_symbol="EGFR", variant_category="MUTATION")) == g(hugo_symbol="EGFR")


def test_bang_mutation_dropped():
    assert validate_genomic_node(g(hugo_symbol="KRAS", variant_category="!Mutation", wildtype=True)) == \
        g(hugo_symbol="KRAS", wildtype=True)


def test_wildtype_string_dropped():
    out = validate_genomic_node(g(hugo_symbol="BRAF", variant_category="Mutation", wildtype="false"))
    assert out == g(hugo_symbol="BRAF", variant_category="Mutation", wildtype=False)  # dropped, then defaulted


def test_wildtype_required_defaults_false(caplog):
    with caplog.at_level(logging.INFO):
        out = validate_genomic_node(g(hugo_symbol="KRAS", variant_category="Mutation"))
    assert out == g(hugo_symbol="KRAS", variant_category="Mutation", wildtype=False)
    assert any(getattr(r, "event", None) == "llm.genomic_wildtype_default" for r in caplog.records)
    assert validate_genomic_node(g(hugo_symbol="BRCA2", variant_category="Mutation", wildtype=True))["genomic"]["wildtype"] is True
    assert "wildtype" not in validate_genomic_node(g(mmr_status="MMR-Deficient"))["genomic"]


@pytest.mark.parametrize("category", ["Copy Number Variation", "Structural Variation"])
def test_wildtype_defaults_for_cnv_and_sv(category):
    assert validate_genomic_node(g(hugo_symbol="ALK", variant_category=category))["genomic"]["wildtype"] is False


def test_protein_change_needs_p_prefix():
    for key, good in (("protein_change", "p.V600E"), ("wildcard_protein_change", "p.V600")):
        assert validate_genomic_node(g(hugo_symbol="BRAF", **{key: good})) == g(hugo_symbol="BRAF", **{key: good})
        assert validate_genomic_node(g(hugo_symbol="BRAF", **{key: good[2:]})) == g(hugo_symbol="BRAF")


def test_cnv_call_vocabulary():
    base = {"hugo_symbol": "MYCN", "variant_category": "Copy Number Variation", "wildtype": False}
    assert validate_genomic_node(g(**base, cnv_call="High Amplification")) == g(**base, cnv_call="High Amplification")
    assert validate_genomic_node(g(**base, cnv_call="amp")) == g(**base)


def test_mmr_ms_values_per_key():
    for v in ("MMR-Deficient", "MMR-Proficient"):
        assert validate_genomic_node(g(mmr_status=v)) == g(mmr_status=v)
    for v in ("MSI-H", "MSI-L", "MSS"):
        assert validate_genomic_node(g(ms_status=v)) == g(ms_status=v)
    assert validate_genomic_node(g(mmr_status="MSI-H")) is None
    assert validate_genomic_node(g(ms_status="MMR-Deficient")) is None
    assert validate_genomic_node(g(mmr_status="Deficient (MMR-D / MSI-H)")) is None
    assert validate_genomic_node(g(ms_status="Deficient (MMR-D / MSI-H)")) is None


def test_variant_classification_and_exon():
    base = {"hugo_symbol": "EGFR", "variant_category": "Mutation", "wildtype": False}
    assert validate_genomic_node(g(**base, variant_classification="Missense_Mutation", exon=19)) == \
        g(**base, variant_classification="Missense_Mutation", exon=19)
    assert validate_genomic_node(g(**base, variant_classification="missense")) == g(**base)
    assert validate_genomic_node(g(**base, exon="19")) == g(**base)
    assert validate_genomic_node(g(**base, exon=True)) == g(**base)


def test_unknown_keys_and_clinical_leaves_dropped():
    assert validate_genomic_node(g(hugo_symbol="X", foo=1)) == g(hugo_symbol="X")
    assert validate_genomic_node({"clinical": {"gender": "Female"}}) is None
    assert validate_genomic_node({"and": [{"clinical": {"gender": "Female"}}, g(hugo_symbol="X")]}) == g(hugo_symbol="X")


def test_and_or_recursion():
    a, b = g(ms_status="MSI-H"), g(mmr_status="MMR-Deficient")
    assert validate_genomic_node({"or": [a, b]}) == {"or": [a, b]}
    assert validate_genomic_node({"or": [a, g(mmr_status="bogus")]}) == a
    assert validate_genomic_node(None) is None
    assert validate_genomic_node("junk") is None


def test_invalid_drop_is_logged(caplog):
    with caplog.at_level(logging.INFO):
        validate_genomic_node(g(hugo_symbol="X", mmr_status="bogus"))
    hits = [r for r in caplog.records if getattr(r, "event", None) == "llm.genomic_invalid"]
    assert hits and hits[0].field == "mmr_status"
