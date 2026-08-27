"""Canonical locations for packaged reference data and per-user caches.

Two different kinds of path live here, and the distinction matters:

* Reference data ships *inside* the package, so an installed wheel works without
  a source checkout. Read-only; anchored to ``ctm/``.
* LLM response caches are machine-generated and can reach hundreds of MB. They
  live outside the repo/package. Resolution: ``CTM_CACHE_DIR`` (explicit
  override, also settable via ``.env``) → the shared ``/var/lib/ctm/cache`` when
  it exists → ``XDG_CACHE_HOME/ctm`` → ``~/.cache/ctm``. The shared default lets
  several server accounts share one warm cache; a workstation or container
  without that directory falls back to a per-user location.
"""
import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

PACKAGE_DIR = Path(__file__).parent

REFS_DIR = PACKAGE_DIR / "refs"
DEFAULT_KB_PATH = REFS_DIR / "gene_variant_descriptions_v2.json"


def load_env() -> Path | None:
    """Load ``.env`` from the working directory, returning the file used.

    ``load_dotenv()`` with no arguments searches upward from the *calling
    module's* directory. For an installed (non-editable) package that is
    site-packages, so a ``.env`` sitting next to the user's data is never found
    and the CLI fails with "UMGPT_API_KEY not set" despite the file being right
    there. ``usecwd=True`` searches from where the command was actually run,
    which is the behaviour the README documents.

    Existing environment variables win, so exporting a value or using a wrapper
    script still overrides the file.
    """
    found = find_dotenv(usecwd=True)
    if not found:
        return None
    load_dotenv(found)
    return Path(found)


# Shared server cache: several accounts read/write one warm cache when this
# directory exists (an admin creates it group-writable). A module constant so it
# stays overridable in tests.
DEFAULT_SHARED_CACHE = Path("/var/lib/ctm/cache")


def cache_dir() -> Path:
    """Directory for ctm's caches. Created on demand by :func:`cache_path`.

    Order: ``CTM_CACHE_DIR`` override → the shared ``/var/lib/ctm/cache`` when it
    exists → ``XDG_CACHE_HOME/ctm`` → ``~/.cache/ctm``.
    """
    if override := os.environ.get("CTM_CACHE_DIR"):
        return Path(override).expanduser()
    if DEFAULT_SHARED_CACHE.is_dir():
        return DEFAULT_SHARED_CACHE
    if xdg := os.environ.get("XDG_CACHE_HOME"):
        return Path(xdg).expanduser() / "ctm"
    return Path.home() / ".cache" / "ctm"


def cache_path(name: str) -> Path:
    """Absolute path for cache file ``name``, ensuring its parent exists."""
    path = cache_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
