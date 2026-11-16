"""ctm-trials-report: the Monday summary of what the weekly run changed.

Read-only. Everything comes from collections the pipeline already writes: the
run's 02 diff, 04 autofill reports and 07 snapshot, plus the previous dated
run's 06 snapshot (new vs changed, and which manual edits were replaced).
"""

import pytest
from mongo_doubles import FakeCollection, FakeDatabase

from ctm import trials_report_cli
from ctm.trials_report import build_report, render

# ── fixtures ─────────────────────────────────────────────────────────────────

GENOMIC = {"genomic": {"hugo_symbol": "BRAF", "variant_category": "Mutation"}}
AGE = {"clinical": {"age_numerical": ">=18"}}
DX = {"clinical": {"oncotree_primary_diagnosis": "Glioma"}}


def _t(key, entity="amc", **extra):
    field = "protocol_no" if entity == "amc" else "nct_id"
    return {"entity": entity, field: key, "trial_key": key,
            "long_title": f"Title of {key}", **extra}


def _auto(key, match, filled=True, reason="filled"):
    return _t(key, treatment_list={"step": [{"match": match}]},
              _llm_curation={"autofill": {"filled": filled, "reason": reason}})


def _run(**over):
    data = {
        "db": "2026-11-23",
        "collections": ["00_raw_trials", "01_normalized_trials", "02_diff_trials",
                        "03c_clinical_trials", "03g_genomic_trials", "04_autofill_trials",
                        "06_master_trials", "07_filtered_trials"],
        "diff": [_t("NEW1", diff_status="changed"), _t("CHG1", diff_status="changed"),
                 _t("EDIT1", diff_status="changed"),
                 _t("SAME", diff_status="unchanged"), _t("GONE", diff_status="deleted")],
        "autofill": [_auto("NEW1", [{"and": [GENOMIC, AGE, DX]}]),
                     _auto("CHG1", [{"and": [AGE, DX]}]),
                     _auto("EDIT1", [], filled=False, reason="no suggestions")],
        "filtered": [_t("NEW1", match_level=3), _t("CHG1", match_level=2),
                     _t("EDIT1", match_level=0), _t("SAME", match_level=2)],
        "previous_db": "2026-11-16",
        "previous_master": [_t("CHG1", curated_by="auto"),
                            _t("EDIT1", curated_by="manual-edit"),
                            _t("SAME", curated_by="auto"), _t("GONE", curated_by="auto")],
        "previous_filtered": 4,
    }
    return {**data, **over}


# ── build_report ─────────────────────────────────────────────────────────────

def test_status_ok_when_07_was_written():
    assert build_report(_run())["status"] == {"ok": True, "last_stage": "07_filtered_trials"}


def test_status_names_the_last_stage_written_when_the_run_stopped():
    run = _run(collections=["00_raw_trials", "01_normalized_trials", "02_diff_trials",
                            "03c_clinical_trials"])
    assert build_report(run)["status"] == {"ok": False, "last_stage": "03c_clinical_trials"}


def test_a_missing_run_database_is_a_failed_run():
    report = build_report(_run(collections=None, diff=[], autofill=[], filtered=[]))
    assert report["status"] == {"ok": False, "last_stage": None}


def test_totals_and_levels():
    totals = build_report(_run())["totals"]
    assert totals == {"trials": 4, "previous": 4, "levels": {3: 1, 2: 2, 1: 0, 0: 1}}


def test_week_counts_split_new_from_changed():
    week = build_report(_run())["week"]
    assert week == {"new": 1, "changed": 2, "deleted": 1, "unchanged": 1,
                    "filled": 2, "annotated": 3}


def test_rows_for_each_new_or_changed_trial():
    rows = {r["key"]: r for r in build_report(_run())["rows"]}
    assert rows["NEW1"] == {"key": "NEW1", "kind": "NEW", "entity": "amc",
                            "title": "Title of NEW1", "level": 3, "filled": True,
                            "reason": "filled", "leaves": 3, "genomic": 1}
    assert rows["CHG1"]["kind"] == "CHG" and rows["CHG1"]["genomic"] == 0
    assert rows["EDIT1"]["filled"] is False and rows["EDIT1"]["reason"] == "no suggestions"
    assert build_report(_run())["rows"][0]["key"] == "NEW1"   # new first


def test_a_changed_trial_filtered_out_of_07_has_no_level():
    """A West copy of an AMC trial is annotated but deduplicated away."""
    run = _run(filtered=[t for t in _run()["filtered"] if t["trial_key"] != "CHG1"])
    rows = {r["key"]: r for r in build_report(run)["rows"]}
    assert rows["CHG1"]["level"] is None


def test_deleted_keys():
    assert build_report(_run())["deleted"] == ["GONE"]


def test_needs_a_look():
    look = build_report(_run())["look"]
    assert look["manual_edit_replaced"] == ["EDIT1"]
    assert look["level_0"] == ["EDIT1"]
    assert look["not_filled"] == [("EDIT1", "no suggestions")]


def test_without_a_previous_run_nothing_is_called_new():
    report = build_report(_run(previous_db=None, previous_master=[], previous_filtered=None))
    assert report["week"]["new"] == 0 and report["week"]["changed"] == 3
    assert report["totals"]["previous"] is None
    assert report["look"]["manual_edit_replaced"] == []


