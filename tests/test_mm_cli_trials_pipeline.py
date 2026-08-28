"""Tests for the ctm-mm trials-diff / trials-merge CLI subcommands and the
trial_hash stamping added to the existing trials command.

trials-diff writes to MongoDB as well as to disk. Nothing here needs a live
server: the `fake_mongo` fixture replaces db.py's three driver-touching
functions and captures what would have been written.
"""
import argparse
import json

import pytest


def _diff_args(**overrides):
    """A trials-diff Namespace with every argparse-supplied field present.

    `disk` defaults to whether an out_prefix was named, so tests that assert on
    file contents read as asking for files. The flag's own validation rules are
    exercised explicitly by the --disk tests below.
    """
    defaults = {
        "new": None,
        "master": None,
        "out_prefix": None,
        "disk": None,
        "db": None,
        "master_db": None,
        "master_collection": None,
        "run_date": "2026-08-17",
        "allow_empty_master": False,
    }
    args = argparse.Namespace(**{**defaults, **overrides})
    if args.disk is None:
        args.disk = args.out_prefix is not None
    return args


def _curate_args(**overrides):
    """A trials-curate Namespace with every argparse-supplied field present."""
    defaults = {
        "trials": None, "out": None, "cache": None, "kb": None,
        "disk": None, "db": None, "run_date": None, "yes": True,
    }
    args = argparse.Namespace(**{**defaults, **overrides})
    if args.disk is None:
        args.disk = args.out is not None
    return args


def _trials_args(*argv):
    """A `trials` Namespace built by the real parser.

    Hand-rolling argparse.Namespace here meant every new source flag broke this
    test with an AttributeError; going through the parser keeps defaults in one
    place and makes a missing flag impossible.
    """
    import sys as _sys
    from unittest.mock import patch

    from ctm import mm_cli

    captured = {}
    with patch.object(mm_cli, "_cmd_trials", lambda a: captured.setdefault("args", a)), \
         patch.object(_sys, "argv", ["ctm-mm", "trials", *argv]):
        mm_cli.main()
    return captured["args"]


def test_cmd_trials_stamps_trial_hash(tmp_path, monkeypatch, fake_mongo):
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

    args = _trials_args("--amc", str(amc_xml), "--out", str(out_path))
    _cmd_trials(args)

    trials = json.loads(out_path.read_text())
    assert len(trials) == 1
    assert trials[0]["trial_hash"]
    assert isinstance(trials[0]["trial_hash"], str)
    assert len(trials[0]["trial_hash"]) == 64  # sha256 hex digest length


def _three_bucket_case(tmp_path):
    """A --new/--master pair producing exactly one unchanged, changed and deleted."""
    eligibility = {"inclusion": [], "exclusion": []}
    master = [
        {"entity": "amc", "protocol_no": "2015.063", "nct_id": None,
         "eligibility": eligibility, "treatment_list": {"step": []},
         "trial_hash": "1" * 64, "_raw": {}},
        {"entity": "amc", "protocol_no": "2019.058", "nct_id": None,
         "eligibility": eligibility, "treatment_list": {"step": []},
         "trial_hash": "2" * 64, "_raw": {}},
    ]
    new = [
        # 2015.063: identical eligibility -> unchanged
        {"entity": "amc", "protocol_no": "2015.063", "nct_id": None,
         "eligibility": eligibility, "treatment_list": {"step": []},
         "trial_hash": "3" * 64, "_raw": {"status": "open"}},
        # 2021.070: brand new -> changed
        {"entity": "amc", "protocol_no": "2021.070", "nct_id": None,
         "eligibility": {"inclusion": [{"text": "New", "sub_criteria": []}], "exclusion": []},
         "treatment_list": {"step": []}, "trial_hash": "4" * 64, "_raw": {}},
        # 2019.058 is absent from `new` -> deleted
    ]

    master_path = tmp_path / "master.json"
    master_path.write_text(json.dumps(master))
    new_path = tmp_path / "new.json"
    new_path.write_text(json.dumps(new))

    return master, new, _diff_args(
        new=str(new_path), master=str(master_path), out_prefix=str(tmp_path / "2026-07-14"),
    )


