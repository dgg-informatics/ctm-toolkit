"""ctm-llm — the pipeline's LLM-assisted curation stages.

Usage:
  ctm-llm clinical --out JSON [--trials JSON] [--limit N] [--nct ID ...]
  ctm-llm genomic  --out JSON [--trials JSON] [--kb JSON]

Two stages, split by the kind of CTML they draft:

* ``clinical`` — clinical match nodes, one model call per piece of text (each
  top-level criterion with its sub-criteria, and the long title), plus the
  ``conditions`` record and the ``disease_site`` suggestion. Reads the
  ``02_diff_trials`` documents routed ``diff_status: "changed"``, writes
  ``03_llm_general_trials``.
* ``genomic`` — genomic match nodes and ``molecular_genomic_refs``, one model
  call per piece (criteria, title, conditions, curator genes of interest). No
  OncoTree lookup. Reads ``03_llm_general_trials``, writes
  ``04_llm_biomarker_trials`` and the to-curate handoff export.

Both write ``node_type``-tagged entries into ``_llm_curation._ctml_suggestions``
(nulls in ``_null_ctml_suggestions``) and their own ``parsed_sections`` key,
and replace only their own entries — so either can be rerun without touching
the other's work. Run ``clinical`` then ``genomic``.

Deprecated until 3.0.0: ``general`` (→ ``clinical``), ``biomarkers``
(→ ``genomic``), and the older ``ctm-ctml`` / ``ctm-mm trials-curate``.

Requires in .env:
  UMGPT_API_KEY=...
  UMGPT_BASE_URL=https://...
  UMGPT_MODEL=gpt-4o   (optional, defaults to gpt-4o)
"""
import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from ctm.logging_config import (
    command_context,
    configure_logging,
    fail,
    log_event,
    verbosity_from_args,
)
from ctm.paths import (
    DEFAULT_KB_PATH,
    cache_dir,
    cache_path,
    llm_biomarker_export_dir,
    load_env,
)

log = logging.getLogger(__name__)

_CLINICAL_CACHE = ".ctml_clinical_cache.json"
_GENOMIC_CACHE = ".ctml_genomic_cache.json"

# Deprecated subcommand → its replacement. Removed in 3.0.0.
_ALIASES = {"general": "clinical", "biomarkers": "genomic"}


def _confirm_cold_cache(cache: dict, cache_file: Path, assume_yes: bool) -> None:
    """Guard an expensive cold run. With no cached responses the stage would make
    live LLM calls, so warn and require confirmation. ``--yes`` skips the prompt;
    a non-interactive shell without ``--yes`` aborts rather than hang.
    """
    if cache or assume_yes:
        return
    log.warning("no cached LLM responses at %s — this run will make live LLM calls "
                "(UMGPT quota / API cost).", cache_file,
                extra={"event": "llm.cold_cache", "cache_file": str(cache_file)})
    if not sys.stdin.isatty():
        fail("non-interactive shell: pass --yes to run without a cache. Aborting.")
    if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
        log.info("Aborted.")
        sys.exit(1)


def _add_shared_args(parser, cache_default: str) -> None:
    parser.add_argument("--trials", metavar="JSON",
                        help="Read trials from a JSON file instead of this stage's source collection")
    parser.add_argument("--out", metavar="JSON",
                        help="Write the JSON output to this path (overrides any default export)")
    parser.add_argument("--cache", default=None, metavar="JSON",
                        help=f"LLM response cache (default: {cache_dir() / cache_default})")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip the cold-cache confirmation prompt (for non-interactive runs)")
    # v2: MongoDB only by default. --disk/--no-disk force the file on/off; a stage
    # with a canonical export (genomic) still writes it unless --no-disk.
    parser.add_argument("--disk", action=argparse.BooleanOptionalAction, default=None,
                        help="Force the JSON file output on/off (default: MongoDB only)")
    parser.add_argument("--db", metavar="NAME", help="Override MONGO_DBNAME for this run")
    parser.add_argument("--run-date", dest="run_date", metavar="YYYY-MM-DD",
                        help="Override the run_date inherited from the source documents")


