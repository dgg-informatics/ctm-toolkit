"""Fetch AMC's OnCORE trial feed → list of RawAMCTrial Pydantic models.

AMC publishes a daily refresh of its OnCORE trial list at a fixed OCTSU URL.
The whole list arrives in one response — there is no per-trial endpoint and no
pagination, so a fetch is always a full snapshot of every AMC trial.

The URL is hardcoded below rather than configured: it is a stable institutional
feed, and an env var would only add a way for a run to silently read the wrong
one. Equivalent to `curl <FEED_URL>`; ``urllib`` is used instead of shelling out
to curl to match the ClinicalTrials.gov fetch in :mod:`ctgov_to_raw` and to keep
the toolkit free of a subprocess dependency.

The response is *not* written to disk. The raw records are archived in MongoDB's
``00_raw_trials`` collection by ``ctm-fetch --amc``, which is the durable copy.
"""
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from ..schemas.raw.models import RawAMCTrial
from .amc_xml_to_raw import ROOT_TAG, from_root

FEED_URL = "https://octsu.med.umich.edu/xmlfeed/clinical_trial_list_healthcare_provider.xml"

# Generous relative to the single-study CTGov call: this is the entire AMC trial
# list in one document, and the feed is regenerated on a schedule rather than
# served from a cache.
_TIMEOUT = 120


def fetch_xml(url: str = FEED_URL) -> bytes:
    """Raw feed bytes.

    Raises:
        ValueError: on any HTTP or transport failure, with the URL named.
    """
    request = urllib.request.Request(url, headers={"Accept": "application/xml"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise ValueError(f"AMC feed returned HTTP {exc.code} ({exc.reason}) for {url}") from exc
    except urllib.error.URLError as exc:
        # Institutional feed — off-VPN this is the common failure, so say so.
        raise ValueError(
            f"could not reach the AMC feed at {url}: {exc.reason}. "
            "Check network access to octsu.med.umich.edu"
        ) from exc


def fetch(url: str = FEED_URL) -> list[RawAMCTrial]:
    """Every trial in the AMC feed.

    Raises:
        ValueError: on transport failure, unparseable XML, or an empty feed.
    """
    payload = fetch_xml(url)

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        # Showing the opening bytes turns a bare parse error into a diagnosis.
        raise ValueError(
            f"AMC feed at {url} did not return parseable XML ({exc}). "
            f"Response began: {payload[:120]!r}"
        ) from exc

    # The root tag is checked, not just the trial count, because a login
    # redirect or error page arrives as HTTP 200 and well-formed markup — it
    # parses cleanly and yields zero <PROTOCOL> elements, which would otherwise
    # be reported as "the feed is empty" and send someone hunting upstream at
    # AMC for a problem that is actually authentication or a bad URL.
    if root.tag != ROOT_TAG:
        raise ValueError(
            f"AMC feed at {url} returned <{root.tag}>, not <{ROOT_TAG}> — the "
            f"endpoint answered with something other than the trial feed. "
            f"Response began: {payload[:120]!r}"
        )

    trials = from_root(root)

    if not trials:
        # Must not read as "AMC has no trials": that would reach trials-diff and
        # route every AMC trial to 'deleted', wiping curated match trees.
        raise ValueError(
            f"AMC feed at {url} parsed but contained no <PROTOCOL> elements — "
            "treating as an upstream fault rather than an empty trial list"
        )

    return trials