def test_cmd_trials_diff_writes_three_files(tmp_path, fake_mongo):
    from ctm.mm_cli import _cmd_trials_diff

    _, _, args = _three_bucket_case(tmp_path)
    _cmd_trials_diff(args)

    unchanged = json.loads((tmp_path / "2026-07-14-unchanged.json").read_text())
    changed = json.loads((tmp_path / "2026-07-14-changed.json").read_text())
    deleted = json.loads((tmp_path / "2026-07-14-deleted.json").read_text())

    assert [t["protocol_no"] for t in unchanged] == ["2015.063"]
    assert [t["protocol_no"] for t in changed] == ["2021.070"]
    assert [t["protocol_no"] for t in deleted] == ["2019.058"]


def test_cmd_trials_diff_stores_all_three_buckets_in_one_collection(tmp_path, fake_mongo):
    """One collection with a diff_status field, not three collections."""
    from ctm.db import DIFF_COLLECTION
    from ctm.mm_cli import _cmd_trials_diff

    _, _, args = _three_bucket_case(tmp_path)
    _cmd_trials_diff(args)

    written = fake_mongo["written"]
    assert written["name"] == DIFF_COLLECTION == "02_diff_trials"
    assert written["unique_key"] == "trial_hash"
    assert written["lookup_keys"] == ("entity", "trial_key")

    by_key = {d["trial_key"]: d for d in written["docs"]}
    assert by_key["2015.063"]["diff_status"] == "unchanged"
    assert by_key["2021.070"]["diff_status"] == "changed"
    assert by_key["2019.058"]["diff_status"] == "deleted"

    for doc in written["docs"]:
        assert doc["run_date"] == "2026-08-17"
        assert doc["processed_with"].startswith("ctm-mm trials-diff ")


@pytest.mark.parametrize(
    "argv, expected_disk",
    [
        ([], None),                # v2: omitted -> MongoDB only
        (["--disk"], True),
        (["--no-disk"], False),
    ],
)
def test_trials_diff_disk_flag_defaults_to_mongo_only(monkeypatch, argv, expected_disk):
    """v2 breaking change: omitting --disk stores to MongoDB only; --out-prefix or
    --disk opts into the files."""
    import sys as _sys

    from ctm import mm_cli

    captured = {}
    monkeypatch.setattr(mm_cli, "_cmd_trials_diff", lambda args: captured.update(vars(args)))
    monkeypatch.setattr(
        _sys, "argv",
        ["ctm-mm", "trials-diff", "--new", "n.json", "--out-prefix", "p", *argv],
    )

    mm_cli.main()

    assert captured["disk"] is expected_disk


def test_cmd_trials_diff_without_out_prefix_writes_only_mongo(tmp_path, fake_mongo):
    """v2: omitting --out-prefix stores to MongoDB only — no files, no error."""
    from ctm.mm_cli import _cmd_trials_diff

    _, _, args = _three_bucket_case(tmp_path)
    args.disk = None
    args.out_prefix = None

    _cmd_trials_diff(args)   # must not raise

    assert list(tmp_path.glob("*-unchanged.json")) == []
    assert len(fake_mongo["written"]["docs"]) == 3


def test_cmd_trials_diff_no_disk_suppresses_files_even_with_a_prefix(tmp_path, fake_mongo):
    """--no-disk stores to MongoDB only, ignoring a given --out-prefix."""
    from ctm.mm_cli import _cmd_trials_diff

    _, _, args = _three_bucket_case(tmp_path)
    args.disk = False  # out_prefix is still set by the helper

    _cmd_trials_diff(args)

    assert list(tmp_path.glob("*-unchanged.json")) == []
    assert len(fake_mongo["written"]["docs"]) == 3


