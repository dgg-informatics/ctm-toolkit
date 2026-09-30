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
    """Load the ``.env`` for this run, returning the file used.

    ``CTM_ENV_FILE`` names one explicitly; otherwise the working directory is
    searched upward.

    The explicit form is what lets a scheduled command find a server-wide
    configuration file. The search cannot: ``find_dotenv`` walks up from the
    working directory, and cron runs with a home directory as cwd, so
    ``/etc/ctm/.env`` is never on the path. That job used to belong to a bash
    wrapper doing ``set -a; . /etc/ctm/.env; set +a`` before invoking the CLI —
    naming the file instead keeps the wrappers out of it.

    ``load_dotenv()`` with no arguments searches from the *calling module's*
    directory, which for an installed package is site-packages; ``usecwd=True``
    searches from where the command was actually run, which is the behaviour the
    README documents.

    Existing environment variables win in both cases, so exporting a value still
    overrides the file.
    """
    explicit = os.environ.get("CTM_ENV_FILE", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            # Loud, because the alternative is every variable silently missing
            # and each command failing separately on whichever it needed first.
            raise ValueError(f"CTM_ENV_FILE={explicit} is not a file")
        load_dotenv(path)
        return path

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


def llm_biomarker_export_dir() -> Path:
    """Directory `ctm-llm biomarkers` writes its to-curate JSON into by default.
    Override with ``LLM_BIOMARKER_EXPORT_DIR`` (an empty value is treated as unset,
    so a blank env var can't resolve to ``Path("")`` = the current directory)."""
    return Path(
        os.environ.get("LLM_BIOMARKER_EXPORT_DIR") or "/var/lib/ctm/to-curate"
    ).expanduser()


def master_trial_export_dir() -> Path:
    """Directory `ctm-mm trials-merge` writes the master backup JSON into by
    default. Override with ``MASTER_TRIAL_EXPORT_DIR`` (an empty value is treated
    as unset, not as the current directory)."""
    return Path(
        os.environ.get("MASTER_TRIAL_EXPORT_DIR") or "/var/lib/ctm/trials"
    ).expanduser()


def report_export_dir() -> Path:
    """Directory `ctm-report` writes per-patient PDFs into by default.
    Override with ``REPORT_EXPORT_DIR`` (an empty value is treated as unset, not
    as the current directory)."""
    return Path(
        os.environ.get("REPORT_EXPORT_DIR") or "/var/lib/ctm/reports"
    ).expanduser()


def patient_raw_dir() -> Path:
    """Directory patient workbooks are dropped into, and where a bare
    ``ctm-mm patients`` looks for the newest one.

    Override with ``PATIENT_RAW_DIR`` (an empty value is treated as unset, not as
    the current directory). Inputs live here; the normalized JSON the pipeline
    derives from them goes to :func:`patient_export_dir` — separating the two
    keeps "what has been ingested?" answerable by comparing the newest file in
    each, and keeps Excel's ``~$``-prefixed lock files out of the output set.
    """
    return Path(
        os.environ.get("PATIENT_RAW_DIR") or "/var/lib/ctm/patients"
    ).expanduser()


def patient_export_dir() -> Path:
    """Directory `ctm-mm patients` writes its normalized bundle into by default.

    Override with ``PATIENT_EXPORT_DIR`` (an empty value is treated as unset).
    The default sits under :func:`patient_raw_dir` so everything patient-shaped
    stays in one tree with one set of permissions — this content is PHI, unlike
    the trial exports.

    A disk copy is the point: the bundle is the lossless record of a workbook,
    so a database that is dropped or re-loaded can always be rebuilt from it.
    """
    return Path(
        os.environ.get("PATIENT_EXPORT_DIR") or "/var/lib/ctm/patients/normalized"
    ).expanduser()


def curated_dir() -> Path:
    """Directory a curator drops the hand-curated trials file into, and where a
    bare ``ctm-post-curate`` looks for the newest one.

    Override with ``CURATED_DIR`` (an empty value is treated as unset). The
    counterpart to :func:`llm_biomarker_export_dir`: the pipeline writes
    ``to-curate/``, a human reads it, edits it, and drops the result here. Those
    are two directories rather than one file edited in place so that "has this
    week been curated?" is answerable by looking, and so an interrupted edit
    cannot be mistaken for finished work.
    """
    return Path(
        os.environ.get("CURATED_DIR") or "/var/lib/ctm/curated"
    ).expanduser()


def match_export_dir() -> Path:
    """Directory `ctm-match` writes the dated ``trial_match`` export into.

    Override with ``MATCH_EXPORT_DIR`` (an empty value is treated as unset).

    This is what makes the ``<date>_match`` databases disposable: the matches
    themselves are kept on disk indefinitely, so Mongo only has to hold however
    many recent runs are convenient.
    """
    return Path(
        os.environ.get("MATCH_EXPORT_DIR") or "/var/lib/ctm/matches"
    ).expanduser()


def west_trials_path() -> Path:
    """Default UMH-West trials workbook, read when ``--west`` is passed bare.

    Override with ``WEST_TRIALS_PATH`` (an empty value is treated as unset, not as
    ``Path("")``). A stable filename is deliberate — the file is replaced in place
    rather than versioned, and which version fed a run is recorded from its mtime.
    """
    return Path(
        os.environ.get("WEST_TRIALS_PATH")
        or "/var/lib/ctm/sources/trials-west-latest.xlsx"
    ).expanduser()


def cache_path(name: str) -> Path:
    """Absolute path for cache file ``name``, ensuring its parent exists."""
    path = cache_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
