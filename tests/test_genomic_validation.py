"""Genomic CTML is checked against matchengine's trial-side vocabulary in code."""
import logging

import pytest

from ctm.transformers.genomic_ctml import validate_genomic_node


def g(**fields):
    return {"genomic": fields}


def test_patient_side_category_drops_the_leaf():
    assert validate_genomic_node(g(hugo_symbol="EGFR", variant_category="MUTATION")) is None


def test_bang_mutation_is_valid_and_defaults_wildtype_false():
    """"No documented KRAS mutation" = !Mutation + wildtype false (keeps WT and untested)."""
    no_known = g(hugo_symbol="KRAS", variant_category="!Mutation", wildtype=False)
    assert validate_genomic_node(no_known) == no_known
    assert validate_genomic_node(g(hugo_symbol="KRAS", variant_category="!Mutation")) == no_known


@pytest.mark.parametrize("category", ["!Copy Number Variation", "!Structural Variation"])
def test_bang_on_cnv_and_sv_drops_the_leaf(category):
    assert validate_genomic_node(g(hugo_symbol="ALK", variant_category=category, wildtype=False)) is None


def test_signature_leaves_never_carry_wildtype():
    assert validate_genomic_node(g(ms_status="MSS")) == g(ms_status="MSS")
    assert validate_genomic_node(g(ms_status="MSS", wildtype=False)) is None
    assert validate_genomic_node(g(hugo_symbol="MLH1", mmr_status="MMR-Deficient", wildtype=True)) is None


def test_wildtype_string_drops_the_leaf():
    assert validate_genomic_node(g(hugo_symbol="BRAF", variant_category="Mutation", wildtype="false")) is None


def test_wildtype_required_defaults_false(caplog):
    with caplog.at_level(logging.INFO):
        out = validate_genomic_node(g(hugo_symbol="KRAS", variant_category="Mutation"))
    assert out == g(hugo_symbol="KRAS", variant_category="Mutation", wildtype=False)
    assert any(getattr(r, "event", None) == "llm.genomic_wildtype_default" for r in caplog.records)
    assert validate_genomic_node(g(hugo_symbol="BRCA2", variant_category="Mutation", wildtype=True))["genomic"]["wildtype"] is True
    assert "wildtype" not in validate_genomic_node(g(mmr_status="MMR-Deficient"))["genomic"]


def test_protein_change_needs_p_prefix():
    for key, good in (("protein_change", "p.V600E"), ("wildcard_protein_change", "p.V600")):
        assert validate_genomic_node(g(hugo_symbol="BRAF", **{key: good})) == g(hugo_symbol="BRAF", **{key: good})
        assert validate_genomic_node(g(hugo_symbol="BRAF", **{key: good[2:]})) is None


def test_mmr_ms_values_per_key():
    for v in ("MMR-Deficient", "MMR-Proficient"):
        assert validate_genomic_node(g(mmr_status=v)) == g(mmr_status=v)
    for v in ("MSI-H", "MSI-L", "MSS"):
        assert validate_genomic_node(g(ms_status=v)) == g(ms_status=v)
    assert validate_genomic_node(g(mmr_status="MSI-H")) is None
    assert validate_genomic_node(g(ms_status="MMR-Deficient")) is None
    assert validate_genomic_node(g(mmr_status="Deficient (MMR-D / MSI-H)")) is None
    assert validate_genomic_node(g(ms_status="Deficient (MMR-D / MSI-H)")) is None


def test_match_ignored_keys_are_kept_when_valid():
    """2026-11-05: exon, variant_classification and cnv_call stay in the trial even though
    patient docs can't match them yet; matchengine skips them via "ignore": true in
    dfci_config.json. Un-ignoring is then a config change, no AI re-run."""
    base = {"hugo_symbol": "EGFR", "variant_category": "Mutation", "wildtype": False}
    full = g(**base, exon=19, variant_classification="In_Frame_Del")
    assert validate_genomic_node(full) == full
    cnv = g(hugo_symbol="MYCN", variant_category="Copy Number Variation", wildtype=False,
            cnv_call="High Amplification")
    assert validate_genomic_node(cnv) == cnv


def test_invalid_match_ignored_value_is_stripped_not_the_leaf(caplog):
    """Matching ignores these keys, so a bad value only loses that detail; the rest of
    the leaf is still a correct requirement."""
    base = {"hugo_symbol": "EGFR", "variant_category": "Mutation", "wildtype": False}
    with caplog.at_level(logging.INFO):
        for extra in ({"exon": "19"}, {"variant_classification": "missense"}):
            assert validate_genomic_node(g(**base, **extra)) == g(**base), extra
        mycn = {"hugo_symbol": "MYCN", "variant_category": "Copy Number Variation", "wildtype": False}
        assert validate_genomic_node(g(**mycn, cnv_call="amplified")) == g(**mycn)
    hits = [r for r in caplog.records if getattr(r, "event", None) == "llm.genomic_invalid_ignored_key"]
    assert [h.field for h in hits] == ["exon", "variant_classification", "cnv_call"]
    # The gene-scoped rule still applies: no gene, no leaf.
    assert validate_genomic_node(g(exon=19)) is None