def test_cmd_trials_diff_keeps_both_copies_of_a_sparrow_west_nct_collision(tmp_path, fake_mongo):
    """Sparrow and UMH-West both curate trials by NCT number and neither has a
    protocol_no, so trial_key() returns the same id for both — 30 such pairs in
    the 03aug26 normalization. They are distinct source records (differing _raw,
    hence differing trial_hash), so both must survive rather than one clobbering
    the other or the unique index rejecting the batch."""
    from ctm.mm_cli import _cmd_trials_diff

    eligibility = {"inclusion": [], "exclusion": []}
    new = [
        {"entity": "sparrow", "protocol_no": None, "nct_id": "NCT05812807",
         "eligibility": eligibility, "treatment_list": {"step": []},
         "trial_hash": "a" * 64, "_raw": {"nct_id": "NCT05812807"}},
        {"entity": "west", "protocol_no": None, "nct_id": "NCT05812807",
         "eligibility": eligibility, "treatment_list": {"step": []},
         "trial_hash": "b" * 64, "_raw": {"nct_id": "NCT05812807", "_west": {"group": "CRCWM"}}},
    ]
    new_path = tmp_path / "new.json"
    new_path.write_text(json.dumps(new))
    master_path = tmp_path / "master.json"
    master_path.write_text(json.dumps(new))

    args = _diff_args(new=str(new_path), master=str(master_path),
                      out_prefix=str(tmp_path / "2026-07-14"))
    _cmd_trials_diff(args)

    docs = fake_mongo["written"]["docs"]
    assert len(docs) == 2, "both the sparrow and west copies must be stored"
    assert {d["entity"] for d in docs} == {"sparrow", "west"}
    assert {d["trial_key"] for d in docs} == {"NCT05812807"}
    # Distinct source records, so the identifying hash must not be collapsed.
    assert {d["trial_hash"] for d in docs} == {"a" * 64, "b" * 64}


def test_cmd_trials_diff_metadata_never_enters_the_json_files(tmp_path, fake_mongo):
    """The files must stay byte-identical to pre-Mongo output, so downstream
    trials-curate / trials-merge are provably unaffected."""
    from ctm.mm_cli import _cmd_trials_diff

    _, _, args = _three_bucket_case(tmp_path)
    _cmd_trials_diff(args)

    for bucket in ("unchanged", "changed", "deleted"):
        trials = json.loads((tmp_path / f"2026-07-14-{bucket}.json").read_text())
        for trial in trials:
            for field in ("diff_status", "trial_key", "processed_with", "run_date", "_id"):
                assert field not in trial, f"{field} leaked into the {bucket} file"


def test_cmd_trials_diff_writes_to_the_run_database_not_the_master_one(tmp_path, fake_mongo):
    from ctm.mm_cli import _cmd_trials_diff

    _, _, args = _three_bucket_case(tmp_path)
    _cmd_trials_diff(args)

    assert fake_mongo["written"]["db"] == "<db 2026-08-17_test>"


def test_cmd_trials_diff_db_flag_overrides_the_run_database(tmp_path, fake_mongo):
    from ctm.mm_cli import _cmd_trials_diff

    _, _, args = _three_bucket_case(tmp_path)
    args.db = "2026-09-01_dev"
    _cmd_trials_diff(args)

    assert fake_mongo["written"]["db"] == "<db 2026-09-01_dev>"


def test_cmd_trials_diff_missing_master_file_is_an_error(tmp_path, fake_mongo):
    """A --master typo used to fall back to [] and silently re-curate everything."""
    from ctm.mm_cli import _cmd_trials_diff

    new = [{"entity": "amc", "protocol_no": "2021.070", "nct_id": None,
            "eligibility": {"inclusion": [], "exclusion": []},
            "treatment_list": {"step": []}, "_raw": {}}]
    new_path = tmp_path / "new.json"
    new_path.write_text(json.dumps(new))

    args = _diff_args(
        new=str(new_path),
        master=str(tmp_path / "does-not-exist.json"),
        out_prefix=str(tmp_path / "2026-07-14"),
    )

    with pytest.raises(SystemExit):
        _cmd_trials_diff(args)

    assert not (tmp_path / "2026-07-14-changed.json").exists()
    assert fake_mongo["written"] is None


def test_cmd_trials_diff_empty_master_is_an_error(tmp_path, fake_mongo):
    from ctm.mm_cli import _cmd_trials_diff

    new = [{"entity": "amc", "protocol_no": "2021.070", "nct_id": None,
            "eligibility": {"inclusion": [], "exclusion": []},
            "treatment_list": {"step": []}, "_raw": {}}]
    new_path = tmp_path / "new.json"
    new_path.write_text(json.dumps(new))
    master_path = tmp_path / "master.json"
    master_path.write_text("[]")

    args = _diff_args(new=str(new_path), master=str(master_path),
                      out_prefix=str(tmp_path / "2026-07-14"))

    with pytest.raises(SystemExit):
        _cmd_trials_diff(args)

    assert fake_mongo["written"] is None