def _add_stage_parser(sub, name: str, help: str, cache_default: str, clinical: bool) -> None:
    p = sub.add_parser(name, help=help)
    _add_shared_args(p, cache_default)
    if clinical:
        p.add_argument("--limit", type=int, default=None, metavar="N",
                       help="Process only the first N trials (for testing)")
        p.add_argument("--nct", default=None, metavar="ID", nargs="+",
                       help="Process only trials matching these NCT or protocol numbers")
    else:
        p.add_argument("--kb", default=None, metavar="JSON",
                       help="Known gene/variant knowledge base (default: the packaged copy)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctm-llm", description="LLM-assisted trial curation stages")
    sub = parser.add_subparsers(dest="command", required=True)

    clinical_help = "Draft clinical CTML from each criterion, the title, conditions and disease site"
    genomic_help = ("Draft genomic CTML and molecular refs from each criterion, the title, "
                    "conditions and curator genes")
    _add_stage_parser(sub, "clinical", clinical_help, _CLINICAL_CACHE, clinical=True)
    _add_stage_parser(sub, "genomic", genomic_help, _GENOMIC_CACHE, clinical=False)
    _add_stage_parser(sub, "general", "[deprecated] alias for clinical; removed in 3.0.0",
                      _CLINICAL_CACHE, clinical=True)
    _add_stage_parser(sub, "biomarkers", "[deprecated] alias for genomic; removed in 3.0.0",
                      _GENOMIC_CACHE, clinical=False)
    return parser


def _resolve_out(args, default_path: Path | None) -> Path | None:
    """The JSON export path, or None for MongoDB-only.

    ``--out`` wins; ``--no-disk`` forces MongoDB-only; otherwise the stage's
    ``default_path`` (a canonical export path, or None for stages that don't
    write a file by default).
    """
    if args.disk is False:          # explicit --no-disk
        return None
    if args.out:
        return Path(args.out)
    return default_path             # canonical export, or None


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
        fail("no trials in %s. %s", described, hint)
    return trials, described


def _resolve_run_date(args, ctm_db, trials: list[dict]) -> str:
    """--run-date wins; otherwise inherit so the stage stays on its run's timeline.

    A JSON file carries no run to inherit from, so there today is the only honest
    answer.
    """
    if args.run_date:
        return args.run_date
    return ctm_db.inherited_run_date(trials, fallback=date.today().isoformat())


def _cmd_clinical(args) -> None:
    from ctm import db as ctm_db
    from ctm.transformers.clinical_ctml import draft_clinical
    from ctm.transformers.eligibility_to_ctml import (
        build_client,
        fetch_oncotree_names,
        load_cache,
        save_cache,
    )

    config = ctm_db.mongo_config()
    target_db = args.db or config["dbname"]
    cache_file = Path(args.cache) if args.cache else cache_path(_CLINICAL_CACHE)

    # Only the `changed` documents: `unchanged` carries curated match nodes forward
    # from the master and `deleted` is a terminal record, so drafting either would
    # spend model calls to no effect.
    trials, source = _load_trials(
        args, ctm_db, config, target_db, ctm_db.DIFF_COLLECTION,
        {"diff_status": "changed"}, "Run ctm-mm trials-diff first, or pass --trials.",
    )
    log_event(log, "llm.input", "Read %d trial(s) from %s", len(trials), source,
              stage="clinical", count=len(trials), source_description=source)

    run_date = _resolve_run_date(args, ctm_db, trials)
    # Upstream provenance has done its job now that run_date is known; it must not
    # reach the JSON output, and each stage re-stamps rather than inherits.
    trials = [ctm_db.strip_metadata(trial) for trial in trials]

    if args.nct:
        ids = set(args.nct)
        trials = [t for t in trials if t.get("nct_id") in ids or t.get("protocol_no") in ids]
        if not trials:
            fail("no trials found matching: %s", ", ".join(ids))
    elif args.limit:
        trials = trials[:args.limit]

    from ctm.transformers import disease_site_to_oncotree as site_map
    client = build_client()
    cache = load_cache(cache_file)
    site_file = site_map.default_draft_path()
    reviewed = site_map.load_reviewed()
    site_drafts = site_map.load_drafts(site_file)
    _confirm_cold_cache(cache, cache_file, args.yes)

    log.info("Fetching OncoTree names...")
    valid_oncotree = fetch_oncotree_names()
    log.info(f"  {len(valid_oncotree)} valid tumor types loaded")

    target = ctm_db.prepare_collection(
        ctm_db.get_database(config, target_db),
        ctm_db.LLM_GENERAL_COLLECTION, ctm_db.DIFF_UNIQUE_KEY, ctm_db.DIFF_LOOKUP_KEYS,
    )
    log.info(f"Target: {target_db}.{ctm_db.LLM_GENERAL_COLLECTION} (run_date {run_date})")

    results = []
    for i, trial in enumerate(trials):
        label = trial.get("protocol_no") or trial.get("nct_id") or f"trial-{i}"
        log_event(log, "llm.trial", "[%d/%d] %s", i + 1, len(trials), label,
                  trial=label, index=i + 1, total=len(trials), stage="clinical")
        drafted = draft_clinical(trial, cache, client, valid_oncotree, reviewed, site_drafts)
        save_cache(cache, cache_file)  # after each trial so progress survives interruption
        site_map.save_drafts(site_drafts, site_file)
        results.append(drafted)
        # Stored per trial for the same reason: this stage costs a model call per
        # criterion, so an interruption must not discard trials already paid for.
        ctm_db.upsert_doc(target, ctm_db.stamp(drafted, "ctm-llm clinical", run_date),
                          ctm_db.DIFF_UNIQUE_KEY)

    log_event(log, "db.stored", "Stored %d doc(s) → %s.%s",
              len(results), target_db, ctm_db.LLM_GENERAL_COLLECTION,
              count=len(results), database=target_db,
              collection=ctm_db.LLM_GENERAL_COLLECTION, stage="llm.clinical")
    out = _resolve_out(args, None)   # clinical has no canonical export
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2, default=str))
        log.info(f"Saved → {out}")
    log_event(log, "llm.cache", "Cache entries: %d", len(cache), entries=len(cache))


