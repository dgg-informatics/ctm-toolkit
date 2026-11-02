"""ctm-ctml — DEPRECATED alias for `ctm-llm clinical`.

The LLM stages were partitioned by what the model is asked to do rather than by
the old CLI boundary, because this command and `ctm-mm trials-curate` did not
divide along that line: trials-curate's title call used *this* command's prompt
and produced *this* command's output shape, while its biomarker scan is a
different job entirely.

This shim forwards every argument to `ctm-llm clinical` (formerly `general`) so
existing scripts and runbooks keep working. It is removed in 3.0.0 with the
other pre-2.4.0 names.
"""
import logging
import sys

log = logging.getLogger(__name__)


def main() -> None:
    from ctm.llm_cli import main as llm_main

    log.warning("DEPRECATED: ctm-ctml is now ctm-llm clinical and will be removed in 3.0.0.")
    llm_main(["clinical", *sys.argv[1:]])


if __name__ == "__main__":
    main()
