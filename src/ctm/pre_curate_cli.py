"""ctm-pre-curate — DEPRECATED in 2.6.0, removed in the next release.

Forwards to ``ctm-pipeline --end 04``: the old weekly refresh up to the curator
handoff. Nobody stands at 04 any more; the cron line is now
``ctm-pipeline --end 07_filtered_trials`` (see ``deploy/cron.d/ctm``).
"""
import argparse
import logging

from ctm import pipeline_cli
from ctm.paths import load_env

log = logging.getLogger(__name__)


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(
        prog="ctm-pre-curate",
        description="DEPRECATED: use ctm-pipeline. Forwards to ctm-pipeline --end 04",
    )
    parser.add_argument("--sources", nargs="+", metavar="FLAG")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    args, rest = parser.parse_known_args()

    argv = ["--end", "04"]
    if args.sources:
        argv += ["--sources", *args.sources]
    if args.yes:
        argv.append("--yes")
    if args.dry_run:
        argv.append("--dry-run")
    log.warning("DEPRECATED: ctm-pre-curate is now `ctm-pipeline %s` and will be "
                "removed in the next release. Weekly cron: ctm-pipeline --end "
                "07_filtered_trials", " ".join(argv),
                extra={"event": "cli.deprecated", "old": "ctm-pre-curate",
                       "new": "ctm-pipeline"})
    pipeline_cli.main(argv + rest)


if __name__ == "__main__":
    main()
