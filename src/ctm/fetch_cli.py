"""ctm-fetch — fetch a clinical trial from ClinicalTrials.gov.

Usage:
  ctm-fetch --nct NCT03067181 --output nct.json
  ctm-fetch --nct NCT03067181 --output nct.json --fmt-mm

Output formats:
  default   RawCTGovTrial JSON (raw API fields + fetched_at timestamp)
  --fmt-mm  MatchMiner CTML JSON (normalized for MatchMiner trial collection)
"""
import argparse
import json
import logging
from pathlib import Path

from ctm.logging_config import (
    add_logging_arguments,
    command_context,
    configure_logging,
    fail,
    log_event,
    verbosity_from_args,
)
from ctm.paths import load_env

log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ctm-fetch",
        description="Fetch a clinical trial from ClinicalTrials.gov",
    )
    parser.add_argument(
        "--nct", required=True, metavar="ID",
        help="NCT identifier (e.g. NCT03067181)",
    )
    parser.add_argument(
        "--output", "-o", required=True, metavar="PATH",
        help="Output JSON file path",
    )
    parser.add_argument(
        "--fmt-mm", action="store_true", dest="fmt_mm",
        help="Output in MatchMiner CTML format instead of raw",
    )
    add_logging_arguments(parser)
    args = parser.parse_args()
    load_env()
    configure_logging(verbosity=verbosity_from_args(args))

    with command_context(log, "ctm-fetch"):
        _run(args)


def _run(args) -> None:
    from ctm.transformers.ctgov_to_raw import fetch

    log.info("Fetching %s ...", args.nct)
    try:
        trial = fetch(args.nct)
    except ValueError as exc:
        fail(str(exc))

    if args.fmt_mm:
        from ctm.transformers.raw_ctgov_to_ctml import to_ctml_dict
        doc = to_ctml_dict(trial)
        fmt_label = "MatchMiner CTML"
    else:
        doc = trial.model_dump()
        fmt_label = "raw"

    out_path = Path(args.output)
    out_path.write_text(json.dumps(doc, indent=2, default=str))
    log_event(log, "trials.fetched", "Saved %s → %s", fmt_label, out_path,
              nct_id=args.nct, fmt=fmt_label, path=str(out_path))


if __name__ == "__main__":
    main()
