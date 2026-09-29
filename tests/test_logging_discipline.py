"""Guardrails that keep the logging migration from silently unravelling.

These are cheap, whole-tree assertions rather than per-module tests: the failure
mode they guard against is a *new* call site, written months from now, that never
reaches the run log or that writes PHI into it.
"""
import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "ctm"

# Every print() that may remain in src/, and why.
PRINT_ALLOWLIST = {
    # The one genuine data channel: `ctm-mm patients` with no --out writes the
    # JSON bundle to stdout for a pipe to consume. Logging goes to stderr and the
    # file; this must stay a plain write.
    ("mm_cli.py", "json_str"),
    # Logging cannot report its own failure through logging.
    ("logging_config.py", "disabled"),
    # ctm-status's inventory is the command's output, in both forms. Output goes
    # to stdout; only diagnostics about it go through logging.
    ("status_cli.py", "_render"),
    ("status_cli.py", "json.dumps"),
}


def _print_calls():
    for path in sorted(SRC.rglob("*.py")):
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print":
                yield path, node, ast.get_source_segment(source, node) or ""


def test_no_new_prints_in_src():
    """print() bypasses the file handler entirely — a message written that way
    never reaches the run log, and under cron nothing captures stdout."""
    offenders = []
    for path, node, segment in _print_calls():
        if not any(path.name == name and token in segment
                   for name, token in PRINT_ALLOWLIST):
            offenders.append(f"{path.relative_to(SRC)}:{node.lineno}  {segment[:70]}")
    assert not offenders, (
        "use logging instead of print() (or extend PRINT_ALLOWLIST with a reason):\n"
        + "\n".join(offenders)
    )


def test_modules_do_not_configure_logging_themselves():
    """Only an entry point's main() may configure. A library module that calls
    basicConfig steals the configuration from whatever imported it."""
    offenders = [
        f"{path.relative_to(SRC)}"
        for path in SRC.rglob("*.py")
        if "logging.basicConfig" in path.read_text()
    ]
    assert not offenders, f"logging.basicConfig outside logging_config: {offenders}"


@pytest.mark.parametrize("cli", [
    "mm_cli.py", "llm_cli.py", "report_cli.py", "fetch_cli.py", "meta_cli.py",
])
def test_every_entry_point_configures_logging_and_loads_env(cli):
    """`load_env()` before `configure_logging()`: .env carries the CTM_LOG_*
    settings, so a server that puts them there must be read first.

    report_cli, fetch_cli and meta_cli did not call load_env() at all before this
    — `ctm-report --all` read MONGO_* straight from os.environ and failed for
    anyone whose values lived in .env, which is where the README puts them.
    """
    source = (SRC / cli).read_text()
    assert "configure_logging(" in source
    assert "load_env()" in source


def test_no_secret_or_phi_fields_are_formatted_into_log_calls():
    """The rule the redaction filter is a backstop for: log pt_uuid, never mrn.

    Catches the f-string shape `log.info(f"... mrn={x}")` at the call site, where
    it is cheap to fix, rather than relying on the filter at runtime.
    """
    banned = re.compile(r"(mrn|first_name|last_name|dob)\s*=\s*\{")
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if "log." in line and banned.search(line):
                offenders.append(f"{path.relative_to(SRC)}:{lineno}  {line.strip()[:70]}")
    assert not offenders, "PHI in a log call — use pt_uuid:\n" + "\n".join(offenders)
