"""``ctm-llm autofill-ctml`` [BETA] — the one writer of ``treatment_list.step[0].match``.

Builds a trial's match clause from its ``_llm_curation._ctml_suggestions``
(clinical and genomic, written by ``ctm-llm clinical`` / ``ctm-llm genomic``).
No model call. ``source`` values are field paths. Rules:

* Every criterion node (``eligibility.inclusion`` / ``eligibility.exclusion``,
  both node types) and every age/sex node (``_raw.minimum_age``, ``_raw.sex``,
  ``_raw.age_group``) is included. Exclusion nodes already carry their
  negation (``!`` on clinical values, ``!Mutation`` on genomic).
* Diagnosis: if any criterion node names a (non-negated)
  ``oncotree_primary_diagnosis``, the criteria's diagnosis stands. Otherwise
  one ``or`` of the distinct diagnoses from ``_summary.long_title``,
  ``_raw.conditions``, ``_raw.categorys`` and the disease-site fields is added —
  never AND-ed together, which would match almost nobody.
* Genomic: nodes from ``_summary.long_title``, ``_raw.conditions`` and
  ``_raw.octsu_genes_interest`` are added only when no criterion produced one.
* ``ecog_score`` is not used in match clauses yet: it is removed from every
  node before writing (the suggestion itself is kept). A node left empty is
  dropped and its entry marked not transferred.
* Identical nodes collapse to one. The match is a plain list of nodes — a
  top-level list is already AND-ed, so no ``and`` wrapper. A ``mixed`` piece's
  clinical and genomic nodes are AND-ed that way — counted in the report.
* Every clinical/genomic leaf gets ``_CURATED_METHOD: "auto"``. matchengine
  must list ``_CURATED_METHOD`` with ``"ignore": true`` under both clinical and
  genomic ``trial_key_mappings``, or these clauses match nobody.
* Only an empty match is filled unless ``overwrite``. When it fills, every
  non-legacy entry (both lists) gets ``transferred_to_match`` true if used,
  false if not; when it skips, the flags stay None (never decided). A report is
  stored at ``_llm_curation.autofill``.

Pure: no I/O, inputs never mutated. Legacy entries (no ``node_type``) are ignored.
"""
import copy

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
)

CURATED_METHOD_KEY = "_CURATED_METHOD"

_CRITERIA = (INCLUSION, EXCLUSION)
_AGE_SEX = (MINIMUM_AGE, SEX, AGE_GROUP)
_DIAGNOSIS_FALLBACK = (LONG_TITLE, CONDITIONS, CATEGORYS, AMC_DISEASE_SITE, DDOTS_DISEASE_SITE)
_GENOMIC_FALLBACK = (LONG_TITLE, CONDITIONS, GENES_OF_INTEREST)
_DX = "oncotree_primary_diagnosis"
# Clinical fields suggested but not yet used in match clauses.
NOT_IN_MATCH = frozenset({"ecog_score"})


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


def _without_unused_fields(node: dict) -> dict | None:
    """``node`` without NOT_IN_MATCH clinical fields; None if nothing is left."""
    for key in ("and", "or"):
        if key in node:
            children = [c for c in (_without_unused_fields(c) for c in node[key]) if c is not None]
            if not children:
                return None
            return children[0] if len(children) == 1 else {key: children}
    if "clinical" in node:
        fields = {k: v for k, v in node["clinical"].items() if k not in NOT_IN_MATCH}
        return {"clinical": fields} if fields else None
    return node


def _mark(node: dict) -> dict:
    """Deep copy of ``node`` with the auto marker on every clinical/genomic leaf."""
    for key in ("and", "or"):
        if key in node:
            return {key: [_mark(c) for c in node[key]]}
    return {kind: {**fields, CURATED_METHOD_KEY: "auto"} for kind, fields in node.items()}


def autofill_trial(trial: dict, overwrite: bool = False) -> tuple[dict, dict]:
    """(new trial, report). The report is also stored at ``_llm_curation.autofill``."""
    out = copy.deepcopy(trial)
    curation = out.setdefault("_llm_curation", {})
    entries = [e for e in curation.get("_ctml_suggestions") or []
               if e.get("node_type") and e.get("suggested_node") is not None]
    report = {"method": "auto", "filled": False, "reason": None, "nodes": 0,
              "mixed_pieces": 0, "diagnosis_from": None}

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

    def add(node: dict, entry: dict) -> None:
        node = _without_unused_fields(node)
        if node is None:
            return
        if node not in nodes:
            nodes.append(node)
        if not any(entry is u for u in used):
            used.append(entry)

    criteria = [e for e in entries if e["source"] in _CRITERIA]
    for e in entries:
        if e["source"] in _CRITERIA or e["source"] in _AGE_SEX:
            add(e["suggested_node"], e)

    if any(_positive_diagnoses(e["suggested_node"]) for e in criteria if e["node_type"] == "clinical"):
        report["diagnosis_from"] = "criteria"
    else:
        names, sources = [], []
        for e in entries:
            if e["node_type"] == "clinical" and e["source"] in _DIAGNOSIS_FALLBACK:
                found = _positive_diagnoses(e["suggested_node"])
                if found:
                    sources.append(e)
                    names += [n for n in found if n not in names]
        if names:
            leaves = [{"clinical": {_DX: n}} for n in names]
            for e in sources:
                add(leaves[0] if len(leaves) == 1 else {"or": leaves}, e)
            report["diagnosis_from"] = "fallback"

    if not any(e["node_type"] == "genomic" for e in criteria):
        for e in entries:
            if e["node_type"] == "genomic" and e["source"] in _GENOMIC_FALLBACK:
                add(e["suggested_node"], e)

    if not nodes:
        report["reason"] = "no suggestions"
        curation["autofill"] = report
        return out, report

    steps[0]["match"] = [_mark(n) for n in nodes]
    for e in (curation.get("_ctml_suggestions") or []) + (curation.get("_null_ctml_suggestions") or []):
        if e.get("node_type"):
            e["transferred_to_match"] = any(e is u for u in used)
    report.update(filled=True, nodes=len(nodes),
                  mixed_pieces=len({(e["source"], e["text"]) for e in used if e.get("mixed")}))
    curation["autofill"] = report
    return out, report

