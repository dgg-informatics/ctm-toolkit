"""One-liner PDF export. Run with:

    python build_pdf.py [output_path] [--mock]
    python build_pdf.py [output_path] --excel PATH --mm-export PATH
"""
import argparse
import os
import platform
import sys
from pathlib import Path

# On macOS, Homebrew's lib dir isn't on the default dlopen search path, which
# makes WeasyPrint's Pango/GObject bindings fail to load even when installed.
if platform.system() == "Darwin":
    homebrew_lib = "/opt/homebrew/lib"
    if os.path.isdir(homebrew_lib):
        os.environ["DYLD_LIBRARY_PATH"] = homebrew_lib + os.pathsep + os.environ.get("DYLD_LIBRARY_PATH", "")

from weasyprint import HTML  # noqa: E402

from ctm.reports.builder import BASE_DIR, render_html, render_html_from_sources  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", default=None, help="Output PDF path (default: output/report.pdf)")
    parser.add_argument("--mock", action="store_true", help="Use data/mock/ (default)")
    parser.add_argument("--excel", metavar="PATH", help="Path to patient_data_template.xlsx")
    parser.add_argument("--mm-export", dest="mm_export", metavar="PATH",
                        help="Path to matchminer export JSON")
    args = parser.parse_args()

    use_real_sources = bool(args.excel and args.mm_export)
    if (args.excel or args.mm_export) and not use_real_sources:
        parser.error("--excel and --mm-export must be used together")

    output_path = Path(args.output) if args.output else BASE_DIR / "output" / "report.pdf"
    output_path.parent.mkdir(exist_ok=True, parents=True)

    if use_real_sources:
        html = render_html_from_sources(args.excel, args.mm_export)
    else:
        html = render_html(use_real=False)

    HTML(string=html).write_pdf(str(output_path))
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
