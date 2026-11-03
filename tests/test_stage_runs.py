"""_llm_curation.stage_runs: when each stage last ran, and which version wrote it."""
import json
from datetime import datetime

from llm_fakes import QueuedClient

from ctm.transformers.autofill_ctml import autofill_trial
from ctm.transformers.clinical_ctml import draft_clinical
from ctm.transformers.genomic_ctml import draft_genomic

CLIN_RUN = {"ran_at": "2026-11-02T14:05:11Z", "processed_with": "ctm-llm clinical 2.4.0"}
GEN_RUN = {"ran_at": "2026-11-03T09:12:40Z", "processed_with": "ctm-llm genomic 2.4.0"}
AUTO_RUN = {"ran_at": "2026-11-03T09:30:02Z", "processed_with": "ctm-llm autofill-ctml 2.4.0"}


def _trial():
    return {"nct_id": "NCT1", "_summary": {"long_title": "t"},
            "treatment_list": {"step": [{"match": [], "arm": []}]}}


def test_each_stage_writes_only_its_own_record():
    t = draft_clinical(_trial(), {}, QueuedClient(["null"]), set(), {}, {}, stage_run=CLIN_RUN)
    t = draft_genomic(t, {}, QueuedClient(['{"node": null, "refs": []}']), set(), stage_run=GEN_RUN)
    t, _ = autofill_trial(t, stage_run=AUTO_RUN)
    assert t["_llm_curation"]["stage_runs"] == {"clinical": CLIN_RUN, "genomic": GEN_RUN,
                                                "autofill": AUTO_RUN}
    later = {**CLIN_RUN, "ran_at": "2026-11-04T08:00:00Z"}
    t = draft_clinical(t, {}, QueuedClient(["null"]), set(), {}, {}, stage_run=later)
    assert t["_llm_curation"]["stage_runs"]["clinical"] == later
    assert t["_llm_curation"]["stage_runs"]["genomic"] == GEN_RUN


def test_autofill_records_its_run_even_when_it_skips():
    trial = _trial()
    trial["treatment_list"]["step"][0]["match"] = [{"clinical": {"gender": "Female"}}]
    out, report = autofill_trial(trial, stage_run=AUTO_RUN)
    assert report["filled"] is False
    assert out["_llm_curation"]["stage_runs"]["autofill"] == AUTO_RUN


def test_no_stage_run_means_no_record():
    t = draft_clinical(_trial(), {}, QueuedClient(["null"]), set(), {}, {})
    assert "stage_runs" not in t["_llm_curation"]


def test_cli_stamps_utc_time_and_version(fake_mongo, tmp_path, monkeypatch):
    from ctm import db as ctm_db
    from ctm import llm_cli

    monkeypatch.setattr(llm_cli, "load_env", lambda: None)
    src, out = tmp_path / "in.json", tmp_path / "o.json"
    src.write_text(json.dumps([_trial()]))
    llm_cli.main(["autofill-ctml", "--trials", str(src), "--out", str(out),
                  "--cache", str(tmp_path / "c.json")])
    run = json.loads(out.read_text())[0]["_llm_curation"]["stage_runs"]["autofill"]
    assert run["processed_with"] == f"ctm-llm autofill-ctml {ctm_db.toolkit_version()}"
    assert run["ran_at"].endswith("Z")
    datetime.fromisoformat(run["ran_at"].replace("Z", "+00:00"))


def test_storage_accepts_stage_runs():
    from ctm.schemas.storage import LlmCuration

    c = LlmCuration.model_validate({"stage_runs": {"clinical": CLIN_RUN}})
    assert c.model_dump(by_alias=True)["stage_runs"]["clinical"] == CLIN_RUN
