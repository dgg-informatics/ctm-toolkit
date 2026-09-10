"""Derive one deduplicated trial set from the master, for matching.

sparrow-api and west resolve against ClinicalTrials.gov; amc comes from OnCore
CTMS with its own protocol numbers and its own (often edited) titles. The same
NCT therefore appears in the master two to four times, and matchengine emits one
match per document — so a patient's report shows the same trial repeatedly with
different match reasons.

This module picks which document represents a trial. It does not merge: the
winning document is returned as is, plus two keys. Nothing upstream changes, and
``07_filtered_trials`` is regenerable from ``06_master_trials`` at any time.

Pure: no Mongo, no I/O, no clock. Every ordering is explicit, so the same master
always produces the same filtered set.
"""
import hashlib
import re

from .eligibility_to_ctml import _criterion_full_text

# Highest precedence first. AMC is the local enrolling site and the freshest
# source (OnCore is updated far more often than ClinicalTrials.gov), so where a
# trial appears in more than one source, AMC's document represents it. Measured
# on the 2026-09-08 master, these are the only three entities present
# (amc 279, west 108, sparrow-api 42).
ENTITY_PRECEDENCE = ("amc", "sparrow-api", "west")

_SECTIONS = ("inclusion", "exclusion")

_WHITESPACE = re.compile(r"\s+")

# Reflowed text and smart-quote substitution are the differences between two
# copies of one study that carry no clinical meaning. Deliberately NOT
# lowercasing or stripping punctuation: the same rule that hides "ECOG" vs
# "ecog" would also hide "<= 2" vs ">= 2".
_PUNCTUATION = str.maketrans({
    chr(0x2018): "'",  # Left single quotation mark U+2018 -> apostrophe
    chr(0x2019): "'",  # Right single quotation mark U+2019 -> apostrophe
    chr(0x201A): "'",  # Single low-9 quotation mark U+201A -> apostrophe
    chr(0x201C): '"',  # Left double quotation mark U+201C -> quotation mark
    chr(0x201D): '"',  # Right double quotation mark U+201D -> quotation mark
    chr(0x2013): "-",  # En dash U+2013 -> hyphen-minus
    chr(0x2014): "-",  # Em dash U+2014 -> hyphen-minus
    chr(0x2212): "-",  # Minus sign U+2212 -> hyphen-minus
    chr(0x00A0): " ",  # Non-breaking space U+00A0 -> space
})


def normalize_criterion_text(text: str) -> str:
    """Comparison form of a criterion's text: punctuation folded, whitespace collapsed."""
    return _WHITESPACE.sub(" ", (text or "").translate(_PUNCTUATION)).strip()


def criterion_hash(criterion: dict, section: str) -> str:
    """Content identity of one eligibility bullet, including its sub-criteria.

    ``section`` is part of the hash because identical text means the opposite
    thing under inclusion and exclusion — mirroring the section-qualified
    stage-03 LLM cache key in ``eligibility_to_ctml``.
    """
    normalized = normalize_criterion_text(_criterion_full_text(criterion))
    return hashlib.sha256(f"{section}:{normalized}".encode()).hexdigest()


def eligibility_fingerprint(eligibility: dict | None) -> str:
    """Order-insensitive fingerprint of an eligibility block.

    Sorting the hashes is what makes two copies of one study compare equal when a
    source reorders its criteria. Used for comparison only and never stored:
    ``07_filtered_trials`` is regenerable, so a cached fingerprint would only be
    an opportunity to drift.
    """
    hashes = {
        criterion_hash(criterion, section)
        for section in _SECTIONS
        for criterion in ((eligibility or {}).get(section) or [])
    }
    return hashlib.sha256("\n".join(sorted(hashes)).encode()).hexdigest()


def group_key(trial: dict) -> str:
    """What makes two documents candidates for being the same trial.

    NCT first, ``protocol_no`` as fallback — measured at exactly one AMC trial
    with no NCT on the 2026-09-08 master. Such a trial simply never groups with
    anything else, which is correct: without an NCT nothing can be known to be
    the same trial.
    """
    key = trial.get("nct_id") or trial.get("protocol_no")
    if not key:
        raise ValueError(
            "trial has neither nct_id nor protocol_no, so it cannot be grouped: "
            f"trial_hash={trial.get('trial_hash')!r}"
        )
    return key


def winning_entity(rows: list[dict]) -> str | None:
    """The highest-precedence entity present among ``rows``.

    An entity added upstream without being added to ENTITY_PRECEDENCE sorts
    after the known ones by name, so the choice stays deterministic instead of
    depending on input order.
    """
    present = {row.get("entity") for row in rows if row.get("entity")}
    if not present:
        return None
    for entity in ENTITY_PRECEDENCE:
        if entity in present:
            return entity
    return sorted(present)[0]


def _grouped(rows: list[dict]) -> dict[str, list[dict]]:
    """Rows bucketed by group_key, keys in sorted order for deterministic output."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(group_key(row), []).append(row)
    return {key: groups[key] for key in sorted(groups)}


def filter_trials(rows: list[dict]) -> list[dict]:
    """The subset of master rows that represents each trial exactly once.

    Two steps per group:

    1. **Entity precedence.** Only the highest-precedence entity's rows survive.
       Content is not consulted — a trial present at AMC is represented by AMC's
       document even when another source's copy is more detailed, because AMC's
       criteria are the operative ones for enrolment there.
    2. **Eligibility collapse.** Among the winning entity's rows, those with
       identical eligibility are true duplicates and collapse to the one with the
       lowest ``trial_hash``; those with differing eligibility are distinct studies
       sharing an NCT (measured: two AMC protocols under NCT02445222) and are all
       kept.

    ``treatment_list`` is excluded from the equality test because it is
    hand-curated and can differ subtly between copies of one study;
    ``short_title`` is excluded because AMC edits titles. Including either would
    wrongly preserve duplicates. Eligibility drives matching, so it is the whole
    test.

    Each returned document is its source row verbatim plus ``entities`` (every
    contributing row's entity, sorted, duplicates retained) and
    ``filtered_reason``.
    """
    filtered = []
    for group in _grouped(rows).values():
        entities = sorted(row.get("entity") for row in group if row.get("entity"))
        winner = winning_entity(group)

        # Sorted by trial_hash so the first row of each fingerprint bucket is the
        # lowest-hash one, making the collapse tie-break fall out of the ordering.
        candidates = sorted(
            (row for row in group if row.get("entity") == winner),
            key=lambda row: row.get("trial_hash") or "",
        )

        buckets: dict[str, list[dict]] = {}
        for row in candidates:
            buckets.setdefault(eligibility_fingerprint(row.get("eligibility")), []).append(row)

        for members in buckets.values():
            if len(members) > 1:
                reason = "same-nct-duplicate-eligibility"
            elif len(group) == 1:
                reason = "unique-nct"
            elif len(buckets) > 1:
                reason = "same-nct-unique-eligibility"
            else:
                reason = "entity-precedence"
            filtered.append({**members[0], "entities": entities, "filtered_reason": reason})
    return filtered
