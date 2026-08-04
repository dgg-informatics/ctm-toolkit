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

# TODO: hardcoded until referring-physician / genetic-report-physician are
# real per-patient fields in the intake data — replace once that data exists.
_REFERRING_PHYSICIAN_PLACEHOLDER = "Dr. Paul Swiecicki"
_GENETIC_REPORT_PHYSICIAN_PLACEHOLDER = "Dr. Kelly Malloy"


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

def _match_reason(m: dict) -> str:
    """Human-meaningful match reason for display, instead of the raw match_type
    (`generic_clinical`, `gene`, ...). Genomic → the gene/alteration; TMB → TMB;
    otherwise the patient's OncoTree diagnosis (the driver of a clinical match)."""
    if m.get("reason_type") == "genomic":
        return m.get("genomic_alteration") or m.get("true_hugo_symbol") or "Genomic"
    if m.get("match_type") == "tmb":
        return "TMB"
    return m.get("oncotree_primary_diagnosis_name") or "Clinical criteria"


def _match_priority(m: dict) -> tuple:
    """Ranking key — lower is better. Prefers arm over step, then a genomic
    reason over clinical, then matchengine's own sort_order. Used both to pick
    the single primary match and to pick the best (most meaningful) match to
    represent a trial when it matched for several reasons (e.g. age AND a gene)."""
    level = 0 if m.get("match_level") == "arm" else 1
    reason = 0 if m.get("reason_type") == "genomic" else 1
    sort = m.get("sort_order") or [99] * 6
    return (level, reason, sort)


def _select_primary_match(trial_matches: list[dict]) -> dict | None:
    if not trial_matches:
        return None
    return min(trial_matches, key=_match_priority)


