"""Tests for the ctm-mm trials-diff / trials-merge CLI subcommands and the
trial_hash stamping added to the existing trials command."""
import argparse
import json


def test_cmd_trials_stamps_trial_hash(tmp_path, monkeypatch):
    from ctm.mm_cli import _cmd_trials

    amc_xml = tmp_path / "amc.xml"
    amc_xml.write_text("""<PROTOCOL_SUMMARY>
      <PROTOCOL>
        <NO>2021.070</NO>
        <NCT_NUMBER>NCT04858334</NCT_NUMBER>
        <STATUS>OPEN TO ACCRUAL</STATUS>
        <TITLE>Test Trial</TITLE>
        <ELIGIBILITY>Inclusion Criteria:
~Age &gt;= 18</ELIGIBILITY>
      </PROTOCOL>
    </PROTOCOL_SUMMARY>""")
    out_path = tmp_path / "out.json"

    args = argparse.Namespace(amc=str(amc_xml), ct=None, sparrow=None, west=None, out=str(out_path))
    _cmd_trials(args)

    trials = json.loads(out_path.read_text())
    assert len(trials) == 1
    assert trials[0]["trial_hash"]
    assert isinstance(trials[0]["trial_hash"], str)
    assert len(trials[0]["trial_hash"]) == 64  # sha256 hex digest length


def test_cmd_trials_diff_writes_three_files(tmp_path):
    from ctm.mm_cli import _cmd_trials_diff

    eligibility = {"inclusion": [], "exclusion": []}
    master = [
        {"entity": "amc", "protocol_no": "2015.063", "nct_id": None,
         "eligibility": eligibility, "treatment_list": {"step": []}, "_raw": {}},
        {"entity": "amc", "protocol_no": "2019.058", "nct_id": None,
         "eligibility": eligibility, "treatment_list": {"step": []}, "_raw": {}},
    ]
    new = [
        # 2015.063: identical eligibility -> unchanged
        {"entity": "amc", "protocol_no": "2015.063", "nct_id": None,
         "eligibility": eligibility, "treatment_list": {"step": []}, "_raw": {"status": "open"}},
        # 2021.070: brand new -> changed
        {"entity": "amc", "protocol_no": "2021.070", "nct_id": None,
         "eligibility": {"inclusion": [{"text": "New", "sub_criteria": []}], "exclusion": []},
         "treatment_list": {"step": []}, "_raw": {}},
        # 2019.058 is absent from `new` -> deleted
    ]

    master_path = tmp_path / "master.json"
    master_path.write_text(json.dumps(master))
    new_path = tmp_path / "new.json"
    new_path.write_text(json.dumps(new))
    out_prefix = str(tmp_path / "2026-07-14")

    args = argparse.Namespace(new=str(new_path), master=str(master_path), out_prefix=out_prefix)
    _cmd_trials_diff(args)

    unchanged = json.loads((tmp_path / "2026-07-14-unchanged.json").read_text())
    changed = json.loads((tmp_path / "2026-07-14-changed.json").read_text())
    deleted = json.loads((tmp_path / "2026-07-14-deleted.json").read_text())

    assert [t["protocol_no"] for t in unchanged] == ["2015.063"]
    assert [t["protocol_no"] for t in changed] == ["2021.070"]
    assert [t["protocol_no"] for t in deleted] == ["2019.058"]


def test_cmd_trials_diff_missing_master_treats_everything_as_changed(tmp_path):
    from ctm.mm_cli import _cmd_trials_diff

    new = [{"entity": "amc", "protocol_no": "2021.070", "nct_id": None,
            "eligibility": {"inclusion": [], "exclusion": []},
            "treatment_list": {"step": []}, "_raw": {}}]
    new_path = tmp_path / "new.json"
    new_path.write_text(json.dumps(new))
    missing_master_path = tmp_path / "does-not-exist.json"
    out_prefix = str(tmp_path / "2026-07-14")

    args = argparse.Namespace(new=str(new_path), master=str(missing_master_path), out_prefix=out_prefix)
    _cmd_trials_diff(args)

    changed = json.loads((tmp_path / "2026-07-14-changed.json").read_text())
    assert [t["protocol_no"] for t in changed] == ["2021.070"]


def test_cmd_trials_merge_concatenates_to_out(tmp_path):
    from ctm.mm_cli import _cmd_trials_merge

    unchanged = [{"entity": "amc", "protocol_no": "2015.063"}]
    changed = [{"entity": "amc", "protocol_no": "2021.070"}]

    unchanged_path = tmp_path / "unchanged.json"
    unchanged_path.write_text(json.dumps(unchanged))
    changed_path = tmp_path / "changed.json"
    changed_path.write_text(json.dumps(changed))
    out_path = tmp_path / "2026-07-14-trials.json"

    args = argparse.Namespace(unchanged=str(unchanged_path), changed=str(changed_path), out=str(out_path))
    _cmd_trials_merge(args)

    master = json.loads(out_path.read_text())
    assert [t["protocol_no"] for t in master] == ["2015.063", "2021.070"]
