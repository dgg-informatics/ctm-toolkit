"""ctm-ctml — LLM-assisted CTML match node draft generator.

Usage:
  ctm-ctml --trials amc-normalized.json --out amc-ctml-draft.json [--limit N]

Reads a normalized trials JSON (output of ctm-mm trials), sends each eligibility
criterion to UMGPT, and writes a draft JSON with suggested CTML match nodes
attached to each trial under _ctml_suggestions.

Results are cached in data/dump/.ctml_cache.json — unchanged criteria are free.

Requires in .env:
  UMGPT_API_KEY=...
  UMGPT_BASE_URL=https://...
  UMGPT_MODEL=gpt-4o   (optional, defaults to gpt-4o)
"""
import argparse
import json
import sys
from pathlib import Path

_DEFAULT_CACHE = ".ctml_cache.json"


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(prog="ctm-ctml", description="Draft CTML match nodes from eligibility text")
    parser.add_argument("--trials", required=True, metavar="JSON", help="Normalized trials JSON from ctm-mm trials")
    parser.add_argument("--out",    required=True, metavar="JSON", help="Output draft JSON")
    parser.add_argument("--cache",  default=_DEFAULT_CACHE, metavar="JSON", help=f"Cache file path (default: {_DEFAULT_CACHE})")
    parser.add_argument("--limit",  type=int, default=None, metavar="N", help="Process only first N trials (for testing)")
    parser.add_argument("--nct",    default=None, metavar="ID", nargs="+", help="Process only trials matching these NCT or protocol numbers")
    args = parser.parse_args()

    from ctm.transformers.eligibility_to_ctml import build_client, fetch_oncotree_names, load_cache, process_trial, save_cache

    cache_path = Path(args.cache)

    with open(args.trials) as f:
        trials = json.load(f)

    if args.nct:
        ids = set(args.nct)
        trials = [t for t in trials if t.get("nct_id") in ids or t.get("protocol_no") in ids]
        if not trials:
            print(f"No trials found matching: {', '.join(ids)}", file=sys.stderr)
            sys.exit(1)
    elif args.limit:
        trials = trials[:args.limit]

    client = build_client()
    cache = load_cache(cache_path)

    print("Fetching OncoTree names...", file=sys.stderr)
    valid_oncotree = fetch_oncotree_names()
    print(f"  {len(valid_oncotree)} valid tumor types loaded", file=sys.stderr)

    results = []
    for i, trial in enumerate(trials):
        protocol = trial.get("protocol_no") or trial.get("nct_id") or f"trial-{i}"
        print(f"[{i+1}/{len(trials)}] {protocol}", file=sys.stderr)
        results.append(process_trial(trial, cache, client, valid_oncotree))
        save_cache(cache, cache_path)  # save after each trial so progress survives interruption

    Path(args.out).write_text(json.dumps(results, indent=2))
    print(f"Saved → {args.out}", file=sys.stderr)
    print(f"Cache entries: {len(cache)}", file=sys.stderr)


if __name__ == "__main__":
    main()
