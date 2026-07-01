"""Normalized clinical trial schema shared across all trial sources.

ClinicalTrialNormalized is the common output model for:
  - AMC XML export     (raw_amc_to_ctml)
  - ClinicalTrials.gov (raw_ctgov_to_ctml)
  - Future sources     (Sparrow, West, etc.)

The treatment_list structure mirrors the MatchMiner CTML format so these
documents can be loaded into a MatchMiner trial collection with minimal
further transformation. The match tree (and/or nodes with clinical/genomic
leaves) is populated with what can be derived automatically (age, gender);
the rest requires manual curation.

Reference: https://matchminer.gitbook.io
"""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class CtmlEligibilityCriterion(BaseModel):
    """One eligibility bullet, recursively containing sub-criteria.

    Depth mirrors the source nesting:
      ~text    → top-level criterion
      ~~text   → sub_criteria of its parent
      ~~~text  → sub_criteria of the ~~ item
      ~~~~text → sub_criteria of the ~~~ item (seen in lab-value tables)
    """
    text: str
    sub_criteria: list[CtmlEligibilityCriterion] = Field(default_factory=list)


CtmlEligibilityCriterion.model_rebuild()


class CtmlEligibility(BaseModel):
    """Parsed free-text eligibility criteria with full hierarchy preserved."""
    inclusion: list[CtmlEligibilityCriterion] = Field(default_factory=list)
    exclusion: list[CtmlEligibilityCriterion] = Field(default_factory=list)


class CtmlDoseLevel(BaseModel):
    level_internal_id: int
    level_code: str = "-1"
    level_suspended: str = "N"
    level_description: str | None = None
    match: list[Any] = Field(default_factory=list)


class CtmlArm(BaseModel):
    arm_internal_id: int
    arm_code: str
    arm_description: str | None = None
    arm_suspended: str = "N"
    match: list[Any] = Field(default_factory=list)
    dose_level: list[CtmlDoseLevel] = Field(default_factory=list)


class CtmlStep(BaseModel):
    step_internal_id: int
    step_code: str = "1"
    step_type: str = "Registration"
    match: list[Any] = Field(default_factory=list)
    arm: list[CtmlArm] = Field(default_factory=list)


class CtmlTreatmentList(BaseModel):
    step: list[CtmlStep] = Field(default_factory=list)


class ClinicalTrialNormalized(BaseModel):
    """Normalized clinical trial document, CTML-compatible for MatchMiner."""
    model_config = ConfigDict(populate_by_name=True)
    protocol_no: str | None = None
    nct_id: str | None = None
    status: str | None = None
    entity: str | None = None          # source: "amc" | "ctgov" | "sparrow" | etc.
    treatment_list: CtmlTreatmentList = Field(default_factory=CtmlTreatmentList)
    eligibility: CtmlEligibility = Field(default_factory=CtmlEligibility)
    summary: dict = Field(default_factory=dict)  # serialized as "_summary" by to_ctml_dict()
    raw: dict = Field(default_factory=dict)       # serialized as "_raw" by to_ctml_dict()
