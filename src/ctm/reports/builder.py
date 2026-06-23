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

PATIENT_HEADER_FIELDS = {
    "mrn": "MRN",
    "first_name": "First Name",
    "last_name": "Last Name",
    "sex": "Gender",
    "dob": "Date of Birth",
    "vital_status": "Vital Status",
    "entity": "Institution",
    "oncotree_primary_diagnosis": "Diagnosis (OncoTree)",
}

PATIENT_DETAIL_FIELDS = {
    "primary_dx": "Primary Diagnosis",
    "metastasis_sites": "Metastasis Sites",
}

GENOMIC_FIELDS = {
    "true_hugo_symbol": "Gene",
    "true_protein_change": "Protein Change",
    "true_cdna_change": "cDNA Change",
    "true_variant_classification": "Variant Classification",
    "variant_category": "Variant Category",
    "allele_fraction": "Allele Fraction",
    "tier": "Tier",
    "chromosome": "Chromosome",
    "position": "Position",
    "reference_allele": "Reference Allele",
}


def _row(label: str, value: object, bold: bool = False) -> dict:
    return {"label": label, "value": value, "bold": bold}


def _load_json(data_dir: Path, filename: str):
    with open(data_dir / filename) as f:
        return json.load(f)


def _extract(raw: dict, field_map: dict) -> list[dict]:
    rows = []
    for key, label in field_map.items():
        value = raw.get(key)
        if value in (None, ""):
            continue
        rows.append(_row(label, value))
    return rows


def load_context(use_real: bool = False) -> dict:
    data_dir = DATA_DIR / ("real" if use_real else "mock")

    patient = _load_json(data_dir, "patient.json")
    patient_header = _extract(patient, PATIENT_HEADER_FIELDS)
    patient_detail = _extract(patient.get("clinical_detail", {}), PATIENT_DETAIL_FIELDS)

    matches = _load_json(data_dir, "matches.json")
    raw_match = matches["primary"]

    trial_rows = [
        _row("Trial Name", "EGFR-TKI Resistance Combination Study"),  #mock
        _row("NCT ID", raw_match.get("nct_id")),
        _row("Protocol No.", raw_match.get("protocol_no")),
        _row("Trial Therapy", "Osimertinib + MET inhibitor add-on"),  #mock
        _row("Trial Source", "Regional"),  #mock
        _row("Match Level", raw_match.get("match_level")),
        _row("Match Engine", "MatchMiner-v2"),  #mock
    ]

    match_detail_rows = [
        _row("Cancer Type Match", raw_match.get("cancer_type_match")),
        _row("Reason Type", raw_match.get("reason_type")),
        _row("Match Type", raw_match.get("match_type")),
        _row("Age Eligibility", "18-75 years"),  #mock
        _row("ECOG Status", "0-2"),  #mock
        _row("Location", "Ann Arbor, MI"),  #mock
    ]

    primary_match = {
        "nct_id": raw_match.get("nct_id"),
        "trial_status": raw_match.get("trial_summary_status", "").capitalize(),
        "trial": trial_rows,
        "match_detail": match_detail_rows,
        "genomic": _extract(raw_match, GENOMIC_FIELDS),
    }

    others = matches["others"]
    other_matches = [
        {
            "protocol_no": m.get("trial_id"),
            "nct_id": m.get("trial_id"),
            "match_level": None,
            "match_type": None,
            "genomic_alteration": "",
            "source": m.get("source", ""),
        }
        for m in others
    ]

    methods = _load_json(data_dir, "methods.json")["body"]  # list of paragraphs

    return {
        "primary_match": primary_match,
        "patient_header": patient_header,
        "patient_detail": patient_detail,
        "other_matches": other_matches,
        "methods": methods,
        "provenance": {
            "generated_on": datetime.now().strftime("%d%b%Y"),
            "data_source": DATA_SOURCE_VERSION,
            "sample_id": matches.get("pt_uuid"),
            "record_hash": (raw_match.get("hash") or "")[:8],
        },
    }


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


def _build_primary_match_context(match: dict) -> dict:
    trial_rows = [
        _row("NCT ID", match.get("nct_id")),
        _row("Protocol No.", match.get("protocol_no")),
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
# Public loader: matchminer export
# ---------------------------------------------------------------------------

def load_context_from_mm_matches(mm_export_path: str) -> dict:
    with open(mm_export_path) as f:
        data = json.load(f)

    visible = [m for m in data.get("trial_match", []) if m.get("show_in_ui")]
    primary = _select_primary_match(visible)

    return {
        "primary_match": _build_primary_match_context(primary) if primary else None,
        "other_matches": _build_other_matches(visible, primary),
    }


# ---------------------------------------------------------------------------
# Public loader: Excel workbook
# ---------------------------------------------------------------------------

def _build_reports_context(metadata: list, findings: list) -> list[dict]:
    findings_by_report: dict[int, list] = {}
    for f in findings:
        findings_by_report.setdefault(f.report_uuid, []).append({
            "gene": f.gene,
            "protein": f.protein,
            "variant_type": f.variant_type,
            "result_summary": f.result_summary,
            "raw": f.raw,
        })
    return [
        {
            "source": m.source,
            "test_name": m.test_name,
            "accession_no": m.accession_no,
            "physician": m.physician,
            "date_completed": m.date_completed.isoformat() if m.date_completed else None,
            "findings": findings_by_report.get(m.report_uuid, []),
        }
        for m in metadata
    ]


def load_context_from_raw_excel(excel_path: str) -> dict:
    _empty = {"patient_header": [], "patient_detail": [], "reports": []}
    try:
        from ctm.transformers.excel_reader import read_and_normalize
        patients, metadata, findings = read_and_normalize(Path(excel_path))
    except (FileNotFoundError, OSError):
        return _empty

    if not patients:
        return _empty

    patient_dict = patients[0].model_dump()

    return {
        "patient_header": _extract(patient_dict, PATIENT_HEADER_FIELDS),
        "patient_detail": _extract(patient_dict, PATIENT_DETAIL_FIELDS),
        "reports": _build_reports_context(metadata, findings),
    }


# ---------------------------------------------------------------------------
# Public orchestrator: merge both sources and render
# ---------------------------------------------------------------------------

def render_html_from_sources(excel_path: str, mm_export_path: str) -> str:
    excel_ctx = load_context_from_raw_excel(excel_path)
    mm_ctx = load_context_from_mm_matches(mm_export_path)
    ctx = {**excel_ctx, **mm_ctx}
    ctx["methods"] = []
    ctx["provenance"] = {
        "generated_on": datetime.now().strftime("%d%b%Y"),
        "data_source": DATA_SOURCE_VERSION,
        "sample_id": (mm_ctx.get("primary_match") or {}).get("nct_id", ""),
        "record_hash": "",
    }
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("report.html")
    css = (STATIC_DIR / "report.css").read_text()
    return template.render(css=css, **ctx)


def render_html(use_real: bool = False, context_override: dict | None = None) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("report.html")
    css = (STATIC_DIR / "report.css").read_text()
    ctx = context_override if context_override is not None else load_context(use_real=use_real)
    return template.render(css=css, **ctx)
