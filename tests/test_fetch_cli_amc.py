"""ctm-fetch --amc: locating and normalizing AMC's SharePoint daily dump.

The dump is read from a locally synced folder, so these tests need no network
mocking — AMC_EXPORT_DIR simply points at tmp_path. CTM_CACHE_DIR is redirected
in every test so the snapshot never lands in the real ~/.cache/ctm/.
"""
import json
import os
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
AMC_FIXTURE = FIXTURES / "test-trials-amc-v0.0.1.xml"


def _protocol_xml(protocol_no: str) -> str:
    """A minimal one-trial export, identifiable by protocol_no."""
    return f"""<PROTOCOL_SUMMARY>
      <PROTOCOL>
        <NO>{protocol_no}</NO>
        <NCT_NUMBER>NCT04858334</NCT_NUMBER>
        <STATUS>OPEN TO ACCRUAL</STATUS>
        <TITLE>Test Trial</TITLE>
        <ELIGIBILITY>Inclusion Criteria||~Age &gt;= 18</ELIGIBILITY>
      </PROTOCOL>
    </PROTOCOL_SUMMARY>"""


@pytest.fixture
def dump(tmp_path, monkeypatch):
    """An empty SharePoint dump directory, wired up with a redirected cache."""
    export_dir = tmp_path / "sharepoint"
    export_dir.mkdir()
    monkeypatch.setenv("AMC_EXPORT_DIR", str(export_dir))
    monkeypatch.delenv("AMC_EXPORT_GLOB", raising=False)
    monkeypatch.setenv("CTM_CACHE_DIR", str(tmp_path / "cache"))
    # A .env on the developer's machine must not leak into these assertions.
    monkeypatch.setattr("ctm.fetch_cli.load_env", lambda: None)
    return export_dir


def _run(output: Path, *extra: str) -> None:
    """Invoke ctm-fetch's main() with argv, as a user would."""
    from ctm import fetch_cli

    argv = ["ctm-fetch", "--amc", "--output", str(output), *extra]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", argv)
        fetch_cli.main()


def test_amc_writes_normalized_ctml_list(dump, tmp_path):
    """Output is a JSON list of CTML trials, hashed like `ctm-mm trials` output."""
    (dump / "amc.xml").write_text(AMC_FIXTURE.read_text())
    out_path = tmp_path / "out.json"

    _run(out_path)

    trials = json.loads(out_path.read_text())
    assert isinstance(trials, list)
    assert trials, "expected at least one trial from the fixture export"
    for trial in trials:
        assert trial["entity"] == "amc"
        assert len(trial["trial_hash"]) == 64  # sha256 hex digest
        assert "_summary" in trial and "_raw" in trial


def test_amc_picks_the_newest_file(dump, tmp_path):
    """A daily dump accumulates; today's export is the one that counts."""
    for name, protocol_no, mtime in [
        ("dump-monday.xml", "2020.001", 1_700_000_000),
        ("dump-wednesday.xml", "2020.003", 1_700_200_000),  # newest
        ("dump-tuesday.xml", "2020.002", 1_700_100_000),
    ]:
        path = dump / name
        path.write_text(_protocol_xml(protocol_no))
        os.utime(path, (mtime, mtime))  # explicit, not write-order dependent

    out_path = tmp_path / "out.json"
    _run(out_path)

    trials = json.loads(out_path.read_text())
    assert [t["protocol_no"] for t in trials] == ["2020.003"]


def test_amc_ignores_non_matching_files(dump, tmp_path):
    """Sync clients leave .tmp partials and stray docs beside the real export."""
    (dump / "amc.xml").write_text(_protocol_xml("2020.001"))
    # Newer, but neither should be considered
    for name in ("readme.txt", "amc.xml.tmp"):
        path = dump / name
        path.write_text("not an export")
        os.utime(path, (1_800_000_000, 1_800_000_000))

    out_path = tmp_path / "out.json"
    _run(out_path)

    assert [t["protocol_no"] for t in json.loads(out_path.read_text())] == ["2020.001"]


def test_amc_respects_a_custom_glob(dump, tmp_path, monkeypatch):
    """AMC_EXPORT_GLOB is the hedge against the dump's naming or format moving."""
    monkeypatch.setenv("AMC_EXPORT_GLOB", "oncore-*.xml")
    (dump / "oncore-2026-08-17.xml").write_text(_protocol_xml("2020.007"))
    (dump / "unrelated.xml").write_text(_protocol_xml("2020.999"))
    os.utime(dump / "unrelated.xml", (1_800_000_000, 1_800_000_000))

    out_path = tmp_path / "out.json"
    _run(out_path)

    assert [t["protocol_no"] for t in json.loads(out_path.read_text())] == ["2020.007"]


