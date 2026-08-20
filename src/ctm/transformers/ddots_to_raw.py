"""Read the DDOTS /protocol endpoint → list[RawSparrowTrial].

DDOTS is Sparrow's protocol registry and replaces the hand-maintained marketing
Excel sheet as the trial list. Clinical content still comes from
ClinicalTrials.gov: see raw_sparrow_to_ctml, which is unchanged by this module.

Three things about the payload shape drive this code.

**Columnar, not row objects.** The response is
``{"COLUMNS": [...], "DATA": [[...], ...]}``, with ``COLUMNS`` UPPERCASE while
query parameters and field names are lowercase. `rows()` zips them back into
lowercase-keyed dicts.

**``return_fields`` replaces the default column set rather than adding to it.**
A bare call returns one set of columns; a call naming ``return_fields`` returns
those plus only a small always-present core. So a query that forgets to name
``status`` or ``disease_site`` silently loses them even though a bare call would
have included them. DEFAULT_RETURN_FIELDS names everything the pipeline wants.

**Errors arrive as a 200 with a normal-looking body.** A throttled request
returns ``{"COLUMNS": ["CALLDSN", "ERRORTEXT"], "DATA": [["429", "Too Many
Requests"]]}`` — well-formed JSON, no HTTP error, nothing for urlopen to raise
on. Left unchecked it parses into one row with no ``nct_number``, gets dropped as
"no usable NCT number", and surfaces as *zero trials*, which is
indistinguishable from an empty result set. ``raise_for_api_error`` catches the
envelope on both the fetch and the file-replay path.

**``NCT_NUMBER`` arrives unprefixed** — ``"00114140"`` for NCT00114140. It is the
trial's identity (`trial_key` returns ``nct_id`` for Sparrow) and the CTGov
lookup key, so it is normalized to the prefixed form and validated here rather
than failing later.
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from ..schemas.raw.models import RawDdotsTrial

BASE_URL = "https://www.ddotscredit.com/rest/protocol/get"

# Named explicitly because return_fields is not additive — see module docstring.
# `sponsor` is deliberately absent: it is documented for /protocol but is not
# returned for this configuration, so CTGov remains the sponsor source.
DEFAULT_RETURN_FIELDS = (
    "eligibility",
    "protocol_title",
    "protocol_title_short",
    "protocol_summary",
    "min_age",
    "max_age",
    "nct_number",
    "local_id",
    "protocol_type",
    "status",
    "status_short",
    "disease_site",
    "disease_site_list",
    "disease_category",
    "investigator",
    "investigator_email",
    "coordinator",
    "coordinator_email",
    "department_name",
    "hospital",
    "hospital_id",
    "hospital_email",
)

# The DDOTS instance is shared across institutions — a payload's credentialing
# notes name Trinity Health, Sparrow, Genesys, Hurley and Lehigh Valley among
# others. An unscoped query therefore returns other hospitals' protocols, which
# this pipeline would then stamp entity="sparrow-api". 18 is Sparrow.
DEFAULT_HOSPITAL_ID = "18"

# "O" = open. DDOTS also returns e.g. "P" (permanently closed), "C" (closed).
DEFAULT_STATUS_SHORT = "O"

_NCT_DIGITS_RE = re.compile(r"^(?:NCT)?(\d{8})$", re.IGNORECASE)

# DDOTS signals failure in the payload rather than the HTTP status, using this
# exact column pair. Matched as a set so column order cannot break detection.
_ERROR_COLUMNS = frozenset({"calldsn", "errortext"})


class DdotsApiError(RuntimeError):
    """An error DDOTS reported inside a 200 response body."""

    def __init__(self, code: str, text: str):
        self.code = code
        self.text = text
        super().__init__(f"DDOTS API error {code}: {text}")

    @property
    def is_rate_limited(self) -> bool:
        return str(self.code) == "429"


def raise_for_api_error(payload: dict) -> None:
    """Raise DdotsApiError if ``payload`` is an error envelope rather than data.

    Called from both fetch() and to_raw_trials(), so a saved error response that
    gets replayed with --ddots <file> fails just as loudly as a live one.
    """
    columns = [str(c).lower() for c in payload.get("COLUMNS") or []]
    # Compared as a set so column order cannot break detection, but zipped in the
    # payload's own order so the values land on the right names.
    if set(columns) != _ERROR_COLUMNS:
        return
    data = payload.get("DATA") or []
    row = dict(zip(columns, data[0], strict=False)) if data else {}
    raise DdotsApiError(row.get("calldsn", "unknown"), row.get("errortext", "unknown error"))


def normalize_nct(raw: object) -> str | None:
    """``"00114140"`` or ``"NCT00114140"`` → ``"NCT00114140"``; None if malformed.

    DDOTS returns the digits without the prefix, which the Excel loader's
    ``^NCT\\d{8}$`` check would reject outright.
    """
    if raw is None:
        return None
    cleaned = re.sub(r"\s+", "", str(raw).strip()).upper()
    match = _NCT_DIGITS_RE.match(cleaned)
    return f"NCT{match.group(1)}" if match else None


def parse_documents(raw: object) -> dict:
    """``DOCUMENTS`` is a JSON document encoded as a string inside the JSON."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def rows(payload: dict) -> list[dict]:
    """``{"COLUMNS": [...], "DATA": [[...]]}`` → list of lowercase-keyed dicts."""
    columns = [str(c).lower() for c in payload.get("COLUMNS") or []]
    if not columns:
        return []
    out = []
    for row in payload.get("DATA") or []:
        out.append(dict(zip(columns, row, strict=False)))
    return out


