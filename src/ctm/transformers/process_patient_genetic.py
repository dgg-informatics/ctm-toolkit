"""Patient genomic data transformer.

Raw input: see ctm/schemas/raw/patient_genetic.py
Processed output: PatientGenetic (ctm/schemas/processed/models.py)
"""
from ctm.schemas.processed.models import PatientGenetic

__version__ = "0.1.0"


def process(raw: dict) -> dict:
    model = PatientGenetic(
        sample_id=raw.get("sample_id"),
        mrn=raw.get("mrn"),
        gene=raw.get("true_hugo_symbol"),
        protein_change=raw.get("true_protein_change"),
        cdna_change=raw.get("true_cdna_change"),
        variant_classification=raw.get("true_variant_classification"),
        variant_category=raw.get("variant_category"),
        allele_fraction=raw.get("allele_fraction"),
        tier=raw.get("tier"),
        chromosome=raw.get("chromosome"),
        position=raw.get("position"),
        reference_allele=raw.get("reference_allele"),
        wildtype=raw.get("wildtype"),
    )
    return model.model_dump()
