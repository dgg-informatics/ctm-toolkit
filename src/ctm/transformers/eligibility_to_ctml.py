"""LLM-assisted translation of eligibility free text → CTML match nodes.

Uses an OpenAI-compatible API (UMGPT) to suggest CTML match nodes for each
eligibility criterion. Results are cached by MD5 hash of the criterion text
so unchanged criteria never re-hit the API.

Cache file: data/dump/.ctml_cache.json (gitignored)
"""
import hashlib
import json
import os
from pathlib import Path

CACHE_PATH = Path("data/dump/.ctml_cache.json")

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

If the source is "exclusion", prefix all field values with "!" to negate the match.
  e.g. exclusion diagnosis → {"clinical": {"oncotree_primary_diagnosis": "!Lung"}}
  e.g. exclusion gender    → {"clinical": {"gender": "!Female"}}

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

  "Prior treatment with cisplatin required"
  → null

  "Serum creatinine based on age/gender table"
  → null

  "Able to speak English"
  → null"""


def _cache_key(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2))


def suggest_node(text: str, source: str, cache: dict, client) -> dict | None:
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


def process_trial(trial: dict, cache: dict, client) -> dict:
    # Flatten all criteria texts first so we know total count for progress
    items = []
    for source in ("inclusion", "exclusion"):
        for criterion in trial.get("eligibility", {}).get(source, []):
            texts = [criterion["text"]] + [s["text"] for s in criterion.get("sub_criteria", [])]
            for text in texts:
                items.append((source, text))

    total = len(items)
    # below - used for printing output for every 25% of progress. Sanity check that a trial isn't hanging
    milestones = {int(total * p) for p in (0.25, 0.50, 0.75, 1.0)} - {0}
    suggestions = []

    for i, (source, text) in enumerate(items, 1):
        node = suggest_node(text, source, cache, client)
        suggestions.append({"source": source, "text": text, "suggested_node": node})
        if i in milestones:
            pct = int(i / total * 100)
            cached = sum(1 for _, t in items[:i] if _cache_key(f"{source}:{t}") in cache)
            print(f"  {pct}% ({i}/{total}, {cached} cached)", file=__import__("sys").stderr, flush=True)

    return {**trial, "_ctml_suggestions": suggestions}
