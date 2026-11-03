"""``ctm-llm clinical`` — clinical CTML suggestions for one trial.

One model call per piece of text (each top-level criterion with its
sub-criteria, and the long title), with a clinical-only prompt. Two more
clinical entries need no prompt of their own: ``conditions`` (the keyword
mapping the CTGov transform already applied) and ``disease_site`` (reviewed
table → draft cache → model; see disease_site_to_oncotree).

Everything is written through :func:`llm_curation.merge_stage` as
``node_type: "clinical"`` entries, so the genomic stage's entries and any
legacy pre-2.4.0 entries are left alone.

Cache: ``<cache_dir>/.ctml_clinical_cache.json``, keyed on
``clinical:<prompt hash>:<source>:<text>``; the value is the model's raw
answer, validated on every read. A new file rather than ``.ctml_cache.json``,
because the old file's answers came from the mixed clinical+genomic prompt.
"""
import hashlib
import json
import logging
import os

from .disease_site_to_oncotree import SITE_FIELDS, site_suggestion, trial_sites
from .eligibility_to_ctml import (
    _cache_key,
    _validate_node,
    conditions_suggestion,
    is_content_filter,
)
from .genomic_ctml import strip_fences
from .llm_curation import (
    AGE_GROUP,
    CATEGORYS,
    CLINICAL,
    CONDITIONS,
    EXCLUSION,
    INCLUSION,
    LONG_TITLE,
    MINIMUM_AGE,
    PROMPT_LABELS,
    SEX,
    make_entry,
    merge_stage,
    piece_texts,
    section_row,
)
from .structured_ctml import age_sex_suggestions, amc_categories_suggestion

log = logging.getLogger(__name__)

CLINICAL_SYSTEM_PROMPT = """You are a clinical trial eligibility translator for MatchMiner.

Given a free-text eligibility criterion, output a MatchMiner CTML clinical match node as JSON.
Use only clinical fields. If a criterion is purely genetic or molecular, return null.
Only return null if the criterion truly cannot be expressed with the available fields
(e.g. prior treatment history, lab values with age-dependent thresholds, consent language,
administrative requirements, or enrollment at specific institutions).

If a criterion is partially expressible, output the expressible portion and ignore the rest.
For example, "Patients must have B-ALL with >= 5% blasts" — the blast percentage cannot be
expressed, but the diagnosis can: output {"clinical": {"oncotree_primary_diagnosis": "B-Lymphoblastic Leukemia/Lymphoma"}}.

Available fields:
  clinical:
    age_numerical       - numeric comparison string: ">=18", "<11", ">=11"
    oncotree_primary_diagnosis - OncoTree cancer type name (parent matches all children)
    gender              - "Male" or "Female"
    ecog_score          - numeric comparison string: "<=2", "<=3"

If the source is "exclusion", prefix clinical field values with "!" to negate the match.
  e.g. exclusion diagnosis → {"clinical": {"oncotree_primary_diagnosis": "!Lung"}}
  e.g. exclusion gender    → {"clinical": {"gender": "!Female"}}
  e.g. exclusion list      → {"and": [{"clinical": {"oncotree_primary_diagnosis": "!Dysgerminoma"}},
                                       {"clinical": {"oncotree_primary_diagnosis": "!Seminoma"}}]}

Output format — return ONLY valid JSON, no explanation:
  Single node:   {"clinical": {"age_numerical": ">=18"}}
  AND of nodes:  {"and": [{"clinical": {...}}, {"clinical": {...}}]}
  OR of nodes:   {"or": [{"clinical": {...}}, {"clinical": {...}}]}
  Cannot express: null

Examples:
  "Patients must be >= 18 years of age"
  → {"clinical": {"age_numerical": ">=18"}}

  "ECOG performance status 0, 1, or 2"
  → {"clinical": {"ecog_score": "<=2"}}

  "ECOG performance status 0, 1, 2, or 3"
  → {"clinical": {"ecog_score": "<=3"}}

  "Patients must have a diagnosis of acute lymphoblastic leukemia"
  → {"clinical": {"oncotree_primary_diagnosis": "Acute Lymphoblastic Leukemia"}}

  "Patients must have B-ALL or B lymphoblastic lymphoma"
  → {"clinical": {"oncotree_primary_diagnosis": "B-Lymphoblastic Leukemia/Lymphoma"}}

  "Female patients only"
  → {"clinical": {"gender": "Female"}}

  "Age >= 11 and < 25"
  → {"and": [{"clinical": {"age_numerical": ">=11"}}, {"clinical": {"age_numerical": "<25"}}]}

  Source: exclusion
  "Prior CNS tumor including:
    Primary CNS germ cell tumor
    Spermatocytic seminoma"
  → {"and": [{"clinical": {"oncotree_primary_diagnosis": "!Germ Cell Tumor, Brain"}},
              {"clinical": {"oncotree_primary_diagnosis": "!Seminoma"}}]}

  "Histologically confirmed non-seminoma or seminoma, or AFP >= 1000 ng/mL without histologic confirmation"
  → {"or": [{"clinical": {"oncotree_primary_diagnosis": "Non-Seminomatous Germ Cell Tumor"}},
             {"clinical": {"oncotree_primary_diagnosis": "Seminoma"}}]}

  "Newly diagnosed de novo AML per 2016 WHO classification with or without extramedullary disease; patient must have 1 of the following:"
  → {"clinical": {"oncotree_primary_diagnosis": "Acute Myeloid Leukemia"}}

  "EGFR mutation required"
  → null

  "Prior treatment with cisplatin required"
  → null

  "Serum creatinine based on age/gender table"
  → null

  "Able to speak English"
  → null"""

