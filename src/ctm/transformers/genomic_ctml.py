"""``ctm-llm genomic`` — genomic CTML suggestions and molecular refs for one trial.

One model call per piece of text (criteria, long title, conditions, curator
genes of interest; never disease site), returning a genomic node and the
molecular refs found in that piece. Nodes are written through
:func:`llm_curation.merge_stage` as ``node_type: "genomic"`` entries. Refs go
to ``molecular_genomic_refs``, a wider net than the nodes: everything
molecular, including what genomic CTML can't express yet. ``section`` is set
by code from the piece's source, and ``origin`` (germline / somatic /
unknown) comes from the model, coerced to that set.

Cache: ``<cache_dir>/.ctml_genomic_cache.json``, keyed on
``genomic:<source>:<text>``; the value is ``{"node", "refs"}``.

Validation pins every genomic node to the **trial-side** vocabulary matchengine
maps from (the user's key table, 02Nov2026). A wrong ``mmr_status`` /
``ms_status`` value crashes matchengine, and a wrong ``variant_category`` or a
string ``wildtype`` silently matches nothing, so nothing the model writes reaches
a curator unchecked.
"""
import json
import logging
import os

from .eligibility_to_ctml import _cache_key, is_content_filter
from .llm_curation import GENOMIC, GENOMIC_SOURCES, make_entry, merge_stage, piece_texts, section_row
from .trials_curate import _trial_id

log = logging.getLogger(__name__)

VARIANT_CATEGORIES = frozenset({"Mutation", "Copy Number Variation", "Structural Variation"})
CNV_CALLS = frozenset({"High Amplification", "Low Amplification",
                       "Homozygous Deletion", "Heterozygous Deletion"})
MMR_VALUES = frozenset({"MMR-Deficient", "MMR-Proficient"})
MS_VALUES = frozenset({"MSI-H", "MSI-L", "MSS"})
VARIANT_CLASSIFICATIONS = frozenset({"Missense_Mutation", "Nonsense_Mutation", "In_Frame_Del",
                                     "Frame_Shift_Del", "Splice_Site"})
GENOMIC_KEYS = frozenset({"hugo_symbol", "variant_category", "protein_change",
                          "wildcard_protein_change", "wildtype", "cnv_call", "mmr_status",
                          "ms_status", "variant_classification", "exon"})


def _valid_field(key: str, value) -> bool:
    if key == "hugo_symbol":
        return isinstance(value, str) and bool(value.strip())
    if key == "variant_category":
        return value in VARIANT_CATEGORIES
    if key == "wildtype":
        return isinstance(value, bool)
    if key in ("protein_change", "wildcard_protein_change"):
        return isinstance(value, str) and value.startswith("p.") and len(value) > 2
    if key == "cnv_call":
        return value in CNV_CALLS
    if key == "mmr_status":
        return value in MMR_VALUES
    if key == "ms_status":
        return value in MS_VALUES
    if key == "variant_classification":
        return value in VARIANT_CLASSIFICATIONS
    if key == "exon":
        return isinstance(value, int) and not isinstance(value, bool)
    return False


def _validate_leaf(fields) -> dict | None:
    if not isinstance(fields, dict):
        return None
    kept = {}
    for key, value in fields.items():
        if _valid_field(key, value):
            kept[key] = value
        else:
            log.info("  dropped invalid genomic field %s", key,
                     extra={"event": "llm.genomic_invalid", "field": key,
                            "value": repr(value)[:80], "stage": "genomic"})
    if "variant_category" in kept and "wildtype" not in kept:
        # Every Mutation / CNV / SV node carries wildtype. A bare mention
        # ("KRAS mutation") asks for an alteration, so false.
        kept["wildtype"] = False
        log.info("  defaulted wildtype=false on %s", kept["variant_category"],
                 extra={"event": "llm.genomic_wildtype_default",
                        "variant_category": kept["variant_category"], "stage": "genomic"})
    return {"genomic": kept} if kept else None


def validate_genomic_node(node) -> dict | None:
    """``node`` with clinical leaves and invalid genomic fields removed; None if empty."""
    if not isinstance(node, dict):
        return None
    for key in ("and", "or"):
        if key in node:
            children = [c for c in (validate_genomic_node(c) for c in node.get(key) or [])
                        if c is not None]
            if not children:
                return None
            return children[0] if len(children) == 1 else {key: children}
    if "genomic" in node:
        return _validate_leaf(node["genomic"])
    return None


