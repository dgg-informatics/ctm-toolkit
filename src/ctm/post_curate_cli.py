"""ctm-post-curate — DEPRECATED in 2.6.0, removed in the next release.

Forwards to ``ctm-apply-edits --file``. With no ``--curated``, the newest JSON
in ``CURATED_DIR`` is used, as before. The run database is no longer read from
the file's date prefix: ``ctm-apply-edits`` uses today's unless ``--db`` is given.
"""
import argparse
import logging
from pathlib import Path

from ctm import apply_edits_cli
from ctm.logging_config import fail
from ctm.paths import curated_dir, load_env
from ctm.pipelines import newest_file

log = logging.getLogger(__name__)


def resolve_curated(explicit: str | None) -> Path:
    """The file named, else the newest JSON in CURATED_DIR."""
    if explicit:
        return Path(explicit).expanduser()
    path = newest_file(curated_dir(), "*.json", "curated trials file")
    if path.stat().st_size == 0:
        fail("empty: %s", path)
    return path


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(
        prog="ctm-post-curate",
        description="DEPRECATED: use ctm-apply-edits --file. Forwards to it",
    )
    parser.add_argument("--curated", metavar="PATH")
    parser.add_argument("--db", metavar="NAME")
    parser.add_argument("--curated-by-user", dest="curated_by_user", metavar="NAME")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    args, rest = parser.parse_known_args()

    argv = ["--file", str(resolve_curated(args.curated))]
    if args.db:
        argv += ["--db", args.db]
    if args.curated_by_user:
        argv += ["--curated-by-user", args.curated_by_user]
    if args.dry_run:
        argv.append("--dry-run")
    log.warning("DEPRECATED: ctm-post-curate is now `ctm-apply-edits %s` and will be "
                "removed in the next release.", " ".join(argv),
                extra={"event": "cli.deprecated", "old": "ctm-post-curate",
                       "new": "ctm-apply-edits"})
    apply_edits_cli.main(argv + rest)


if __name__ == "__main__":
    main()
