"""The shared ``_llm_curation`` model both LLM stages write into.

``ctm-llm clinical`` and ``ctm-llm genomic`` each cut a trial into *pieces* of
text, draft one entry per piece, and merge those entries here. The rules that
make the two stages safe to run in either order, or to rerun, live in this one
place:

* Every entry carries ``node_type`` ("clinical" | "genomic"). An entry with a
  node goes in ``_ctml_suggestions``; a null one in ``_null_ctml_suggestions``.
  Nulls are recorded per stage.
* A stage removes and rewrites only entries of its own ``node_type``. A
  **legacy** entry (no ``node_type``, written before 2.4.0) is never removed or
  edited, and neither is a legacy ``biomarker_references`` key.
* ``mixed`` is true on both entries of a piece (same ``source`` and ``text``)
  that produced a clinical **and** a genomic node — the and/or between them is
  lost in the split, so a curator must read the text. It is recomputed on every
  merge, the one place a stage touches the other stage's entries.
* ``parsed_sections[node_type]`` records, per source, whether the trial had that
  text (``found``), whether this stage processed it (``scanned``) and how many
  pieces were sent (``pieces``).

Pure: no I/O, and inputs are never mutated.
"""
import copy

from .eligibility_to_ctml import _criterion_full_text

CLINICAL = "clinical"
GENOMIC = "genomic"

# ``source`` is the path of the field the text came from, so an entry is
# self-explanatory: you can look the text up in the trial at that path.
INCLUSION = "eligibility.inclusion"
EXCLUSION = "eligibility.exclusion"
LONG_TITLE = "_summary.long_title"
MINIMUM_AGE = "_raw.minimum_age"          # CTGov-sourced (Sparrow, West, DDOTS)
SEX = "_raw.sex"                          # CTGov-sourced
AGE_GROUP = "_raw.age_group"              # AMC
CONDITIONS = "_raw.conditions"            # CTGov-sourced
CATEGORYS = "_raw.categorys"              # AMC (the field's own spelling)
AMC_DISEASE_SITE = "_raw.disease_site"
DDOTS_DISEASE_SITE = "_raw._ddots.disease_site"
GENES_OF_INTEREST = "_raw.octsu_genes_interest"

CLINICAL_SOURCES = (INCLUSION, EXCLUSION, LONG_TITLE, MINIMUM_AGE, SEX, AGE_GROUP, CONDITIONS,
                    CATEGORYS, AMC_DISEASE_SITE, DDOTS_DISEASE_SITE)
GENOMIC_SOURCES = (INCLUSION, EXCLUSION, LONG_TITLE, CONDITIONS, GENES_OF_INTEREST)

# What the model is told the piece is. Plain words: the prompts key negation
# on "exclusion", and a field path means nothing to the model.
PROMPT_LABELS = {INCLUSION: "inclusion", EXCLUSION: "exclusion", LONG_TITLE: "summary (trial title)",
                 CONDITIONS: "conditions", GENES_OF_INTEREST: "genes_of_interest"}

SUGGESTIONS = "_ctml_suggestions"
NULL_SUGGESTIONS = "_null_ctml_suggestions"
PARSED_SECTIONS = "parsed_sections"


def piece_texts(trial: dict, source: str) -> list[str]:
    """The texts one stage sends for ``source``: one per piece, empty if absent."""
    raw = trial.get("_raw") or {}
    if source in (INCLUSION, EXCLUSION):
        criteria = (trial.get("eligibility") or {}).get(source.split(".")[1]) or []
        return [_criterion_full_text(c) for c in criteria]
    if source == LONG_TITLE:
        title = (trial.get("_summary") or {}).get("long_title")
        return [title] if title else []
    if source == CONDITIONS:
        conditions = [c for c in raw.get("conditions") or [] if c]
        return ["; ".join(conditions)] if conditions else []
    if source == GENES_OF_INTEREST:
        genes = raw.get("octsu_genes_interest")
        return [genes] if isinstance(genes, str) and genes.strip() else []
    raise ValueError(f"no prompted pieces for source {source!r}")


def make_entry(source: str, text: str, node: dict | None, node_type: str, **extra) -> dict:
    """One entry. ``transferred_to_match`` stays None until autofill-ctml decides."""
    return {"source": source, "text": text, "suggested_node": node,
            "transferred_to_match": None, "node_type": node_type, "mixed": False, **extra}


def section_row(found: bool, scanned: bool, pieces: int) -> dict:
    return {"found": found, "scanned": scanned, "pieces": pieces}


def recompute_mixed(suggestions: list[dict]) -> None:
    """Set ``mixed`` on every non-legacy entry, in place."""
    types_by_piece: dict[tuple, set] = {}
    for e in suggestions:
        if "node_type" in e:
            types_by_piece.setdefault((e.get("source"), e.get("text")), set()).add(e["node_type"])
    for e in suggestions:
        if "node_type" in e:
            e["mixed"] = types_by_piece[(e.get("source"), e.get("text"))] >= {CLINICAL, GENOMIC}


def merge_stage(curation: dict | None, node_type: str, entries: list[dict],
                sections: dict[str, dict], replace_sources: set[str] | None = None) -> dict:
    """New curation dict with this stage's entries and sections replaced."""
    out = copy.deepcopy(curation) if curation else {}

    def owned(e: dict) -> bool:
        return e.get("node_type") == node_type and (
            replace_sources is None or e.get("source") in replace_sources)

    suggestions = [e for e in out.get(SUGGESTIONS) or [] if not owned(e)]
    nulls = [e for e in out.get(NULL_SUGGESTIONS) or [] if not owned(e)]
    for e in copy.deepcopy(entries):
        (suggestions if e.get("suggested_node") is not None else nulls).append(e)

    recompute_mixed(suggestions)
    for e in nulls:
        if "node_type" in e:
            e["mixed"] = False

    out[SUGGESTIONS] = suggestions
    out[NULL_SUGGESTIONS] = nulls
    parsed = out.setdefault(PARSED_SECTIONS, {})
    parsed.setdefault(node_type, {}).update(copy.deepcopy(sections))
    return out
