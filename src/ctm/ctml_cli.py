"""ctm-ctml — DEPRECATED alias for `ctm-llm general`.

The LLM stages were partitioned by what the model is asked to do rather than by
the old CLI boundary, because this command and `ctm-mm trials-curate` did not
divide along that line: trials-curate's title call used *this* command's prompt
and produced *this* command's output shape, while its biomarker scan is a
different job entirely.

This shim forwards every argument to `ctm-llm general` so existing scripts and
runbooks keep working. It will be removed in 2.0.0 along with the disk-output
default flip, so the CLI breaks exactly once.
"""
import sys


def main() -> None:
    from ctm.llm_cli import main as llm_main

    print(
        "DEPRECATED: ctm-ctml is now `ctm-llm general` and will be removed in 2.0.0. "
        "Forwarding...",
        file=sys.stderr,
    )
    llm_main(["general", *sys.argv[1:]])


if __name__ == "__main__":
    main()