GENOMIC_SYSTEM_PROMPT = """You translate clinical trial text into MatchMiner CTML genomic match nodes,
and log every molecular biomarker the text mentions.

You get one piece of trial text and its source (inclusion, exclusion, summary, conditions or
genes_of_interest). Return ONLY a JSON object, no explanation, no code fences:
  {"node": <genomic node or null>, "refs": [<ref>, ...]}

GENOMIC NODE
A leaf is {"genomic": {...}}. Combine leaves with {"and": [...]} or {"or": [...]}.
Allowed keys and values (use these exact spellings):
  hugo_symbol             HUGO gene symbol, e.g. "EGFR", "BRAF", "ERBB2"
  variant_category        "Mutation" | "Copy Number Variation" | "Structural Variation"
  wildtype                JSON true or false (never a string). REQUIRED whenever variant_category is set.
  protein_change          HGVS protein change with the p. prefix, exact: "p.V600E", "p.G12C"
  wildcard_protein_change p. + codon, matches any substitution there: "p.V600", "p.G12"
  variant_classification  "Missense_Mutation" | "Nonsense_Mutation" | "In_Frame_Del" | "Frame_Shift_Del" | "Splice_Site"
  exon                    integer, e.g. 19
  cnv_call                "High Amplification" | "Low Amplification" | "Homozygous Deletion" | "Heterozygous Deletion"
  mmr_status              "MMR-Deficient" | "MMR-Proficient"
  ms_status               "MSI-H" | "MSI-L" | "MSS"

wildtype says whether the patient must have the alteration:
  - must have the alteration, or must NOT be wildtype      → wildtype: false
  - must be wildtype, or must NOT have the alteration       → wildtype: true
This is how exclusions are written. Never put "!" on a genomic field.
Fusions/rearrangements are "Structural Variation" on the gene (partner may be unknown).
Amplification/deletion is "Copy Number Variation"; add cnv_call only when the text says which.
If the text mentions no expressible genomic requirement, "node" is null. TMB, HRD, IHC/protein
expression (HER2 IHC 3+, PD-L1, ER/PR) and anything else without a key above → node null, but log it in refs.

REFS
Log every genetic or molecular alteration or biomarker the text mentions, whether or not it fits a node:
  - gene-level alterations: SNV/indel, CNV/amplification/deletion, fusion/rearrangement, HGVS changes
  - tumor-agnostic markers: MSI, MMR (dMMR/pMMR), TMB, HRD
  - receptor/IHC status tied to a gene or protein: HER2, ER, PR, PD-L1
Do NOT log: serum tumor markers (AFP, beta-HCG, LDH, CA-125, PSA, CEA), histology or diagnosis
language ("yolk sac tumor"), or non-molecular labs (blood counts, organ function).
Each ref:
  {"biomarker": "<gene symbol or marker: BRCA1, MSI, MMR, TMB, HER2>",
   "type": "snv" | "cnv" | "fusion" | "msi" | "mmr" | "tmb" | "hrd" | "ihc" | "other",
   "reference": "<shortest exact quote from the text that shows it>",
   "origin": "germline" | "somatic" | "unknown"}
origin: "germline" when the text asks for an inherited/germline alteration ("germline BRCA1/2",
"pathogenic germline variant", "hereditary"); "somatic" when it asks for a tumor alteration
("somatic", "tumor-based NGS", "detected in tumor tissue", "ctDNA"); otherwise "unknown".
If nothing molecular is mentioned, "refs" is [].

EXAMPLES
"Documented activating EGFR mutation"
→ {"node": {"genomic": {"hugo_symbol": "EGFR", "variant_category": "Mutation", "wildtype": false}},
   "refs": [{"biomarker": "EGFR", "type": "snv", "reference": "activating EGFR mutation", "origin": "unknown"}]}

"BRAF V600E mutation detected by tumor-based NGS"
→ {"node": {"genomic": {"hugo_symbol": "BRAF", "variant_category": "Mutation", "protein_change": "p.V600E", "wildtype": false}},
   "refs": [{"biomarker": "BRAF", "type": "snv", "reference": "BRAF V600E mutation", "origin": "somatic"}]}

Source: exclusion — "Known KRAS mutation"
→ {"node": {"genomic": {"hugo_symbol": "KRAS", "variant_category": "Mutation", "wildtype": true}},
   "refs": [{"biomarker": "KRAS", "type": "snv", "reference": "KRAS mutation", "origin": "unknown"}]}

"Tumor must be KRAS wildtype"
→ {"node": {"genomic": {"hugo_symbol": "KRAS", "variant_category": "Mutation", "wildtype": true}},
   "refs": [{"biomarker": "KRAS", "type": "snv", "reference": "KRAS wildtype", "origin": "somatic"}]}

"Must not be BRCA2 wildtype"
→ {"node": {"genomic": {"hugo_symbol": "BRCA2", "variant_category": "Mutation", "wildtype": false}},
   "refs": [{"biomarker": "BRCA2", "type": "snv", "reference": "not be BRCA2 wildtype", "origin": "unknown"}]}

"Deleterious germline BRCA1 or BRCA2 mutation"
→ {"node": {"or": [{"genomic": {"hugo_symbol": "BRCA1", "variant_category": "Mutation", "wildtype": false}},
                   {"genomic": {"hugo_symbol": "BRCA2", "variant_category": "Mutation", "wildtype": false}}]},
   "refs": [{"biomarker": "BRCA1", "type": "snv", "reference": "germline BRCA1", "origin": "germline"},
            {"biomarker": "BRCA2", "type": "snv", "reference": "BRCA2 mutation", "origin": "germline"}]}

"EGFR exon 19 deletion"
→ {"node": {"genomic": {"hugo_symbol": "EGFR", "variant_category": "Mutation", "exon": 19, "variant_classification": "In_Frame_Del", "wildtype": false}},
   "refs": [{"biomarker": "EGFR", "type": "snv", "reference": "EGFR exon 19 deletion", "origin": "unknown"}]}

"MYCN amplification"
→ {"node": {"genomic": {"hugo_symbol": "MYCN", "variant_category": "Copy Number Variation", "cnv_call": "High Amplification", "wildtype": false}},
   "refs": [{"biomarker": "MYCN", "type": "cnv", "reference": "MYCN amplification", "origin": "unknown"}]}

"Microsatellite instability-high (MSI-H) or mismatch repair deficient (dMMR) tumors"
→ {"node": {"or": [{"genomic": {"ms_status": "MSI-H"}}, {"genomic": {"mmr_status": "MMR-Deficient"}}]},
   "refs": [{"biomarker": "MSI", "type": "msi", "reference": "MSI-H", "origin": "somatic"},
            {"biomarker": "MMR", "type": "mmr", "reference": "dMMR", "origin": "somatic"}]}

"ALK rearrangement"
→ {"node": {"genomic": {"hugo_symbol": "ALK", "variant_category": "Structural Variation", "wildtype": false}},
   "refs": [{"biomarker": "ALK", "type": "fusion", "reference": "ALK rearrangement", "origin": "unknown"}]}

"TMB >= 10 mut/Mb"
→ {"node": null, "refs": [{"biomarker": "TMB", "type": "tmb", "reference": "TMB >= 10 mut/Mb", "origin": "somatic"}]}

"HER2-positive (IHC 3+) breast cancer"
→ {"node": null, "refs": [{"biomarker": "HER2", "type": "ihc", "reference": "HER2-positive (IHC 3+)", "origin": "unknown"}]}

"Age >= 18"
→ {"node": null, "refs": []}"""

