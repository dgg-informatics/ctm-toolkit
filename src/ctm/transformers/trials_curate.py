"""LLM curation synthesis stage — runs after ctm-ctml.

For each trial, adds a biomarker-reference scan (an LLM pass over the full
eligibility text, cross-checked against a curated gene/variant knowledge
base) and a title-derived suggestion (running the same suggest_node()
ctm-ctml already uses, but against _summary.long_title instead of a single
criterion), then restructures the trial's LLM-derived fields under
_llm_curation with a unioned final_suggested_ctml.

BIOMARKER_SYSTEM_PROMPT and _parse_json_array below are moved verbatim from
scripts/scan_biomarker_mentions.py (validated against real trial data before
being absorbed here — see docs/superpowers/specs/2026-07-20-trials-curate-design.md).
"""
import hashlib
import json
import os
from pathlib import Path

from .eligibility_to_ctml import _criterion_full_text

BIOMARKER_SYSTEM_PROMPT = """You are scanning clinical trial eligibility criteria for genetic and molecular biomarker requirements.

Given the full eligibility text for one trial, find every mention of a genetic or molecular
alteration or biomarker:
  - gene-level alterations: mutations/variants (SNV, indel), CNV/amplification/deletion,
    fusion/rearrangement, specific HGVS changes (e.g. "EGFR exon 19 deletion", "BRAF V600E")
  - tumor-agnostic molecular markers: MSI, MMR (dMMR/pMMR), TMB, HRD
  - receptor/IHC status when tied to a specific gene/protein: HER2, ER, PR, PD-L1

Do NOT include: general serum tumor markers (AFP, beta-HCG, LDH, CA-125, PSA, CEA), histologic
subtype/diagnosis language (e.g. "yolk sac tumor", "embryonal carcinoma"), or non-molecular lab
values (blood counts, organ function tests).

For each genuine genetic/molecular mention, return an object with:
  biomarker: the gene symbol or marker name (e.g. "BRCA1", "MSI", "MMR", "HER2")
  type: the kind of alteration/marker (e.g. "snv", "cnv", "fusion", "msi", "mmr", "tmb", "ihc", "other")
  reference: the exact quoted snippet of eligibility text that mentions it (as short as possible while still showing the actual reference)

Return ONLY a JSON array of these objects, no markdown code fences, no explanation. If there are no
genetic/molecular mentions, return [].
"""


def _parse_json_array(raw: str) -> list:
    """Strip markdown code fences if present, then parse a JSON array.

    Models sometimes wrap output in ```json ... ``` despite being told not to.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return []
    return result if isinstance(result, list) else []


def _trial_id(trial: dict) -> str:
    return trial.get("nct_id") or trial.get("protocol_no") or "unknown"


def _trial_full_eligibility_text(trial: dict) -> str:
    lines = []
    for source in ("inclusion", "exclusion"):
        for criterion in trial.get("eligibility", {}).get(source, []):
            lines.append(_criterion_full_text(criterion))
    return "\n".join(lines)


def _cache_key(trial_id: str, text: str) -> str:
    return hashlib.md5(f"{trial_id}:{text}".encode()).hexdigest()


def load_cache(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_cache(cache: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2))


def load_known_genes(kb_path: Path) -> set[str]:
    kb = json.loads(kb_path.read_text())
    return {g["name"].upper() for g in kb}


def union_match_nodes(ctml_suggestions: list[dict]) -> list[dict]:
    """Every non-null suggested_node across all sources, flattened. No dedup."""
    return [s["suggested_node"] for s in ctml_suggestions if s.get("suggested_node")]


def scan_biomarkers(trial: dict, client, cache: dict, known_genes: set[str]) -> list[dict]:
    """One LLM call per trial (cache-checked first) scanning the full
    eligibility text for genetic/molecular biomarker mentions."""
    text = _trial_full_eligibility_text(trial)
    if not text.strip():
        return []

    trial_id = _trial_id(trial)
    key = _cache_key(trial_id, text)
    if key in cache:
        hits = cache[key]
    else:
        model = os.environ.get("UMGPT_MODEL", "gpt-4o")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": BIOMARKER_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0,
            max_tokens=2000,
        )
        hits = _parse_json_array(response.choices[0].message.content)
        cache[key] = hits

    results = []
    for hit in hits:
        biomarker = (hit.get("biomarker") or "").strip()
        results.append({
            "trial_nct": trial_id,
            "reference": hit.get("reference", ""),
            "biomarker": biomarker,
            "type": hit.get("type", "other"),
            "in_kb": biomarker.upper() in known_genes,
        })
    return results
