"""Tests for the ctm-llm partition.

The LLM work is split by what the model is asked to do, not by the old CLI
boundary: `general` owns every match-node suggestion (per criterion *and* from the
title, which share a prompt and an output shape), `biomarkers` owns the biomarker
scan. Each writes exactly one key under _llm_curation and merges into whatever is
already there, so neither can destroy the other's work.
"""
from types import SimpleNamespace

import pytest


class _QueuedClient:
    """Returns queued chat-completion responses in call order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.call_count = 0
        self.prompts = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.call_count += 1
        self.prompts.append(kwargs["messages"][1]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=self._responses.pop(0))
        )])


def _trial(**extra):
    return {
        "nct_id": "NCT00000001",
        "protocol_no": None,
        "entity": "west",
        "eligibility": {
            "inclusion": [{"text": "Age >= 18", "sub_criteria": []}],
            "exclusion": [{"text": "No brain metastases", "sub_criteria": []}],
        },
        "_summary": {"long_title": "A Study of Olaparib in Patients with a BRCA1 Mutation"},
        **extra,
    }


def test_draft_trial_covers_criteria_and_the_title():
    """Three calls: two criteria plus the title, all through the same prompt."""
    from ctm.transformers.eligibility_to_ctml import draft_trial

    client = _QueuedClient([
        '{"clinical": {"age_numerical": ">=18"}}',
        'null',
        '{"genomic": {"hugo_symbol": "BRCA1"}}',
    ])

    result = draft_trial(_trial(), cache={}, client=client, valid_oncotree=set())
    suggestions = result["_llm_curation"]["_ctml_suggestions"]

    assert client.call_count == 3
    assert [s["source"] for s in suggestions] == ["inclusion", "exclusion", "summary"]


def test_draft_trial_writes_under_llm_curation_not_top_level():
    """The old shape put _ctml_suggestions at the top level for a later stage to
    relocate. `general` writes its final home directly."""
    from ctm.transformers.eligibility_to_ctml import draft_trial

    client = _QueuedClient(['null', 'null', 'null'])
    result = draft_trial(_trial(), cache={}, client=client, valid_oncotree=set())

    assert "_ctml_suggestions" not in result
    assert "_ctml_suggestions" in result["_llm_curation"]


def test_draft_trial_drops_a_legacy_top_level_key():
    from ctm.transformers.eligibility_to_ctml import draft_trial

    client = _QueuedClient(['null', 'null', 'null'])
    trial = _trial(_ctml_suggestions=[{"source": "inclusion", "suggested_node": None}])

    result = draft_trial(trial, cache={}, client=client, valid_oncotree=set())

    assert "_ctml_suggestions" not in result


def test_draft_trial_preserves_existing_biomarker_references():
    """Running `general` after `biomarkers` must not discard the other stage's key.
    Order-dependence is the intended workflow, but merging is what makes a re-run
    of either stage non-destructive."""
    from ctm.transformers.eligibility_to_ctml import draft_trial

    client = _QueuedClient(['null', 'null', 'null'])
    trial = _trial(_llm_curation={
        "biomarker_references": [{"biomarker": "BRCA1", "type": "snv"}],
    })

    result = draft_trial(trial, cache={}, client=client, valid_oncotree=set())

    assert result["_llm_curation"]["biomarker_references"][0]["biomarker"] == "BRCA1"
    assert "_ctml_suggestions" in result["_llm_curation"]


def test_draft_trial_does_not_mutate_its_input():
    from ctm.transformers.eligibility_to_ctml import draft_trial

    trial = _trial()
    client = _QueuedClient(['null', 'null', 'null'])
    draft_trial(trial, cache={}, client=client, valid_oncotree=set())

    assert "_llm_curation" not in trial


def test_title_suggestion_absent_without_a_long_title():
    from ctm.transformers.eligibility_to_ctml import title_suggestion

    client = _QueuedClient([])  # a call would raise IndexError
    assert title_suggestion({"_summary": {}}, {}, client, set()) is None
    assert title_suggestion({}, {}, client, set()) is None
    assert client.call_count == 0


def test_title_suggestion_is_labelled_summary():
    from ctm.transformers.eligibility_to_ctml import title_suggestion

    client = _QueuedClient(['{"genomic": {"hugo_symbol": "BRCA1"}}'])
    result = title_suggestion(_trial(), {}, client, set())

    assert result["source"] == "summary"
    assert result["text"] == "A Study of Olaparib in Patients with a BRCA1 Mutation"
    assert result["transferred_to_match"] is False


def test_final_suggested_ctml_is_gone():
    """Deleted deliberately: the suggestion was not useful, and it was the only
    field in _llm_curation computed from another stage's output."""
    from ctm.transformers import trials_curate
    from ctm.transformers.eligibility_to_ctml import draft_trial

    assert not hasattr(trials_curate, "union_match_nodes")
    assert not hasattr(trials_curate, "curate_trial")

    client = _QueuedClient(['null', 'null', 'null'])
    result = draft_trial(_trial(), cache={}, client=client, valid_oncotree=set())
    assert "final_suggested_ctml" not in result["_llm_curation"]


def test_the_two_stages_compose_without_clobbering_each_other():
    """The end-to-end property the partition exists to guarantee."""
    from ctm.transformers.eligibility_to_ctml import draft_trial
    from ctm.transformers.trials_curate import annotate_biomarkers

    drafted = draft_trial(
        _trial(), cache={},
        client=_QueuedClient(['{"clinical": {"age_numerical": ">=18"}}', 'null', 'null']),
        valid_oncotree=set(),
    )
    annotated = annotate_biomarkers(
        drafted,
        _QueuedClient(['[{"biomarker": "BRCA1", "type": "snv", "reference": "BRCA1"}]']),
        cache={}, known_genes={"BRCA1"},
    )

    curation = annotated["_llm_curation"]
    assert len(curation["_ctml_suggestions"]) == 3
    assert len(curation["biomarker_references"]) == 1


@pytest.mark.parametrize("subcommand", ["general", "biomarkers"])
def test_cli_requires_out_unless_no_disk(subcommand, capsys):
    """Disk stays the default through 1.x, so --out is effectively required."""
    from ctm.llm_cli import build_parser

    args = build_parser().parse_args([subcommand])
    assert args.disk is True

    from ctm.llm_cli import _check_disk_args

    with pytest.raises(SystemExit):
        _check_disk_args(args)
    assert "--out is required" in capsys.readouterr().err


@pytest.mark.parametrize("subcommand", ["general", "biomarkers"])
def test_cli_rejects_out_with_no_disk(subcommand, capsys):
    from ctm.llm_cli import _check_disk_args, build_parser

    args = build_parser().parse_args([subcommand, "--no-disk", "--out", "x.json"])

    with pytest.raises(SystemExit):
        _check_disk_args(args)
    assert "no effect with --no-disk" in capsys.readouterr().err


def test_ctm_ctml_forwards_to_general(monkeypatch):
    """The deprecated alias must reach the same code path as a real invocation."""
    import sys

    from ctm import ctml_cli

    seen = {}
    monkeypatch.setattr("ctm.llm_cli.main", lambda argv: seen.setdefault("argv", argv))
    monkeypatch.setattr(sys, "argv", ["ctm-ctml", "--out", "o.json", "--limit", "2"])

    ctml_cli.main()

    assert seen["argv"] == ["general", "--out", "o.json", "--limit", "2"]
