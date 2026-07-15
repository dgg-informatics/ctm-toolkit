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
