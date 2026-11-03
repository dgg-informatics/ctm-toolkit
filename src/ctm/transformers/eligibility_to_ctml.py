"""Shared helpers for the LLM curation stages.

The drafting itself lives in ``clinical_ctml`` (``ctm-llm clinical``) and
``genomic_ctml`` (``ctm-llm genomic``). This module keeps what they, and
other code, share: the UMGPT client, the OncoTree name list and clinical-node
validation, content-filter detection, criterion text, JSON caches keyed by MD5,
and the ``conditions`` record.
"""
import hashlib
import json
import logging
import os
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)


def _cache_key(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def fetch_oncotree_names() -> set[str]:
    url = "https://oncotree.info/api/tumorTypes"
    with urllib.request.urlopen(url, timeout=10) as r:
        return {t["name"] for t in json.loads(r.read())}


def _validate_node(node: dict | None, valid: set[str]) -> dict | None:
    """Recursively walk a CTML node and fix/drop invalid oncotree names."""
    from .oncotree_mapping import fix_oncotree_name
    if node is None:
        return None
    # logical wrappers
    for key in ("and", "or"):
        if key in node:
            fixed = [_validate_node(child, valid) for child in node[key]]
            fixed = [c for c in fixed if c is not None]
            return {key: fixed} if fixed else None
    # clinical leaf
    if "clinical" in node:
        clinical = dict(node["clinical"])
        if "oncotree_primary_diagnosis" in clinical:
            fixed = fix_oncotree_name(clinical["oncotree_primary_diagnosis"], valid)
            if fixed is None:
                del clinical["oncotree_primary_diagnosis"]
            else:
                clinical["oncotree_primary_diagnosis"] = fixed
        return {"clinical": clinical} if clinical else None
    # genomic leaf — no oncotree fields, pass through
    return node


def load_cache(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_cache(cache: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2))


def is_content_filter(exc: Exception) -> bool:
    """True if an LLM call failed because a provider content filter rejected it.

    Azure returns this as a hard 400 with ``code='content_filter'`` — deterministic
    for a given prompt, so retrying is pointless. Clinical eligibility text (tumor,
    death, toxicity language) trips it routinely, and one criterion must not crash a
    run of hundreds of trials. Detected without importing openai, which lives behind
    the [llm] extra.
    """
    return getattr(exc, "code", None) == "content_filter" or "content_filter" in str(exc)


def build_client():
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("Run: uv pip install 'ctm-toolkit[llm]'") from None

    api_key = os.environ.get("UMGPT_API_KEY")
    base_url = os.environ.get("UMGPT_BASE_URL")
    if not api_key:
        raise ValueError("UMGPT_API_KEY not set in environment")
    if not base_url:
        raise ValueError("UMGPT_BASE_URL not set in environment")

    # Which model answered is part of a run's provenance: a drafted match node is
    # only reproducible against the model that produced it.
    log.info("LLM: %s via %s", os.environ.get("UMGPT_MODEL") or "gpt-4o", base_url,
             extra={"event": "llm.client", "model": os.environ.get("UMGPT_MODEL") or "gpt-4o",
                    "base_url": base_url})
    return OpenAI(api_key=api_key, base_url=base_url, timeout=30.0)


def _criterion_full_text(criterion: dict, depth: int = 0) -> str:
    """Recursively build indented text for a criterion and all its sub_criteria."""
    indent = "  " * depth
    lines = [f"{indent}{criterion['text']}"]
    for sub in criterion.get("sub_criteria", []):
        lines.append(_criterion_full_text(sub, depth + 1))
    return "\n".join(lines)


def conditions_suggestion(trial: dict) -> dict | None:
    """The ``_raw.conditions`` entry: CTGov conditions keyword-mapped to OncoTree.

    Sparrow, West and DDOTS only (AMC has no conditions). No model call — the
    keyword list in oncotree_mapping, which ``ctm-mm trials`` used to write
    straight into the match tree. Only autofill-ctml writes the match now.
    """
    from .oncotree_mapping import ctgov_conditions_to_oncotree, diagnoses_to_match_node
    conditions = [c for c in (trial.get("_raw") or {}).get("conditions") or [] if c]
    if not conditions:
        return None
    diagnoses = ctgov_conditions_to_oncotree(conditions)
    return {
        "source": "_raw.conditions",
        "text": "; ".join(conditions),
        "suggested_node": diagnoses_to_match_node(diagnoses),
        "transferred_to_match": None,
        "mapping_source": "keyword_oncotree_map",
    }

