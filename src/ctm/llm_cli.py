"""ctm-llm — the pipeline's LLM-assisted curation stages.

Usage:
  ctm-llm general    --out JSON [--trials JSON] [--limit N] [--nct ID ...]
  ctm-llm biomarkers --out JSON [--trials JSON] [--kb JSON]

Two subcommands, split along what the model is actually asked to do rather than
along the older CLI boundary:

* ``general`` drafts CTML match nodes. One call per eligibility criterion plus one
  for ``_summary.long_title`` — the same prompt and the same output shape, so they
  belong together. Reads the ``02_diff_trials`` documents routed
  ``diff_status: "changed"``, writes ``03_ctml_drafted_trials``.
* ``biomarkers`` scans for genetic/molecular biomarker references. Different
  prompt, different output shape, no match nodes and therefore no OncoTree
  lookup. Reads ``03_ctml_drafted_trials``, writes ``04_curated_trials``.

Each subcommand writes exactly one key under ``_llm_curation`` and merges into
whatever is already there, so neither can destroy the other's work.

The stages are order-dependent by design: run ``general`` then ``biomarkers``.

Replaces ``ctm-ctml`` (now ``general``) and ``ctm-mm trials-curate`` (now
``biomarkers``). Both old entry points still work as deprecated aliases and will
be removed in 2.0.0.

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

from ctm.paths import DEFAULT_KB_PATH, cache_dir, cache_path, load_env

_GENERAL_CACHE = ".ctml_cache.json"
_BIOMARKER_CACHE = ".trials_curate_cache.json"


def _add_shared_args(parser, cache_default: str) -> None:
    parser.add_argument("--trials", metavar="JSON",
                        help="Read trials from a JSON file instead of this stage's source collection")
    parser.add_argument("--out", metavar="JSON",
                        help="Output JSON path. Required unless --no-disk")
    parser.add_argument("--cache", default=None, metavar="JSON",
                        help=f"LLM response cache (default: {cache_dir() / cache_default})")
    # Default True through the 1.x line; the 2.0.0 release flips every stage at once.
    parser.add_argument("--disk", action=argparse.BooleanOptionalAction, default=True,
                        help="Write the JSON output in addition to MongoDB (default: enabled)")
    parser.add_argument("--db", metavar="NAME", help="Override MONGO_DBNAME for this run")
    parser.add_argument("--run-date", dest="run_date", metavar="YYYY-MM-DD",
                        help="Override the run_date inherited from the source documents")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctm-llm", description="LLM-assisted trial curation stages")
    sub = parser.add_subparsers(dest="command", required=True)

    p_general = sub.add_parser(
        "general",
        help="Draft CTML match nodes from each eligibility criterion and the trial title",
    )
    _add_shared_args(p_general, _GENERAL_CACHE)
    p_general.add_argument("--limit", type=int, default=None, metavar="N",
                           help="Process only the first N trials (for testing)")
    p_general.add_argument("--nct", default=None, metavar="ID", nargs="+",
                           help="Process only trials matching these NCT or protocol numbers")

    p_biomarkers = sub.add_parser(
        "biomarkers",
        help="Scan titles, disease keywords, curator genes and criteria for biomarker references",
    )
    _add_shared_args(p_biomarkers, _BIOMARKER_CACHE)
    p_biomarkers.add_argument("--kb", default=None, metavar="JSON",
                              help="Known gene/variant knowledge base (default: the packaged copy)")

    return parser


def _check_disk_args(args) -> None:
    """--out is meaningless with --no-disk, and disk output cannot write without it."""
    if args.disk and not args.out:
        print("Error: --out is required (pass --no-disk to store only to MongoDB)",
              file=sys.stderr)
        sys.exit(1)
    if args.out and not args.disk:
        print("Error: --out has no effect with --no-disk", file=sys.stderr)
        sys.exit(1)


def _load_trials(args, ctm_db, config, target_db, collection: str, query: dict | None,
                 hint: str) -> tuple[list[dict], str]:
    """This stage's input, from --trials if given or else from ``collection``."""
    if args.trials:
        return json.loads(Path(args.trials).read_text()), args.trials

    trials = ctm_db.read_collection(
        ctm_db.get_database(config, target_db), collection, query, keep_metadata=True,
    )
    described = f"{target_db}.{collection}"
    if query:
        described += f" ({', '.join(f'{k}={v}' for k, v in query.items())})"
    if not trials:
        print(f"No trials in {described}. {hint}", file=sys.stderr)
        sys.exit(1)
    return trials, described


def _resolve_run_date(args, ctm_db, trials: list[dict]) -> str:
    """--run-date wins; otherwise inherit so the stage stays on its run's timeline.

    A JSON file carries no run to inherit from, so there today is the only honest
    answer.
    """
    if args.run_date:
        return args.run_date
    return ctm_db.inherited_run_date(trials, fallback=date.today().isoformat())


