"""Locate the AMC trial export in the SharePoint daily dump → RawAMCTrial models.

AMC drops a fresh export into a SharePoint library once a day. This module's
job is *finding* that file; :mod:`amc_xml_to_raw` parses whatever it is handed.

Access route is deliberately isolated in :func:`_candidate_files`. Today it
globs a locally synced copy of the library (OneDrive/SharePoint sync client),
which needs no credentials and no HTTP. If IT requires Microsoft Graph instead,
that one function changes to download into ``cache_dir()`` and return the
resulting path — everything downstream is unaffected.

The newest matching file wins. That is the whole selection rule: a daily dump
accumulates, and the operator wants today's. The chosen file and its
modification time are printed to stderr, because with "newest wins" a stale
sync is otherwise completely invisible.
"""
import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

from ..paths import cache_path
from ..schemas.raw.models import RawAMCTrial
from .amc_xml_to_raw import load

_DEFAULT_GLOB = "*.xml"


def export_dir() -> Path:
    """The synced SharePoint directory, from ``AMC_EXPORT_DIR``.

    Fails fast and by name, matching how ``mongo_config()`` and
    ``build_client()`` report missing configuration.
    """
    raw = os.environ.get("AMC_EXPORT_DIR")
    if not raw:
        raise ValueError(
            "AMC_EXPORT_DIR not set — point it at the locally synced SharePoint "
            "folder holding AMC's daily trial export (see .env.example)"
        )
    return Path(raw).expanduser()


def export_glob() -> str:
    """Filename pattern for the dump. ``AMC_EXPORT_GLOB``, default ``*.xml``."""
    return os.environ.get("AMC_EXPORT_GLOB") or _DEFAULT_GLOB


def _candidate_files() -> list[Path]:
    """Every file in the dump matching the configured pattern.

    THE ACCESS SEAM. To switch to the Microsoft Graph API, replace the body
    with a download into ``cache_dir()`` and return the downloaded path(s);
    the caller only needs local paths back.
    """
    directory = export_dir()
    if not directory.is_dir():
        raise ValueError(f"AMC_EXPORT_DIR is not a directory: {directory}")
    return [p for p in directory.glob(export_glob()) if p.is_file()]


def _newest(paths: list[Path]) -> Path:
    """Most recently modified path."""
    return max(paths, key=lambda p: p.stat().st_mtime)


def _snapshot(source: Path) -> Path:
    """Copy *source* into the cache, returning the copy.

    A sync client overwrites or removes yesterday's dump, so the file that
    produced a given normalization would otherwise be unrecoverable. The
    snapshot is what makes a past run reproducible.
    """
    destination = cache_path(f"amc-export-{date.today().isoformat()}{source.suffix}")
    shutil.copy2(source, destination)
    return destination


def fetch() -> list[RawAMCTrial]:
    """Newest export from the dump, snapshotted and parsed.

    Raises:
        ValueError: AMC_EXPORT_DIR unset/not a directory, or nothing matched.
    """
    candidates = _candidate_files()
    if not candidates:
        # An empty result must not look like "AMC has no trials" — that would
        # reach trials-diff and mark every AMC trial deleted.
        raise ValueError(
            f"no files matching {export_glob()!r} in {export_dir()} — "
            "check the SharePoint sync has completed"
        )

    source = _newest(candidates)
    modified = datetime.fromtimestamp(source.stat().st_mtime)
    print(f"AMC export: {source.name} (modified {modified:%Y-%m-%d %H:%M})", file=sys.stderr)
    if len(candidates) > 1:
        print(f"  newest of {len(candidates)} matching file(s)", file=sys.stderr)

    snapshot = _snapshot(source)
    print(f"  snapshot → {snapshot}", file=sys.stderr)

    return load(snapshot)
