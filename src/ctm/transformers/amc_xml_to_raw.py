"""Read AMC protocol XML export → list of RawAMCTrial Pydantic models.

The XML root is <PROTOCOL_SUMMARY> containing <PROTOCOL> elements.
Empty string values are normalized to None.
"""
from pathlib import Path
import xml.etree.ElementTree as ET

from ..schemas.raw.models import RawAMCTrial

_TAG_MAP = {
    "amc_id": "ID",
    "protocol_no": "NO",
    "nct_number": "NCT_NUMBER",
    "status": "STATUS",
    "title": "TITLE",
    "full_title": "FULL_TITLE",
    "summary_obj": "SUMMARY_OBJ",
    "secondary_protocol_no": "SECONDARY_PROTOCOL_NO",
    "sponsor_type": "SPONSOR_TYPE",
    "age_group": "AGE_GROUP",
    "phase": "PHASE",
    "cancer_prevention": "CANCER_PREVENTION",
    "scope": "SCOPE",
    "disease_site": "DISEASE_SITE",
    "lay_description": "LAY_DESCRIPTION",
    "pi": "PI",
    "institutions": "INSTITUTIONS",
    "oncology_group": "ONCOLOGY_GROUP",
    "management_group": "MANAGEMENT_GROUP",
    "summary4_type": "SUMMARY4_TYPE",
    "octsu_genes_interest": "OCTSU_GENES_INTEREST",
    "eligibility": "ELIGIBILITY",
    "categorys": "CATEGORYS",
    "satellite_sites": "SATELLITE_SITES",
}


def _text(element: ET.Element, tag: str) -> str | None:
    val = element.findtext(tag, "")
    cleaned = (val or "").strip()
    return cleaned or None


def _parse_categories(proto: ET.Element) -> list[dict]:
    cats = []
    for cat_el in proto.findall("CATEGORYS/CATEGORY"):
        c1 = (cat_el.findtext("CAT1") or "").strip()
        c2 = (cat_el.findtext("CAT2") or "").strip()
        c3 = (cat_el.findtext("CAT3") or "").strip()
        if c1:
            cats.append({"cat1": c1, "cat2": c2, "cat3": c3})
    return cats


def load(path: str | Path) -> list[RawAMCTrial]:
    """Parse *path* and return one RawAMCTrial per <PROTOCOL> element."""
    root = ET.parse(path).getroot()
    trials: list[RawAMCTrial] = []
    for proto in root.findall("PROTOCOL"):
        raw = {field: _text(proto, xml_tag) for field, xml_tag in _TAG_MAP.items() if field != "categorys"}
        raw["categorys"] = _parse_categories(proto)
        trials.append(RawAMCTrial.model_validate(raw))
    return trials
