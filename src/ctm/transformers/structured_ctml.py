"""Clinical entries from structured source fields — no model call.

``ctm-mm trials`` used to write these straight into
``treatment_list.step[0].match``. Only ``ctm-llm autofill-ctml`` writes match
clauses now, so the same values are recorded as clinical entries instead, with
exactly the mappings the normalizers used:

* ``structured`` — age and gender. CTGov-sourced trials (Sparrow, West, DDOTS):
  ``minimum_age`` ("18 Years" → ">=18"; months/blank give nothing) and ``sex``
  (ALL gives nothing). AMC: ``age_group`` (Adults → ">=18", Children → "<18").
* ``amc_categories`` — AMC's cat1/cat2/cat3 categories mapped to OncoTree.

``conditions`` (CTGov conditions keyword-mapped) lives in eligibility_to_ctml.
"""
from .oncotree_mapping import amc_categories_to_oncotree, diagnoses_to_match_node
from .raw_amc_to_ctml import _AGE_MATCH
from .raw_ctgov_to_ctml import _normalize_gender, _parse_minimum_age


def _suggestion(source: str, text: str, node: dict | None, mapping_source: str) -> dict:
    return {"source": source, "text": text, "suggested_node": node,
            "transferred_to_match": False, "mapping_source": mapping_source}


def structured_suggestion(trial: dict) -> dict | None:
    """Age/gender from the trial's structured fields, or None if it has neither."""
    raw = trial.get("_raw") or {}
    if "age_group" in raw:                                    # AMC
        if not raw.get("age_group"):
            return None
        age = _AGE_MATCH.get(raw["age_group"].strip().lower())
        node = {"clinical": {"age_numerical": age}} if age else None
        return _suggestion("structured", f"age_group: {raw['age_group']}", node, "structured")

    minimum_age, sex = raw.get("minimum_age"), raw.get("sex")   # CTGov-sourced
    if not minimum_age and not sex:
        return None
    clinical = {}
    age = _parse_minimum_age(minimum_age)
    if age:
        clinical["age_numerical"] = age
    gender = _normalize_gender(sex)
    if gender and gender != "All":
        clinical["gender"] = gender
    text = "; ".join(f"{k}: {v}" for k, v in (("minimum_age", minimum_age), ("sex", sex)) if v)
    return _suggestion("structured", text, {"clinical": clinical} if clinical else None, "structured")


def amc_categories_suggestion(trial: dict) -> dict | None:
    """AMC category diagnosis, or None for a trial with no AMC categories."""
    categories = (trial.get("_raw") or {}).get("categorys") or []
    if not categories:
        return None
    text = "; ".join(" > ".join(c.get(k) for k in ("cat1", "cat2", "cat3") if c.get(k))
                     for c in categories)
    node = diagnoses_to_match_node(amc_categories_to_oncotree(categories))
    return _suggestion("amc_categories", text, node, "keyword")
