"""Disease site → ``oncotree_primary_diagnosis`` suggestion.

Some trials state their cancer type only in the registry disease-site field
("Colon", "Melanoma, Skin", "Lymphoid Leukemia"), never in the criteria or title.
This turns that field into one extra ``_ctml_suggestions`` entry with
``source: "disease_site"``, added alongside the criteria and title suggestions.

Each distinct site value (lowercased, trimmed) is mapped once and remembered:

1. ``src/ctm/refs/disease_site_oncotree.json`` — the reviewed table, in the repo.
2. ``<cache_dir>/.disease_site_cache.json`` — AI drafts, not yet reviewed.
3. Otherwise ask the model, validate the name, and save it as a draft.

Promote a draft by copying its row from the cache into the refs file. Row shape
(both files)::

    "melanoma, skin": {"site": "Melanoma, Skin", "oncotree": "Melanoma", "type": "solid"}

``oncotree`` may be null (e.g. "Unknown Sites", "Cancer Control") or, in the
reviewed table only, a list of names for a site with no single OncoTree parent
("GU", "Lymphoma"); ``type`` is "solid", "liquid" or null. A trial listing :data:`MANY_SITES` or more distinct sites collapses to the
special values ``_SOLID_`` / ``_LIQUID_`` (all solid / all liquid cancers).

Sources read: AMC ``_raw.disease_site``, DDOTS ``_raw._ddots.disease_site`` and
West ``_raw._west.Disease Site`` (the sheet's column header), each split on ";" and recorded as its own entry (``source`` = the field path).
``mapping_source`` is ``reviewed_ref`` when every site came from the reviewed
table, else ``ai_draft``. DDOTS ``disease_site_list`` is ignored until real multi-site
data shows its shape.
"""
import json
import logging
import os
from pathlib import Path

from ..paths import REFS_DIR, cache_path

log = logging.getLogger(__name__)

REVIEWED_PATH = REFS_DIR / "disease_site_oncotree.json"
DRAFT_CACHE_NAME = ".disease_site_cache.json"
AMC_FIELD = "_raw.disease_site"
DDOTS_FIELD = "_raw._ddots.disease_site"
WEST_FIELD = "_raw._west.Disease Site"
SITE_FIELDS = (AMC_FIELD, DDOTS_FIELD, WEST_FIELD)
# The field each source is expected to carry. A trial from that source without
# it is reported (llm.field_missing), never an error: the sheets' columns drift.
EXPECTED_SITE_FIELD = {"amc": AMC_FIELD, "sparrow-api": DDOTS_FIELD, "west": WEST_FIELD}
REVIEWED_REF = "reviewed_ref"
AI_DRAFT = "ai_draft"
MANY_SITES = 10
SPECIAL = {"_SOLID_", "_LIQUID_"}

SYSTEM_PROMPT = """You map cancer registry disease-site categories to OncoTree.

Given one disease-site category from a clinical trial registry, return JSON:
  {"oncotree": "<OncoTree cancer type name or null>", "type": "solid" | "liquid" | null}

Rules:
- Use the exact OncoTree tumor type name, at the broadest level that fully covers the site
  (e.g. "Breast" → "Breast"; "Melanoma, Skin" → "Melanoma"; "Lymphoid Leukemia" → "Lymphoid").
- "type" is "liquid" for leukemias, lymphomas, myeloma and other hematopoietic sites,
  "solid" for everything else.
- If the site is a broad bucket with no single OncoTree equivalent, use "_SOLID_" for an
  all-solid-tumor bucket or "_LIQUID_" for an all-hematologic bucket.
- If the site carries no diagnostic meaning (e.g. "Unknown Sites", "Ill-Defined Sites"),
  return {"oncotree": null, "type": null}.
Return only the JSON object."""


def _norm(site: str) -> str:
    return " ".join(site.split()).lower()


def default_draft_path() -> Path:
    return cache_path(DRAFT_CACHE_NAME)


def _load_table(path: Path) -> dict:
    if not path.exists():
        return {}
    return {_norm(k): v for k, v in json.loads(path.read_text()).items()}


def load_reviewed(path: Path | None = None) -> dict:
    return _load_table(path or REVIEWED_PATH)


def load_drafts(path: Path | None = None) -> dict:
    return _load_table(path or default_draft_path())


def save_drafts(drafts: dict, path: Path | None = None) -> None:
    path = path or default_draft_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(drafts, indent=2, sort_keys=True))