def _build_other_matches(
    trial_matches: list[dict], primary: dict | None, trials_by_protocol: dict[str, dict] | None = None
) -> list[dict]:
    trials_by_protocol = trials_by_protocol or {}
    primary_protocol = primary.get("protocol_no") if primary else None

    # keep the single best (most meaningful) match doc per trial — a trial that
    # matched on both age and a gene should surface the gene, not the age.
    best_by_protocol: dict[str, dict] = {}
    for m in trial_matches:
        protocol = m.get("protocol_no")
        if not protocol or protocol == primary_protocol:
            continue
        cur = best_by_protocol.get(protocol)
        if cur is None or _match_priority(m) < _match_priority(cur):
            best_by_protocol[protocol] = m

    others = []
    for protocol, m in best_by_protocol.items():
        summary = (trials_by_protocol.get(protocol) or {}).get("_summary") or {}
        others.append({
            "protocol_no": protocol,
            "nct_id": m.get("nct_id"),
            "trial_name": summary.get("long_title") or summary.get("short_title"),
            "match_level": m.get("match_level"),
            "match_reason": _match_reason(m),
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


def _build_primary_match_context(
    match: dict, trial: dict | None = None, known_biomarker_count: int | None = None
) -> dict:
    summary = (trial or {}).get("_summary") or {}
    raw = (trial or {}).get("_raw") or {}
    trial_rows = [
        _row("Trial Name", summary.get("long_title") or summary.get("short_title")),
        _row("NCT ID", match.get("nct_id")),
        _row("Protocol No.", match.get("protocol_no")),
        _row("Phase", summary.get("phase")),
        _row("Investigator", (summary.get("investigator") or {}).get("full_name")),
        _row("Disease Site", raw.get("disease_site")),
        _row("Trial Status", (match.get("trial_summary_status") or "").capitalize()),
    ]
    match_detail_rows = [
        _row("Cancer Type Match", match.get("oncotree_primary_diagnosis_name")),
        _row("Reason Type", match.get("reason_type")),
        _row("Match Reason", _match_reason(match)),
        _row("Match Level", match.get("match_level")),
        _row("Match Engine", "MatchMiner-v2"),
    ]
    if match.get("code"):
        match_detail_rows.append(_row("Arm", match["code"]))

    genomic_rows = _extract(match, _GENOMIC_MATCH_FIELDS)
    if known_biomarker_count is not None:
        genomic_rows.append(_row("Known Biomarkers", f"{known_biomarker_count} on file"))

    return {
        "nct_id": match.get("nct_id"),
        "trial_status": (match.get("trial_summary_status") or "").capitalize(),
        "trial": [r for r in trial_rows if r["value"] not in (None, "")],
        "match_detail": [r for r in match_detail_rows if r["value"] not in (None, "")],
        "genomic": genomic_rows,
    }


# ---------------------------------------------------------------------------
# Public loader: flat trial_match list (test-matches-v0.0.1.json shape)
# ---------------------------------------------------------------------------

def _build_genetic_profile(genomic_docs: list[dict]) -> list[dict]:
    """Patient's full set of positive genomic findings (WILDTYPE: false), for
    a scannable profile table — independent of which single finding drove
    any particular trial match (that's what Genomic Match Detail is for)."""
    profile = []
    for doc in genomic_docs:
        if doc.get("WILDTYPE") is not False:
            continue
        profile.append({
            "gene": doc.get("TRUE_HUGO_SYMBOL"),
            "variant_category": doc.get("VARIANT_CATEGORY"),
            "protein_change": doc.get("TRUE_PROTEIN_CHANGE"),
            "cdna_change": doc.get("TRUE_CDNA_CHANGE"),
            "cnv_call": doc.get("CNV_CALL"),
        })
    return sorted(profile, key=lambda row: row["gene"] or "")


def load_context_from_flat_matches(
    matches: list[dict],
    sample_id: str,
    trials_by_protocol: dict[str, dict] | None = None,
    known_biomarker_count: int | None = None,
) -> dict:
    """Build report context from a flat list of trial_match docs for one patient.

    Takes an already-loaded list spanning multiple patients and filters by
    sample_id first — the shape test-matches-v0.0.1.json (and the real
    MatchMiner trial_match Mongo collection) is in.

    known_biomarker_count: total genomic docs on file for this patient
    (tested, regardless of WILDTYPE) — shown in Genomic Match Detail so that
    column isn't left blank for a clinical-reason match.
    """
    trials_by_protocol = trials_by_protocol or {}
    visible = [
        m for m in matches
        if m.get("sample_id") == sample_id and m.get("show_in_ui")
    ]
    primary = _select_primary_match(visible)
    primary_trial = trials_by_protocol.get(primary.get("protocol_no")) if primary else None

    return {
        "primary_match": (
            _build_primary_match_context(primary, primary_trial, known_biomarker_count)
            if primary else None
        ),
        "other_matches": _build_other_matches(visible, primary, trials_by_protocol),
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

    patient_detail = _extract(patient, PATIENT_DETAIL_FIELDS)
    patient_detail.append(_row("Referring Physician", _REFERRING_PHYSICIAN_PLACEHOLDER))
    patient_detail.append(_row("Genetic Report Physician", _GENETIC_REPORT_PHYSICIAN_PLACEHOLDER))

    return {
        "patient_header": _extract(patient, PATIENT_HEADER_FIELDS),
        "patient_detail": patient_detail,
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


def _trial_is_meaningful(trial: dict) -> bool:
    """A trial is 'meaningful' if its step[0].match has an oncotree diagnosis
    or a genomic clause — i.e. not an age/gender/ecog-only or empty match that
    matches nearly everyone."""
    from ..transformers.confidence_split import has_genomic, has_oncotree_diagnosis
    steps = trial.get("treatment_list", {}).get("step", [])
    match = steps[0].get("match", []) if steps else []
    return has_oncotree_diagnosis(match) or has_genomic(match)


def render_html_from_pt_trials_matches(
    pts_path: str, trials_path: str, matches_path: str, sample_id: str,
    meaningful_only: bool = False,
) -> str:
    """Build a report for one patient from a patient collection, a trial
    collection, and a trial_match collection — the one report-building
    path. Trims trials to only those referenced by the patient's own matches.

    --matches accepts either shape of the trial_match collection: a flat
    list (e.g. a raw Mongo trial_match dump spanning multiple patients), or
    the single-patient {"clinical": ..., "genomic": ..., "trial_match": [...]}
    envelope that matchengine-V2's export_matches.py actually produces.
    Both represent the same underlying documents, just packaged differently.

    When the envelope shape is present, its "genomic" array (the patient's
    full genomic collection, not just the fields behind one match) also
    populates the Patient Genetic Profile section.

    meaningful_only: keep only matches whose trial has an oncotree diagnosis or
    genomic criterion, dropping age/gender-only ("matches everyone") trials.
    """
    matches_data = json.loads(Path(matches_path).read_text())
    matches = matches_data["trial_match"] if isinstance(matches_data, dict) else matches_data
    genomic_docs = matches_data.get("genomic", []) if isinstance(matches_data, dict) else []
    trials = json.loads(Path(trials_path).read_text())

    patient_matches = [m for m in matches if m.get("sample_id") == sample_id]

    if meaningful_only:
        meaningful_keys = set()
        for t in trials:
            if _trial_is_meaningful(t):
                if t.get("protocol_no") is not None:
                    meaningful_keys.add(t["protocol_no"])
                if t.get("nct_id") is not None:
                    meaningful_keys.add(t["nct_id"])
        patient_matches = [
            m for m in patient_matches
            if m.get("protocol_no") in meaningful_keys or m.get("nct_id") in meaningful_keys
        ]

    referenced_protocols = {m.get("protocol_no") for m in patient_matches}
    trials_by_protocol = {
        t.get("protocol_no"): t for t in trials if t.get("protocol_no") in referenced_protocols
    }

    pt_ctx = load_context_from_normalized_json(pts_path, sample_id=sample_id)
    mm_ctx = load_context_from_flat_matches(
        patient_matches, sample_id, trials_by_protocol, known_biomarker_count=len(genomic_docs)
    )
    genetic_profile = _build_genetic_profile(genomic_docs)
    return _render_report({**pt_ctx, **mm_ctx, "genetic_profile": genetic_profile}, sample_id)
