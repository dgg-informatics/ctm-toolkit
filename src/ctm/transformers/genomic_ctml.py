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
``genomic:<prompt hash>:<source>:<text>``; the value is the model's raw
``{"node", "refs"}``, validated on every read.

Validation pins every genomic node to the **trial-side** vocabulary matchengine
maps from (the user's key table, 02Nov2026). A wrong ``mmr_status`` /
``ms_status`` value crashes matchengine, and a wrong ``variant_category`` or a
string ``wildtype`` silently matches nothing, so nothing the model writes reaches
a curator unchecked.
"""
import hashlib
import json
import logging
import os
import re

from .eligibility_to_ctml import _cache_key, is_content_filter
from .llm_curation import (
    GENOMIC,
    GENOMIC_SOURCES,
    PROMPT_LABELS,
    make_entry,
    merge_stage,
    piece_texts,
    record_stage_run,
    section_row,
)
from .trials_curate import _trial_id

log = logging.getLogger(__name__)

# "!" negates Mutation only ("no documented mutation"); CNV and SV have no "!" form.
VARIANT_CATEGORIES = frozenset({"Mutation", "!Mutation", "Copy Number Variation", "Structural Variation"})
CNV_CALLS = frozenset({"High Amplification", "Low Amplification",
                       "Homozygous Deletion", "Heterozygous Deletion"})
MMR_VALUES = frozenset({"MMR-Deficient", "MMR-Proficient"})
MS_VALUES = frozenset({"MSI-H", "MSI-L", "MSS"})
VARIANT_CLASSIFICATIONS = frozenset({"Missense_Mutation", "Nonsense_Mutation", "In_Frame_Del",
                                     "Frame_Shift_Del", "Splice_Site"})
GENOMIC_KEYS = frozenset({"hugo_symbol", "variant_category", "protein_change",
                          "wildcard_protein_change", "wildtype", "cnv_call", "mmr_status",
                          "ms_status", "variant_classification", "exon"})


# Valid CTML, but our patient docs carry no TRUE_TRANSCRIPT_EXON /
# TRUE_VARIANT_CLASSIFICATION yet, so a leaf using them matches nobody. Stripped
# (deliberately widening to the gene-level node) until patient data has them.
UNSUPPORTED_KEYS = frozenset({"exon", "variant_classification"})

_HUGO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
# Keys that only mean something for a named gene; without one the leaf would
# ask for "this alteration in any gene".
_GENE_SCOPED = frozenset({"variant_category", "protein_change", "wildcard_protein_change",
                          "exon", "cnv_call", "variant_classification", "wildtype"})


_STATUS_KNOWN_CATEGORIES = ("Mutation", "Copy Number Variation")


def _valid_field(key: str, value) -> bool:
    if key == "hugo_symbol":
        return isinstance(value, str) and bool(_HUGO.fullmatch(value))
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


def _drop(reason: str, **fields) -> None:
    log.info("  dropped genomic leaf: %s", reason,
             extra={"event": "llm.genomic_invalid", "reason": reason, "stage": "genomic", **fields})


def _validate_leaf(fields, source: str | None) -> dict | None:
    """The leaf unchanged, or None. Never partially stripped: removing one field
    (a gene, an exon, a cnv_call) silently widens what the leaf asks for."""
    if not isinstance(fields, dict) or not fields:
        return None
    unsupported = UNSUPPORTED_KEYS & fields.keys()
    if unsupported:
        log.info("  stripped %s (no patient data for it yet)", ", ".join(sorted(unsupported)),
                 extra={"event": "llm.genomic_unsupported_key", "fields": sorted(unsupported),
                        "stage": "genomic"})
        fields = {k: v for k, v in fields.items() if k not in UNSUPPORTED_KEYS}
        if not fields:
            return None
    if "wildtype" in fields and fields["wildtype"] is None:
        # "Status must be known, either result": no WILDTYPE in the query matches a
        # tested patient whether mutated (false) or wildtype (true). Only Mutation /
        # CNV have tested-negative patient docs, and only gene-level (a protein
        # change etc. would only ever match the mutated ones).
        if (fields.get("variant_category") not in _STATUS_KNOWN_CATEGORIES
                or fields.keys() - {"hugo_symbol", "variant_category", "wildtype"}
                or not _valid_field("hugo_symbol", fields.get("hugo_symbol"))):
            _drop("wildtype null outside a gene-level Mutation/CNV leaf", field="wildtype")
            return None
        return {"genomic": {k: v for k, v in fields.items() if k != "wildtype"}}
    for key, value in fields.items():
        if not _valid_field(key, value):
            _drop("invalid field", field=key, value=repr(value)[:80])
            return None
    if "hugo_symbol" not in fields and _GENE_SCOPED & fields.keys():
        _drop("gene-scoped keys without hugo_symbol", field="hugo_symbol")
        return None
    if "wildtype" in fields and "variant_category" not in fields:
        # wildtype belongs to Mutation / CNV / SV only; MSI/MMR signature
        # leaves (ms_status / mmr_status) never carry it.
        _drop("wildtype without variant_category", field="wildtype")
        return None
    if fields.get("variant_category") == "Structural Variation" and fields.get("wildtype") is True:
        # Patient SV docs exist only for detected fusions (to_matchminer skips
        # wildtype=TRUE SV rows and writes WILDTYPE false on the rest), so
        # "fusion documented absent" would match nobody.
        _drop("Structural Variation with wildtype true", field="wildtype")
        return None
    kept = dict(fields)
    if "variant_category" in kept and "wildtype" not in kept:
        # Every Mutation / !Mutation / CNV / SV node carries wildtype. false is
        # right for both "has a documented mutation" (Mutation) and "no
        # documented mutation" (!Mutation); omitting it is the WT trap.
        kept["wildtype"] = False
        log.info("  defaulted wildtype=false on %s", kept["variant_category"],
                 extra={"event": "llm.genomic_wildtype_default",
                        "variant_category": kept["variant_category"], "stage": "genomic"})
    return {"genomic": kept}


def validate_genomic_node(node, source: str | None = None) -> dict | None:
    """``node`` with clinical and invalid genomic leaves removed; None if empty.

    ``source`` is accepted for logging context and future source-specific rules.
    """
    if not isinstance(node, dict):
        return None
    for key in ("and", "or"):
        if key in node:
            children = [c for c in (validate_genomic_node(c, source) for c in node.get(key) or [])
                        if c is not None]
            if not children:
                return None
            return children[0] if len(children) == 1 else {key: children}
    if "genomic" in node:
        return _validate_leaf(node["genomic"], source)
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
  variant_category        "Mutation" | "!Mutation" | "Copy Number Variation" | "Structural Variation"
  wildtype                JSON true or false (never a string)
  protein_change          HGVS protein change with the p. prefix, exact: "p.V600E", "p.G12C"
  wildcard_protein_change p. + codon, matches any substitution there: "p.V600", "p.G12"
Do not use exon or variant_classification: patient data has no exon or classification yet, so a
node with them matches nobody. "EGFR exon 19 deletion" → an EGFR "Mutation" node (the exon detail
stays in the ref).
  cnv_call                "High Amplification" | "Low Amplification" | "Homozygous Deletion" | "Heterozygous Deletion"
  mmr_status              "MMR-Deficient" | "MMR-Proficient"
  ms_status               "MSI-H" | "MSI-L" | "MSS"

WILDTYPE — every node with a variant_category MUST carry wildtype (true, false, or null — see below).
A wildtype test result is stored as a mutation record with wildtype true, so leaving wildtype
out changes what matches. Mutations:
  must HAVE a documented mutation          → "Mutation",  wildtype: false
  must be documented/confirmed wildtype    → "Mutation",  wildtype: true   (untested patients do NOT match)
  must have NO documented mutation         → "!Mutation", wildtype: false  (wildtype and untested patients match)
  status must be KNOWN, either result      → "Mutation",  "wildtype": null (mutated and wildtype match; untested do NOT)
    ("TP53 gene mutation analysis performed", "KRAS status must be known"). Gene + category only;
    also works for "Copy Number Variation". A test the trial itself performs ("with TP53
    sequencing" in a title) is not an eligibility requirement → node null.
This last row is the standard way to write a mutation exclusion ("no known KRAS mutation",
exclusion source "known KRAS mutation", "KRAS wild-type or untested"). Never write "!Mutation"
without wildtype: false — that also drops patients with a documented wildtype result.
Copy number and fusions have no "!" form — never "!" on "Copy Number Variation" or
"Structural Variation". They still carry wildtype:
  CNV present            → "Copy Number Variation", wildtype: false (add cnv_call only when the text says which)
  CNV documented absent  → "Copy Number Variation", wildtype: true
  fusion present         → "Structural Variation",  wildtype: false (partner may be unknown)
  Fusions only ever take wildtype: false. There is no way to express that a fusion is absent
  ("no ALK rearrangement"): set "node" to null and log the fusion in refs.
MSI and MMR are the exception: use ms_status / mmr_status only, with no variant_category and
no wildtype. Never put "!" on any field other than variant_category "!Mutation".
EXCLUSIONS — exclusion text describes who is kept OUT. The node must describe who may still enroll,
which is the opposite of the text. Never copy the excluded group as written:
  excluded if mutation present ("known KRAS mutation")      → "!Mutation", wildtype: false
  excluded if no mutation ("KRAS wild-type", "without KRAS mutation") → "Mutation", wildtype: false
  excluded if CNV present ("MYCN amplification")            → node null (no "!" for CNV), log in refs
  excluded if CNV absent ("without MYCN amplification")     → "Copy Number Variation", wildtype: false
                                                              (+ cnv_call when the text says which)
  excluded if fusion present                                → node null, log in refs
  excluded if fusion absent ("without ALK fusion")          → "Structural Variation", wildtype: false
  excluded MSI/MMR status                                   → node null, log in refs
If the exclusion only covers one subgroup of the trial's patients (a stage, risk group or histology,
e.g. "INRG stage L2 tumors without amplification of MYCN"), the flipped node would wrongly apply to
everyone → node null, log in refs. The same holds for inclusion text that is one of several
alternative ways to qualify.
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
MSI/MMR/TMB/HRD and IHC are tumor tests → "somatic" even without tumor wording.
If nothing molecular is mentioned, "refs" is [].

EXAMPLES
"Documented activating EGFR mutation"
→ {"node": {"genomic": {"hugo_symbol": "EGFR", "variant_category": "Mutation", "wildtype": false}},
   "refs": [{"biomarker": "EGFR", "type": "snv", "reference": "activating EGFR mutation", "origin": "unknown"}]}

"BRAF V600E mutation detected by tumor-based NGS"
→ {"node": {"genomic": {"hugo_symbol": "BRAF", "variant_category": "Mutation", "protein_change": "p.V600E", "wildtype": false}},
   "refs": [{"biomarker": "BRAF", "type": "snv", "reference": "BRAF V600E mutation", "origin": "somatic"}]}

Source: exclusion — "Known KRAS mutation"
→ {"node": {"genomic": {"hugo_symbol": "KRAS", "variant_category": "!Mutation", "wildtype": false}},
   "refs": [{"biomarker": "KRAS", "type": "snv", "reference": "KRAS mutation", "origin": "unknown"}]}

"No documented KRAS mutation (wild-type or not tested)"
→ {"node": {"genomic": {"hugo_symbol": "KRAS", "variant_category": "!Mutation", "wildtype": false}},
   "refs": [{"biomarker": "KRAS", "type": "snv", "reference": "No documented KRAS mutation", "origin": "unknown"}]}

"Tumor must be confirmed KRAS wild-type by NGS"
→ {"node": {"genomic": {"hugo_symbol": "KRAS", "variant_category": "Mutation", "wildtype": true}},
   "refs": [{"biomarker": "KRAS", "type": "snv", "reference": "KRAS wild-type", "origin": "somatic"}]}

"Deleterious germline BRCA1 or BRCA2 mutation"
→ {"node": {"or": [{"genomic": {"hugo_symbol": "BRCA1", "variant_category": "Mutation", "wildtype": false}},
                   {"genomic": {"hugo_symbol": "BRCA2", "variant_category": "Mutation", "wildtype": false}}]},
   "refs": [{"biomarker": "BRCA1", "type": "snv", "reference": "germline BRCA1", "origin": "germline"},
            {"biomarker": "BRCA2", "type": "snv", "reference": "BRCA2 mutation", "origin": "germline"}]}

"TP53 gene mutation analysis performed at any CLIA-approved laboratory"
→ {"node": {"genomic": {"hugo_symbol": "TP53", "variant_category": "Mutation", "wildtype": null}},
   "refs": [{"biomarker": "TP53", "type": "snv", "reference": "TP53 gene mutation analysis", "origin": "unknown"}]}

"EGFR exon 19 deletion"
→ {"node": {"genomic": {"hugo_symbol": "EGFR", "variant_category": "Mutation", "wildtype": false}},
   "refs": [{"biomarker": "EGFR", "type": "snv", "reference": "EGFR exon 19 deletion", "origin": "unknown"}]}

"MYCN amplification"
→ {"node": {"genomic": {"hugo_symbol": "MYCN", "variant_category": "Copy Number Variation", "cnv_call": "High Amplification", "wildtype": false}},
   "refs": [{"biomarker": "MYCN", "type": "cnv", "reference": "MYCN amplification", "origin": "unknown"}]}

"ALK rearrangement"
→ {"node": {"genomic": {"hugo_symbol": "ALK", "variant_category": "Structural Variation", "wildtype": false}},
   "refs": [{"biomarker": "ALK", "type": "fusion", "reference": "ALK rearrangement", "origin": "unknown"}]}

Source: exclusion — "Without MYCN amplification"
→ {"node": {"genomic": {"hugo_symbol": "MYCN", "variant_category": "Copy Number Variation", "cnv_call": "High Amplification", "wildtype": false}},
   "refs": [{"biomarker": "MYCN", "type": "cnv", "reference": "without MYCN amplification", "origin": "unknown"}]}

Source: exclusion — "Patients with INRG stage L2 tumors without amplification of MYCN"
→ {"node": null, "refs": [{"biomarker": "MYCN", "type": "cnv", "reference": "without amplification of MYCN", "origin": "unknown"}]}

Source: exclusion — "Known ALK rearrangement"
→ {"node": null, "refs": [{"biomarker": "ALK", "type": "fusion", "reference": "ALK rearrangement", "origin": "unknown"}]}

"Microsatellite instability-high (MSI-H) or mismatch repair deficient (dMMR) tumors"
→ {"node": {"or": [{"genomic": {"ms_status": "MSI-H"}}, {"genomic": {"mmr_status": "MMR-Deficient"}}]},
   "refs": [{"biomarker": "MSI", "type": "msi", "reference": "MSI-H", "origin": "somatic"},
            {"biomarker": "MMR", "type": "mmr", "reference": "dMMR", "origin": "somatic"}]}

"Microsatellite stable (MSS) colorectal cancer"
→ {"node": {"genomic": {"ms_status": "MSS"}},
   "refs": [{"biomarker": "MSI", "type": "msi", "reference": "Microsatellite stable (MSS)", "origin": "somatic"}]}

"TMB >= 10 mut/Mb"
→ {"node": null, "refs": [{"biomarker": "TMB", "type": "tmb", "reference": "TMB >= 10 mut/Mb", "origin": "somatic"}]}

"HER2-positive (IHC 3+) breast cancer"
→ {"node": null, "refs": [{"biomarker": "HER2", "type": "ihc", "reference": "HER2-positive (IHC 3+)", "origin": "somatic"}]}

"Age >= 18"
→ {"node": null, "refs": []}"""

_ORIGINS = ("germline", "somatic")
_REF_TYPES = frozenset({"snv", "cnv", "fusion", "msi", "mmr", "tmb", "hrd", "ihc", "other"})


def PROMPT_VERSION() -> str:
    """Short hash of the current prompt; part of every cache key, so editing the
    prompt never serves answers the old prompt produced."""
    return hashlib.md5(GENOMIC_SYSTEM_PROMPT.encode()).hexdigest()[:8]


def strip_fences(raw: str | None) -> str:
    """Model output without a markdown code fence (models add one despite being told not to)."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0].strip()
    return text


def _clean_refs(refs) -> list[dict]:
    """Refs whose every field is the type storage requires; junk refs dropped."""
    if not isinstance(refs, list):
        return []
    cleaned = []
    for r in refs:
        if not (isinstance(r, dict) and isinstance(r.get("biomarker"), str) and r["biomarker"].strip()):
            continue
        ref_type = r.get("type")
        cleaned.append({**r,
                        "reference": r["reference"] if isinstance(r.get("reference"), str) else "",
                        "type": ref_type if isinstance(ref_type, str) and ref_type in _REF_TYPES else "other"})
    return cleaned


def suggest_genomic(text: str, source: str, cache: dict, client) -> tuple[dict | None, list[dict]]:
    """(validated genomic node or None, cleaned refs) for one piece.

    The cache holds the model's raw answer, keyed on prompt version + source +
    text, and is validated on every read — so a validator fix reaches answers
    cached before it, and a prompt edit never reuses old answers.
    """
    key = _cache_key(f"genomic:{PROMPT_VERSION()}:{source}:{text}")
    if key not in cache:
        try:
            response = client.chat.completions.create(
                model=os.environ.get("UMGPT_MODEL", "gpt-4o"),
                messages=[
                    {"role": "system", "content": GENOMIC_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Source: {PROMPT_LABELS[source]}\nText: {text}"},
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
                parsed = json.loads(strip_fences(response.choices[0].message.content))
            except json.JSONDecodeError:
                parsed = None
            parsed = parsed if isinstance(parsed, dict) else {}
            cache[key] = {"node": parsed.get("node"), "refs": parsed.get("refs") or []}
    hit = cache[key] if isinstance(cache[key], dict) else {}
    return validate_genomic_node(hit.get("node"), source), _clean_refs(hit.get("refs"))


def _ref(trial_nct: str, source: str, raw: dict, known_genes: set[str]) -> dict:
    biomarker = raw["biomarker"].strip()
    origin = str(raw.get("origin") or "").strip().lower()
    return {"trial_nct": trial_nct, "reference": raw["reference"],
            "biomarker": biomarker, "type": raw["type"], "section": source,
            "in_kb": biomarker.upper() in known_genes,
            "origin": origin if origin in _ORIGINS else "unknown"}


def draft_genomic(trial: dict, cache: dict, client, known_genes: set[str],
                  stage_run: dict | None = None) -> dict:
    """New trial with genomic entries, ``parsed_sections.genomic`` and
    ``molecular_genomic_refs`` written; everything else left as found."""
    trial_nct = _trial_id(trial)
    entries, sections, refs = [], {}, []
    for source in GENOMIC_SOURCES:
        texts = piece_texts(trial, source)
        for text in texts:
            node, raw_refs = suggest_genomic(text, source, cache, client)
            entries.append(make_entry(source, text, node, GENOMIC, mapping_source="ai"))
            refs.extend(_ref(trial_nct, source, r, known_genes) for r in raw_refs)
        sections[source] = section_row(bool(texts), bool(texts), len(texts))

    drafted = {k: v for k, v in trial.items() if k != "_ctml_suggestions"}
    curation = merge_stage(trial.get("_llm_curation"), GENOMIC, entries, sections)
    curation["molecular_genomic_refs"] = refs
    record_stage_run(curation, GENOMIC, stage_run)
    drafted["_llm_curation"] = curation
    return drafted