def test_cmd_trials_diff_allow_empty_master_routes_everything_to_changed(tmp_path, fake_mongo):
    """The first-ever run, made explicit rather than silent."""
    from ctm.mm_cli import _cmd_trials_diff

    new = [{"entity": "amc", "protocol_no": "2021.070", "nct_id": None,
            "eligibility": {"inclusion": [], "exclusion": []},
            "treatment_list": {"step": []}, "_raw": {}}]
    new_path = tmp_path / "new.json"
    new_path.write_text(json.dumps(new))
    master_path = tmp_path / "master.json"
    master_path.write_text("[]")

    args = _diff_args(new=str(new_path), master=str(master_path),
                      out_prefix=str(tmp_path / "2026-07-14"), allow_empty_master=True)
    _cmd_trials_diff(args)

    changed = json.loads((tmp_path / "2026-07-14-changed.json").read_text())
    assert [t["protocol_no"] for t in changed] == ["2021.070"]
    assert [d["diff_status"] for d in fake_mongo["written"]["docs"]] == ["changed"]


def test_cmd_trials_diff_reads_master_from_mongo_when_no_master_flag(tmp_path, fake_mongo):
    from ctm.mm_cli import _cmd_trials_diff

    eligibility = {"inclusion": [], "exclusion": []}
    fake_mongo["master"] = [
        {"entity": "amc", "protocol_no": "2015.063", "nct_id": None,
         "eligibility": eligibility, "treatment_list": {"step": []}, "_raw": {}},
    ]
    new = [{"entity": "amc", "protocol_no": "2015.063", "nct_id": None,
            "eligibility": eligibility, "treatment_list": {"step": []}, "_raw": {"status": "open"}}]
    new_path = tmp_path / "new.json"
    new_path.write_text(json.dumps(new))

    args = _diff_args(new=str(new_path), out_prefix=str(tmp_path / "2026-07-14"))
    _cmd_trials_diff(args)

    # Master read from the stable master DB, diff written to the per-run DB.
    assert fake_mongo["read_from"] == ("<db ctm_master_test>", "06_master_trials")
    assert fake_mongo["written"]["db"] == "<db 2026-08-17_test>"

    unchanged = json.loads((tmp_path / "2026-07-14-unchanged.json").read_text())
    assert [t["protocol_no"] for t in unchanged] == ["2015.063"]


def test_cmd_trials_diff_master_db_and_collection_flags_override_the_source(tmp_path, fake_mongo):
    """For a master that isn't in 06_master_trials — e.g. matchengine's `trials`."""
    from ctm.mm_cli import _cmd_trials_diff

    eligibility = {"inclusion": [], "exclusion": []}
    fake_mongo["master"] = [
        {"entity": "amc", "protocol_no": "2015.063", "nct_id": None,
         "eligibility": eligibility, "treatment_list": {"step": []}, "_raw": {}},
    ]
    new_path = tmp_path / "new.json"
    new_path.write_text(json.dumps(fake_mongo["master"]))

    args = _diff_args(new=str(new_path), out_prefix=str(tmp_path / "2026-07-14"),
                      master_db="2026-08-10_dev", master_collection="trials")
    _cmd_trials_diff(args)

    assert fake_mongo["read_from"] == ("<db 2026-08-10_dev>", "trials")


