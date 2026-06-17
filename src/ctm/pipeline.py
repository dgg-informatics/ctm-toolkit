"""Orchestrates a full CTM processing run.

Scans input path subfolders, runs the appropriate transformer for each,
stores raw + processed docs in MongoDB, writes output files, and
optionally generates a PDF report.
"""
import json
import uuid
from datetime import datetime
from pathlib import Path

from ctm.db import store
from ctm.fetchers.fetch_similar_patients import fetch as fetch_similar
from ctm.transformers import process_ct_data, process_patient_general, process_patient_genetic

TRANSFORMER_MAP = {
    "clinical_trials": process_ct_data,
    "patient_general": process_patient_general,
    "patient_genetic": process_patient_genetic,
}

TRANSFORMER_VERSIONS = {
    name: mod.__version__ for name, mod in TRANSFORMER_MAP.items()
}


def generate_run_id() -> str:
    date_part = datetime.now().strftime("%d%b%Y")
    hex_part = uuid.uuid4().hex[:8]
    return f"{date_part}-{hex_part}"


def run_pipeline(
    input_path: str,
    generate_report: bool = True,
    output_base: Path | None = None,
    *,
    db=None,
) -> str:
    run_id = generate_run_id()
    base_dir = Path(__file__).parent.parent.parent
    output_dir = (output_base or base_dir / "data" / "output") / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    input_dir = Path(input_path)
    processed: dict[str, dict] = {}
    mrn: str | None = None

    for subfolder in sorted(input_dir.iterdir()):
        if not subfolder.is_dir():
            continue
        transformer_module = TRANSFORMER_MAP.get(subfolder.name)
        if not transformer_module:
            continue

        for json_file in sorted(subfolder.glob("*.json")):
            raw = json.loads(json_file.read_text())
            result = transformer_module.process(raw)
            processed[subfolder.name] = result

            store.insert_raw_document(
                run_id, subfolder.name, json_file.name, raw, db=db
            )
            store.insert_processed_document(
                run_id,
                subfolder.name,
                transformer_module.__version__,
                result,
                db=db,
            )

            if subfolder.name in ("patient_general", "patient_genetic") and not mrn:
                mrn = result.get("mrn")

        out_file = output_dir / f"{subfolder.name}.json"
        out_file.write_text(json.dumps(processed.get(subfolder.name, {}), indent=2))

    # Fetch similar patients if we have a patient MRN
    if mrn:
        similar = fetch_similar(mrn, top_n=5, db=db)
        store.insert_similar_patients(
            run_id, mrn, similar["top_n"], similar["matches"], db=db
        )
        (output_dir / "similar_patients.json").write_text(json.dumps(similar, indent=2))

    store.insert_run(
        run_id,
        str(input_path),
        TRANSFORMER_VERSIONS,
        str(output_dir),
        db=db,
    )

    if generate_report:
        _generate_report(processed, output_dir)

    print(f"Run complete: {run_id}")
    print(f"Output: {output_dir}")
    return run_id


def _generate_report(processed: dict, output_dir: Path) -> None:
    try:
        import os
        import platform

        if platform.system() == "Darwin":
            homebrew_lib = "/opt/homebrew/lib"
            if os.path.isdir(homebrew_lib):
                os.environ["DYLD_LIBRARY_PATH"] = (
                    homebrew_lib + os.pathsep + os.environ.get("DYLD_LIBRARY_PATH", "")
                )

        from weasyprint import HTML
        from ctm.reports.builder import render_html

        html = render_html(context_override=processed)
        (output_dir / "report.html").write_text(html)
        HTML(string=html).write_pdf(str(output_dir / "report.pdf"))
        print(f"Report: {output_dir / 'report.pdf'}")
    except Exception as exc:
        print(f"Warning: report generation failed — {exc}")
