"""MatchMiner patient data schemas — clinical and genomic MongoDB collections.

MatchMiner links documents via SAMPLE_ID (string on both) and CLINICAL_ID
(ObjectId on genomic docs pointing to the clinical doc's _id).

Reference: https://matchminer.gitbook.io
"""
from typing import Any
from pydantic import BaseModel


class MMClinical(BaseModel):
    """One document per patient sample in the MatchMiner `clinical` collection."""
    SAMPLE_ID: str
    ONCOTREE_PRIMARY_DIAGNOSIS_NAME: str | None = None
    BIRTH_DATE: str | None = None           # ISO date string: "1972-03-14"
    VITAL_STATUS: str = "Alive"             # "Alive" | "Deceased"
    GENDER: str | None = None              # "Male" | "Female"
    TUMOR_MUTATIONAL_BURDEN_PER_MEGABASE: float | None = None
    REPORT_DATE: str | None = None         # ISO date string of most recent report


class MMGenomic(BaseModel):
    """One document per alteration in the MatchMiner `genomic` collection."""
    SAMPLE_ID: str
    CLINICAL_ID: Any = None                # ObjectId reference to clinical doc._id
    TRUE_HUGO_SYMBOL: str | None = None
    VARIANT_CATEGORY: str | None = None   # MUTATION | CNV | SV | WT | SIGNATURE
    TRUE_PROTEIN_CHANGE: str | None = None
    TRUE_CDNA_CHANGE: str | None = None
    TRUE_VARIANT_CLASSIFICATION: str | None = None
    TRUE_TRANSCRIPT_EXON: int | None = None
    CNV_CALL: str | None = None
    WILDTYPE: bool = False
    MMR_STATUS: str | None = None
    POLE_STATUS: str | None = None
    APOBEC_STATUS: str | None = None
    TOBACCO_STATUS: str | None = None
    LEFT_PARTNER_GENE: str | None = None
    RIGHT_PARTNER_GENE: str | None = None
