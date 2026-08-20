"""Read AMC protocol XML → list of RawAMCTrial Pydantic models.

The XML root is <PROTOCOL_SUMMARY> containing <PROTOCOL> elements.
Empty string values are normalized to None.

Two sources, same parser: ``load()`` reads a local export, ``fetch()`` pulls the
OCTSU feed. Parsing is factored into ``parse()`` so neither path can drift from
the other, and both stamp ``fetched_at`` so a trial records when its source data
was actually obtained rather than leaving it to be inferred from a sibling API
call.
"""
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from ..schemas.raw.models import RawAMCTrial

FEED_URL = "https://octsu.med.umich.edu/xmlfeed/clinical_trial_list_healthcare_provider.xml"

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


def parse(root: ET.Element, *, fetched_at: datetime | None = None) -> list[RawAMCTrial]:
    """One RawAMCTrial per <PROTOCOL> element under *root*.

    ``fetched_at`` records when the source data was obtained. It is passed in
    rather than defaulted here so every trial in one pull shares a single
    timestamp, instead of each getting its own microsecond.
    """
    stamp = fetched_at or datetime.now(tz=UTC)
    trials: list[RawAMCTrial] = []
    for proto in root.findall("PROTOCOL"):
        raw = {field: _text(proto, xml_tag) for field, xml_tag in _TAG_MAP.items() if field != "categorys"}
        raw["categorys"] = _parse_categories(proto)
        raw["fetched_at"] = stamp
        trials.append(RawAMCTrial.model_validate(raw))
    return trials


def load(path: str | Path, *, fetched_at: datetime | None = None) -> list[RawAMCTrial]:
    """Parse a local AMC XML export.

    ``fetched_at`` defaults to now, which for a file means "when we read it" — the
    file's own age is not knowable from its contents. Pass it explicitly to record
    when the export was actually produced.
    """
    return parse(ET.parse(path).getroot(), fetched_at=fetched_at)


def fetch(*, url: str | None = None, timeout: int = 60) -> list[RawAMCTrial]:
    """Pull and parse the OCTSU feed.

    The timestamp is taken before the request rather than after parsing, so it
    marks when the data was requested rather than how long the parse took.
    """
    import os

    target = url or os.environ.get("AMC_FEED_URL") or FEED_URL
    stamp = datetime.now(tz=UTC)
    try:
        with urllib.request.urlopen(target, timeout=timeout) as response:
            root = ET.fromstring(response.read())
    except ET.ParseError as exc:
        raise RuntimeError(f"AMC feed at {target} returned unparseable XML: {exc}") from None
    except Exception as exc:
        raise RuntimeError(f"AMC feed request to {target} failed: {type(exc).__name__}") from None

    trials = parse(root, fetched_at=stamp)
    if not trials:
        # A served error page can be perfectly well-formed XML — "<html><body>503
        # …</body></html>" parses fine, contains no <PROTOCOL>, and would otherwise
        # yield zero trials indistinguishably from an empty feed. A live feed
        # returning nothing is far more likely a wrong response than a real answer,
        # so say so. load() stays tolerant: an empty local export is the caller's
        # own file, and their problem to explain.
        raise RuntimeError(
            f"AMC feed at {target} contained no <PROTOCOL> elements "
            f"(root element was <{root.tag}>) — likely an error page rather than the feed"
        )
    return trials