def _cmd_general(args) -> None:
    from ctm import db as ctm_db
    from ctm.transformers.eligibility_to_ctml import (
        build_client,
        draft_trial,
        fetch_oncotree_names,
        load_cache,
        save_cache,
    )

    _check_disk_args(args)
    config = ctm_db.mongo_config()
    target_db = args.db or config["dbname"]
    cache_file = Path(args.cache) if args.cache else cache_path(_GENERAL_CACHE)

    # Only the `changed` documents: `unchanged` carries curated match nodes forward
    # from the master and `deleted` is a terminal record, so drafting either would
    # spend model calls to no effect.
    trials, source = _load_trials(
        args, ctm_db, config, target_db, ctm_db.DIFF_COLLECTION,
        {"diff_status": "changed"}, "Run ctm-mm trials-diff first, or pass --trials.",
    )
    print(f"Read {len(trials)} trial(s) from {source}", file=sys.stderr)

    run_date = _resolve_run_date(args, ctm_db, trials)
    # Upstream provenance has done its job now that run_date is known; it must not
    # reach the JSON output, and each stage re-stamps rather than inherits.
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
        ctm_db.get_database(config, target_db),
        ctm_db.CTML_COLLECTION, ctm_db.DIFF_UNIQUE_KEY, ctm_db.DIFF_LOOKUP_KEYS,
    )
    print(f"Target: {target_db}.{ctm_db.CTML_COLLECTION} (run_date {run_date})", file=sys.stderr)

    results = []
    for i, trial in enumerate(trials):
        label = trial.get("protocol_no") or trial.get("nct_id") or f"trial-{i}"
        print(f"[{i + 1}/{len(trials)}] {label}", file=sys.stderr)
        drafted = draft_trial(trial, cache, client, valid_oncotree)
        results.append(drafted)
        save_cache(cache, cache_file)  # after each trial so progress survives interruption
        # Stored per trial for the same reason: this stage costs a model call per
        # criterion, so an interruption must not discard trials already paid for.
        ctm_db.upsert_doc(target, ctm_db.stamp(drafted, "ctm-llm general", run_date),
                          ctm_db.DIFF_UNIQUE_KEY)

    print(f"Stored {len(results)} doc(s) → {target_db}.{ctm_db.CTML_COLLECTION}", file=sys.stderr)
    if args.disk:
        Path(args.out).write_text(json.dumps(results, indent=2, default=str))
        print(f"Saved → {args.out}", file=sys.stderr)
    print(f"Cache entries: {len(cache)}", file=sys.stderr)


def _cmd_biomarkers(args) -> None:
    from ctm import db as ctm_db
    from ctm.transformers.eligibility_to_ctml import build_client
    from ctm.transformers.trials_curate import (
        annotate_biomarkers,
        load_cache,
        load_known_genes,
        save_cache,
    )

    _check_disk_args(args)
    config = ctm_db.mongo_config()
    target_db = args.db or config["dbname"]
    cache_file = Path(args.cache) if args.cache else cache_path(_BIOMARKER_CACHE)

    trials, source = _load_trials(
        args, ctm_db, config, target_db, ctm_db.CTML_COLLECTION, None,
        "Run ctm-llm general first, or pass --trials.",
    )
    print(f"Read {len(trials)} trial(s) from {source}", file=sys.stderr)

    run_date = _resolve_run_date(args, ctm_db, trials)
    trials = [ctm_db.strip_metadata(trial) for trial in trials]

    kb_path = Path(args.kb) if args.kb else DEFAULT_KB_PATH
    known_genes = load_known_genes(kb_path)
    print(f"{len(known_genes)} known genes loaded from {kb_path}", file=sys.stderr)

    # No OncoTree fetch: this stage produces biomarker references, not match nodes.
    client = build_client()
    cache = load_cache(cache_file)

    target = ctm_db.prepare_collection(
        ctm_db.get_database(config, target_db),
        ctm_db.CURATED_COLLECTION, ctm_db.DIFF_UNIQUE_KEY, ctm_db.DIFF_LOOKUP_KEYS,
    )
    print(f"Target: {target_db}.{ctm_db.CURATED_COLLECTION} (run_date {run_date})", file=sys.stderr)

    for i, trial in enumerate(trials, 1):
        label = trial.get("nct_id") or trial.get("protocol_no") or "unknown"
        print(f"[{i}/{len(trials)}] {label}", file=sys.stderr)
        annotate_biomarkers(trial, client, cache, known_genes)
        save_cache(cache, cache_file)  # after each trial so progress survives interruption
        ctm_db.upsert_doc(target, ctm_db.stamp(trial, "ctm-llm biomarkers", run_date),
                          ctm_db.DIFF_UNIQUE_KEY)

    print(f"Stored {len(trials)} doc(s) → {target_db}.{ctm_db.CURATED_COLLECTION}", file=sys.stderr)
    if args.disk:
        Path(args.out).write_text(json.dumps(trials, indent=2, default=str))
        print(f"Saved {len(trials)} trial(s) → {args.out}", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    load_env()
    args = build_parser().parse_args(argv)
    if args.command == "general":
        _cmd_general(args)
    elif args.command == "biomarkers":
        _cmd_biomarkers(args)


if __name__ == "__main__":
    main()
