"""ctm-ctml — LLM-assisted CTML match node draft generator.

Usage:
  ctm-ctml --out amc-ctml-draft.json [--trials JSON] [--limit N]

Reads the trials that need curation, sends each eligibility criterion to UMGPT,
and attaches suggested CTML match nodes to each trial under _ctml_suggestions.

Input comes from the 02_diff_trials collection — only the documents routed
`diff_status: "changed"`, which are the ones whose eligibility actually moved.
`--trials JSON` reads a file instead (the output of ctm-mm trials, or a
trials-diff *-changed.json). Output is stored in 03_ctml_drafted_trials and, while
--disk stays the default, also written to --out.

Results are cached in ~/.cache/ctm/.ctml_cache.json — unchanged criteria are free.
Override the location with --cache, or point CTM_CACHE_DIR at another directory.

Requires in .env:
  UMGPT_API_KEY=...
  UMGPT_BASE_URL=https://...
  UMGPT_MODEL=gpt-4o   (optional, defaults to gpt-4o)
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from ctm.paths import cache_dir, cache_path, load_env

_DEFAULT_CACHE = ".ctml_cache.json"


def main() -> None:
    load_env()

    parser = argparse.ArgumentParser(prog="ctm-ctml", description="Draft CTML match nodes from eligibility text")
    parser.add_argument("--trials", metavar="JSON",
                        help="Read trials from a JSON file instead of the 02_diff_trials collection")
    parser.add_argument("--out",    metavar="JSON", help="Output draft JSON. Required unless --no-disk")
    parser.add_argument("--cache",  default=None, metavar="JSON",
                        help=f"Cache file path (default: {cache_dir() / _DEFAULT_CACHE})")
    parser.add_argument("--limit",  type=int, default=None, metavar="N", help="Process only first N trials (for testing)")
    parser.add_argument("--nct",    default=None, metavar="ID", nargs="+", help="Process only trials matching these NCT or protocol numbers")
    # Default True through the 1.x line — see the note on trials-diff's --disk.
    parser.add_argument("--disk", action=argparse.BooleanOptionalAction, default=True,
                        help="Write the draft JSON in addition to MongoDB (default: enabled)")
    parser.add_argument("--db", metavar="NAME", help="Override MONGO_DBNAME for this run")
    parser.add_argument("--run-date", dest="run_date", metavar="YYYY-MM-DD",
                        help="Override the run_date inherited from the source documents")
    args = parser.parse_args()

    if args.disk and not args.out:
        print("Error: --out is required (pass --no-disk to store only to MongoDB)", file=sys.stderr)
        sys.exit(1)
    if args.out and not args.disk:
        print("Error: --out has no effect with --no-disk", file=sys.stderr)
        sys.exit(1)

    from ctm import db as ctm_db
    from ctm.transformers.eligibility_to_ctml import (
        build_client,
        fetch_oncotree_names,
        load_cache,
        process_trial,
        save_cache,
    )

    config = ctm_db.mongo_config()
    target_db_name = args.db or config["dbname"]
    cache_file = Path(args.cache) if args.cache else cache_path(_DEFAULT_CACHE)

    if args.trials:
        with open(args.trials) as f:
            trials = json.load(f)
        source = args.trials
    else:
        # Only the `changed` documents: `unchanged` already carries curated match
        # nodes forward and `deleted` is a terminal record, so re-drafting either
        # would spend LLM calls to no effect.
        source_db = ctm_db.get_database(config, target_db_name)
        trials = ctm_db.read_collection(
            source_db, ctm_db.DIFF_COLLECTION, {"diff_status": "changed"},
            keep_metadata=True,
        )
        source = f"{target_db_name}.{ctm_db.DIFF_COLLECTION} (diff_status=changed)"
        if not trials:
            print(f"No trials with diff_status='changed' in {source}. "
                  "Run ctm-mm trials-diff first, or pass --trials.", file=sys.stderr)
            sys.exit(1)

    print(f"Read {len(trials)} trial(s) from {source}", file=sys.stderr)

    # --run-date wins outright. Otherwise inherit from the source documents so
    # this stage stays on its run's timeline; a JSON file has no run to inherit
    # from, so there today's date is the only honest answer.
    if args.run_date:
        run_date = args.run_date
    else:
        run_date = ctm_db.inherited_run_date(trials, fallback=date.today().isoformat())

    # run_date has been read, so the upstream stage's metadata has done its job.
    # Drop it now rather than after processing: it must not reach the JSON file,
    # and a stage's provenance is re-stamped on write, never inherited.
    trials = [ctm_db.strip_metadata(trial) for trial in trials]

    if args.nct:
        ids = set(args.nct)
        trials = [t for t in trials if t.get("nct_id") in ids or t.get("protocol_no") in ids]
        if not trials:
            print(f"No trials found matching: {', '.join(ids)}", file=sys.stderr)
            sys.exit(1)
    elif args.limit:
        trials = trials[:args.limit]

    client = build_client()
    cache = load_cache(cache_file)

    print("Fetching OncoTree names...", file=sys.stderr)
    valid_oncotree = fetch_oncotree_names()
    print(f"  {len(valid_oncotree)} valid tumor types loaded", file=sys.stderr)

    target = ctm_db.prepare_collection(
        ctm_db.get_database(config, target_db_name),
        ctm_db.CTML_COLLECTION, ctm_db.DIFF_UNIQUE_KEY, ctm_db.DIFF_LOOKUP_KEYS,
    )
    print(f"Target: {target_db_name}.{ctm_db.CTML_COLLECTION} (run_date {run_date})",
          file=sys.stderr)

    results = []
    for i, trial in enumerate(trials):
        protocol = trial.get("protocol_no") or trial.get("nct_id") or f"trial-{i}"
        print(f"[{i+1}/{len(trials)}] {protocol}", file=sys.stderr)
        drafted = process_trial(trial, cache, client, valid_oncotree)
        results.append(drafted)
        save_cache(cache, cache_file)  # save after each trial so progress survives interruption
        # Store per trial for the same reason: this stage costs a model call per
        # criterion, so an interruption must not discard the trials already paid for.
        ctm_db.upsert_doc(
            target,
            ctm_db.stamp(drafted, "ctm-ctml", run_date),
            ctm_db.DIFF_UNIQUE_KEY,
        )

    print(f"Stored {len(results)} doc(s) → {target_db_name}.{ctm_db.CTML_COLLECTION}",
          file=sys.stderr)

    if args.disk:
        Path(args.out).write_text(json.dumps(results, indent=2, default=str))
        print(f"Saved → {args.out}", file=sys.stderr)
    print(f"Cache entries: {len(cache)}", file=sys.stderr)


if __name__ == "__main__":
    main()