def test_unknown_keys_and_clinical_leaves_dropped():
    assert validate_genomic_node(g(hugo_symbol="X", foo=1)) is None
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



# ── final-review fixes ───────────────────────────────────────────────────────

def test_any_invalid_field_drops_the_whole_leaf():
    """Stripping one field widens the requirement (any gene, any exon) — drop instead."""
    assert validate_genomic_node(g(hugo_symbol=7, variant_category="Mutation", wildtype=False)) is None
    assert validate_genomic_node(g(hugo_symbol="EGFR", variant_category="Mutation",
                                   wildtype=False, protein_change="L858R")) is None


def test_gene_scoped_keys_need_a_gene():
    assert validate_genomic_node(g(protein_change="p.V600E")) is None
    assert validate_genomic_node(g(variant_category="Mutation", wildtype=False)) is None
    assert validate_genomic_node(g(ms_status="MSI-H")) == g(ms_status="MSI-H")


def test_hugo_symbol_rejects_negation_and_junk():
    assert validate_genomic_node(g(hugo_symbol="!KRAS")) is None
    assert validate_genomic_node(g(hugo_symbol="KRAS G12C")) is None
    assert validate_genomic_node(g(hugo_symbol="C9orf72")) == g(hugo_symbol="C9orf72")


def test_missing_wildtype_defaults_false_on_every_source():
    for source in ("inclusion", "exclusion", "summary", None):
        for category in ("Mutation", "!Mutation", "Copy Number Variation", "Structural Variation"):
            out = validate_genomic_node(g(hugo_symbol="KRAS", variant_category=category), source=source)
            assert out["genomic"]["wildtype"] is False


def test_sv_wildtype_true_drops_the_leaf():
    """Patient SV docs exist only for detected fusions and matchengine's SV query
    ignores WILDTYPE, so "fusion documented absent" would match fusion-positive patients."""
    assert validate_genomic_node(g(hugo_symbol="ALK", variant_category="Structural Variation",
                                   wildtype=True)) is None
    present = g(hugo_symbol="ALK", variant_category="Structural Variation", wildtype=False)
    assert validate_genomic_node(present) == present


def test_wildtype_null_means_status_known_either_result():
    """2026-11-04: "TP53 gene mutation analysis performed at any CLIA-approved lab".
    No WILDTYPE in the query matches tested patients with either result (a wildtype
    result is a doc with WILDTYPE true); untested patients still don't match."""
    from ctm.transformers.genomic_ctml import validate_genomic_node
    for category in ("Mutation", "Copy Number Variation"):
        node = {"genomic": {"hugo_symbol": "TP53", "variant_category": category, "wildtype": None}}
        assert validate_genomic_node(node) == \
            {"genomic": {"hugo_symbol": "TP53", "variant_category": category}}
    # Only where tested-negative patient docs exist, and only gene-level.
    for bad in ({"hugo_symbol": "TP53", "variant_category": "!Mutation", "wildtype": None},
                {"hugo_symbol": "ALK", "variant_category": "Structural Variation", "wildtype": None},
                {"hugo_symbol": "TP53", "variant_category": "Mutation", "protein_change": "p.R175H",
                 "wildtype": None},
                {"hugo_symbol": "TP53", "wildtype": None}):
        assert validate_genomic_node({"genomic": bad}) is None, bad
    # A forgotten wildtype is still the WT trap and still defaults to false.
    assert validate_genomic_node({"genomic": {"hugo_symbol": "TP53", "variant_category": "Mutation"}}) == \
        {"genomic": {"hugo_symbol": "TP53", "variant_category": "Mutation", "wildtype": False}}


def test_wildtype_true_with_a_protein_change_drops_the_leaf():
    """2026-11-05 (pediatric LGG trial): a documented-wildtype result is a gene-level doc
    with no protein change, so "BRAF p.V600E + wildtype true" matches nobody."""
    for key, value in (("protein_change", "p.V600E"), ("wildcard_protein_change", "p.V600")):
        leaf = g(hugo_symbol="BRAF", variant_category="Mutation", wildtype=True, **{key: value})
        assert validate_genomic_node(leaf) is None, key
    # Gene-level confirmed wildtype and "no documented V600E" stay valid.
    for ok in (g(hugo_symbol="BRAF", variant_category="Mutation", wildtype=True),
               g(hugo_symbol="BRAF", variant_category="!Mutation", protein_change="p.V600E",
                 wildtype=False)):
        assert validate_genomic_node(ok) == ok
