"""Clinical entries from source fields — no model call, one entry per field.

``ctm-mm trials`` used to write these straight into
``treatment_list.step[0].match``. Only ``ctm-llm autofill-ctml`` writes match
clauses now, so the same values are recorded as clinical entries instead, with
exactly the mappings the normalizers used. ``source`` is the field's path:

* ``_raw.minimum_age`` (CTGov-sourced): "18 Years" → ``age_numerical ">=18"``;
  months or unparseable → null node.
* ``_raw.sex`` (CTGov-sourced): FEMALE/MALE → ``gender``; ALL → null node.
* ``_raw.age_group`` (AMC): Adults → ">=18", Children → "<18"; Both /
  Unspecified → null node.
* ``_raw.categorys`` (AMC): cat1/cat2/cat3 → OncoTree via the AMC category map.

``_raw.conditions`` (CTGov conditions keyword-mapped) lives in eligibility_to_ctml.
A field that is present but maps to nothing still gets an entry (null node), so
the curator can see it was read.
"""
from .llm_curation import AGE_GROUP, CATEGORYS, MINIMUM_AGE, SEX
from .oncotree_mapping import amc_categories_to_oncotree, diagnoses_to_match_node
from .raw_amc_to_ctml import _AGE_MATCH
from .raw_ctgov_to_ctml import _normalize_gender, _parse_minimum_age

FIELD_VALUE_MAP = "field_value_map"
AMC_CATEGORY_ONCOTREE_MAP = "amc_category_oncotree_map"


def _suggestion(source: str, text: str, node: dict | None, mapping_source: str) -> dict:
    return {"source": source, "text": text, "suggested_node": node,
            "transferred_to_match": None, "mapping_source": mapping_source}


def _clinical(**fields) -> dict | None:
    fields = {k: v for k, v in fields.items() if v}
    return {"clinical": fields} if fields else None


def age_sex_suggestions(trial: dict) -> list[dict]:
    """One entry per age/sex field the trial has (minimum_age, sex, age_group)."""
    raw = trial.get("_raw") or {}
    out = []
    minimum_age = raw.get("minimum_age")
    if minimum_age:
        out.append(_suggestion(MINIMUM_AGE, minimum_age,
                               _clinical(age_numerical=_parse_minimum_age(minimum_age)), FIELD_VALUE_MAP))
    sex = raw.get("sex")
    if sex:
        gender = _normalize_gender(sex)
        out.append(_suggestion(SEX, sex, _clinical(gender=gender if gender != "All" else None),
                               FIELD_VALUE_MAP))
    age_group = raw.get("age_group")
    if age_group:
        age = _AGE_MATCH.get(age_group.strip().lower())
        out.append(_suggestion(AGE_GROUP, age_group, _clinical(age_numerical=age), FIELD_VALUE_MAP))
    return out


def amc_categories_suggestion(trial: dict) -> dict | None:
    """AMC category diagnosis, or None for a trial with no AMC categories."""
    categories = (trial.get("_raw") or {}).get("categorys") or []
    if not categories:
        return None
    text = "; ".join(" > ".join(c.get(k) for k in ("cat1", "cat2", "cat3") if c.get(k))
                     for c in categories)
    node = diagnoses_to_match_node(amc_categories_to_oncotree(categories))
    return _suggestion(CATEGORYS, text, node, AMC_CATEGORY_ONCOTREE_MAP)