def _cmd_genomic(args) -> None:
    from ctm import db as ctm_db
    from ctm.transformers.eligibility_to_ctml import build_client
    from ctm.transformers.genomic_ctml import draft_genomic
    from ctm.transformers.trials_curate import load_cache, load_known_genes, save_cache

    config = ctm_db.mongo_config()
    target_db = args.db or config["dbname"]
    cache_file = Path(args.cache) if args.cache else cache_path(_GENOMIC_CACHE)

    trials, source = _load_trials(
        args, ctm_db, config, target_db, ctm_db.LLM_GENERAL_COLLECTION, None,
        "Run ctm-llm clinical first, or pass --trials.",
    )
    log_event(log, "llm.input", "Read %d trial(s) from %s", len(trials), source,
              stage="genomic", count=len(trials), source_description=source)

    run_date = _resolve_run_date(args, ctm_db, trials)
    trials = [ctm_db.strip_metadata(trial) for trial in trials]

    kb_path = Path(args.kb) if args.kb else DEFAULT_KB_PATH
    known_genes = load_known_genes(kb_path)
    log.info(f"{len(known_genes)} known genes loaded from {kb_path}")

    # No OncoTree fetch: genomic nodes carry no diagnosis.
    client = build_client()
    cache = load_cache(cache_file)
    _confirm_cold_cache(cache, cache_file, args.yes)

    target = ctm_db.prepare_collection(
        ctm_db.get_database(config, target_db),
        ctm_db.LLM_BIOMARKER_COLLECTION, ctm_db.DIFF_UNIQUE_KEY, ctm_db.DIFF_LOOKUP_KEYS,
    )
    log.info(f"Target: {target_db}.{ctm_db.LLM_BIOMARKER_COLLECTION} (run_date {run_date})")

    results = []
    for i, trial in enumerate(trials, 1):
        label = trial.get("nct_id") or trial.get("protocol_no") or "unknown"
        log_event(log, "llm.trial", "[%d/%d] %s", i, len(trials), label,
                  trial=label, index=i, total=len(trials), stage="genomic")
        drafted = draft_genomic(trial, cache, client, known_genes)
        results.append(drafted)
        save_cache(cache, cache_file)  # after each trial so progress survives interruption
        ctm_db.upsert_doc(target, ctm_db.stamp(drafted, "ctm-llm genomic", run_date),
                          ctm_db.DIFF_UNIQUE_KEY)
    trials = results

    log_event(log, "db.stored", "Stored %d doc(s) → %s.%s",
              len(trials), target_db, ctm_db.LLM_BIOMARKER_COLLECTION,
              count=len(trials), database=target_db,
              collection=ctm_db.LLM_BIOMARKER_COLLECTION, stage="llm.genomic")
    # Canonical export by default: the to-curate handoff file for manual curation.
    default_export = llm_biomarker_export_dir() / f"{run_date}_llm-biomarkers_trials.json"
    out = _resolve_out(args, default_export)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(trials, indent=2, default=str))
        log.info(f"Saved {len(trials)} trial(s) → {out}")


def main(argv: list[str] | None = None) -> None:
    load_env()
    args = build_parser().parse_args(argv)
    configure_logging(verbosity=verbosity_from_args(args))
    command = _ALIASES.get(args.command, args.command)
    if command != args.command:
        log.warning("DEPRECATED: ctm-llm %s is now ctm-llm %s and will be removed in 3.0.0.",
                    args.command, command,
                    extra={"event": "cli.deprecated", "old": args.command, "new": command})
    with command_context(log, f"ctm-llm {command}"):
        {"clinical": _cmd_clinical, "genomic": _cmd_genomic}[command](args)


if __name__ == "__main__":
    main()
