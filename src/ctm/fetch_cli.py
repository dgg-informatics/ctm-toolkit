"""ctm-fetch — fetch trial data from ClinicalTrials.gov or AMC's SharePoint dump.

Usage:
  ctm-fetch --nct NCT03067181 --output nct.json
  ctm-fetch --nct NCT03067181 --output nct.json --fmt-mm
  ctm-fetch --amc --output amc-normalized.json

Exactly one source is required.

--nct output formats:
  default   RawCTGovTrial JSON (raw API fields + fetched_at timestamp), a single object
  --fmt-mm  MatchMiner CTML JSON (normalized for MatchMiner trial collection)

--amc output is always a JSON *list* — one normalized CTML entry per trial in
the export — ready for `ctm-mm trials --amc`. Requires AMC_EXPORT_DIR in .env.
"""
import argparse
import json
import sys
from pathlib import Path

from ctm.paths import load_env


def main() -> None:
    # --amc reads AMC_EXPORT_DIR / AMC_EXPORT_GLOB
    load_env()

    parser = argparse.ArgumentParser(
        prog="ctm-fetch",
        description="Fetch trial data from ClinicalTrials.gov or AMC's SharePoint dump",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--nct", metavar="ID",
        help="NCT identifier (e.g. NCT03067181)",
    )
    source.add_argument(
        "--amc", action="store_true",
        help="Read the newest AMC trial export from the synced SharePoint folder "
             "named by AMC_EXPORT_DIR. Output is always normalized CTML JSON",
    )
    parser.add_argument(
        "--output", "-o", required=True, metavar="PATH",
        help="Output JSON file path",
    )
    parser.add_argument(
        "--fmt-mm", action="store_true", dest="fmt_mm",
        help="Output in MatchMiner CTML format instead of raw (--nct only)",
    )
    args = parser.parse_args()

    if args.amc and args.fmt_mm:
        print("Error: --fmt-mm has no effect with --amc (its output is always "
              "MatchMiner CTML)", file=sys.stderr)
        sys.exit(1)

    if args.amc:
        doc, fmt_label = _fetch_amc()
    else:
        doc, fmt_label = _fetch_nct(args.nct, args.fmt_mm)

    out_path = Path(args.output)
    out_path.write_text(json.dumps(doc, indent=2, default=str))
    print(f"Saved {fmt_label} → {out_path}", file=sys.stderr)


def _fetch_nct(nct_id: str, fmt_mm: bool) -> tuple[dict, str]:
    from ctm.transformers.ctgov_to_raw import fetch

    print(f"Fetching {nct_id} ...", file=sys.stderr)
    try:
        trial = fetch(nct_id)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if fmt_mm:
        from ctm.transformers.raw_ctgov_to_ctml import to_ctml_dict
        return to_ctml_dict(trial), "MatchMiner CTML"
    return trial.model_dump(), "raw"


def _fetch_amc() -> tuple[list[dict], str]:
    """Newest SharePoint export → normalized CTML dicts, hashed like `ctm-mm trials`."""
    from ctm.transformers.amc_sharepoint_to_raw import fetch
    from ctm.transformers.raw_amc_to_ctml import to_ctml_dict
    from ctm.trials_lifecycle import compute_trial_hash

    try:
        raw_trials = fetch()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"  {len(raw_trials)} AMC trial(s)", file=sys.stderr)

    trials = [to_ctml_dict(t) for t in raw_trials]
    # trials-diff expects this fingerprint on every trial; to_ctml_dict does not
    # add it, so ctm-mm trials stamps it separately and this path must match.
    for trial in trials:
        trial["trial_hash"] = compute_trial_hash(trial)

    return trials, f"{len(trials)} MatchMiner CTML trial(s)"


if __name__ == "__main__":
    main()
