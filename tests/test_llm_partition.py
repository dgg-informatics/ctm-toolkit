"""Shared flags of the ctm-llm stages (clinical, genomic): file output,
--out/--no-disk semantics, and the clinical-only filters."""
import pytest


@pytest.mark.parametrize("subcommand", ["clinical", "genomic"])
def test_cli_disk_defaults_to_none_mongo_only(subcommand):
    """v2: no --disk flag means MongoDB only (no file) for both stages."""
    from ctm.llm_cli import build_parser

    args = build_parser().parse_args([subcommand])
    assert args.disk is None


@pytest.mark.parametrize("subcommand", ["clinical", "genomic"])
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


@pytest.mark.parametrize("subcommand", ["clinical", "genomic"])
def test_diagnosis_only_is_gone(subcommand):
    from ctm.llm_cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args([subcommand, "--diagnosis-only"])


def test_limit_and_nct_are_clinical_only():
    from ctm.llm_cli import build_parser

    args = build_parser().parse_args(["clinical", "--limit", "2", "--nct", "NCT1"])
    assert args.limit == 2 and args.nct == ["NCT1"]
    assert build_parser().parse_args(["genomic", "--kb", "kb.json"]).kb == "kb.json"


@pytest.mark.parametrize("removed", ["general", "biomarkers"])
def test_the_2_4_aliases_are_gone(removed):
    """Removed in 3.0.0: use clinical / genomic."""
    from ctm.llm_cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args([removed])