def test_cmd_trials_diff_master_db_flag_satisfies_the_required_env_var(tmp_path, fake_mongo,
                                                                      monkeypatch):
    """--master-db names the database, so MONGO_MASTER_DBNAME is not needed."""
    from ctm.mm_cli import _cmd_trials_diff

    monkeypatch.delenv("MONGO_MASTER_DBNAME")

    eligibility = {"inclusion": [], "exclusion": []}
    fake_mongo["master"] = [
        {"entity": "amc", "protocol_no": "2015.063", "nct_id": None,
         "eligibility": eligibility, "treatment_list": {"step": []}, "_raw": {}},
    ]
    new_path = tmp_path / "new.json"
    new_path.write_text(json.dumps(fake_mongo["master"]))

    args = _diff_args(new=str(new_path), out_prefix=str(tmp_path / "2026-07-14"),
                      master_db="ctm_master_explicit")
    _cmd_trials_diff(args)

    assert fake_mongo["read_from"] == ("<db ctm_master_explicit>", "06_master_trials")


def test_cmd_trials_diff_without_master_flag_requires_the_master_dbname(tmp_path, fake_mongo,
                                                                       monkeypatch):
    from ctm.mm_cli import _cmd_trials_diff

    monkeypatch.delenv("MONGO_MASTER_DBNAME")

    new_path = tmp_path / "new.json"
    new_path.write_text("[]")
    args = _diff_args(new=str(new_path), out_prefix=str(tmp_path / "2026-07-14"))

    with pytest.raises(ValueError, match="MONGO_MASTER_DBNAME"):
        _cmd_trials_diff(args)


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


def test_cmd_trials_curate_writes_curated_output(tmp_path, monkeypatch, fake_mongo):
    from ctm import mm_cli

    trials = [{
        "nct_id": "NCT00000009",
        "protocol_no": None,
        "eligibility": {"inclusion": [{"text": "Age >= 18", "sub_criteria": []}], "exclusion": []},
        "_summary": {"long_title": "A Study in Patients with a BRCA1 Mutation"},
        "_ctml_suggestions": [
            {"source": "inclusion", "text": "Age >= 18",
             "suggested_node": {"clinical": {"age_numerical": ">=18"}}, "transferred_to_match": False},
        ],
    }]
    trials_path = tmp_path / "draft.json"
    trials_path.write_text(json.dumps(trials))

    kb_path = tmp_path / "kb.json"
    kb_path.write_text(json.dumps([{"name": "BRCA1"}]))

    out_path = tmp_path / "curated.json"
    cache_path = tmp_path / "cache.json"

    class _FakeClient:
        def __init__(self):
            self.chat = self
            self.completions = self

        def create(self, **kwargs):
            from types import SimpleNamespace
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"genomic": {"hugo_symbol": "BRCA1"}}')
            )])

    monkeypatch.setattr("ctm.transformers.eligibility_to_ctml.build_client", lambda: _FakeClient())
    monkeypatch.setattr("ctm.transformers.eligibility_to_ctml.fetch_oncotree_names", lambda: set())

    args = _curate_args(trials=str(trials_path), out=str(out_path),
                        cache=str(cache_path), kb=str(kb_path))
    mm_cli._cmd_trials_curate(args)

    result = json.loads(out_path.read_text())
    assert len(result) == 1
    # The deprecated alias reaches `ctm-llm biomarkers`, which writes exactly one
    # key and leaves every other field — including a legacy top-level
    # _ctml_suggestions — untouched.
    assert "biomarker_references" in result[0]["_llm_curation"]
    assert "final_suggested_ctml" not in result[0]["_llm_curation"]


def test_cmd_trials_curate_alias_only_makes_the_biomarker_call(tmp_path, monkeypatch, fake_mongo):
    """The alias must reach `biomarkers`, not the old fused stage: one call, and no
    OncoTree fetch, since biomarker references are not match nodes."""
    from ctm import mm_cli

    trials_path = tmp_path / "draft.json"
    trials_path.write_text(json.dumps([{
        "nct_id": "NCT00000009", "protocol_no": None,
        "eligibility": {"inclusion": [{"text": "BRCA1 mutation", "sub_criteria": []}], "exclusion": []},
        "_summary": {"long_title": "A Study in Patients with a BRCA1 Mutation"},
    }]))
    kb_path = tmp_path / "kb.json"
    kb_path.write_text(json.dumps([{"name": "BRCA1"}]))

    calls = []

    class _OneCallClient:
        def __init__(self):
            self.chat = self
            self.completions = self

        def create(self, **kwargs):
            from types import SimpleNamespace
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='[{"biomarker": "BRCA1", "type": "snv", "reference": "BRCA1"}]')
            )])

    monkeypatch.setattr("ctm.transformers.eligibility_to_ctml.build_client", lambda: _OneCallClient())

    def _no_oncotree():
        raise AssertionError("biomarkers must not fetch OncoTree")

    monkeypatch.setattr("ctm.transformers.eligibility_to_ctml.fetch_oncotree_names", _no_oncotree)

    mm_cli._cmd_trials_curate(_curate_args(
        trials=str(trials_path), out=str(tmp_path / "out.json"),
        cache=str(tmp_path / "c.json"), kb=str(kb_path),
    ))

    assert len(calls) == 1


