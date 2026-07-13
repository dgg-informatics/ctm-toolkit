"""LLM-assisted translation of eligibility free text → CTML match nodes.

Uses an OpenAI-compatible API (UMGPT) to suggest CTML match nodes for each
eligibility criterion. Results are cached by MD5 hash of the criterion text
so unchanged criteria never re-hit the API.

Cache file: data/dump/.ctml_cache.json (gitignored)
"""
import hashlib
import json
import os
import urllib.request
from pathlib import Path

DEFAULT_CACHE_PATH = Path(".ctml_cache.json")

SYSTEM_PROMPT = """You are a clinical trial eligibility translator for MatchMiner.

Given a free-text eligibility criterion, output a MatchMiner CTML match node as JSON.
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
  genomic:
    hugo_symbol         - HUGO gene symbol: "EGFR", "BRAF", "TP53"
    variant_category    - "MUTATION", "CNV", "SV", "SIGNATURE", "WT"
    mmr_status          - "Deficient (MMR-D / MSI-H)" or "Proficient (MMR-P / MSS)"
    variant_classification - e.g. "Missense_Mutation", "Nonsense_Mutation"

If the source is "exclusion", prefix clinical field values with "!" to negate the match.
The "!" negation applies ONLY to clinical fields (oncotree_primary_diagnosis, gender, ecog_score, age_numerical).
Do NOT apply "!" to genomic fields — if the exclusion criterion is genomic-only, return null.
  e.g. exclusion diagnosis → {"clinical": {"oncotree_primary_diagnosis": "!Lung"}}
  e.g. exclusion gender    → {"clinical": {"gender": "!Female"}}
  e.g. exclusion list      → {"and": [{"clinical": {"oncotree_primary_diagnosis": "!Dysgerminoma"}},
                                       {"clinical": {"oncotree_primary_diagnosis": "!Seminoma"}}]}

Output format — return ONLY valid JSON, no explanation:
  Single clinical node:  {"clinical": {"age_numerical": ">=18"}}
  Single genomic node:   {"genomic": {"hugo_symbol": "EGFR", "variant_category": "MUTATION"}}
  AND of nodes:          {"and": [{"clinical": {...}}, {"genomic": {...}}]}
  OR of nodes:           {"or": [{"clinical": {...}}, {"clinical": {...}}]}
  Cannot express:        null

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

  "EGFR mutation required"
  → {"genomic": {"hugo_symbol": "EGFR", "variant_category": "MUTATION"}}

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

  "Prior treatment with cisplatin required"
  → null

  "Serum creatinine based on age/gender table"
  → null

  "Able to speak English"
  → null"""


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


def load_cache(path: Path = DEFAULT_CACHE_PATH) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_cache(cache: dict, path: Path = DEFAULT_CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2))


def suggest_node(text: str, source: str, cache: dict, client, valid_oncotree: set[str]) -> dict | None:
    key = _cache_key(f"{source}:{text}")
    if key in cache:
        return cache[key]

    model = os.environ.get("UMGPT_MODEL", "gpt-4o")
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Source: {source}\nCriterion: {text}"},
        ],
        temperature=0,
        max_tokens=300,
    )

    raw = response.choices[0].message.content.strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = None

    result = _validate_node(result, valid_oncotree)
    cache[key] = result
    return result


def build_client():
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("Run: uv pip install 'ctm-toolkit[llm]'")

    api_key = os.environ.get("UMGPT_API_KEY")
    base_url = os.environ.get("UMGPT_BASE_URL")
    if not api_key:
        raise ValueError("UMGPT_API_KEY not set in environment")
    if not base_url:
        raise ValueError("UMGPT_BASE_URL not set in environment")

    return OpenAI(api_key=api_key, base_url=base_url, timeout=30.0)


def _criterion_full_text(criterion: dict, depth: int = 0) -> str:
    """Recursively build indented text for a criterion and all its sub_criteria."""
    indent = "  " * depth
    lines = [f"{indent}{criterion['text']}"]
    for sub in criterion.get("sub_criteria", []):
        lines.append(_criterion_full_text(sub, depth + 1))
    return "\n".join(lines)


def process_trial(trial: dict, cache: dict, client, valid_oncotree: set[str]) -> dict:
    # One item per top-level criterion; sub_criteria are included in the text
    items = []
    for source in ("inclusion", "exclusion"):
        for criterion in trial.get("eligibility", {}).get(source, []):
            items.append((source, _criterion_full_text(criterion)))

    total = len(items)
    milestones = {int(total * p) for p in (0.25, 0.50, 0.75, 1.0)} - {0}
    suggestions = []

    for i, (source, text) in enumerate(items, 1):
        node = suggest_node(text, source, cache, client, valid_oncotree)
        suggestions.append({"source": source, "text": text, "suggested_node": node, "transferred_to_match": False})
        if i in milestones:
            pct = int(i / total * 100)
            cached = sum(1 for s, t in items[:i] if _cache_key(f"{s}:{t}") in cache)
            print(f"  {pct}% ({i}/{total}, {cached} cached)", file=__import__("sys").stderr, flush=True)

    return {**trial, "_ctml_suggestions": suggestions}
