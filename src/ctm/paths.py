"""Canonical locations for packaged reference data and per-user caches.

Two different kinds of path live here, and the distinction matters:

* Reference data ships *inside* the package, so an installed wheel works without
  a source checkout. Read-only; anchored to ``ctm/``.
* LLM response caches are per-user, machine-generated, and can reach hundreds of
  MB. They belong in the user's cache directory, never in the repo or the
  package. Honours ``CTM_CACHE_DIR``, then ``XDG_CACHE_HOME``, then
  ``~/.cache`` — so Linux, macOS, and containers all land somewhere writable.
"""
import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).parent

REFS_DIR = PACKAGE_DIR / "refs"
DEFAULT_KB_PATH = REFS_DIR / "gene_variant_descriptions_v2.json"


def cache_dir() -> Path:
    """Directory for ctm's caches. Created on demand by :func:`cache_path`."""
    if override := os.environ.get("CTM_CACHE_DIR"):
        return Path(override).expanduser()
    if xdg := os.environ.get("XDG_CACHE_HOME"):
        return Path(xdg).expanduser() / "ctm"
    return Path.home() / ".cache" / "ctm"


def cache_path(name: str) -> Path:
    """Absolute path for cache file ``name``, ensuring its parent exists."""
    path = cache_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