def test_cmd_trials_stores_normalized_output_in_the_first_collection(tmp_path, fake_mongo):
    """01_normalized_trials is the head of the chain, so every later stage's
    run_date traces back to what this stage stamped."""
    from ctm.db import NORMALIZED_COLLECTION
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
    out = tmp_path / "out.json"

    _cmd_trials(_trials_args("--amc", str(amc_xml), "--out", str(out),
                             "--run-date", "2026-08-20"))

    written = fake_mongo["written"]
    assert written["name"] == NORMALIZED_COLLECTION == "01_normalized_trials"
    assert written["unique_key"] == "trial_hash"
    assert [d["run_date"] for d in written["docs"]] == ["2026-08-20"]
    assert written["docs"][0]["processed_with"].startswith("ctm-mm trials ")

    # Storage metadata must not leak into the JSON file.
    for trial in json.loads(out.read_text()):
        for field in ("run_date", "processed_with", "trial_key", "_id"):
            assert field not in trial


def test_cmd_trials_no_disk_writes_only_to_mongo(tmp_path, fake_mongo):
    from ctm.mm_cli import _cmd_trials

    amc_xml = tmp_path / "amc.xml"
    amc_xml.write_text("""<PROTOCOL_SUMMARY><PROTOCOL><NO>2021.070</NO>
      <NCT_NUMBER>NCT04858334</NCT_NUMBER><STATUS>OPEN TO ACCRUAL</STATUS>
      <TITLE>T</TITLE><ELIGIBILITY>Inclusion Criteria:
~Age &gt;= 18</ELIGIBILITY></PROTOCOL></PROTOCOL_SUMMARY>""")

    _cmd_trials(_trials_args("--amc", str(amc_xml), "--no-disk"))

    assert list(tmp_path.glob("*.json")) == []
    assert len(fake_mongo["written"]["docs"]) == 1


def test_cmd_trials_out_without_disk_is_an_error(tmp_path, fake_mongo):
    from ctm.mm_cli import _cmd_trials

    args = _trials_args("--amc", "x.xml", "--no-disk", "--out", str(tmp_path / "o.json"))
    with pytest.raises(SystemExit):
        _cmd_trials(args)
    assert fake_mongo["written"] is None


def test_cmd_trials_diff_reads_new_from_the_normalized_collection(tmp_path, fake_mongo):
    """--new becomes optional: omit it and the diff reads what ctm-mm trials stored."""
    from ctm.db import NORMALIZED_COLLECTION
    from ctm.mm_cli import _cmd_trials_diff

    eligibility = {"inclusion": [], "exclusion": []}
    normalized = [
        {"entity": "amc", "protocol_no": "2015.063", "nct_id": None,
         "eligibility": eligibility, "treatment_list": {"step": []},
         "trial_hash": "1" * 64, "_raw": {},
         # Stamped by ctm-mm trials; the diff should inherit this run_date.
         "run_date": "2026-08-20", "processed_with": "ctm-mm trials 1.2.0"},
    ]
    fake_mongo["collections"] = {NORMALIZED_COLLECTION: normalized}
    fake_mongo["master"] = [
        {"entity": "amc", "protocol_no": "2015.063", "nct_id": None,
         "eligibility": eligibility, "treatment_list": {"step": []},
         "trial_hash": "0" * 64, "_raw": {}},
    ]

    args = _diff_args(out_prefix=str(tmp_path / "2026-08-20"),
                      master=None, run_date=None)
    _cmd_trials_diff(args)

    assert fake_mongo["queries"][0]["name"] == NORMALIZED_COLLECTION
    # run_date inherited from the normalized documents, not read from the clock.
    assert [d["run_date"] for d in fake_mongo["written"]["docs"]] == ["2026-08-20"]

    unchanged = json.loads((tmp_path / "2026-08-20-unchanged.json").read_text())
    assert [t["protocol_no"] for t in unchanged] == ["2015.063"]
    # Upstream provenance stripped rather than carried into the files.
    assert "processed_with" not in unchanged[0]


