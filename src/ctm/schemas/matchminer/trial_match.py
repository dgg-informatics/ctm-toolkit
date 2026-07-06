"""MatchMiner trial-match export schema (documentation model).

Describes the shape of the JSON export produced by MatchMiner when it runs
the matching engine against a patient. The top-level document is
MMTrialMatchExport; each entry in trial_match is an MMTrialMatch.

This model is not used for runtime validation today — it exists to document
the external contract so field names and types are explicit and searchable.

Reference: MatchMiner API /api/v1/trialmatches export format.
"""
from __future__ import annotations
from pydantic import BaseModel, Field


class MMTrialMatch(BaseModel):
    """One row from MatchMiner's trial_match collection."""
    # Trial identification
    protocol_no: str | None = None
    nct_id: str | None = None
    trial_summary_status: str | None = None  # open to accrual | closed | suspended

    # Match metadata
    match_level: str | None = None        # "arm" | "step"
    reason_type: str | None = None        # "genomic" | "clinical"
    match_type: str | None = None         # "positive" | "negative"
    cancer_type_match: str | None = None  # "specific" | "general" | "all_solid" etc.
    code: str | None = None               # arm/step code matched against
    show_in_ui: bool = False              # MatchMiner filter: only show_in_ui=True shown to clinician

    # Sort key — 6-element list used by MatchMiner to rank matches
    sort_order: list[int] = Field(default_factory=list)

    # Genomic alteration fields (populated when reason_type == "genomic")
    true_hugo_symbol: str | None = None
    true_protein_change: str | None = None
    true_cdna_change: str | None = None
    true_variant_classification: str | None = None
    variant_category: str | None = None
    genomic_alteration: str | None = None
    allele_fraction: float | None = None
    tier: str | None = None
    chromosome: str | None = None
    position: int | None = None
    reference_allele: str | None = None


class MMPatientRef(BaseModel):
    """Minimal patient identifiers embedded in the MM export envelope."""
    SAMPLE_ID: str | None = None
    MRN: str | None = None
    FIRST_NAME: str | None = None
    LAST_NAME: str | None = None


class MMTrialMatchExport(BaseModel):
    """Top-level MatchMiner export document for a single patient run."""
    pt_uuid: str | None = None
    clinical: MMPatientRef = Field(default_factory=MMPatientRef)
    trial_match: list[MMTrialMatch] = Field(default_factory=list)
