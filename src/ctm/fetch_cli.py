"""ctm-fetch — fetch trial data from ClinicalTrials.gov or AMC's OnCORE feed.

Usage:
  ctm-fetch --nct NCT03067181 --output nct.json
  ctm-fetch --nct NCT03067181 --output nct.json --fmt-mm
  ctm-fetch --amc --output amc-normalized.json [--db NAME] [--no-store]

Exactly one source is required.

--nct output formats:
  default   RawCTGovTrial JSON (raw API fields + fetched_at timestamp), a single object
  --fmt-mm  MatchMiner CTML JSON (normalized for MatchMiner trial collection)

--amc pulls AMC's whole trial list from the OCTSU XML feed. Output is always a
JSON *list* — one normalized CTML entry per trial — ready for
`ctm-mm trials --amc`. The raw records are additionally archived to the
00_raw_trials collection of MONGO_DBNAME, so the .env MongoDB variables are
required unless --no-store is passed. Install with: uv pip install 'ctm-toolkit[db]'
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from ctm.paths import load_env


def main() -> None:
    # --amc archives raw records to Mongo, so it needs the MONGO_* variables
    load_env()

    parser = argparse.ArgumentParser(
        prog="ctm-fetch",
        description="Fetch trial data from ClinicalTrials.gov or AMC's OnCORE feed",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--nct", metavar="ID",
        help="NCT identifier (e.g. NCT03067181)",
    )
    source.add_argument(
        "--amc", action="store_true",
        help="Fetch AMC's full trial list from the OCTSU XML feed. Output is "
             "always normalized CTML JSON",
    )
    parser.add_argument(
        "--output", "-o", required=True, metavar="PATH",
        help="Output JSON file path",
    )
    parser.add_argument(
        "--fmt-mm", action="store_true", dest="fmt_mm",
        help="Output in MatchMiner CTML format instead of raw (--nct only)",
    )
    parser.add_argument(
        "--store", action=argparse.BooleanOptionalAction, default=True,
        help="Archive the raw fetched records to MongoDB's 00_raw_trials "
             "(default: enabled). --no-store writes only the JSON file (--amc only)",
    )
    parser.add_argument(
        "--db", metavar="NAME",
        help="Override MONGO_DBNAME for this run's 00_raw_trials collection (--amc only)",
    )
    parser.add_argument(
        "--run-date", dest="run_date", metavar="YYYY-MM-DD",
        help="Run date stamped on stored documents (default: today) (--amc only)",
    )
    args = parser.parse_args()

    # Silently ignoring a flag that cannot apply is how a run ends up not doing
    # what its command line says; name the mismatch instead.
    if args.amc and args.fmt_mm:
        print("Error: --fmt-mm has no effect with --amc (its output is always "
              "MatchMiner CTML)", file=sys.stderr)
        sys.exit(1)
    if not args.amc and (args.db or args.run_date or not args.store):
        print("Error: --store/--no-store, --db and --run-date apply to --amc only",
              file=sys.stderr)
        sys.exit(1)

    if args.amc:
        doc, fmt_label = _fetch_amc(args)
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


def _fetch_amc(args) -> tuple[list[dict], str]:
    """AMC's OnCORE feed → normalized CTML dicts, hashed like `ctm-mm trials`."""
    from ctm.transformers.amc_feed_to_raw import FEED_URL, fetch
    from ctm.transformers.raw_amc_to_ctml import to_ctml_dict
    from ctm.trials_lifecycle import compute_trial_hash

    print(f"Fetching AMC trial feed {FEED_URL} ...", file=sys.stderr)
    try:
        raw_trials = fetch()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"  {len(raw_trials)} AMC trial(s)", file=sys.stderr)

    # Archive before normalizing, not after: the raw records are the copy that
    # lets a past run be re-derived, so they must survive a normalization fault
    # rather than being lost to one.
    if args.store:
        _store_raw(raw_trials, args)

    trials = [to_ctml_dict(t) for t in raw_trials]
    # trials-diff expects this fingerprint on every trial; to_ctml_dict does not
    # add it, so ctm-mm trials stamps it separately and this path must match.
    for trial in trials:
        trial["trial_hash"] = compute_trial_hash(trial)

    return trials, f"{len(trials)} MatchMiner CTML trial(s)"


def _store_raw(raw_trials: list, args) -> None:
    """Archive the fetched raw records in MONGO_DBNAME's 00_raw_trials."""
    from ctm import db as ctm_db

    try:
        config = ctm_db.mongo_config()
    except ValueError as exc:
        print(f"Error: {exc} (pass --no-store to skip archiving)", file=sys.stderr)
        sys.exit(1)

    run_date = args.run_date or date.today().isoformat()
    target_db = args.db or config["dbname"]

    docs = [
        ctm_db.stamp_raw(trial.model_dump(), "ctm-fetch --amc", run_date, entity="amc")
        for trial in raw_trials
    ]
    ctm_db.replace_collection(
        ctm_db.get_database(config, target_db),
        ctm_db.RAW_COLLECTION,
        docs,
        ctm_db.RAW_UNIQUE_KEY,
        ctm_db.RAW_LOOKUP_KEYS,
    )
    print(f"Stored {len(docs)} raw doc(s) → {target_db}.{ctm_db.RAW_COLLECTION} "
          f"(run_date {run_date})", file=sys.stderr)


if __name__ == "__main__":
    main()