_MISSING = object()


def _field_value(trial: dict, field: str):
    """Value at a dotted path like ``_raw._west.Disease Site``; _MISSING if absent."""
    value = trial
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def trial_sites(trial: dict, field: str) -> list[str]:
    """Distinct disease sites in one field (see SITE_FIELDS), split on ";",
    first-seen order."""
    value = _field_value(trial, field)
    sites, seen = [], set()
    if not isinstance(value, str):
        return sites
    for part in value.split(";"):
        part = " ".join(part.split())
        if part and _norm(part) not in seen:
            seen.add(_norm(part))
            sites.append(part)
    return sites


def notify_missing_site_field(trial: dict) -> None:
    """Warn (don't fail) when a trial lacks its source's disease-site field."""
    field = EXPECTED_SITE_FIELD.get(trial.get("entity"))
    if field and _field_value(trial, field) is _MISSING:
        label = trial.get("protocol_no") or trial.get("nct_id") or "unknown"
        log.warning("  %s: no %s field — disease site not read", label, field,
                    extra={"event": "llm.field_missing", "trial": label, "field": field,
                           "entity": trial.get("entity"), "stage": "clinical"})


def _validate(row: dict | None, site: str, valid: set[str]) -> dict:
    from .oncotree_mapping import fix_oncotree_name
    row = row if isinstance(row, dict) else {}
    name = row.get("oncotree")
    if isinstance(name, str) and name not in SPECIAL:
        name = fix_oncotree_name(name, valid)
    elif not isinstance(name, str):
        name = None
    kind = row.get("type") if row.get("type") in ("solid", "liquid") else None
    if kind is None and name in SPECIAL:
        kind = "solid" if name == "_SOLID_" else "liquid"
    return {"site": site, "oncotree": name, "type": kind}


def _ask_model(site: str, client, valid: set[str]) -> dict:
    from .eligibility_to_ctml import is_content_filter
    model = os.environ.get("UMGPT_MODEL", "gpt-4o")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Disease site: {site}"},
            ],
            temperature=0,
            max_tokens=100,
        )
    except Exception as exc:
        if not is_content_filter(exc):
            raise
        log.warning("  content filter — no mapping for disease site %r", site,
                    extra={"event": "llm.content_filter", "stage": "general",
                           "criterion_source": "disease_site"})
        return _validate(None, site, valid)
    try:
        parsed = json.loads(response.choices[0].message.content.strip())
    except json.JSONDecodeError:
        parsed = None
    return _validate(parsed, site, valid)


def map_site(site: str, reviewed: dict, drafts: dict, client, valid: set[str]) -> tuple[dict, str]:
    """(row, mapping_source) for one site; drafts a new row via the model if unseen."""
    key = _norm(site)
    if key in reviewed:
        return reviewed[key], REVIEWED_REF
    if key not in drafts:
        drafts[key] = _ask_model(site, client, valid)
    return drafts[key], AI_DRAFT


def _leaf(name: str) -> dict:
    return {"clinical": {"oncotree_primary_diagnosis": name}}


def _node(rows: list[dict]) -> dict | None:
    if len(rows) >= MANY_SITES:
        kinds = {r.get("type") for r in rows}
        names = [n for n, k in (("_SOLID_", "solid"), ("_LIQUID_", "liquid")) if k in kinds]
    else:
        names = []
        for r in rows:
            value = r.get("oncotree")
            for name in value if isinstance(value, list) else [value]:
                if name and name not in names:
                    names.append(name)
    if not names:
        return None
    return _leaf(names[0]) if len(names) == 1 else {"or": [_leaf(n) for n in names]}


def site_suggestion(trial: dict, reviewed: dict, drafts: dict, client,
                    valid: set[str], field: str = AMC_FIELD) -> dict | None:
    """The suggestion for one disease-site field, or None when it is empty."""
    sites = trial_sites(trial, field)
    if not sites:
        return None
    mapped = [map_site(s, reviewed, drafts, client, valid) for s in sites]
    rows = [row for row, _ in mapped]
    return {
        "source": field,
        "text": "; ".join(sites),
        "suggested_node": _node(rows),
        "transferred_to_match": None,
        "mapping_source": REVIEWED_REF if all(m == REVIEWED_REF for _, m in mapped) else AI_DRAFT,
        "site_mappings": [{**row, "site": site, "mapping_source": m}
                          for site, (row, m) in zip(sites, mapped, strict=True)],
    }
