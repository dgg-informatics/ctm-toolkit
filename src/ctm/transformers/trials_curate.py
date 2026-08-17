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

from .eligibility_to_ctml import _criterion_full_text, suggest_node

BIOMARKER_SYSTEM_PROMPT = """You are scanning clinical trial text for genetic and molecular biomarker requirements.

You are given several labelled sections for one trial: TRIAL TITLES, DISEASE KEYWORDS,
CURATOR GENES OF INTEREST, and ELIGIBILITY CRITERIA. Any section may be absent. Scan all
of them — a requirement stated only in the title or only in the curator's gene list counts
just as much as one in the criteria.

CURATOR GENES OF INTEREST is a hand-written gene list (e.g. "IDH1 (R132); IDH2 (R172)").
Every gene there is a genuine biomarker for this trial; quote the gene and any variant
shown alongside it as the reference.

Find every mention of a genetic or molecular alteration or biomarker:
  - gene-level alterations: mutations/variants (SNV, indel), CNV/amplification/deletion,
    fusion/rearrangement, specific HGVS changes (e.g. "EGFR exon 19 deletion", "BRAF V600E")
  - tumor-agnostic molecular markers: MSI, MMR (dMMR/pMMR), TMB, HRD
  - receptor/IHC status when tied to a specific gene/protein: HER2, ER, PR, PD-L1

Do NOT include: general serum tumor markers (AFP, beta-HCG, LDH, CA-125, PSA, CEA), histologic
subtype/diagnosis language (e.g. "yolk sac tumor", "embryonal carcinoma"), or non-molecular lab
values (blood counts, organ function tests).

DISEASE KEYWORDS is mostly diagnosis names, which are NOT biomarkers. Take a hit from that
section only when a keyword names a molecular marker outright — "Metastatic HER2-Negative
Breast Carcinoma" yields HER2, while "Anatomic Stage III Breast Cancer AJCC v8" yields nothing.

For each genuine genetic/molecular mention, return an object with:
  biomarker: the gene symbol or marker name (e.g. "BRCA1", "MSI", "MMR", "HER2")
  type: the kind of alteration/marker (e.g. "snv", "cnv", "fusion", "msi", "mmr", "tmb", "ihc", "other")
  reference: the exact quoted snippet of trial text that mentions it (as short as possible while still showing the actual reference)
  section: which labelled section the reference came from — one of "titles", "disease_keywords",
    "genes_of_interest", "eligibility"

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


def _trial_scan_text(trial: dict) -> str:
    """Everything the biomarker scan reads, with each section labelled.

    Beyond the eligibility criteria, two places carry biomarker language that the
    criteria never mention:

    * ``_summary`` titles and ``disease_keywords`` — a trial title can embed the
      requirement outright ("...in Patients with a Pathogenic BRCA1, BRCA2 or
      PALB2 Mutation") without any criterion restating it.
    * ``_raw.octsu_genes_interest`` — a curator-authored gene list, e.g.
      "IDH1 (R132); IDH2 (R172)". Nothing else in the pipeline reads it.

    Sections are labelled because the prompt asks for a quoted snippet as
    ``reference``; without labels a bare gene list gives the model no context to
    quote from, and a title hit is indistinguishable from a criterion hit.
    """
    sections = []

    summary = trial.get("_summary") or {}
    titles = [summary.get("short_title"), summary.get("long_title")]
    titles = [t for t in titles if t]
    if titles:
        sections.append("TRIAL TITLES:\n" + "\n".join(titles))

    keywords = summary.get("disease_keywords") or []
    if keywords:
        sections.append("DISEASE KEYWORDS:\n" + "; ".join(str(k) for k in keywords))

    genes_of_interest = (trial.get("_raw") or {}).get("octsu_genes_interest")
    if genes_of_interest:
        sections.append(f"CURATOR GENES OF INTEREST:\n{genes_of_interest}")

    eligibility = _trial_full_eligibility_text(trial)
    if eligibility.strip():
        sections.append(f"ELIGIBILITY CRITERIA:\n{eligibility}")

    return "\n\n".join(sections)


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
    """One LLM call per trial (cache-checked first) scanning the trial's titles,
    disease keywords, curator gene list and eligibility text for
    genetic/molecular biomarker mentions.

    Note the cache key is derived from this text, so widening what is scanned
    invalidates every existing entry by design — the input genuinely changed.
    """
    text = _trial_scan_text(trial)
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
            # Which section the hit came from, so a curator can tell a title- or
            # curator-list-derived biomarker from one stated in the criteria.
            "section": hit.get("section", "eligibility"),
            "in_kb": biomarker.upper() in known_genes,
        })
    return results


def curate_trial(trial: dict, client, cache: dict, known_genes: set[str], valid_oncotree: set[str]) -> dict:
    """Restructure one ctm-ctml-drafted trial into the _llm_curation shape:
    adds a summary-sourced suggestion to _ctml_suggestions, scans for
    biomarker references, moves _ctml_suggestions under _llm_curation, and
    unions everything into final_suggested_ctml. Mutates and returns trial.
    """
    suggestions = trial.get("_ctml_suggestions", [])

    long_title = trial.get("_summary", {}).get("long_title")
    if long_title:
        summary_node = suggest_node(long_title, "summary", cache, client, valid_oncotree)
        suggestions.append({
            "source": "summary",
            "text": long_title,
            "suggested_node": summary_node,
            "transferred_to_match": False,
        })

    biomarker_hits = scan_biomarkers(trial, client, cache, known_genes)

    trial.pop("_ctml_suggestions", None)
    trial["_llm_curation"] = {
        "_ctml_suggestions": suggestions,
        "biomarker_references": biomarker_hits,
        "final_suggested_ctml": union_match_nodes(suggestions),
    }
    return trial
