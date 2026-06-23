"""ctm-mm — MatchMiner import tooling CLI.

Usage:
  ctm-mm raw-to-mm PATH/TO/patient_data_template.xlsx [options]

Options:
  --pt-uuid N    Filter to one patient by pt_uuid
  --out PATH     Save JSON output to file (default: print to stdout)
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="ctm-mm",
        description="CTM → MatchMiner import tooling",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "raw-to-mm",
        help="Normalize Excel template → matchminer-compatible JSON ({clinical, genomic})",
    )
    p.add_argument("excel", metavar="EXCEL",
                   help="Path to patient_data_template.xlsx")
    p.add_argument("--pt-uuid", type=int, dest="pt_uuid", metavar="N",
                   help="Filter to one patient by pt_uuid")
    p.add_argument("--out", metavar="PATH",
                   help="Save JSON output to file (default: print to stdout)")

    args = parser.parse_args()

    if args.command == "raw-to-mm":
        _cmd_raw_to_mm(args)


def _build_extras(patients: list, metadata: list, findings: list) -> dict:
    if not patients:
        return {}
    patient = patients[0]
    pt_uuid = patient.pt_uuid

    findings_by_report: dict[int, list] = {}
    for f in [f for f in findings if f.pt_uuid == pt_uuid]:
        findings_by_report.setdefault(f.report_uuid, []).append({
            "gene": f.gene,
            "protein": f.protein,
            "nucleotide": f.nucleotide,
            "variant_type": f.variant_type,
            "result_summary": f.result_summary,
            "raw": f.raw,
        })

    pt_metadata = [m for m in metadata if m.pt_uuid == pt_uuid]
    reports = [
        {
            "source": m.source,
            "test_name": m.test_name,
            "accession_no": m.accession_no,
            "physician": m.physician,
            "date_completed": m.date_completed.isoformat() if m.date_completed else None,
            "findings": findings_by_report.get(m.report_uuid, []),
        }
        for m in pt_metadata
    ]

    return {"patient": patient.model_dump(), "reports": reports}


def _cmd_raw_to_mm(args) -> None:
    from ctm.transformers.excel_reader import read_and_normalize
    from ctm.transformers.to_matchminer import to_clinical, to_genomic_docs

    excel_path = Path(args.excel)
    if not excel_path.exists():
        print(f"Error: file not found: {excel_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading {excel_path} ...", file=sys.stderr)
    patients, metadata, findings = read_and_normalize(excel_path, pt_uuid_filter=args.pt_uuid)

    if not patients:
        print("No patients found (check --pt-uuid or pt_general sheet).", file=sys.stderr)
        sys.exit(1)

    print(f"  {len(patients)} patient(s)  {len(metadata)} report(s)  {len(findings)} finding(s)",
          file=sys.stderr)

    findings_by_pt: dict[int, list] = defaultdict(list)
    for f in findings:
        findings_by_pt[f.pt_uuid].append(f)

    metadata_by_pt: dict[int, list] = defaultdict(list)
    for m in metadata:
        metadata_by_pt[m.pt_uuid].append(m)

    all_clinical: list[dict] = []
    all_genomic: list[dict] = []

    for patient in patients:
        pt_findings = findings_by_pt[patient.pt_uuid]
        pt_meta = metadata_by_pt[patient.pt_uuid]

        dates = [m.date_completed for m in pt_meta if m.date_completed]
        report_date = max(dates).isoformat() if dates else None

        clinical = to_clinical(patient, pt_findings, report_date=report_date)
        genomic = to_genomic_docs(patient, pt_findings, clinical_id=None)

        all_clinical.append(clinical)
        all_genomic.extend(genomic)

        print(f"  pt_uuid={patient.pt_uuid}  mrn={patient.mrn}  "
              f"→ {len(genomic)} genomic doc(s)", file=sys.stderr)

    output = {
        "clinical": all_clinical,
        "genomic": all_genomic,
        "extras": _build_extras(patients, metadata, findings),
    }
    json_str = json.dumps(output, indent=2, default=str)

    if args.out:
        Path(args.out).write_text(json_str)
        print(f"Saved → {args.out}", file=sys.stderr)
    else:
        print(json_str)


if __name__ == "__main__":
    main()
