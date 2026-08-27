"""Tests for the ctm-llm cold-cache confirmation guard."""
import sys
from pathlib import Path

import pytest

from ctm.llm_cli import _confirm_cold_cache

CACHE = Path("/tmp/does-not-matter.json")


class _Stdin:
    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_warm_cache_never_prompts(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("should not prompt"))
    _confirm_cold_cache({"trial": "cached"}, CACHE, assume_yes=False)


def test_assume_yes_skips_prompt_even_when_cold(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("should not prompt"))
    _confirm_cold_cache({}, CACHE, assume_yes=True)


def test_cold_and_non_interactive_aborts(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Stdin(tty=False))
    with pytest.raises(SystemExit):
        _confirm_cold_cache({}, CACHE, assume_yes=False)


@pytest.mark.parametrize("reply", ["y", "Y", " y ", "yes", "YES"])
def test_cold_interactive_yes_continues(monkeypatch, reply):
    monkeypatch.setattr(sys, "stdin", _Stdin(tty=True))
    monkeypatch.setattr("builtins.input", lambda *_: reply)
    _confirm_cold_cache({}, CACHE, assume_yes=False)


@pytest.mark.parametrize("reply", ["n", "", "no", "nope", "anything"])
def test_cold_interactive_non_yes_aborts(monkeypatch, reply):
    monkeypatch.setattr(sys, "stdin", _Stdin(tty=True))
    monkeypatch.setattr("builtins.input", lambda *_: reply)
    with pytest.raises(SystemExit):
        _confirm_cold_cache({}, CACHE, assume_yes=False)
