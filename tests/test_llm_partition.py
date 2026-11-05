"""Tests for the ctm-llm partition.

The LLM work is split by what the model is asked to do, not by the old CLI
boundary: `general` owns every match-node suggestion (per criterion *and* from the
title, which share a prompt and an output shape), `biomarkers` owns the biomarker
scan. Each writes exactly one key under _llm_curation and merges into whatever is
already there, so neither can destroy the other's work.
"""
import pytest


@pytest.mark.parametrize("subcommand", ["clinical", "genomic", "general", "biomarkers"])
def test_cli_disk_defaults_to_none_mongo_only(subcommand):
    """v2: no --disk flag means MongoDB only (no file) for both stages."""
    from ctm.llm_cli import build_parser

    args = build_parser().parse_args([subcommand])
    assert args.disk is None


@pytest.mark.parametrize("subcommand", ["clinical", "genomic", "general", "biomarkers"])
def test_resolve_out_semantics(subcommand):
    """--out wins; --no-disk forces MongoDB-only; otherwise the stage default."""
    from pathlib import Path

    from ctm.llm_cli import _resolve_out, build_parser

    canonical = Path("/exports/canonical.json")

    # no flags → the stage's default (None for general, canonical for biomarkers)
    args = build_parser().parse_args([subcommand])
    assert _resolve_out(args, canonical) == canonical
    assert _resolve_out(args, None) is None

    # --out overrides whatever the default is
    args = build_parser().parse_args([subcommand, "--out", "x.json"])
    assert _resolve_out(args, canonical) == Path("x.json")

    # --no-disk suppresses even a canonical export
    args = build_parser().parse_args([subcommand, "--no-disk"])
    assert _resolve_out(args, canonical) is None


def test_ctm_ctml_forwards_to_clinical(monkeypatch):
    """The deprecated alias must reach the same code path as a real invocation."""
    import sys

    from ctm import ctml_cli

    seen = {}
    monkeypatch.setattr("ctm.llm_cli.main", lambda argv: seen.setdefault("argv", argv))
    monkeypatch.setattr(sys, "argv", ["ctm-ctml", "--out", "o.json", "--limit", "2"])

    ctml_cli.main()

    assert seen["argv"] == ["clinical", "--out", "o.json", "--limit", "2"]


@pytest.mark.parametrize("subcommand", ["clinical", "genomic", "general", "biomarkers"])
def test_diagnosis_only_is_gone(subcommand):
    from ctm.llm_cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args([subcommand, "--diagnosis-only"])


def test_limit_and_nct_are_clinical_only():
    from ctm.llm_cli import build_parser

    args = build_parser().parse_args(["general", "--limit", "2", "--nct", "NCT1"])
    assert args.limit == 2 and args.nct == ["NCT1"]
    assert build_parser().parse_args(["biomarkers", "--kb", "kb.json"]).kb == "kb.json"


@pytest.mark.parametrize("old,new,handler", [("general", "clinical", "_cmd_clinical"),
                                             ("biomarkers", "genomic", "_cmd_genomic")])
def test_alias_logs_deprecation_and_runs_new_handler(monkeypatch, caplog, old, new, handler):
    import logging

    from ctm import llm_cli

    ran = []
    monkeypatch.setattr(llm_cli, "load_env", lambda: None)
    monkeypatch.setattr(llm_cli, "_cmd_clinical", lambda args: ran.append("_cmd_clinical"))
    monkeypatch.setattr(llm_cli, "_cmd_genomic", lambda args: ran.append("_cmd_genomic"))
    with caplog.at_level(logging.WARNING, logger="ctm.llm_cli"):
        llm_cli.main([old])
    assert ran == [handler]
    assert (f"DEPRECATED: ctm-llm {old} is now ctm-llm {new} and will be removed in 3.0.0."
            in caplog.text)
