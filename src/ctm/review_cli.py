"""ctm-review — review one trial's match clause in a browser, and log every change.

Runs on the server, as the account that runs the pipeline, and listens on
127.0.0.1 only. From your laptop:

    ssh -L 8765:localhost:8765 <server>     then open http://localhost:8765

Search an NCT ID (or AMC protocol number), then on each leaf of
treatment_list.step[].match: ✓ verify, ✗ delete, ✎ edit, or + add a leaf under an
and/or. Actions are staged in the page; Apply sends them all through
ctm-apply-edits (05 → 06 → 07) and logs each one to curation_reviews. Staged actions
that were never applied are gone when the page reloads or the server stops.

Usage:
  ctm-review
  ctm-review --reviewer dane --port 8800
"""
import argparse
import logging
import os

from ctm.logging_config import (
    add_logging_arguments,
    command_context,
    configure_logging,
    verbosity_from_args,
)
from ctm.paths import load_env

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctm-review",
        description="Review a trial's match clause in a browser (verify / edit / delete / add), "
                    "apply through ctm-apply-edits, and log each change to curation_reviews",
    )
    parser.add_argument("--port", type=int, default=8765,
                        help="Port on 127.0.0.1 (default: 8765)")
    parser.add_argument("--reviewer", metavar="NAME",
                        help="Recorded as the reviewer and editor (default: $SUDO_USER, then $USER). "
                             "Set it when running as the service account")
    parser.add_argument("--db", metavar="NAME",
                        help="Run database for 05, passed to ctm-apply-edits (default: today)")
    add_logging_arguments(parser)
    return parser


def main(argv: list[str] | None = None) -> None:
    load_env()
    args = build_parser().parse_args(argv)
    configure_logging(verbosity=verbosity_from_args(args))

    with command_context(log, "ctm-review"):
        from ctm import db as ctm_db
        from ctm.review_server import REVIEWS_COLLECTION, ReviewContext, serve

        reviewer = (args.reviewer or os.environ.get("SUDO_USER")
                    or os.environ.get("USER") or "unknown")
        config = ctm_db.mongo_config(require_master=True, require_dbname=False)
        master_db = ctm_db.get_database(config, config["master_dbname"])
        ctx = ReviewContext(master=master_db[config["master_collection"]],
                            reviews=master_db[REVIEWS_COLLECTION],
                            reviewer=reviewer, db=args.db)
        serve(ctx, args.port)


if __name__ == "__main__":
    main()
