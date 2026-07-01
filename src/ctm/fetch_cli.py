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
import sys
from pathlib import Path


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
    args = parser.parse_args()

    from ctm.transformers.ctgov_to_raw import fetch

    print(f"Fetching {args.nct} ...", file=sys.stderr)
    try:
        trial = fetch(args.nct)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.fmt_mm:
        from ctm.transformers.raw_ctgov_to_ctml import to_ctml_dict
        doc = to_ctml_dict(trial)
        fmt_label = "MatchMiner CTML"
    else:
        doc = trial.model_dump()
        fmt_label = "raw"

    out_path = Path(args.output)
    out_path.write_text(json.dumps(doc, indent=2, default=str))
    print(f"Saved {fmt_label} → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
