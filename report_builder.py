"""Loads trial-match report data and renders it through the Jinja2 template."""
import json
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Hardcoded for this POC — the real pipeline would stamp this at export time.
DATA_SOURCE_VERSION = "TrialDBv0.1-jun26"

# Maps raw JSON field -> human-readable label. Internal bookkeeping fields
# (Mongo _id, hashes, query_hash, sort_order, _me_id, combo_coord, etc.)
# are intentionally excluded from the report.
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


def _load_json(filename: str):
    with open(DATA_DIR / filename) as f:
        return json.load(f)


def _extract(raw: dict, field_map: dict) -> list[dict]:
    """Pull label/value rows out of raw using field_map, skipping blanks."""
    rows = []
    for key, label in field_map.items():
        value = raw.get(key)
        if value in (None, ""):
            continue
        rows.append(_row(label, value))
    return rows


def load_context() -> dict:
    raw_match = _load_json("sample_match.json")

    # Trial Name, Trial Therapy, Match Engine, and Match Certainty aren't in
    # the real JSON yet — hardcoded here as a placeholder for fields the
    # matching pipeline is expected to add later.
    trial_rows = [
        _row("Trial Name", "EGFR-TKI Resistance Combination Study"),
        _row("NCT ID", raw_match.get("nct_id")),
        _row("Protocol No.", raw_match.get("protocol_no")),
        _row("Trial Therapy", "Osimertinib + MET inhibitor add-on"),
        _row("Match Level", raw_match.get("match_level")),
        _row("Reason Type", raw_match.get("reason_type")),
        _row("Cancer Type Match", raw_match.get("cancer_type_match")),
        _row("Match Type", raw_match.get("match_type")),
        _row("Match Engine", "MatchMiner-v2"),
        _row("Match Certainty", "99%", bold=True),
    ]

    primary_match = {
        "nct_id": raw_match.get("nct_id"),
        "trial_status": raw_match.get("trial_summary_status", "").capitalize(),
        "patient": _extract(raw_match, PRIMARY_MATCH_FIELDS["patient"]),
        "trial": trial_rows,
        "genomic": _extract(raw_match, PRIMARY_MATCH_FIELDS["genomic"]),
    }

    return {
        "primary_match": primary_match,
        "other_matches": _load_json("mock_other_matches.json"),
        "similar_patients": _load_json("mock_similar_patients.json"),
        "patient_detail": _load_json("mock_patient_detail.json"),
        "provenance": {
            "generated_on": datetime.now().strftime("%d%b%Y"),
            "data_source": DATA_SOURCE_VERSION,
            "sample_id": raw_match.get("sample_id"),
            "record_hash": (raw_match.get("hash") or "")[:8],
        },
    }


def render_html() -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("report.html")
    css = (STATIC_DIR / "report.css").read_text()
    return template.render(css=css, **load_context())
