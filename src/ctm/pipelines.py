"""Multi-stage pipelines, as entry points rather than shell wrappers.

``ctm-pre-curate`` and ``ctm-post-curate`` used to be bash in ``/usr/local/bin``.
Moving them here is not only about a shorter cron line:

* They are **versioned and deployable**. They ship in the wheel, deploy with the
  release and roll back with the symlink. An untracked wrapper on a server has
  no rollback at all, and drifts from the repo it drives.
* They are **testable**. Both bugs these wrappers actually produced were
  shell-shaped — ``$!`` not being set by a process substitution, and ``set -u``
  firing on an argument the script never defined.
* The ``set -E``/``errtrace`` class of mistake, where a failing stage inside a
  shell function skips the ERR trap and so never sends its alert, cannot happen.

Stop-at-first-failure, per-stage logging and a non-zero exit are what the shell
versions provided; :func:`run_stages` provides them here. The exit status is
load-bearing under cron — it is what turns a failed run into mail.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ctm.logging_config import fail, log_event

log = logging.getLogger(__name__)


@dataclass
class Stage:
    """One step of a pipeline: a label for the log, and the thing to run."""

    label: str
    run: Callable[[], int | None]


def run_stages(stages: list[Stage], dry_run: bool = False) -> int:
    """Run each stage in order, stopping at the first failure.

    Returns the failing stage's exit code, or 0. Stopping rather than continuing
    is deliberate: every stage here consumes the previous one's output, so
    carrying on past a failure produces a master built from half a run.
    """
    for number, stage in enumerate(stages, start=1):
        if dry_run:
            log.warning("dry run [%d/%d] %s", number, len(stages), stage.label)
            continue

        log_event(log, "stage.begin", "[%d/%d] %s", number, len(stages), stage.label,
                  stage=stage.label, position=number, total=len(stages))
        code = stage.run()
        if code:
            # Named explicitly: under cron the console line is all that reaches
            # the mail, and "stage 3 of 4 failed" is the first thing to know.
            log_event(log, "stage.failed", "%s failed (exit %d)", stage.label, code,
                      level=logging.ERROR, stage=stage.label, exit_code=code)
            return code
        log_event(log, "stage.ok", "[%d/%d] %s — ok", number, len(stages), stage.label,
                  stage=stage.label, position=number, total=len(stages))
    return 0


def newest_file(directory: Path, pattern: str, description: str) -> Path:
    """The most recently modified match for ``pattern``, or a clean failure.

    ``~$``-prefixed files are skipped: Excel creates one beside any workbook
    opened over SMB, it is newer than the file it locks, and it disappears when
    the file is closed — so it would intermittently win an mtime sort.
    """
    candidates = sorted(
        (p for p in directory.glob(pattern) if not p.name.startswith("~$")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        fail("no %s in %s — drop one there, or name it explicitly",
             description, directory)
    if len(candidates) > 1:
        log.info("  %d candidates in %s; using the newest", len(candidates), directory)
    return candidates[0]