def build_url(api_key: str, secret_key: str, *, base_url: str = BASE_URL,
              status_short: str | None = DEFAULT_STATUS_SHORT,
              hospital_id: str | None = DEFAULT_HOSPITAL_ID,
              return_fields: tuple[str, ...] = DEFAULT_RETURN_FIELDS,
              **extra) -> str:
    """The full request URL. Kept separate so it can be tested without a network."""
    params = {
        "api_key": api_key,
        "api_secret_key": secret_key,
        "return_fields": ",".join(return_fields),
    }
    if hospital_id:
        params["hospital_id"] = hospital_id
    if status_short:
        params["status_short"] = status_short
    params.update({k: v for k, v in extra.items() if v is not None})
    return f"{base_url}?{urllib.parse.urlencode(params)}"


def _credentials() -> tuple[str, str]:
    api_key = os.environ.get("DDOTS_API_KEY")
    secret_key = os.environ.get("DDOTS_SECRET_KEY")
    for name, value in (("DDOTS_API_KEY", api_key), ("DDOTS_SECRET_KEY", secret_key)):
        if not value:
            raise ValueError(f"{name} not set in environment")
    return api_key, secret_key


def fetch(*, status_short: str | None = DEFAULT_STATUS_SHORT,
          hospital_id: str | None = None,
          return_fields: tuple[str, ...] = DEFAULT_RETURN_FIELDS,
          timeout: int = 60, **extra) -> dict:
    """GET the /protocol endpoint and return the raw payload.

    The secret key travels in the query string, so nothing here prints the URL —
    it would land in shell history, logs, and any traceback. Errors name the
    endpoint only.
    """
    api_key, secret_key = _credentials()
    base_url = os.environ.get("DDOTS_BASE_URL", BASE_URL)
    # Resolution order: explicit argument, DDOTS_HOSPITAL_ID, then the Sparrow
    # default. Never unscoped unless a caller passes hospital_id="" deliberately.
    if hospital_id is None:
        hospital_id = os.environ.get("DDOTS_HOSPITAL_ID") or DEFAULT_HOSPITAL_ID
    url = build_url(api_key, secret_key, base_url=base_url, status_short=status_short,
                    hospital_id=hospital_id, return_fields=return_fields, **extra)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read())
    except Exception as exc:
        raise RuntimeError(f"DDOTS request to {base_url} failed: {type(exc).__name__}") from None

    # DDOTS reports throttling and other faults in the body, not the status line.
    raise_for_api_error(payload)
    return payload


def to_raw_trials(payload: dict) -> list[RawDdotsTrial]:
    """Payload → RawDdotsTrial list, skipping rows with no usable NCT number.

    Field names pass through verbatim, so this is deliberately a thin mapping: the
    only transformations are the NCT prefixing and the nested-JSON DOCUMENTS parse.
    A row without a usable NCT number cannot be given an identity (`trial_key`
    returns `nct_id` for non-AMC trials) and cannot be looked up on
    ClinicalTrials.gov, so it is dropped with a warning rather than silently.
    """
    raise_for_api_error(payload)

    trials: list[RawDdotsTrial] = []
    for row in rows(payload):
        nct_id = normalize_nct(row.get("nct_number"))
        if not nct_id:
            label = row.get("protocol") or row.get("protocol_id") or "<unknown>"
            print(f"  Warning: skipping DDOTS protocol {label} — "
                  f"no usable NCT number ({row.get('nct_number')!r})", file=sys.stderr)
            continue

        trials.append(RawDdotsTrial(
            nct_id=nct_id,
            **{k: v for k, v in row.items() if k not in ("documents", "county_distance")},
            documents=parse_documents(row.get("documents")),
        ))
    return trials


def load(path: str | Path) -> list[RawDdotsTrial]:
    """Read a saved DDOTS response from disk. Accepts one payload or a list of them.

    A dump is worth supporting beyond testing: it is a reproducible record of what
    the API returned on a given day, which a live call is not.
    """
    data = json.loads(Path(path).read_text())
    payloads = data if isinstance(data, list) else [data]
    trials: list[RawDdotsTrial] = []
    for payload in payloads:
        trials.extend(to_raw_trials(payload))
    return trials
