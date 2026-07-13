"""Loads trial-match report data and renders it through the Jinja2 template."""
import json
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

BASE_DIR = Path(__file__).parent.parent.parent.parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

DATA_SOURCE_VERSION = "TrialDBv0.1-jun26"

METHODS_PATH = DATA_DIR / "content" / "methods.json"

PATIENT_HEADER_FIELDS = {
    "first_name": "First Name",
    "last_name": "Last Name",
    "dob": "Date of Birth",
    "vital_status": "Vital Status",
    "oncotree_primary_diagnosis": "Diagnosis (OncoTree)",
}

PATIENT_DETAIL_FIELDS = {
    "primary_dx": "Primary Diagnosis",
    "metastasis_sites": "Metastasis Sites",
    "mrn": "MRN",
    "sex": "Gender",
    "entity": "Institution",
}


def _row(label: str, value: object, bold: bool = False) -> dict:
    return {"label": label, "value": value, "bold": bold}


def _extract(raw: dict, field_map: dict) -> list[dict]:
    rows = []
    for key, label in field_map.items():
        value = raw.get(key)
        if value in (None, ""):
            continue
        rows.append(_row(label, value))
    return rows


# ---------------------------------------------------------------------------
# Primary-match selection helpers
# ---------------------------------------------------------------------------

def _select_primary_match(trial_matches: list[dict]) -> dict | None:
    if not trial_matches:
        return None

    def _priority(m: dict) -> tuple:
        level = 0 if m.get("match_level") == "arm" else 1
        reason = 0 if m.get("reason_type") == "genomic" else 1
        sort = m.get("sort_order") or [99] * 6
        return (level, reason, sort)

    return min(trial_matches, key=_priority)


def _build_other_matches(trial_matches: list[dict], primary: dict | None) -> list[dict]:
    primary_protocol = primary.get("protocol_no") if primary else None
    seen: set[str] = set()
    others = []
    for m in trial_matches:
        protocol = m.get("protocol_no")
        if not protocol or protocol == primary_protocol or protocol in seen:
            continue
        seen.add(protocol)
        others.append({
            "protocol_no": protocol,
            "nct_id": m.get("nct_id"),
            "match_level": m.get("match_level"),
            "match_type": m.get("match_type"),
            "genomic_alteration": m.get("genomic_alteration", ""),
            "source": "matchminer",
        })
    return others


_GENOMIC_MATCH_FIELDS = {
    "true_hugo_symbol": "Gene",
    "true_protein_change": "Protein Change",
    "true_cdna_change": "cDNA Change",
    "variant_category": "Variant Category",
    "genomic_alteration": "Alteration",
}


def _build_primary_match_context(match: dict, trial: dict | None = None) -> dict:
    summary = (trial or {}).get("_summary") or {}
    trial_rows = [
        _row("Trial Name", summary.get("long_title") or summary.get("short_title")),
        _row("NCT ID", match.get("nct_id")),
        _row("Protocol No.", match.get("protocol_no")),
        _row("Phase", summary.get("phase")),
        _row("Investigator", (summary.get("investigator") or {}).get("full_name")),
        _row("Match Level", match.get("match_level")),
        _row("Trial Status", (match.get("trial_summary_status") or "").capitalize()),
        _row("Match Engine", "MatchMiner-v2"),
    ]
    match_detail_rows = [
        _row("Cancer Type Match", match.get("cancer_type_match")),
        _row("Reason Type", match.get("reason_type")),
        _row("Match Type", match.get("match_type")),
    ]
    if match.get("code"):
        match_detail_rows.append(_row("Arm", match["code"]))

    return {
        "nct_id": match.get("nct_id"),
        "trial_status": (match.get("trial_summary_status") or "").capitalize(),
        "trial": [r for r in trial_rows if r["value"] not in (None, "")],
        "match_detail": [r for r in match_detail_rows if r["value"] not in (None, "")],
        "genomic": _extract(match, _GENOMIC_MATCH_FIELDS),
    }


# ---------------------------------------------------------------------------
# Public loader: flat trial_match list (test-matches-v0.0.1.json shape)
# ---------------------------------------------------------------------------

def load_context_from_flat_matches(
    matches: list[dict], sample_id: str, trials_by_protocol: dict[str, dict] | None = None
) -> dict:
    """Build report context from a flat list of trial_match docs for one patient.

    Takes an already-loaded list spanning multiple patients and filters by
    sample_id first — the shape test-matches-v0.0.1.json (and the real
    MatchMiner trial_match Mongo collection) is in.
    """
    trials_by_protocol = trials_by_protocol or {}
    visible = [
        m for m in matches
        if m.get("sample_id") == sample_id and m.get("show_in_ui")
    ]
    primary = _select_primary_match(visible)
    primary_trial = trials_by_protocol.get(primary.get("protocol_no")) if primary else None

    return {
        "primary_match": _build_primary_match_context(primary, primary_trial) if primary else None,
        "other_matches": _build_other_matches(visible, primary),
        "sample_id": sample_id,
    }


# ---------------------------------------------------------------------------
# Public loader: normalized JSON (output of ctm-mm raw-to-mm)
# ---------------------------------------------------------------------------

def load_context_from_normalized_json(pt_path: str, sample_id: str | None = None) -> dict:
    _empty = {"patient_header": [], "patient_detail": [], "reports": []}
    try:
        data = json.loads(Path(pt_path).read_text())
    except (FileNotFoundError, OSError):
        return _empty

    patients = data.get("extras", {}).get("patients", {})
    if not patients:
        return _empty

    entry = patients.get(sample_id) if sample_id is not None else next(iter(patients.values()))
    if entry is None:
        return _empty

    patient = dict(entry.get("patient", {}))
    metastasis = patient.get("metastasis_sites")
    if isinstance(metastasis, list):
        patient["metastasis_sites"] = ", ".join(metastasis or [])

    return {
        "patient_header": _extract(patient, PATIENT_HEADER_FIELDS),
        "patient_detail": _extract(patient, PATIENT_DETAIL_FIELDS),
        "reports": entry.get("reports", []),
    }


# ---------------------------------------------------------------------------
# Public orchestrators
# ---------------------------------------------------------------------------

def _render_report(ctx: dict, sample_id: str) -> str:
    ctx = {**ctx}
    ctx["methods"] = json.loads(METHODS_PATH.read_text())["body"]
    ctx["provenance"] = {
        "generated_on": datetime.now().strftime("%d%b%Y"),
        "data_source": DATA_SOURCE_VERSION,
        "sample_id": sample_id,
        "record_hash": "",
    }
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("report.html")
    css = (STATIC_DIR / "report.css").read_text()
    return template.render(css=css, **ctx)


def render_html_from_pt_trials_matches(pts_path: str, trials_path: str, matches_path: str, sample_id: str) -> str:
    """Build a report for one patient from a patient collection, a trial
    collection, and a flat trial_match collection — the one report-building
    path. Trims trials to only those referenced by the patient's own matches.
    """
    matches = json.loads(Path(matches_path).read_text())
    trials = json.loads(Path(trials_path).read_text())

    patient_matches = [m for m in matches if m.get("sample_id") == sample_id]
    referenced_protocols = {m.get("protocol_no") for m in patient_matches}
    trials_by_protocol = {
        t.get("protocol_no"): t for t in trials if t.get("protocol_no") in referenced_protocols
    }

    pt_ctx = load_context_from_normalized_json(pts_path, sample_id=sample_id)
    mm_ctx = load_context_from_flat_matches(patient_matches, sample_id, trials_by_protocol)
    return _render_report({**pt_ctx, **mm_ctx}, sample_id)
