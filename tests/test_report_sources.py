"""Mongo-backed report sources: what ctm-report reads when no files are given.

The collections are injected as plain mappings so the suite stays offline —
the production callers pass pymongo Database objects, which index the same way.
"""


class _FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query=None, projection=None):
        query = query or {}
        return iter([d for d in self.docs
                     if all(d.get(k) == v for k, v in query.items())])


class _FakeDatabase:
    """Mirrors pymongo's Database closely enough to catch real misuse.

    Indexable by collection name, and an unknown name yields an empty collection
    rather than raising — same as Mongo, where a missing collection simply has no
    documents. Crucially ``__iter__`` is None, as pymongo sets it (PYTHON-3084),
    so ``"name" in db`` raises TypeError here exactly as it does in production.
    A plain dict double hid that and let a broken membership test ship.
    """
    __iter__ = None

    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections.get(name, _FakeCollection([]))


def _match_db(clinical=(), genomic=(), trial=(), trial_match=()):
    return _FakeDatabase({"clinical": _FakeCollection(list(clinical)),
                          "genomic": _FakeCollection(list(genomic)),
                          "trial": _FakeCollection(list(trial)),
                          "trial_match": _FakeCollection(list(trial_match))})


def _patient_db(patient_data=()):
    return _FakeDatabase({"latest_patient_data": _FakeCollection(list(patient_data))})


# ---------------------------------------------------------------------------
# sample_ids
# ---------------------------------------------------------------------------

def test_sample_ids_includes_patients_with_no_matches():
    """Every patient gets a report, matched or not — 2 of the 37 have none."""
    from ctm.reports.sources import sample_ids
    db = _match_db(clinical=[{"SAMPLE_ID": "pt_2"}, {"SAMPLE_ID": "pt_1"}],
                   trial_match=[{"sample_id": "pt_1"}])
    assert sample_ids(db) == ["pt_1", "pt_2"]


def test_sample_ids_skips_docs_with_no_sample_id():
    from ctm.reports.sources import sample_ids
    db = _match_db(clinical=[{"SAMPLE_ID": "pt_1"}, {"_id": 1}])
    assert sample_ids(db) == ["pt_1"]


# ---------------------------------------------------------------------------
# load_patient
# ---------------------------------------------------------------------------

def test_load_patient_filters_matches_to_that_sample():
    from ctm.reports.sources import load_patient
    db = _match_db(trial_match=[{"sample_id": "pt_1", "nct_id": "NCT1"},
                                {"sample_id": "pt_2", "nct_id": "NCT2"}])
    got = load_patient(db, _patient_db(), "pt_1")
    assert [m["nct_id"] for m in got["matches"]] == ["NCT1"]


def test_load_patient_filters_genomic_to_that_sample():
    from ctm.reports.sources import load_patient
    db = _match_db(genomic=[{"SAMPLE_ID": "pt_1", "TRUE_HUGO_SYMBOL": "BRCA2"},
                            {"SAMPLE_ID": "pt_2", "TRUE_HUGO_SYMBOL": "EGFR"}])
    got = load_patient(db, _patient_db(), "pt_1")
    assert [g["TRUE_HUGO_SYMBOL"] for g in got["genomic_docs"]] == ["BRCA2"]


def test_load_patient_entry_carries_patient_and_reports():
    """latest_patient_data docs are {SAMPLE_ID, patient, reports} (mm_cli.py:365)."""
    from ctm.reports.sources import load_patient
    pdb = _patient_db([{"SAMPLE_ID": "pt_1", "patient": {"name": "A"},
                        "reports": [{"report_uuid": "r1"}]}])
    got = load_patient(_match_db(), pdb, "pt_1")
    assert got["patient_entry"]["patient"] == {"name": "A"}
    assert got["patient_entry"]["reports"] == [{"report_uuid": "r1"}]


def test_load_patient_entry_empty_when_patient_data_missing():
    """A patient in clinical but absent from patient_data still renders."""
    from ctm.reports.sources import load_patient
    got = load_patient(_match_db(), _patient_db(), "pt_1")
    assert got["patient_entry"] == {"patient": {}, "reports": []}


def test_load_trials_returns_every_trial_once():
    from ctm.reports.sources import load_trials
    db = _match_db(trial=[{"protocol_no": "P1"}, {"protocol_no": "P2"}])
    assert [t["protocol_no"] for t in load_trials(db)] == ["P1", "P2"]


