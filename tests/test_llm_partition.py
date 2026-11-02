"""Tests for the ctm-llm partition.

The LLM work is split by what the model is asked to do, not by the old CLI
boundary: `general` owns every match-node suggestion (per criterion *and* from the
title, which share a prompt and an output shape), `biomarkers` owns the biomarker
scan. Each writes exactly one key under _llm_curation and merges into whatever is
already there, so neither can destroy the other's work.
"""
import pytest


@pytest.mark.parametrize("subcommand", ["general", "biomarkers"])
def test_cli_disk_defaults_to_none_mongo_only(subcommand):
    """v2: no --disk flag means MongoDB only (no file) for both stages."""
    from ctm.llm_cli import build_parser

    args = build_parser().parse_args([subcommand])
    assert args.disk is None


@pytest.mark.parametrize("subcommand", ["general", "biomarkers"])
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


def test_ctm_ctml_forwards_to_general(monkeypatch):
    """The deprecated alias must reach the same code path as a real invocation."""
    import sys

    from ctm import ctml_cli

    seen = {}
    monkeypatch.setattr("ctm.llm_cli.main", lambda argv: seen.setdefault("argv", argv))
    monkeypatch.setattr(sys, "argv", ["ctm-ctml", "--out", "o.json", "--limit", "2"])

    ctml_cli.main()

    assert seen["argv"] == ["general", "--out", "o.json", "--limit", "2"]
