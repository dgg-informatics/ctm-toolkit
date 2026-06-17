"""One-liner PDF export. Run with:

    python build_pdf.py [output_path] [--real | --mock]
"""
import argparse
import os
import platform
from pathlib import Path

# On macOS, Homebrew's lib dir isn't on the default dlopen search path, which
# makes WeasyPrint's Pango/GObject bindings fail to load even when installed.
if platform.system() == "Darwin":
    homebrew_lib = "/opt/homebrew/lib"
    if os.path.isdir(homebrew_lib):
        os.environ["DYLD_LIBRARY_PATH"] = homebrew_lib + os.pathsep + os.environ.get("DYLD_LIBRARY_PATH", "")

from weasyprint import HTML  # noqa: E402

from ctm.reports.builder import BASE_DIR, render_html  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", default=None)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--real", action="store_true", help="load data from data/real/")
    source.add_argument("--mock", action="store_true", help="load data from data/mock/ (default)")
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else BASE_DIR / "output" / "report.pdf"
    output_path.parent.mkdir(exist_ok=True, parents=True)
    HTML(string=render_html(use_real=args.real)).write_pdf(str(output_path))
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
