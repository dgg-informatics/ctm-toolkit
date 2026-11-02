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

Sources read: AMC ``_raw.disease_site`` and DDOTS ``_raw._ddots.disease_site``,
both split on ";". DDOTS ``disease_site_list`` is ignored until real multi-site
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
SOURCE = "disease_site"
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


def trial_sites(trial: dict) -> list[str]:
    """Distinct disease sites from AMC and DDOTS raw fields, first-seen order."""
    raw = trial.get("_raw") or {}
    values = [raw.get("disease_site"), (raw.get("_ddots") or {}).get("disease_site")]
    sites, seen = [], set()
    for value in values:
        if not isinstance(value, str):
            continue
        for part in value.split(";"):
            part = " ".join(part.split())
            if part and _norm(part) not in seen:
                seen.add(_norm(part))
                sites.append(part)
    return sites


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
                           "criterion_source": SOURCE})
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
        return reviewed[key], "reviewed"
    if key not in drafts:
        drafts[key] = _ask_model(site, client, valid)
    return drafts[key], "ai_draft"


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
                    valid: set[str]) -> dict | None:
    """The ``disease_site`` suggestion, or None when the trial has no disease site."""
    sites = trial_sites(trial)
    if not sites:
        return None
    mapped = [map_site(s, reviewed, drafts, client, valid) for s in sites]
    rows = [row for row, _ in mapped]
    return {
        "source": SOURCE,
        "text": "; ".join(sites),
        "suggested_node": _node(rows),
        "transferred_to_match": False,
        "mapping_source": "reviewed" if all(m == "reviewed" for _, m in mapped) else "ai_draft",
        "site_mappings": [{**row, "site": site, "mapping_source": m}
                          for site, (row, m) in zip(sites, mapped)],
    }
