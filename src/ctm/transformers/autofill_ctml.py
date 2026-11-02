"""``ctm-llm autofill-ctml`` [BETA] — the one writer of ``treatment_list.step[0].match``.

Builds a trial's match clause from its ``_llm_curation._ctml_suggestions``
(clinical and genomic, written by ``ctm-llm clinical`` / ``ctm-llm genomic``).
No model call. Rules:

* Every criterion node (``inclusion`` / ``exclusion``, both node types) and the
  ``structured`` age/gender node is included. Exclusion nodes already carry
  their negation (``!`` on clinical values, ``!Mutation`` on genomic).
* Diagnosis: if any criterion node names a (non-negated)
  ``oncotree_primary_diagnosis``, the criteria's diagnosis stands. Otherwise
  one ``or`` of the distinct diagnoses from ``summary``, ``conditions``,
  ``amc_categories`` and ``disease_site`` is added — never AND-ed together,
  which would match almost nobody.
* Genomic: nodes from ``summary``, ``conditions`` and ``genes_of_interest`` are
  added only when no criterion produced a genomic node.
* Identical nodes collapse to one; two or more are combined under one ``and``.
  A ``mixed`` piece's clinical and genomic nodes are AND-ed — counted in the
  report so a curator checks those first.
* Every clinical/genomic leaf gets ``_CURATED_METHOD: "auto"``. matchengine
  must list ``_CURATED_METHOD`` with ``"ignore": true`` under both clinical and
  genomic ``trial_key_mappings``, or these clauses match nobody.
* Only an empty match is filled unless ``overwrite``. Used entries are marked
  ``transferred_to_match``; a report is stored at ``_llm_curation.autofill``.

Pure: no I/O, inputs never mutated. Legacy entries (no ``node_type``) are ignored.
"""
import copy

CURATED_METHOD_KEY = "_CURATED_METHOD"

_CRITERIA = ("inclusion", "exclusion")
_DIAGNOSIS_FALLBACK = ("summary", "conditions", "amc_categories", "disease_site")
_GENOMIC_FALLBACK = ("summary", "conditions", "genes_of_interest")
_DX = "oncotree_primary_diagnosis"


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
        if node not in nodes:
            nodes.append(node)
        if entry not in used:
            used.append(entry)

    criteria = [e for e in entries if e["source"] in _CRITERIA]
    for e in entries:
        if e["source"] in _CRITERIA or e["source"] == "structured":
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

    marked = [_mark(n) for n in nodes]
    steps[0]["match"] = [marked[0]] if len(marked) == 1 else [{"and": marked}]
    for e in curation["_ctml_suggestions"]:
        if any(e is u for u in used):
            e["transferred_to_match"] = True
    report.update(filled=True, nodes=len(nodes),
                  mixed_pieces=len({(e["source"], e["text"]) for e in used if e.get("mixed")}))
    curation["autofill"] = report
    return out, report