def test_cmd_trials_diff_empty_normalized_collection_is_an_error(tmp_path, fake_mongo, capsys):
    from ctm.db import NORMALIZED_COLLECTION
    from ctm.mm_cli import _cmd_trials_diff

    fake_mongo["collections"] = {NORMALIZED_COLLECTION: []}

    with pytest.raises(SystemExit):
        _cmd_trials_diff(_diff_args(out_prefix=str(tmp_path / "x"), master=None))
    assert "Run ctm-mm trials first" in capsys.readouterr().err


def test_cmd_trials_diff_new_file_still_wins(tmp_path, fake_mongo):
    """The file path stays available, and a nonexistent one errors rather than
    silently falling back to the collection."""
    from ctm.mm_cli import _cmd_trials_diff

    with pytest.raises(SystemExit):
        _cmd_trials_diff(_diff_args(new=str(tmp_path / "missing.json"),
                                    out_prefix=str(tmp_path / "x")))


def test_cmd_trials_stores_the_verbatim_source_records(tmp_path, fake_mongo, monkeypatch):
    """00_raw_trials keeps what each source actually said, keyed on the same
    trial_hash as its normalization so the two collections join exactly."""
    from ctm.db import NORMALIZED_COLLECTION, RAW_COLLECTION
    from ctm.mm_cli import _cmd_trials

    written = []
    monkeypatch.setattr(
        "ctm.db.replace_collection",
        lambda db, name, docs, unique_key, lookup_keys=(): written.append((name, docs)))

    amc_xml = tmp_path / "amc.xml"
    amc_xml.write_text("""<PROTOCOL_SUMMARY><PROTOCOL><NO>2021.070</NO>
      <NCT_NUMBER>NCT04858334</NCT_NUMBER><STATUS>OPEN TO ACCRUAL</STATUS>
      <TITLE>T</TITLE><ELIGIBILITY>Inclusion Criteria:
~Age &gt;= 18</ELIGIBILITY></PROTOCOL></PROTOCOL_SUMMARY>""")

    _cmd_trials(_trials_args("--amc", str(amc_xml), "--out", str(tmp_path / "o.json")))

    by_name = dict(written)
    assert RAW_COLLECTION == "00_raw_trials"
    assert set(by_name) == {RAW_COLLECTION, NORMALIZED_COLLECTION}

    raw_doc = by_name[RAW_COLLECTION][0]
    normalized_doc = by_name[NORMALIZED_COLLECTION][0]

    # The join key.
    assert raw_doc["trial_hash"] == normalized_doc["trial_hash"]
    # Verbatim source content, plus just enough to identify it without a join.
    assert raw_doc["_raw"]["protocol_no"] == "2021.070"
    assert raw_doc["entity"] == "amc"
    # Not a second copy of the normalization.
    assert "eligibility" not in raw_doc
    assert "treatment_list" not in raw_doc


def test_raw_collection_is_stage_owned():
    """A stage clears its target by dropping it, so 00_raw_trials must be declared
    machine-written or prepare_collection refuses to touch it."""
    from ctm import db as ctm_db

    assert ctm_db.RAW_COLLECTION in ctm_db.MACHINE_WRITTEN
    names = [ctm_db.RAW_COLLECTION, ctm_db.NORMALIZED_COLLECTION, ctm_db.DIFF_COLLECTION,
             ctm_db.CTML_COLLECTION, ctm_db.CURATED_COLLECTION, ctm_db.MANUAL_COLLECTION,
             ctm_db.DEFAULT_MASTER_COLLECTION]
    assert names == sorted(names), "prefixes must sort into pipeline order"
    assert [n.split("_")[0] for n in names] == ["00", "01", "02", "03", "04", "05", "06"]
