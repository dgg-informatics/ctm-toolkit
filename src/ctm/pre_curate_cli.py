"""ctm-pre-curate — the weekly trial refresh, up to the point a human is needed.

Four stages: pull the sources, diff against the master, draft match nodes, scan
for biomarkers. The output is a file in ``LLM_BIOMARKER_EXPORT_DIR`` for a
curator to edit; ``ctm-post-curate`` picks up what they publish.

Designed to be the whole cron line:

    CTM_ENV_FILE=/etc/ctm/.env
    PATH=/opt/ctm/current/venv/bin:/usr/bin:/bin
    0 7 * * MON  dgg-mipro  ctm-pre-curate

Nothing about logging or redirection belongs in that line. ``CTM_LOG_ENV=prod``
in the env file puts the console at WARNING and the run log in
``/var/lib/ctm/logs``, so a clean run mails nothing and mail means something
broke.
"""
import argparse
import logging
import sys
from datetime import date

from ctm.logging_config import (
    add_logging_arguments,
    command_context,
    configure_logging,
    log_event,
    verbosity_from_args,
)
from ctm.paths import llm_biomarker_export_dir, load_env
from ctm.pipelines import Stage, run_stages

log = logging.getLogger(__name__)


def build_stages(args) -> list[Stage]:
    """The four stages, each built through the real ``ctm-mm``/``ctm-llm``
    parser so a new flag cannot silently arrive without its default."""
    from ctm import llm_cli, mm_cli

    def mm(*argv):
        parsed = mm_cli.build_parser().parse_args(argv)
        return lambda: mm_cli.command_handlers()[parsed.command](parsed)

    def llm(*argv):
        parsed = llm_cli.build_parser().parse_args(argv)
        handler = {"general": llm_cli._cmd_general,
                   "biomarkers": llm_cli._cmd_biomarkers}[parsed.command]
        return lambda: handler(parsed)

    sources = args.sources or ["--amc", "--ddots", "--west"]
    return [
        Stage(f"ctm-mm trials {' '.join(sources)}", mm("trials", *sources)),
        Stage("ctm-mm trials-diff", mm("trials-diff")),
        Stage("ctm-llm general", llm("general", "--yes") if args.yes else llm("general")),
        Stage("ctm-llm biomarkers",
              llm("biomarkers", "--yes") if args.yes else llm("biomarkers")),
    ]


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(
        prog="ctm-pre-curate",
        description="Weekly trial refresh through to the curator handoff",
    )
    parser.add_argument("--sources", nargs="+", metavar="FLAG",
                        help="Source flags for `ctm-mm trials` "
                             "(default: --amc --ddots --west)")
    parser.add_argument("--yes", action="store_true",
                        help="Pass --yes to the LLM stages, so a cold cache does "
                             "not stop an unattended run at the confirmation prompt")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="List the stages without running them")
    add_logging_arguments(parser)
    args = parser.parse_args()
    configure_logging(verbosity=verbosity_from_args(args))

    with command_context(log, "ctm-pre-curate"):
        stages = build_stages(args)
        code = run_stages(stages, dry_run=args.dry_run)
        if code:
            sys.exit(code)
        if not args.dry_run:
            log_event(log, "pre_curate.complete",
                      "Pre-curation complete — curate the file in %s",
                      llm_biomarker_export_dir(),
                      to_curate_dir=str(llm_biomarker_export_dir()),
                      run_date=date.today().isoformat())


if __name__ == "__main__":
    main()