_PROMPTED_SOURCES = (INCLUSION, EXCLUSION, LONG_TITLE)


def keep_clinical(node: dict | None) -> dict | None:
    """``node`` with every non-clinical leaf removed; None if nothing is left."""
    if not isinstance(node, dict):
        return None
    for key in ("and", "or"):
        if key in node:
            children = [c for c in (keep_clinical(c) for c in node.get(key) or []) if c is not None]
            if not children:
                return None
            return children[0] if len(children) == 1 else {key: children}
    return {"clinical": node["clinical"]} if isinstance(node.get("clinical"), dict) else None


def PROMPT_VERSION() -> str:
    """Short hash of the current prompt; part of every cache key."""
    return hashlib.md5(CLINICAL_SYSTEM_PROMPT.encode()).hexdigest()[:8]


def suggest_clinical(text: str, source: str, cache: dict, client,
                     valid_oncotree: set[str]) -> dict | None:
    """Validated clinical node or None. The cache holds the model's raw answer,
    keyed on prompt version + source + text, and is validated on every read."""
    key = _cache_key(f"clinical:{PROMPT_VERSION()}:{source}:{text}")
    if key not in cache:
        try:
            response = client.chat.completions.create(
                model=os.environ.get("UMGPT_MODEL", "gpt-4o"),
                messages=[
                    {"role": "system", "content": CLINICAL_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Source: {PROMPT_LABELS[source]}\nCriterion: {text}"},
                ],
                temperature=0,
                max_tokens=300,
            )
        except Exception as exc:
            if not is_content_filter(exc):
                raise
            log.warning("  content filter — no clinical suggestion for %s piece: %r", source, text[:80],
                        extra={"event": "llm.content_filter", "criterion_source": source,
                               "stage": "clinical"})
            cache[key] = None
        else:
            try:
                cache[key] = json.loads(strip_fences(response.choices[0].message.content))
            except json.JSONDecodeError:
                cache[key] = None
    return _validate_node(keep_clinical(cache[key]), valid_oncotree)


def _as_entry(suggestion: dict) -> dict:
    """A field-derived suggestion as a clinical entry, keeping its extras."""
    return make_entry(**{
        "source": suggestion["source"], "text": suggestion["text"],
        "node": suggestion.get("suggested_node"), "node_type": CLINICAL,
        **{k: v for k, v in suggestion.items()
           if k not in ("source", "text", "suggested_node", "node_type", "mixed")},
    })


def draft_clinical(trial: dict, cache: dict, client, valid_oncotree: set[str],
                   reviewed: dict, site_drafts: dict) -> dict:
    """New trial with this stage's entries and ``parsed_sections.clinical`` merged in."""
    entries, sections = [], {}
    for source in _PROMPTED_SOURCES:
        texts = piece_texts(trial, source)
        for text in texts:
            node = suggest_clinical(text, source, cache, client, valid_oncotree)
            entries.append(make_entry(source, text, node, CLINICAL, mapping_source="ai"))
        sections[source] = section_row(bool(texts), bool(texts), len(texts))

    field_entries = age_sex_suggestions(trial)
    for one in (conditions_suggestion(trial), amc_categories_suggestion(trial)):
        if one:
            field_entries.append(one)
    for field in SITE_FIELDS:
        site = site_suggestion(trial, reviewed, site_drafts, client, valid_oncotree, field=field)
        if site:
            field_entries.append(site)
    entries += [_as_entry(e) for e in field_entries]

    found = {e["source"] for e in field_entries}
    for source in (MINIMUM_AGE, SEX, AGE_GROUP, CONDITIONS, CATEGORYS):
        sections[source] = section_row(source in found, source in found, int(source in found))
    for field in SITE_FIELDS:
        n_sites = len(trial_sites(trial, field))
        sections[field] = section_row(bool(n_sites), bool(n_sites), n_sites)

    drafted = {k: v for k, v in trial.items() if k != "_ctml_suggestions"}
    drafted["_llm_curation"] = merge_stage(trial.get("_llm_curation"), CLINICAL, entries, sections)
    return drafted
