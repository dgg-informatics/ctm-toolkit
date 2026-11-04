"""Genomic keyword prefilter: skip the AI call on pieces with nothing molecular in them.

Most criteria (labs, consent, ECOG, prior therapy) can't hold a genomic node or ref,
and the genomic prompt is the expensive one. On 74 real trials (2026-11-04) the
filter skipped ~61% of genomic calls and kept all 116 real genomic nodes.
"""
import json

import pytest
from llm_fakes import QueuedClient

from ctm import llm_cli
from ctm.transformers.genomic_ctml import KEYWORD_PREFILTER, draft_genomic, looks_molecular

KNOWN = {"MYCN", "BRAF", "AR"}

MOLECULAR = [
    "Documented activating EGFR mutation",
    "KRAS G12C",
    "MYCN amplification",
    "ALK rearrangement",
    "NTRK1 fusion-positive solid tumor",
    "MSI-H or dMMR tumors",
    "TMB >= 10 mut/Mb",
    "HER2-positive (IHC 3+) breast cancer",
    "TP53 gene mutation analysis performed at any CLIA-approved laboratory",
    "Deleterious germline BRCA1 or BRCA2 mutation",
    # Real refs a first draft missed (2026-11-04):
    "BRAFV600E",
    "p53wt",
    "ER+ breast cancer",
    "APL with t(15;17)",
    "Lynch syndrome",
    "known succinate dehydrogenase deficiency",
    "no isochromosome 17q",
    # A known gene shorter than the generic gene pattern allows:
    "AR-positive disease",
]

NOT_MOLECULAR = [
    "Patient must have an Eastern Cooperative Oncology Group (ECOG) performance status of 0-2",
    "Hemoglobin >= 9 g/dL (obtained =< 14 days prior to randomization)",
    "The patient must provide study-specific informed consent prior to study entry",
    "HIV-infected patients on effective anti-retroviral therapy with undetectable viral load",
    "Measurable disease per RECIST 1.1",
    "Lactating females who plan to breastfeed their infants",
    "Life expectancy more than 6 months",
    "Prior treatment with cisplatin required",
    "Serum creatinine <= 1.5 x ULN and AST/ALT <= 3 x ULN",
    "Lung Non-Small Cell Carcinoma",
]


@pytest.mark.parametrize("text", MOLECULAR)
def test_molecular_pieces_are_sent(text):
    assert looks_molecular(text, KNOWN), text


@pytest.mark.parametrize("text", NOT_MOLECULAR)
def test_non_molecular_pieces_are_skipped(text):
    assert not looks_molecular(text, KNOWN), text


def _crit(text):
    return {"text": text, "sub_criteria": []}


def _resp(node=None, refs=()):
    return json.dumps({"node": node, "refs": list(refs)})


MYCN = {"genomic": {"hugo_symbol": "MYCN", "variant_category": "Copy Number Variation",
                    "wildtype": False}}


def _trial():
    return {"nct_id": "NCT1",
            "eligibility": {"inclusion": [_crit("MYCN amplification"), _crit("ECOG 0-2")],
                            "exclusion": [_crit("Pregnant or breastfeeding")]},
            "_raw": {"octsu_genes_interest": "see protocol"}}


def test_skipped_pieces_get_a_null_entry_without_a_call():
    client = QueuedClient([_resp(MYCN), _resp()])   # MYCN piece + genes field only
    cur = draft_genomic(_trial(), {}, client, KNOWN)["_llm_curation"]
    assert client.call_count == 2
    skipped = [e for e in cur["_null_ctml_suggestions"] if e["mapping_source"] == KEYWORD_PREFILTER]
    assert sorted(e["text"] for e in skipped) == ["ECOG 0-2", "Pregnant or breastfeeding"]
    assert all(e["node_type"] == "genomic" and e["suggested_node"] is None for e in skipped)
    sections = cur["parsed_sections"]["genomic"]
    assert sections["eligibility.inclusion"] == {"found": True, "scanned": True, "pieces": 2, "skipped": 1}
    assert sections["eligibility.exclusion"]["skipped"] == 1


def test_curator_genes_field_is_always_sent():
    """_raw.octsu_genes_interest exists to name genes; never second-guess it."""
    client = QueuedClient([_resp(MYCN), _resp()])
    cur = draft_genomic(_trial(), {}, client, KNOWN)["_llm_curation"]
    genes = [e for e in cur["_ctml_suggestions"] + cur["_null_ctml_suggestions"]
             if e["source"] == "_raw.octsu_genes_interest"]
    assert genes and genes[0]["mapping_source"] == "ai"
    assert cur["parsed_sections"]["genomic"]["_raw.octsu_genes_interest"]["skipped"] == 0


def test_prefilter_off_sends_every_piece():
    client = QueuedClient([_resp(MYCN)] + [_resp()] * 3)
    cur = draft_genomic(_trial(), {}, client, KNOWN, prefilter=False)["_llm_curation"]
    assert client.call_count == 4
    assert not any(e["mapping_source"] == KEYWORD_PREFILTER
                   for e in cur["_ctml_suggestions"] + cur["_null_ctml_suggestions"])


def test_cli_all_pieces_flag():
    assert llm_cli.build_parser().parse_args(["genomic"]).all_pieces is False
    assert llm_cli.build_parser().parse_args(["genomic", "--all-pieces"]).all_pieces is True
