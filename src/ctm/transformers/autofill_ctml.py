"""``ctm-llm autofill-ctml`` [BETA] — the one writer of ``treatment_list.step[0].match``.

Builds a trial's match clause from its ``_llm_curation._ctml_suggestions``
(clinical and genomic, written by ``ctm-llm clinical`` / ``ctm-llm genomic``).
No model call. ``source`` values are field paths. Rules:

* Every criterion node (``eligibility.inclusion`` / ``eligibility.exclusion``,
  both node types) and every age/sex node (``_raw.minimum_age``, ``_raw.sex``,
  ``_raw.age_group``) is included. Exclusion nodes already carry their
  negation (``!`` on clinical values, ``!Mutation`` on genomic).
* Diagnosis: every positive ``oncotree_primary_diagnosis`` from every clinical
  source (criteria, ``_summary.long_title``, ``_raw.conditions``,
  ``_raw.categorys``, the disease-site fields) goes into **one** ``or`` node,
  de-duplicated, first in the match. A criterion node that also had other
  fields keeps them as its own node. Negated diagnoses (``!Lung``) stay in their
  own nodes, AND-ed with the rest.
* Genomic: nodes from ``_summary.long_title``, ``_raw.conditions`` and
  ``_raw.octsu_genes_interest`` are always added (after the criteria's),
  duplicates collapsed.
* ``ecog_score`` is not used in match clauses yet: it is removed from every
  node before writing (the suggestion itself is kept). A node left empty is
  dropped and its entry marked not transferred.
* Identical nodes collapse to one. The match is a plain list of nodes — a
  top-level list is already AND-ed, so no ``and`` wrapper.
* Mixed pieces (same source + same text → a clinical and a genomic node, from
  any source both stages read: criteria, long title, conditions): the stages
  can't see each other, so the and/or is decided here — one small AI call per
  piece in the CLI (``ai_mixed_resolver``). "or" → one ``{"or": [clinical,
  genomic]}`` item, keeping that diagnosis out of the shared OR; "and" or
  "unresolved" (no answer) → AND-ed. Recorded as ``mixed_logic`` on both entries
  and counted in the report. This is the only AI use in autofill.
* Every clinical/genomic leaf gets ``_CURATED_METHOD: "auto"``. matchengine
  must list ``_CURATED_METHOD`` with ``"ignore": true`` under both clinical and
  genomic ``trial_key_mappings``, or these clauses match nobody.
* Only an empty match is filled unless ``overwrite``. When it fills, every
  non-legacy entry (both lists) gets ``transferred_to_match`` true if used,
  false if not; when it skips, the flags stay None (never decided). A report is
  stored at ``_llm_curation.autofill``.

``autofill_trial`` is pure (no I/O, inputs never mutated); the AI lives behind
the injected resolver. Legacy entries (no ``node_type``) are ignored.
"""
import copy
import hashlib
import json
import logging
import os

from .llm_curation import (
    AGE_GROUP,
    AMC_DISEASE_SITE,
    CATEGORYS,
    CONDITIONS,
    DDOTS_DISEASE_SITE,
    EXCLUSION,
    GENES_OF_INTEREST,
    INCLUSION,
    LONG_TITLE,
    MINIMUM_AGE,
    SEX,
    WEST_DISEASE_SITE,
    record_stage_run,
)

log = logging.getLogger(__name__)

CURATED_METHOD_KEY = "_CURATED_METHOD"
MIXED_LOGIC = ("and", "or", "unresolved")

_CRITERIA = (INCLUSION, EXCLUSION)
_AGE_SEX = (MINIMUM_AGE, SEX, AGE_GROUP)
_DIAGNOSIS_SOURCES = (INCLUSION, EXCLUSION, LONG_TITLE, CONDITIONS, CATEGORYS, AMC_DISEASE_SITE,
                      DDOTS_DISEASE_SITE, WEST_DISEASE_SITE)
_GENOMIC_EXTRA = (LONG_TITLE, CONDITIONS, GENES_OF_INTEREST)
_DX = "oncotree_primary_diagnosis"
# Clinical fields suggested but not yet used in match clauses.
NOT_IN_MATCH = frozenset({"ecog_score"})
# report["reason"] when the match was written; skips use their own reason.
FILLED_EMPTY = "filled empty match"
OVERWROTE = "overwrote existing match"


def _leaves(node):
    if not isinstance(node, dict):
        return
    for key in ("and", "or"):
        if key in node:
            for child in node.get(key) or []:
                yield from _leaves(child)
            return
    yield node