# ---------------------------------------------------------------------------
# output naming
# ---------------------------------------------------------------------------

def test_report_filename_pattern():
    from ctm.reports.sources import report_filename
    assert report_filename("2026-09-15", "pt_0000016") == "2026-09-15_pt_0000016-report.pdf"


# ---------------------------------------------------------------------------
# render_html_from_docs — the shared render path
# ---------------------------------------------------------------------------

def test_render_html_from_docs_renders_blocks_without_touching_disk():
    from ctm.reports.builder import render_html_from_docs
    matches = [{"sample_id": "pt_1", "nct_id": "NCT1", "protocol_no": "P1",
                "match_level": "step", "reason_type": "genomic", "show_in_ui": True,
                "genomic_alteration": "BRCA2", "trial_summary_status": "open",
                "sort_order": [1, 99, 1, 99, 99, 99]}]
    html = render_html_from_docs(
        "pt_1", matches, [{"protocol_no": "P1", "_summary": {"long_title": "A Trial"}}],
        genomic_docs=[], patient_context={"patient_header": [], "patient_detail": [], "reports": []},
    )
    assert "NCT1" in html and "A Trial" in html and "BRCA2" in html


def test_render_html_from_docs_zero_match_patient():
    from ctm.reports.builder import render_html_from_docs
    html = render_html_from_docs(
        "pt_1", [], [], genomic_docs=[],
        patient_context={"patient_header": [], "patient_detail": [], "reports": []})
    assert "No trial matches were found" in html


def test_patient_context_from_entry_joins_metastasis_sites():
    from ctm.reports.builder import patient_context_from_entry
    ctx = patient_context_from_entry(
        {"patient": {"metastasis_sites": ["Liver", "Lung"]}, "reports": []})
    detail = {r["label"]: r["value"] for r in ctx["patient_detail"]}
    assert detail["Metastasis Sites"] == "Liver, Lung"


# ---------------------------------------------------------------------------
# ctm-report --all, end to end against injected databases
# ---------------------------------------------------------------------------

def _wire_fake_mongo(monkeypatch, match_db, patient_db, match_name, patient_name):
    from ctm import db as ctm_db
    monkeypatch.setattr(ctm_db, "mongo_config",
                        lambda **kw: {"patient_dbname": patient_name, "uri": "mongodb://x"})
    monkeypatch.setattr(ctm_db, "get_client",
                        lambda config: {match_name: match_db, patient_name: patient_db})


def test_report_all_writes_one_pdf_per_patient_including_zero_match(monkeypatch, tmp_path):
    """Every patient in clinical gets a file — the 2-of-37 with no matches included."""
    import argparse

    from ctm.report_cli import _run_from_mongo

    match_db = _match_db(
        clinical=[{"SAMPLE_ID": "pt_1"}, {"SAMPLE_ID": "pt_2"}],
        genomic=[{"SAMPLE_ID": "pt_1", "TRUE_HUGO_SYMBOL": "BRCA2", "WILDTYPE": False}],
        trial=[{"protocol_no": "P1", "_summary": {"long_title": "A Trial"}}],
        trial_match=[{"sample_id": "pt_1", "nct_id": "NCT1", "protocol_no": "P1",
                      "match_level": "step", "reason_type": "genomic", "show_in_ui": True,
                      "genomic_alteration": "BRCA2", "trial_summary_status": "open",
                      "sort_order": [1, 99, 1, 99, 99, 99]}],
    )
    patient_db = _patient_db([{"SAMPLE_ID": "pt_1", "patient": {}, "reports": []}])
    _wire_fake_mongo(monkeypatch, match_db, patient_db, "2026-09-15_match", "patients_dev")

    args = argparse.Namespace(
        all=True, sample_id=None, match_db="2026-09-15_match", patient_db=None,
        run_date="2026-09-15", out_dir=str(tmp_path), out=None)
    _run_from_mongo(args, parser=None)

    written = sorted(p.name for p in tmp_path.glob("*.pdf"))
    assert written == ["2026-09-15_pt_1-report.pdf", "2026-09-15_pt_2-report.pdf"]
    assert all(p.stat().st_size > 0 for p in tmp_path.glob("*.pdf"))
