"""ctm-process — CTM data processing pipeline entry point."""
import argparse
import sys
from pathlib import Path


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="ctm-process",
        description="Transform raw clinical/genomic data, store in MongoDB, and generate reports.",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("-i", "--input", metavar="PATH", help="Path containing raw data subfolders")
    source.add_argument("--mock", action="store_true", help="Use data/mock/ (dev mode, skips MongoDB)")
    parser.add_argument("--no-report", action="store_true", help="Skip PDF report generation")
    args = parser.parse_args()

    if not args.input and not args.mock:
        parser.print_help()
        sys.exit(1)

    from ctm.pipeline import run_pipeline

    base_dir = Path(__file__).parent.parent.parent
    input_path = str(base_dir / "data" / "mock") if args.mock else args.input

    run_pipeline(input_path, generate_report=not args.no_report)


if __name__ == "__main__":
    main()