def _positive_diagnoses(node) -> list[str]:
    names = []
    for leaf in _leaves(node):
        value = (leaf.get("clinical") or {}).get(_DX)
        if isinstance(value, str) and value and not value.startswith("!") and value not in names:
            names.append(value)
    return names


def _is_positive_dx(key: str, value) -> bool:
    return key == _DX and isinstance(value, str) and bool(value) and not value.startswith("!")


def _strip(node: dict, drop) -> dict | None:
    """``node`` without the clinical fields ``drop(key, value)`` selects; None if empty."""
    for key in ("and", "or"):
        if key in node:
            children = [c for c in (_strip(c, drop) for c in node[key]) if c is not None]
            if not children:
                return None
            return children[0] if len(children) == 1 else {key: children}
    if "clinical" in node:
        fields = {k: v for k, v in node["clinical"].items() if not drop(k, v)}
        return {"clinical": fields} if fields else None
    return node


def _match_ready(node: dict) -> dict | None:
    """``node`` minus fields not used in matching yet and minus positive diagnoses,
    which are gathered into the one diagnosis OR instead."""
    return _strip(node, lambda k, v: k in NOT_IN_MATCH or _is_positive_dx(k, v))


def _mark(node: dict) -> dict:
    """Deep copy of ``node`` with the auto marker on every clinical/genomic leaf."""
    for key in ("and", "or"):
        if key in node:
            return {key: [_mark(c) for c in node[key]]}
    return {kind: {**fields, CURATED_METHOD_KEY: "auto"} for kind, fields in node.items()}


def autofill_trial(trial: dict, overwrite: bool = False,
                   resolve_mixed=None, stage_run: dict | None = None) -> tuple[dict, dict]:
    """(new trial, report). The report is also stored at ``_llm_curation.autofill``.

    ``resolve_mixed(text, clinical_node, genomic_node)`` returns "and", "or" or
    "unresolved" for a criterion piece that produced both a clinical and a
    genomic node from the same source and text. Without it every such piece is
    "unresolved" (AND-ed).
    """
    out = copy.deepcopy(trial)
    curation = out.setdefault("_llm_curation", {})
    record_stage_run(curation, "autofill", stage_run)
    entries = [e for e in curation.get("_ctml_suggestions") or []
               if e.get("node_type") and e.get("suggested_node") is not None]
    report = {"method": "auto", "filled": False, "reason": None, "nodes": 0,
              "mixed_pieces": 0, "diagnosis_sources": [], "match_sources": [],
              "mixed_logic": dict.fromkeys(MIXED_LOGIC, 0)}

    steps = (out.get("treatment_list") or {}).get("step") or []
    if not steps:
        report["reason"] = "no treatment_list step"
    elif steps[0].get("match") and not overwrite:
        report["reason"] = "match not empty"
    if report["reason"]:
        curation["autofill"] = report
        return out, report

    used: list[dict] = []
    nodes: list[dict] = []

    def use(entry: dict) -> None:
        if not any(entry is u for u in used):
            used.append(entry)

    def add(node: dict | None, entry: dict) -> None:
        if node is None:
            return
        if node not in nodes:
            nodes.append(node)
        use(entry)

    # Mixed pieces: a clinical and a genomic entry with the same source AND the
    # same text, from any source both stages read. Neither stage can see the
    # other, so the and/or between them is decided here (by the AI in the CLI).
    # "or" pieces become one OR item and keep their diagnosis inside it.
    pieces: dict[tuple, dict] = {}
    for e in entries:
        pieces.setdefault((e["source"], e["text"]), {})[e["node_type"]] = e
    or_pieces = set()
    for key, by_type in pieces.items():
        if {"clinical", "genomic"} <= by_type.keys():
            clinical, genomic = by_type["clinical"], by_type["genomic"]
            logic = (resolve_mixed(key[1], clinical["suggested_node"], genomic["suggested_node"])
                     if resolve_mixed else "unresolved")
            logic = logic if logic in MIXED_LOGIC else "unresolved"
            clinical["mixed_logic"] = genomic["mixed_logic"] = logic
            report["mixed_logic"][logic] += 1
            if logic == "or":
                or_pieces.add(key)

    def in_or_piece(e: dict) -> bool:
        return (e["source"], e["text"]) in or_pieces

    # Diagnosis: every positive oncotree_primary_diagnosis from every clinical
    # source, de-duplicated, as one OR. Negated ones stay in their own nodes.
    names, dx_sources = [], []
    for e in entries:
        if (e["node_type"] == "clinical" and e["source"] in _DIAGNOSIS_SOURCES
                and not in_or_piece(e)):
            found = _positive_diagnoses(e["suggested_node"])
            if found:
                use(e)
                names += [n for n in found if n not in names]
                if e["source"] not in dx_sources:
                    dx_sources.append(e["source"])
    if names:
        leaves = [{"clinical": {_DX: n}} for n in names]
        nodes.append(leaves[0] if len(leaves) == 1 else {"or": leaves})
    report["diagnosis_sources"] = dx_sources

    for e in entries:
        if in_or_piece(e):
            if e["node_type"] == "clinical":
                genomic = pieces[(e["source"], e["text"])]["genomic"]
                clinical_part = _strip(e["suggested_node"], lambda k, v: k in NOT_IN_MATCH)
                if clinical_part is None:
                    add(genomic["suggested_node"], genomic)
                else:
                    add({"or": [clinical_part, genomic["suggested_node"]]}, e)
                    use(genomic)
        elif e["source"] in _CRITERIA or e["source"] in _AGE_SEX:
            add(_match_ready(e["suggested_node"]), e)

    for e in entries:
        if (e["node_type"] == "genomic" and e["source"] in _GENOMIC_EXTRA
                and not in_or_piece(e)):
            add(e["suggested_node"], e)

    if not nodes:
        report["reason"] = "no suggestions"
        curation["autofill"] = report
        return out, report

    had_match = bool(steps[0].get("match"))
    steps[0]["match"] = [_mark(n) for n in nodes]
    for e in (curation.get("_ctml_suggestions") or []) + (curation.get("_null_ctml_suggestions") or []):
        if e.get("node_type"):
            e["transferred_to_match"] = any(e is u for u in used)
    report.update(filled=True, nodes=len(nodes),
                  reason=OVERWROTE if had_match else FILLED_EMPTY,
                  match_sources=list(dict.fromkeys(e["source"] for e in used)),
                  mixed_pieces=len({(e["source"], e["text"]) for e in used if e.get("mixed")}))
    curation["autofill"] = report
    return out, report



