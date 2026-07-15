"""ctm-mm — MatchMiner import tooling CLI.

Usage:
  ctm-mm patients PATH/TO/patient_data_template.xlsx [options]
  ctm-mm trials [--sparrow YAML] [--amc YAML] [--west YAML] --out PATH
  ctm-mm trials-diff --new JSON --master JSON --out-prefix PREFIX
  ctm-mm trials-merge --unchanged JSON --changed JSON --out JSON

Options:
  --pt-uuid N    Filter to one patient by pt_uuid (patients command)
  --out PATH     Save output to file (default: print to stdout)
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def main() -> None:

    parser = argparse.ArgumentParser(
        prog="ctm-mm",
        description="CTM → MatchMiner import tooling",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_patients = sub.add_parser(
        "patients",
        help="Normalize Excel template → matchminer-compatible JSON ({clinical, genomic})",
    )
    p_patients.add_argument("excel", metavar="EXCEL",
                            help="Path to patient_data_template.xlsx")
    p_patients.add_argument("--pt-uuid", type=int, dest="pt_uuid", metavar="N",
                            help="Filter to one patient by pt_uuid")
    p_patients.add_argument("--out", metavar="PATH",
                            help="Save JSON output to file (default: print to stdout)")

    p_trials = sub.add_parser(
        "trials",
        help="Normalize raw trial sources → MatchMiner CTML JSON",
    )
    p_trials.add_argument("--amc", metavar="XML", help="Path to AMC trials XML export")
    p_trials.add_argument("--ct", metavar="JSON", help="Path to ClinicalTrials.gov JSON (single study or search response)")
    p_trials.add_argument("--sparrow", metavar="XLSX", help="Path to Sparrow marketing trials Excel sheet")
    p_trials.add_argument("--west", metavar="FILE", help="Path to West trials (not yet implemented)")
    p_trials.add_argument("--out", metavar="PATH", required=True,
                          help="Save MatchMiner CTML JSON output to file")

    p_trials_diff = sub.add_parser(
        "trials-diff",
        help="Split a fresh trial normalization into unchanged/changed/deleted vs. the previous master",
    )
    p_trials_diff.add_argument("--new", required=True, metavar="JSON",
                               help="Fresh normalized trials JSON from ctm-mm trials")
    p_trials_diff.add_argument("--master", required=True, metavar="JSON",
                               help="Previous dated master trials JSON (missing/empty is fine on the first-ever run)")
    p_trials_diff.add_argument("--out-prefix", required=True, metavar="PREFIX",
                               help="Output path prefix; writes PREFIX-unchanged.json, PREFIX-changed.json, PREFIX-deleted.json")

    p_trials_merge = sub.add_parser(
        "trials-merge",
        help="Merge carried-forward and freshly-curated trials into a new dated master",
    )
    p_trials_merge.add_argument("--unchanged", required=True, metavar="JSON",
                                help="Unchanged trials JSON from trials-diff")
    p_trials_merge.add_argument("--changed", required=True, metavar="JSON",
                                help="Curated changed trials JSON (after ctm-ctml + manual review)")
    p_trials_merge.add_argument("--out", required=True, metavar="JSON",
                                help="Output path for the new master trials JSON")

    args = parser.parse_args()

    if args.command == "patients":
        _cmd_raw_to_mm(args)
    elif args.command == "trials":
        _cmd_trials(args)
    elif args.command == "trials-diff":
        _cmd_trials_diff(args)
    elif args.command == "trials-merge":
        _cmd_trials_merge(args)


def _build_extras(patients: list, metadata: list, findings: list) -> dict:
    patients_out = {}
    for patient in patients:
        pt_uuid = patient.pt_uuid
        sample_id = patient.mrn or str(patient.pt_uuid)

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

        patients_out[sample_id] = {"patient": patient.model_dump(), "reports": reports}

    return {"patients": patients_out}


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


def _cmd_trials(args) -> None:
    from ctm.transformers.amc_xml_to_raw import load as load_amc
    from ctm.transformers.raw_amc_to_ctml import to_ctml_dict as amc_to_ctml
    from ctm.transformers.ctgov_to_raw import from_study, from_search_response
    from ctm.transformers.raw_ctgov_to_ctml import to_ctml_dict as ctgov_to_ctml

    trials: list[dict] = []

    if args.amc:
        amc_path = Path(args.amc)
        if not amc_path.exists():
            print(f"Error: file not found: {amc_path}", file=sys.stderr)
            sys.exit(1)
        print(f"Reading AMC XML {amc_path} ...", file=sys.stderr)
        raw_trials = load_amc(amc_path)
        print(f"  {len(raw_trials)} AMC trial(s)", file=sys.stderr)
        trials.extend(amc_to_ctml(t) for t in raw_trials)

    if args.ct:
        from ctm.schemas.raw.models import RawCTGovTrial
        ct_path = Path(args.ct)
        if not ct_path.exists():
            print(f"Error: file not found: {ct_path}", file=sys.stderr)
            sys.exit(1)
        print(f"Reading CTGov JSON {ct_path} ...", file=sys.stderr)
        data = json.loads(ct_path.read_text())
        # Three accepted formats:
        #   - RawCTGovTrial dump (from ctm-fetch, has flat "nct_id" key)
        #   - CTGov API single study response (has "protocolSection" key)
        #   - CTGov API search response (has "studies" key)
        if "nct_id" in data:
            raw_ct = [RawCTGovTrial.model_validate(data)]
        elif "studies" in data:
            raw_ct = from_search_response(data)
        else:
            raw_ct = [from_study(data)]
        print(f"  {len(raw_ct)} CTGov trial(s)", file=sys.stderr)
        trials.extend(ctgov_to_ctml(t) for t in raw_ct)

    if args.sparrow:
        from ctm.transformers.sparrow_xlsx_to_raw import load as load_sparrow
        from ctm.transformers.raw_sparrow_to_ctml import to_ctml_dict as sparrow_to_ctml
        sparrow_path = Path(args.sparrow)
        if not sparrow_path.exists():
            print(f"Error: file not found: {sparrow_path}", file=sys.stderr)
            sys.exit(1)
        print(f"Reading Sparrow XLSX {sparrow_path} ...", file=sys.stderr)
        raw_sparrow = load_sparrow(sparrow_path)
        print(f"  {len(raw_sparrow)} valid Sparrow trial(s) — fetching from ClinicalTrials.gov ...", file=sys.stderr)
        for t in raw_sparrow:
            try:
                trials.append(sparrow_to_ctml(t))
                print(f"    fetched {t.nct_id}", file=sys.stderr)
            except ValueError as exc:
                print(f"  Warning: {exc} (skipping)", file=sys.stderr)
            except Exception as exc:
                print(f"  Warning: failed to fetch {t.nct_id}: {exc} (skipping)", file=sys.stderr)

    if args.west:
        from ctm.transformers.west_xlsx_to_raw import load as load_west
        from ctm.transformers.raw_west_to_ctml import to_ctml_dict as west_to_ctml
        west_path = Path(args.west)
        if not west_path.exists():
            print(f"Error: file not found: {west_path}", file=sys.stderr)
            sys.exit(1)
        print(f"Reading West XLSX {west_path} ...", file=sys.stderr)
        raw_west = load_west(west_path)
        print(f"  {len(raw_west)} West trial(s) with NCT numbers — fetching from ClinicalTrials.gov ...", file=sys.stderr)
        for t in raw_west:
            try:
                trials.append(west_to_ctml(t))
                print(f"    fetched {t.nct_id}", file=sys.stderr)
            except ValueError as exc:
                print(f"  Warning: {exc} (skipping)", file=sys.stderr)
            except Exception as exc:
                print(f"  Warning: failed to fetch {t.nct_id}: {exc} (skipping)", file=sys.stderr)

    if not trials:
        print("Error: no trial sources provided (use --amc, --ct, --sparrow, or --west)", file=sys.stderr)
        sys.exit(1)

    from ctm.trials_lifecycle import compute_trial_hash
    for t in trials:
        t["trial_hash"] = compute_trial_hash(t)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(trials, indent=2, default=str))
    print(f"Saved {len(trials)} trial(s) → {out_path}", file=sys.stderr)


def _cmd_trials_diff(args) -> None:
    from ctm.trials_lifecycle import split_by_eligibility

    new_trials = json.loads(Path(args.new).read_text())

    master_path = Path(args.master)
    master_trials = json.loads(master_path.read_text()) if master_path.exists() else []

    unchanged, changed, deleted = split_by_eligibility(new_trials, master_trials)

    prefix = args.out_prefix
    Path(f"{prefix}-unchanged.json").write_text(json.dumps(unchanged, indent=2, default=str))
    Path(f"{prefix}-changed.json").write_text(json.dumps(changed, indent=2, default=str))
    Path(f"{prefix}-deleted.json").write_text(json.dumps(deleted, indent=2, default=str))

    print(f"{len(unchanged)} unchanged, {len(changed)} changed, {len(deleted)} deleted", file=sys.stderr)
    print(f"Saved → {prefix}-unchanged.json, {prefix}-changed.json, {prefix}-deleted.json", file=sys.stderr)


if __name__ == "__main__":
    main()
