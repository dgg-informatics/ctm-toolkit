"""Validation for what crosses the MongoDB boundary.

* the **storage envelope** — the fields ``stamp()`` adds to every stored document
  (``trial_key``, ``trial_hash``, ``processed_with``, ``run_date``, and
  ``diff_status`` on the diff stage), and
* the **LLM-stage additions** under ``_llm_curation``.
"""
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DiffStatus = Literal["unchanged", "changed", "deleted"]


class StorageMetadata(BaseModel):
    """The envelope stamp() puts on every stored trial.
    """
    model_config = ConfigDict(extra='forbid')

    trial_key: str
    trial_hash: str
    processed_with: str
    run_date: str
    diff_status: DiffStatus | None = None

    @field_validator("trial_hash")
    @classmethod
    def _hash_is_sha256(cls, v: str) -> str:
        # The unique key that joins 00_raw_trials to 01_normalized_trials and keys
        # every collection. A malformed one silently breaks those joins.
        if not _SHA256_HEX.match(v):
            raise ValueError(f"trial_hash must be a 64-char sha256 hex digest, got {v!r}")
        return v

    @field_validator("run_date")
    @classmethod
    def _run_date_is_iso(cls, v: str) -> str:
        # Inherited down the whole chain, so a bad value here misdates every later
        # stage of the run.
        if not _ISO_DATE.match(v):
            raise ValueError(f"run_date must be YYYY-MM-DD, got {v!r}")
        return v

    @field_validator("trial_key", "processed_with")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty string")
        return v


class CtmlSuggestion(BaseModel):
    """One match-node suggestion under ``_llm_curation._ctml_suggestions``.

    Written by ``ctm-llm general`` (per criterion and from the title). ``extra``
    is allowed: the body can legitimately grow, and this only guards the fields
    downstream reads.
    """
    model_config = ConfigDict(extra='allow')

    source: str
    text: str
    suggested_node: dict | None = None
    transferred_to_match: bool = False


class BiomarkerReference(BaseModel):
    """One hit under ``_llm_curation.biomarker_references``, from ``ctm-llm biomarkers``."""
    model_config = ConfigDict(extra='allow')

    trial_nct: str
    reference: str
    biomarker: str
    type: str
    section: str = "eligibility"
    in_kb: bool = False


class LlmCuration(BaseModel):
    """The _llm_curation sub-document the two LLM stages build.

    Each stage owns exactly one key and merges rather than rebuilds, so both are
    optional: ``general`` writes ``_ctml_suggestions`` before ``biomarkers`` adds
    ``biomarker_references``, and either may be validated mid-chain.

    The stored key ``_ctml_suggestions`` has a leading underscore, which Pydantic
    treats as a private attribute, so the field is named ``ctml_suggestions`` and
    aliased. ``populate_by_name`` lets it validate from either spelling.
    """
    model_config = ConfigDict(extra='allow', populate_by_name=True)

    ctml_suggestions: list[CtmlSuggestion] = Field(default=[], alias="_ctml_suggestions")
    biomarker_references: list[BiomarkerReference] = []


class CurationProvenance(BaseModel):
    """Who curated a trial's match clause, and when — sticky across merges.

    Deliberately separate from the run envelope. ``processed_with``/``run_date``
    answer "which stage wrote this document, in which run"; these answer "who
    finalized this trial's curation, when", a fact about the trial that must
    survive being carried forward into every later master. They are therefore
    kept out of ``METADATA_FIELDS``, so ``strip_metadata`` leaves them intact on
    read, and set only by ``add-manual`` — never re-stamped by a stage.

    ``curated_by`` is the *method* (``"manual-curation"`` today, leaving room for
    an automated path later); ``curated_by_user`` is the account that ran the
    curation, from ``getpass.getuser()``. The pair supports "trials curated by a
    human in the last N months" without conflating it with the master's build date.
    """
    model_config = ConfigDict(extra='forbid')

    curated_by: str
    curated_by_user: str
    curated_at: datetime

    @field_validator("curated_by", "curated_by_user")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty string")
        return v


def validate_master(stamped: dict) -> None:
    """Raise unless ``stamped`` is fit for 06_master_trials.

    The master is the collection MatchMiner ultimately consumes, so the guard is
    stricter than the per-stage write check: envelope, ``_llm_curation`` **and**
    curation provenance must all be present and well-formed. Merge calls this on
    every document before it writes the master — nothing malformed gets in.
    """
    validate_storage(stamped)
    CurationProvenance(
        curated_by=stamped.get("curated_by"),
        curated_by_user=stamped.get("curated_by_user"),
        curated_at=stamped.get("curated_at"),
    )


def validate_storage(stamped: dict) -> None:
    """Raise if ``stamped`` violates the storage contract. Called by ``stamp()``.

    Validates the envelope always, and ``_llm_curation`` when present. Constructs
    and discards — the caller keeps the original dict, so nothing is dropped or
    reshaped.
    """
    StorageMetadata(
        trial_key=stamped.get("trial_key"),
        trial_hash=stamped.get("trial_hash"),
        processed_with=stamped.get("processed_with"),
        run_date=stamped.get("run_date"),
        diff_status=stamped.get("diff_status"),
    )
    curation = stamped.get("_llm_curation")
    if curation is not None:
        LlmCuration.model_validate(curation)
