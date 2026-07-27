"""Tests for to_matchminer.py — Excel-normalized findings -> MatchMiner
clinical/genomic document shapes."""
from ctm.schemas.raw.normalized import Finding, Patient


def _patient(pt_uuid=1, mrn="MRN001"):
    return Patient(pt_uuid=pt_uuid, mrn=mrn)


def test_fusion_negative_produces_wildtype_sv_doc_with_no_partner_genes():
    from ctm.transformers.to_matchminer import to_genomic_docs

    patient = _patient()
    findings = [Finding(pt_uuid=1, report_uuid=1, source="tempus",
                        gene="ALK", variant_type="fusion_negative")]

    docs = to_genomic_docs(patient, findings)

    assert len(docs) == 1
    doc = docs[0]
    assert doc["TRUE_HUGO_SYMBOL"] == "ALK"
    assert doc["VARIANT_CATEGORY"] == "SV"
    assert doc["WILDTYPE"] is True
    assert "LEFT_PARTNER_GENE" not in doc
    assert "RIGHT_PARTNER_GENE" not in doc


def test_fusion_positive_still_splits_partner_genes():
    from ctm.transformers.to_matchminer import to_genomic_docs

    patient = _patient()
    findings = [Finding(pt_uuid=1, report_uuid=1, source="tempus",
                        gene="EML4::ALK", variant_type="fusion")]

    docs = to_genomic_docs(patient, findings)

    assert len(docs) == 1
    doc = docs[0]
    assert doc["TRUE_HUGO_SYMBOL"] == "EML4"
    assert doc["LEFT_PARTNER_GENE"] == "EML4"
    assert doc["RIGHT_PARTNER_GENE"] == "ALK"
    assert doc["VARIANT_CATEGORY"] == "SV"
    assert doc["WILDTYPE"] is False


def test_pertinent_negative_still_produces_wildtype_mutation_doc():
    from ctm.transformers.to_matchminer import to_genomic_docs

    patient = _patient()
    findings = [Finding(pt_uuid=1, report_uuid=1, source="tempus",
                        gene="EGFR", variant_type="pertinent_negative")]

    docs = to_genomic_docs(patient, findings)

    assert len(docs) == 1
    doc = docs[0]
    assert doc["TRUE_HUGO_SYMBOL"] == "EGFR"
    assert doc["VARIANT_CATEGORY"] == "MUTATION"
    assert doc["WILDTYPE"] is True
