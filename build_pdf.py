"""One-liner PDF export. Run with:

    python build_pdf.py [output_path]
"""
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

from report_builder import BASE_DIR, render_html  # noqa: E402


def main():
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE_DIR / "output" / "report.pdf"
    output_path.parent.mkdir(exist_ok=True, parents=True)
    HTML(string=render_html()).write_pdf(str(output_path))
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
