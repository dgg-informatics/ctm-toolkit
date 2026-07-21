"""[BETA / EXPERIMENTAL] Confidence tiering for curated trials — runs after
ctm-mm trials-curate.

This is an early, standalone cut of the idea — the split logic isn't final
and the thresholds (which biomarker types count as "safe") are still being
tuned interactively. The likely long-term home for this is folded directly
into trials_curate.py / trials-curate's own output, not a separate pass.
Treat this module and its CLI command as unstable until that happens.

Splits a curated trials JSON into a high-confidence bucket (safe to
auto-pass without a human curator) and a needs-curation bucket, based on:

  1. whether the trial already has an oncotree_primary_diagnosis somewhere
     in treatment_list.step[0].match, and
  2. whether every _llm_curation.biomarker_references entry (if any) is of
     a caller-supplied "low-actionability" type (e.g. "ihc", "other" —
     free-text mentions the LLM flagged but that don't need a structured
     genomic match node).

Optionally, for trials missing a diagnosis, one LLM pass over
_raw.full_title / _raw.summary_obj attempts to recover one — liberally,
so a title naming multiple diseases (e.g. "...high-grade glioma and diffuse
intrinsic pontine glioma and recurrent medulloblastoma") produces an
OR-wrapped match node covering all of them, not just the first/most
prominent one.
"""
import hashlib
import json
import os
from pathlib import Path

from .oncotree_mapping import diagnoses_to_match_node, fix_oncotree_name
from .trials_curate import _parse_json_array, _trial_id

DIAGNOSIS_EXTRACTION_PROMPT = """You are extracting oncology diagnoses from a clinical trial's title and summary text, to build a MatchMiner CTML match clause.

Read the text and identify EVERY distinct primary cancer diagnosis or tumor type explicitly named as part of the eligible population for this trial. Be liberal: if the text names multiple diseases (e.g. "for patients with X and Y and Z"), include ALL of them, not just the most prominent one.

For each diagnosis found, output the closest matching official OncoTree tumor type name. Prefer a broader parent term over an invented specific subtype when the text doesn't call out one precisely — OncoTree parent nodes match all their children in MatchMiner, so a parent term is safe and often more correct than guessing a narrow leaf.

If the text describes a very broad population with no specific disease named (e.g. "advanced solid tumors", "any malignancy"), output the special value "_SOLID_" for a general solid-tumor population, or "_LIQUID_" for a general hematologic-malignancy population.

Do not invent a diagnosis that isn't actually implied by the text. If nothing can be confidently identified, return an empty array.

Return ONLY a JSON array of strings — no explanation, no markdown fences.

Examples:

Text: "...for newly diagnosed pediatric high-grade glioma and diffuse intrinsic pontine glioma and recurrent medulloblastoma"
→ ["High-Grade Glioma, NOS", "Diffuse Midline Glioma, H3 K27-Altered", "Medulloblastoma"]

Text: "...participants with advanced or metastatic solid tumors"
→ ["_SOLID_"]

Text: "...children, adolescent and young adults with acute lymphoblastic leukemia (ALL)"
→ ["B-Lymphoblastic Leukemia/Lymphoma"]

Text: "A study of drug adherence coaching for pediatric oncology patients"
→ []
"""


def has_oncotree_diagnosis(node) -> bool:
    """Recursively check a match node (or list of nodes) for an
    oncotree_primary_diagnosis clinical field, including inside and/or wrappers."""
    if isinstance(node, dict):
        if "oncotree_primary_diagnosis" in node.get("clinical", {}):
            return True
        for key in ("and", "or"):
            if key in node and any(has_oncotree_diagnosis(child) for child in node[key]):
                return True
        return False
    if isinstance(node, list):
        return any(has_oncotree_diagnosis(item) for item in node)
    return False


def _cache_key(trial_id: str, text: str) -> str:
    return hashlib.md5(f"diagnosis:{trial_id}:{text}".encode()).hexdigest()


def load_cache(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_cache(cache: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2))


def extract_diagnoses(trial: dict, client, cache: dict, valid_oncotree: set[str]) -> list[str]:
    """One LLM call (cache-checked) over a trial's full_title + summary_obj,
    liberally extracting every distinct oncotree-mappable diagnosis named."""
    raw = trial.get("_raw", {})
    text = f"{raw.get('full_title') or ''}\n{raw.get('summary_obj') or ''}".strip()
    if not text:
        return []

    trial_id = _trial_id(trial)
    key = _cache_key(trial_id, text)
    if key in cache:
        raw_names = cache[key]
    else:
        model = os.environ.get("UMGPT_MODEL", "gpt-4o")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": DIAGNOSIS_EXTRACTION_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0,
        )
        raw_names = _parse_json_array(response.choices[0].message.content)
        cache[key] = raw_names

    fixed = []
    for name in raw_names:
        if name in ("_SOLID_", "_LIQUID_"):
            if name not in fixed:
                fixed.append(name)
            continue
        corrected = fix_oncotree_name(name, valid_oncotree)
        if corrected and corrected not in fixed:
            fixed.append(corrected)
    return fixed


def is_high_confidence(trial: dict, allowed_biomarker_types: set[str]) -> bool:
    """A trial is high-confidence if it has a diagnosis in step[0].match and
    every biomarker_references entry (if any) is of an allowed type."""
    steps = trial.get("treatment_list", {}).get("step", [])
    match = steps[0].get("match", []) if steps else []
    if not has_oncotree_diagnosis(match):
        return False

    biomarker_refs = trial.get("_llm_curation", {}).get("biomarker_references", [])
    return all(b.get("type") in allowed_biomarker_types for b in biomarker_refs)


def split_by_confidence(
    trials: list[dict],
    allowed_biomarker_types: set[str],
    recover_diagnosis: bool = False,
    client=None,
    cache: dict | None = None,
    valid_oncotree: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split trials into (high_confidence, needs_curation).

    If recover_diagnosis is True, a trial missing a diagnosis gets one LLM
    extraction attempt against _raw.full_title/_raw.summary_obj before being
    classified; when one or more diagnoses are found, a match node is
    appended to treatment_list.step[0].match (OR-wrapped if multiple),
    mutating the trial in place.
    """
    high_confidence, needs_curation = [], []

    for trial in trials:
        if recover_diagnosis:
            steps = trial.get("treatment_list", {}).get("step", [])
            match = steps[0].get("match", []) if steps else []
            if steps and not has_oncotree_diagnosis(match):
                diagnoses = extract_diagnoses(trial, client, cache, valid_oncotree)
                if diagnoses:
                    steps[0]["match"].append(diagnoses_to_match_node(diagnoses))

        if is_high_confidence(trial, allowed_biomarker_types):
            high_confidence.append(trial)
        else:
            needs_curation.append(trial)

    return high_confidence, needs_curation