_ORIGINS = ("germline", "somatic")


def _clean_refs(refs) -> list[dict]:
    if not isinstance(refs, list):
        return []
    return [r for r in refs
            if isinstance(r, dict) and isinstance(r.get("biomarker"), str) and r["biomarker"].strip()]


def suggest_genomic(text: str, source: str, cache: dict, client) -> tuple[dict | None, list[dict]]:
    """(validated genomic node or None, raw refs) for one piece; cached per piece."""
    key = _cache_key(f"genomic:{source}:{text}")
    if key not in cache:
        try:
            response = client.chat.completions.create(
                model=os.environ.get("UMGPT_MODEL", "gpt-4o"),
                messages=[
                    {"role": "system", "content": GENOMIC_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Source: {source}\nText: {text}"},
                ],
                temperature=0,
                max_tokens=800,
            )
        except Exception as exc:
            if not is_content_filter(exc):
                raise
            log.warning("  content filter — no genomic suggestion for %s piece: %r", source, text[:80],
                        extra={"event": "llm.content_filter", "criterion_source": source,
                               "stage": "genomic"})
            cache[key] = {"node": None, "refs": []}
        else:
            try:
                parsed = json.loads(_strip_fences(response.choices[0].message.content))
            except json.JSONDecodeError:
                parsed = None
            if not isinstance(parsed, dict):
                parsed = {}
            cache[key] = {"node": validate_genomic_node(parsed.get("node")),
                          "refs": _clean_refs(parsed.get("refs"))}
    hit = cache[key]
    return hit.get("node"), list(hit.get("refs") or [])


def _strip_fences(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0].strip()
    return text


def _ref(trial_nct: str, source: str, raw: dict, known_genes: set[str]) -> dict:
    biomarker = raw["biomarker"].strip()
    origin = str(raw.get("origin") or "").strip().lower()
    return {"trial_nct": trial_nct, "reference": raw.get("reference") or "",
            "biomarker": biomarker, "type": raw.get("type") or "other", "section": source,
            "in_kb": biomarker.upper() in known_genes,
            "origin": origin if origin in _ORIGINS else "unknown"}


def draft_genomic(trial: dict, cache: dict, client, known_genes: set[str]) -> dict:
    """New trial with genomic entries, ``parsed_sections.genomic`` and
    ``molecular_genomic_refs`` written; everything else left as found."""
    trial_nct = _trial_id(trial)
    entries, sections, refs = [], {}, []
    for source in GENOMIC_SOURCES:
        texts = piece_texts(trial, source)
        for text in texts:
            node, raw_refs = suggest_genomic(text, source, cache, client)
            entries.append(make_entry(source, text, node, GENOMIC))
            refs.extend(_ref(trial_nct, source, r, known_genes) for r in raw_refs)
        sections[source] = section_row(bool(texts), bool(texts), len(texts))

    drafted = {k: v for k, v in trial.items() if k != "_ctml_suggestions"}
    curation = merge_stage(trial.get("_llm_curation"), GENOMIC, entries, sections)
    curation["molecular_genomic_refs"] = refs
    drafted["_llm_curation"] = curation
    return drafted