def test_amc_empty_dump_is_an_error(dump, tmp_path, capsys):
    """An empty dump must not read as 'AMC has no trials'.

    Writing [] here would reach trials-diff and route every AMC trial to
    'deleted' — the same silent-wipe failure the empty-master guard prevents.
    """
    out_path = tmp_path / "out.json"

    with pytest.raises(SystemExit) as exc:
        _run(out_path)

    assert exc.value.code == 1
    stderr = capsys.readouterr().err
    assert "*.xml" in stderr
    assert str(dump) in stderr
    assert not out_path.exists()


def test_amc_export_dir_unset_names_the_variable(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AMC_EXPORT_DIR", raising=False)
    monkeypatch.setenv("CTM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr("ctm.fetch_cli.load_env", lambda: None)

    with pytest.raises(SystemExit) as exc:
        _run(tmp_path / "out.json")

    assert exc.value.code == 1
    assert "AMC_EXPORT_DIR" in capsys.readouterr().err


def test_amc_export_dir_missing_is_an_error(tmp_path, monkeypatch, capsys):
    """A typo'd or not-yet-synced path is a hard error, not an empty result."""
    monkeypatch.setenv("AMC_EXPORT_DIR", str(tmp_path / "nope"))
    monkeypatch.setenv("CTM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr("ctm.fetch_cli.load_env", lambda: None)

    with pytest.raises(SystemExit) as exc:
        _run(tmp_path / "out.json")

    assert exc.value.code == 1
    assert "not a directory" in capsys.readouterr().err


def test_amc_snapshots_the_source_file(dump, tmp_path):
    """The sync client will overwrite the dump; the snapshot keeps the run reproducible."""
    source = dump / "amc.xml"
    source.write_text(AMC_FIXTURE.read_text())

    _run(tmp_path / "out.json")

    snapshots = list((tmp_path / "cache").glob("amc-export-*.xml"))
    assert len(snapshots) == 1
    assert snapshots[0].read_bytes() == source.read_bytes()


def test_snapshot_never_lands_in_the_repo(dump, tmp_path):
    """Raw institutional exports stay out of the checkout."""
    import ctm

    (dump / "amc.xml").write_text(_protocol_xml("2020.001"))
    _run(tmp_path / "out.json")

    package_root = Path(ctm.__file__).parent
    snapshot = next((tmp_path / "cache").glob("amc-export-*.xml"))
    assert not snapshot.is_relative_to(package_root)


# --- parser wiring ----------------------------------------------------------


@pytest.mark.parametrize("argv", [
    ["--output", "x.json"],                                   # no source
    ["--nct", "NCT03067181", "--amc", "--output", "x.json"],  # both sources
    ["--amc"],                                                # no --output
])
def test_invalid_source_combinations_are_rejected(argv, monkeypatch):
    from ctm import fetch_cli

    monkeypatch.setattr("ctm.fetch_cli.load_env", lambda: None)
    monkeypatch.setattr(sys, "argv", ["ctm-fetch", *argv])

    with pytest.raises(SystemExit) as exc:
        fetch_cli.main()

    assert exc.value.code == 2  # argparse usage error


def test_fmt_mm_with_amc_is_rejected(dump, tmp_path, capsys):
    """--amc output is always CTML, so --fmt-mm is a no-op worth flagging."""
    (dump / "amc.xml").write_text(_protocol_xml("2020.001"))
    out_path = tmp_path / "out.json"

    with pytest.raises(SystemExit) as exc:
        _run(out_path, "--fmt-mm")

    assert exc.value.code == 1
    assert "--fmt-mm" in capsys.readouterr().err
    assert not out_path.exists()


# --- handoff into ctm-mm trials ---------------------------------------------


def test_ctm_mm_trials_accepts_the_normalized_json(dump, tmp_path):
    """`ctm-fetch --amc` output feeds straight back into --amc, same as the XML."""
    import argparse

    from ctm.mm_cli import _cmd_trials

    source = dump / "amc.xml"
    source.write_text(AMC_FIXTURE.read_text())
    fetched = tmp_path / "amc-normalized.json"
    _run(fetched)

    from_json = tmp_path / "from-json.json"
    from_xml = tmp_path / "from-xml.json"
    for amc_input, out in ((fetched, from_json), (source, from_xml)):
        _cmd_trials(argparse.Namespace(
            amc=str(amc_input), ct=None, sparrow=None, west=None, out=str(out),
        ))

    assert json.loads(from_json.read_text()) == json.loads(from_xml.read_text())


def test_ctm_mm_trials_rejects_an_unknown_amc_suffix(tmp_path, capsys):
    import argparse

    from ctm.mm_cli import _cmd_trials

    bogus = tmp_path / "amc.csv"
    bogus.write_text("protocol_no\n2020.001\n")

    with pytest.raises(SystemExit) as exc:
        _cmd_trials(argparse.Namespace(
            amc=str(bogus), ct=None, sparrow=None, west=None,
            out=str(tmp_path / "out.json"),
        ))

    assert exc.value.code == 1
    assert ".xml or .json" in capsys.readouterr().err
