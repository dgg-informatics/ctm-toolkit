"""Transform RawDdotsTrial → ClinicalTrialNormalized with entity "sparrow-api".

The legacy Sparrow path and this one differ only in where the trial *list* comes
from:

    sparrow      XLSX  → NCT number → ClinicalTrials.gov
    sparrow-api  DDOTS → NCT number → ClinicalTrials.gov

Clinical content — eligibility included — still comes from ClinicalTrials.gov in
both. The DDOTS payload is stored alongside it under ``_raw._ddots``, separate
from the legacy ``_raw._sparrow``, so a trial's provenance is readable from its
shape without consulting ``entity``.

A distinct entity rather than reusing "sparrow" keeps the legacy pipeline working
untouched and lets both run during the migration. Note they are still the *same
trials*: ``trial_key`` returns ``nct_id`` for both, so running both sources in one
pass yields two entries per shared NCT — the same situation the existing
sparrow/west overlap already produces. ``trial_hash`` separates them in storage
because their ``_raw`` blobs differ.

Teaching the LLM stage to read ``_raw._ddots.eligibility`` is deliberate future
work; see RawDdotsTrial for why the DDOTS text is not a drop-in replacement for
the registry's.
"""
from ..schemas.raw.models import RawDdotsTrial
from .ctgov_to_raw import fetch
from .raw_ctgov_to_ctml import to_ctml

ENTITY = "sparrow-api"


def to_ctml_dict(trial: RawDdotsTrial) -> dict:
    ctgov = fetch(trial.nct_id)
    normalized = to_ctml(ctgov)
    normalized.entity = ENTITY

    d = normalized.model_dump()
    d["_summary"] = d.pop("summary")
    d["_raw"] = d.pop("raw")
    # Keyed _ddots, not _sparrow: the legacy Excel blob has a different shape and
    # conflating them would make the two sources indistinguishable downstream.
    d["_raw"]["_ddots"] = trial.model_dump(exclude={"nct_id"})
    return d