MIXED_LOGIC_PROMPT = """You decide how two parts of one clinical trial eligibility criterion combine.

You get the criterion text, a clinical CTML node and a genomic CTML node that were both
extracted from that same text. Decide whether the text requires BOTH (and) or EITHER (or).

- "and": the patient must satisfy both, e.g. "HER2-amplified breast cancer",
  "NSCLC with an EGFR exon 19 deletion", "BRCA-mutated ovarian cancer".
- "or": either one qualifies, e.g. "breast cancer or a germline BRCA2 mutation",
  "any solid tumor with an NTRK fusion, or a lymphoma".
If the text does not clearly say "or" between the two parts, answer "and".

Return ONLY a JSON object, no explanation, no code fences: {"logic": "and"} or {"logic": "or"}"""


def MIXED_PROMPT_VERSION() -> str:
    return hashlib.md5(MIXED_LOGIC_PROMPT.encode()).hexdigest()[:8]


def ai_mixed_resolver(get_client, cache: dict):
    """A ``resolve_mixed`` that asks the model, cached per text + both nodes.

    ``get_client`` is called once, on the first uncached piece, so a run with no
    mixed pieces never needs UMGPT. Junk answers and content-filter refusals are
    "unresolved" (and cached, so a rerun does not pay for them again).
    """
    from .eligibility_to_ctml import _cache_key, is_content_filter
    from .genomic_ctml import strip_fences

    client = []

    def resolve(text: str, clinical: dict, genomic: dict) -> str:
        nodes = json.dumps([clinical, genomic], sort_keys=True)
        key = _cache_key(f"mixed:{MIXED_PROMPT_VERSION()}:{text}:{nodes}")
        if key not in cache:
            if not client:
                client.append(get_client())
            try:
                response = client[0].chat.completions.create(
                    model=os.environ.get("UMGPT_MODEL", "gpt-4o"),
                    messages=[
                        {"role": "system", "content": MIXED_LOGIC_PROMPT},
                        {"role": "user", "content": f"Criterion: {text}\n"
                                                    f"Clinical node: {json.dumps(clinical)}\n"
                                                    f"Genomic node: {json.dumps(genomic)}"},
                    ],
                    temperature=0,
                    max_tokens=20,
                )
            except Exception as exc:
                if not is_content_filter(exc):
                    raise
                log.warning("  content filter — mixed piece left unresolved: %r", text[:80],
                            extra={"event": "llm.content_filter", "stage": "autofill"})
                cache[key] = "unresolved"
            else:
                try:
                    parsed = json.loads(strip_fences(response.choices[0].message.content))
                except json.JSONDecodeError:
                    parsed = None
                logic = parsed.get("logic") if isinstance(parsed, dict) else None
                cache[key] = logic if logic in ("and", "or") else "unresolved"
        return cache[key]

    return resolve
