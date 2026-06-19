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

PRIMARY_MATCH_FIELDS = {
    "patient": {
        "mrn": "MRN",
        "gender": "Gender",
        "vital_status": "Vital Status",
        "oncotree_primary_diagnosis_name": "Diagnosis",
        "tumor_mutational_burden_per_megabase": "Tumor Mutational Burden (mut/Mb)",
    },
    "genomic": {
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
    },
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
    raw_match = _load_json(data_dir, "sample_match.json")

    trial_rows = [
        _row("Trial Name", "EGFR-TKI Resistance Combination Study"),  #mock
        _row("NCT ID", raw_match.get("nct_id")),
        _row("Protocol No.", raw_match.get("protocol_no")),
        _row("Trial Therapy", "Osimertinib + MET inhibitor add-on"),  #mock
        _row("Trial Source", "Regional"),  #mock
        _row("Match Level", raw_match.get("match_level")),
        _row("Reason Type", raw_match.get("reason_type")),
        _row("Cancer Type Match", raw_match.get("cancer_type_match")),
        _row("Match Type", raw_match.get("match_type")),
        _row("Match Engine", "MatchMiner-v2"),  #mock
        _row("Match Score *", "99%", bold=True),  #mock
    ]

    primary_match = {
        "nct_id": raw_match.get("nct_id"),
        "trial_status": raw_match.get("trial_summary_status", "").capitalize(),
        "patient": _extract(raw_match, PRIMARY_MATCH_FIELDS["patient"]),
        "trial": trial_rows,
        "genomic": _extract(raw_match, PRIMARY_MATCH_FIELDS["genomic"]),
    }

    all_matches = _load_json(data_dir, "other_matches.json")
    regional_matches = [m for m in all_matches if m.get("source") == "regional"]
    ctg_matches = [m for m in all_matches if m.get("source") == "clinicaltrials_gov"]

    methods = _load_json(data_dir, "methods.json")["body"]
    general = _load_json(data_dir, "general.json")

    return {
        "primary_match": primary_match,
        "regional_matches": regional_matches,
        "ctg_matches": ctg_matches,
        "patient_detail": _load_json(data_dir, "patient_detail.json"),
        "methods": methods,
        "disclaimer": general["disclaimer"],
        "provenance": {
            "generated_on": datetime.now().strftime("%d%b%Y"),
            "data_source": DATA_SOURCE_VERSION,
            "sample_id": raw_match.get("sample_id"),
            "record_hash": (raw_match.get("hash") or "")[:8],
        },
    }


def render_html(use_real: bool = False, context_override: dict | None = None) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("report.html")
    css = (STATIC_DIR / "report.css").read_text()
    ctx = context_override if context_override is not None else load_context(use_real=use_real)
    return template.render(css=css, **ctx)