def test_quiet_week():
    run = _run(collections=["00_raw_trials", "01_normalized_trials", "02_diff_trials",
                            "06_master_trials", "07_filtered_trials"],
               diff=[_t("SAME", diff_status="unchanged")], autofill=[])
    report = build_report(run)
    assert report["status"]["ok"] is True
    assert report["rows"] == [] and report["week"]["annotated"] == 0


# ── render ───────────────────────────────────────────────────────────────────

def test_render_has_every_section():
    text = render(build_report(_run()))
    assert text.splitlines()[0].startswith("CTM weekly trials — 2026-11-23")
    assert "pipeline: OK" in text
    assert "4 trials (+0 vs 2026-11-16)" in text
    assert "new 1 · changed 2 · deleted 1 · unchanged 1" in text
    assert "match clauses built: 2 of 3" in text
    assert "NEW  NEW1" in text and "CHG  CHG1" in text
    assert "GONE" in text
    assert "manual edit replaced: EDIT1" in text
    assert "level 0 after AI: EDIT1" in text


def test_render_failed_run_says_where_it_stopped():
    run = _run(collections=["00_raw_trials", "01_normalized_trials", "02_diff_trials"])
    assert "pipeline: INCOMPLETE — last stage written: 02_diff_trials" in render(build_report(run))


def test_render_missing_database():
    text = render(build_report(_run(collections=None, diff=[], autofill=[], filtered=[])))
    assert "pipeline: DID NOT RUN — no database 2026-11-23" in text


def test_render_quiet_week_says_so():
    run = _run(diff=[_t("SAME", diff_status="unchanged")], autofill=[],
               collections=["01_normalized_trials", "02_diff_trials",
                            "06_master_trials", "07_filtered_trials"])
    assert "No new or changed trials this week." in render(build_report(run))


def test_long_titles_are_truncated():
    run = _run(diff=[_t("NEW1", diff_status="changed", long_title="x" * 200)])
    line = next(line for line in render(build_report(run)).splitlines() if "NEW1" in line)
    assert len(line) < 140


# ── gather (Mongo) ───────────────────────────────────────────────────────────

class FakeClient:
    def __init__(self, dbs):
        self._dbs = dbs

    def list_database_names(self):
        return list(self._dbs)

    def __getitem__(self, name):
        return self._dbs.setdefault(name, FakeDatabase())


def _db(**collections):
    return FakeDatabase({name: FakeCollection(docs) for name, docs in collections.items()})


def test_gather_picks_the_latest_earlier_dated_run_with_a_master():
    """A mid-week ctm-apply-edits database is a valid previous snapshot; a
    backfill database (not date-named) and a later one are not."""
    client = FakeClient({
        "2026-11-09": _db(**{"06_master_trials": [_t("A")], "07_filtered_trials": [_t("A")]}),
        "2026-11-18": _db(**{"06_master_trials": [_t("A"), _t("B")],
                             "07_filtered_trials": [_t("A"), _t("B")]}),
        "2026-11-20": _db(**{"02_diff_trials": []}),            # no master: skipped
        "backfill-2026-11-16": _db(**{"06_master_trials": []}),
        "2026-11-30": _db(**{"06_master_trials": []}),          # later: skipped
        "2026-11-23": _db(**{"02_diff_trials": [_t("B", diff_status="changed")],
                             "07_filtered_trials": [_t("B", match_level=2)]}),
        "latest_trials": _db(**{"06_master_trials": []}),
    })
    data = trials_report_cli.gather(client, "2026-11-23")
    assert data["previous_db"] == "2026-11-18"
    assert len(data["previous_master"]) == 2 and data["previous_filtered"] == 2
    assert data["collections"] == ["02_diff_trials", "07_filtered_trials"]
    assert data["diff"][0]["trial_key"] == "B"


def test_gather_missing_run_database():
    data = trials_report_cli.gather(FakeClient({}), "2026-11-23")
    assert data["collections"] is None and data["diff"] == []


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_cli_prints_and_writes_the_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(trials_report_cli, "_client", lambda: FakeClient({}))
    out = tmp_path / "r.txt"
    trials_report_cli.main(["--db", "2026-11-23", "--out", str(out)])
    printed = capsys.readouterr().out
    assert printed.startswith("CTM weekly trials — 2026-11-23")
    assert out.read_text() == printed


def test_cli_default_file_lives_with_the_master_backups(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_TRIAL_EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(trials_report_cli, "_client", lambda: FakeClient({}))
    trials_report_cli.main(["--db", "2026-11-23"])
    assert (tmp_path / "trials_report-2026-11-23.txt").is_file()


def test_cli_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_TRIAL_EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(trials_report_cli, "_client", lambda: FakeClient({}))
    trials_report_cli.main(["--db", "2026-11-23", "--no-file"])
    assert list(tmp_path.iterdir()) == []


def test_cron_line_reports_after_the_pipeline_even_when_it_fails():
    from pathlib import Path

    cron = (Path(__file__).resolve().parent.parent / "deploy/cron.d/ctm").read_text()
    assert "ctm-pipeline --end 07_filtered_trials; ctm-trials-report" in cron


def test_entry_point():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    assert 'ctm-trials-report = "ctm.trials_report_cli:main"' in text


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch):
    monkeypatch.delenv("CTM_ENV_FILE", raising=False)


