"""ctm-mm — MatchMiner import tooling CLI.

Usage:
  ctm-mm patients PATH/TO/patient_data.xlsx [options]
  ctm-mm trials [--amc [XML]] [--ct JSON] [--ddots [JSON]] [--sparrow XLSX] [--west XLSX] --out PATH
  ctm-mm trials-diff [--new JSON] --out-prefix PREFIX [--master JSON] [--db NAME] [--no-disk]
  ctm-mm trials-curate --trials JSON --out JSON --cache JSON
  ctm-mm trials-confidence-split --trials JSON --high-confidence-out JSON --needs-curation-out JSON  [BETA]
  ctm-mm trials-merge --unchanged JSON --changed JSON --out JSON

Options:
  --pt-uuid N[,N...]  Filter to one or more patients by pt_uuid, comma-separated (patients command)
  --out PATH     Save output to file (default: print to stdout)
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from ctm.paths import cache_dir, cache_path, load_env

_CURATE_CACHE = ".trials_curate_cache.json"
_DIAGNOSIS_CACHE = ".diagnosis_extraction_cache.json"

# Sentinels for a bare `--ddots` / `--amc` (no path): pull from the source rather
# than read a file. Omitting the flag entirely still means "skip this source", so a
# forgotten flag never triggers a live pull.
_DDOTS_FETCH = "<fetch>"
_AMC_FETCH = "<fetch>"


def main() -> None:
    # trials-curate and trials-confidence-split reach UMGPT via build_client()
    load_env()

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
    p_patients.add_argument("--pt-uuid", dest="pt_uuid", metavar="N[,N...]",
                            help="Filter to one or more patients by pt_uuid (comma-separated, e.g. 6,7,8,9)")
    p_patients.add_argument("--out", metavar="PATH",
                            help="Save JSON output to file (default: print to stdout)")

    p_trials = sub.add_parser(
        "trials",
        help="Normalize raw trial sources → MatchMiner CTML JSON",
    )
    p_trials.add_argument("--amc", metavar="XML", nargs="?", const=_AMC_FETCH,
                          help="Pull AMC trials from the OCTSU XML feed (override with "
                               "AMC_FEED_URL). Pass a path to read a local export instead")
    p_trials.add_argument("--ct", metavar="JSON", help="Path to ClinicalTrials.gov JSON (single study or search response)")
    p_trials.add_argument("--sparrow", metavar="XLSX",
                          help="Path to the Sparrow marketing trials Excel sheet (NCT numbers are "
                               "resolved against ClinicalTrials.gov). Superseded by --ddots")
    # Optional value: bare --ddots queries the API, --ddots FILE replays a saved
    # response. Omitting it entirely still means "no sparrow-api trials this run",
    # so a forgotten flag never triggers a live pull.
    p_trials.add_argument("--ddots", metavar="JSON", nargs="?", const=_DDOTS_FETCH,
                          help="Pull the Sparrow trial list from the DDOTS API (needs "
                               "DDOTS_API_KEY and DDOTS_SECRET_KEY in .env). Pass a path to "
                               "replay a saved /protocol response instead. NCT numbers are "
                               "resolved against ClinicalTrials.gov; entity is 'sparrow-api'")
    p_trials.add_argument("--ddots-hospital-id", dest="ddots_hospital_id", metavar="ID",
                          default=None,
                          help="DDOTS hospital_id to scope a bare --ddots fetch (default: "
                               "DDOTS_HOSPITAL_ID, else 18 = Sparrow). The registry is shared "
                               "across institutions, so an unscoped query returns other "
                               "hospitals' protocols")
    p_trials.add_argument("--ddots-status-short", dest="ddots_status_short", metavar="CODE",
                          default="O",
                          help="DDOTS status_short filter for a bare --ddots fetch (default: O = open). "
                               "Pass an empty string for every status")
    p_trials.add_argument("--west", metavar="XLSX",
                          help="Path to the UMH-West trials Excel template (NCT numbers are "
                               "resolved against ClinicalTrials.gov)")
    p_trials.add_argument("--out", metavar="PATH",
                          help="Save MatchMiner CTML JSON output to file. Required unless --no-disk")
    # Default True through the 1.x line, like every other migrated stage.
    p_trials.add_argument("--disk", action=argparse.BooleanOptionalAction, default=True,
                          help="Write the JSON output in addition to MongoDB (default: enabled)")
    p_trials.add_argument("--db", metavar="NAME",
                          help="Override MONGO_DBNAME for this run's 00_raw_trials and "
                               "01_normalized_trials collections")
    p_trials.add_argument("--run-date", dest="run_date", metavar="YYYY-MM-DD",
                          help="Run date stamped on stored documents (default: today)")

    p_trials_diff = sub.add_parser(
        "trials-diff",
        help="Split a fresh trial normalization into unchanged/changed/deleted vs. the previous master",
    )
    p_trials_diff.add_argument("--new", metavar="JSON",
                               help="Fresh normalized trials JSON from ctm-mm trials. Omit to read "
                                    "the 01_normalized_trials collection instead")
    p_trials_diff.add_argument("--master", metavar="JSON",
                               help="Previous master trials JSON. Omit to read the master from "
                                    "MONGO_MASTER_COLLECTION in MONGO_MASTER_DBNAME instead")
    # Default True through the 1.x line so the file-based pipeline keeps working
    # while stages migrate one at a time. Flipping this single default to False,
    # across every migrated stage at once, is the 2.0.0 breaking change.
    p_trials_diff.add_argument("--disk", action=argparse.BooleanOptionalAction, default=True,
                               help="Write the three JSON files in addition to MongoDB "
                                    "(default: enabled). --no-disk stores to MongoDB only")
    p_trials_diff.add_argument("--out-prefix", metavar="PREFIX",
                               help="Output path prefix; writes PREFIX-unchanged.json, "
                                    "PREFIX-changed.json, PREFIX-deleted.json. Required unless --no-disk")
    p_trials_diff.add_argument("--db", metavar="NAME",
                               help="Override MONGO_DBNAME for this run's 02_diff_trials collection")
    p_trials_diff.add_argument("--master-db", dest="master_db", metavar="NAME",
                               help="Override MONGO_MASTER_DBNAME — the database the master is read from")
    p_trials_diff.add_argument("--master-collection", dest="master_collection", metavar="NAME",
                               help="Override MONGO_MASTER_COLLECTION (default 06_master_trials)")
    p_trials_diff.add_argument("--run-date", dest="run_date", metavar="YYYY-MM-DD",
                               help="Run date stamped on stored documents (default: today)")
    p_trials_diff.add_argument("--allow-empty-master", dest="allow_empty_master", action="store_true",
                               help="Permit an empty master; every trial routes to 'changed'. "
                                    "Only correct on the first-ever run")

    p_trials_curate = sub.add_parser(
        "trials-curate",
        help="[DEPRECATED] Use `ctm-llm biomarkers`. Forwards there; removed in 2.0.0",
    )
    p_trials_curate.add_argument("--trials", metavar="JSON",
                                 help="Drafted trials JSON. Omit to read the "
                                      "03_ctml_drafted_trials collection")
    p_trials_curate.add_argument("--out", metavar="JSON",
                                 help="Output path for the curated trials JSON. Required unless --no-disk")
    p_trials_curate.add_argument("--disk", action=argparse.BooleanOptionalAction, default=True,
                                 help="Write the JSON output in addition to MongoDB (default: enabled)")
    p_trials_curate.add_argument("--db", metavar="NAME",
                                 help="Override MONGO_DBNAME for this run")
    p_trials_curate.add_argument("--run-date", dest="run_date", metavar="YYYY-MM-DD",
                                 help="Override the run_date inherited from the source documents")
    p_trials_curate.add_argument("--cache", default=None, metavar="JSON",
                                 help="Biomarker-scan LLM response cache "
                                      f"(default: {cache_dir() / _CURATE_CACHE})")
    p_trials_curate.add_argument("--kb", default=None, metavar="JSON",
                                 help="Known gene/variant knowledge base (default: the copy shipped "
                                      "with the package)")

    p_trials_confidence_split = sub.add_parser(
        "trials-confidence-split",
        help="[BETA] Split a trials-curate output into high-confidence / needs-curation buckets "
             "— experimental, thresholds still being tuned; likely to be folded into trials-curate later",
    )
    p_trials_confidence_split.add_argument("--trials", required=True, metavar="JSON",
                                           help="trials-curate output JSON (has _llm_curation.biomarker_references)")
    p_trials_confidence_split.add_argument("--high-confidence-out", required=True, metavar="JSON",
                                           help="Output path for trials safe to auto-pass")
    p_trials_confidence_split.add_argument("--needs-curation-out", required=True, metavar="JSON",
                                           help="Output path for trials still needing a human curator")
    p_trials_confidence_split.add_argument("--allowed-biomarker-types", default="", metavar="TYPES",
                                           help="Comma-separated biomarker_references types considered safe to "
                                                "auto-pass (e.g. ihc,other). A trial with no biomarker_references "
                                                "always passes this check; one is still needed for a diagnosis.")
    p_trials_confidence_split.add_argument("--recover-diagnosis", action="store_true",
                                           help="For trials missing an oncotree diagnosis in match, attempt LLM "
                                                "extraction from _raw.full_title/_raw.summary_obj")
    p_trials_confidence_split.add_argument("--cache", default=None, metavar="JSON",
                                           help="Cache file for diagnosis-recovery LLM calls (only used with "
                                                f"--recover-diagnosis; default: {cache_dir() / _DIAGNOSIS_CACHE})")

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
    elif args.command == "trials-curate":
        _cmd_trials_curate(args)
    elif args.command == "trials-confidence-split":
        _cmd_trials_confidence_split(args)
    elif args.command == "trials-merge":
        _cmd_trials_merge(args)


def _check_disk_args(args, out_flag: str) -> None:
    """Reject the two contradictory disk states.

    ``out_flag`` names the output argument because it differs per subcommand
    (--out for trials, --out-prefix for trials-diff). Writing no files while an
    output path was named, or --disk with nowhere to write, are both worth an
    error rather than a silent no-op.
    """
    out_value = getattr(args, out_flag.lstrip("-").replace("-", "_"))
    if args.disk and not out_value:
        print(f"Error: {out_flag} is required (pass --no-disk to store only to MongoDB)",
              file=sys.stderr)
        sys.exit(1)
    if out_value and not args.disk:
        print(f"Error: {out_flag} has no effect with --no-disk", file=sys.stderr)
        sys.exit(1)


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

    pt_uuid_filter = (
        {int(u.strip()) for u in args.pt_uuid.split(",") if u.strip()}
        if args.pt_uuid else None
    )

    print(f"Reading {excel_path} ...", file=sys.stderr)
    patients, metadata, findings = read_and_normalize(excel_path, pt_uuid_filter=pt_uuid_filter)

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
    from ctm import db as ctm_db
    from ctm.transformers.amc_xml_to_raw import load as load_amc
    from ctm.transformers.ctgov_to_raw import from_search_response, from_study
    from ctm.transformers.raw_amc_to_ctml import to_ctml_dict as amc_to_ctml
    from ctm.transformers.raw_ctgov_to_ctml import to_ctml_dict as ctgov_to_ctml

    # Checked up front: a missing --out or Mongo variable should fail before the
    # first source is fetched, not after paying for a feed pull.
    _check_disk_args(args, "--out")
    config = ctm_db.mongo_config()

    trials: list[dict] = []

    if args.amc:
        from ctm.transformers import amc_xml_to_raw

        if args.amc == _AMC_FETCH:
            print("Fetching the AMC XML feed ...", file=sys.stderr)
            try:
                raw_trials = amc_xml_to_raw.fetch()
            except RuntimeError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
        else:
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
        from ctm.transformers.raw_sparrow_to_ctml import to_ctml_dict as sparrow_to_ctml
        from ctm.transformers.sparrow_xlsx_to_raw import load as load_sparrow
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

    if args.ddots:
        from ctm.transformers import ddots_to_raw
        from ctm.transformers.raw_ddots_to_ctml import to_ctml_dict as ddots_to_ctml

        if args.ddots == _DDOTS_FETCH:
            print("Querying the DDOTS API ...", file=sys.stderr)
            try:
                payload = ddots_to_raw.fetch(
                    status_short=args.ddots_status_short or None,
                    hospital_id=args.ddots_hospital_id,
                )
            except ddots_to_raw.DdotsApiError as exc:
                # Reported in a 200 body, so say plainly that no trials were read
                # rather than letting it read as an empty result set.
                print(f"Error: {exc}", file=sys.stderr)
                if exc.is_rate_limited:
                    print("  DDOTS rate-limits requests — wait before retrying.",
                          file=sys.stderr)
                sys.exit(1)

            raw_ddots = ddots_to_raw.to_raw_trials(payload)
        else:
            ddots_path = Path(args.ddots)
            if not ddots_path.exists():
                print(f"Error: file not found: {ddots_path}", file=sys.stderr)
                sys.exit(1)
            print(f"Reading DDOTS JSON {ddots_path} ...", file=sys.stderr)
            try:
                raw_ddots = ddots_to_raw.load(ddots_path)
            except ddots_to_raw.DdotsApiError as exc:
                print(f"Error: {ddots_path} holds a DDOTS error response, not trial data: {exc}",
                      file=sys.stderr)
                sys.exit(1)

        print(f"  {len(raw_ddots)} DDOTS trial(s) with NCT numbers — "
              "fetching from ClinicalTrials.gov ...", file=sys.stderr)
        for t in raw_ddots:
            try:
                trials.append(ddots_to_ctml(t))
                print(f"    fetched {t.nct_id}", file=sys.stderr)
            except ValueError as exc:
                print(f"  Warning: {exc} (skipping)", file=sys.stderr)
            except Exception as exc:
                print(f"  Warning: failed to fetch {t.nct_id}: {exc} (skipping)", file=sys.stderr)

    if args.west:
        from ctm.transformers.raw_west_to_ctml import to_ctml_dict as west_to_ctml
        from ctm.transformers.west_xlsx_to_raw import load as load_west
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
        print("Error: no trials normalized (use --amc, --ct, --ddots, --sparrow, or --west)",
              file=sys.stderr)
        sys.exit(1)

    from ctm.trials_lifecycle import compute_trial_hash
    for t in trials:
        t["trial_hash"] = compute_trial_hash(t)

    # First stage in the chain, so this is the one place that legitimately reads the
    # clock: there is no upstream run_date to inherit. Every later stage inherits
    # from here, which is what keeps one weekly run on a single timeline.
    run_date = args.run_date or date.today().isoformat()
    target_db = args.db or config["dbname"]
    print(f"Target: {target_db}.{ctm_db.NORMALIZED_COLLECTION} (run_date {run_date})",
          file=sys.stderr)

    # Cheap and deterministic, so batch replaces are fine here — unlike the LLM
    # stages, an interrupted run costs nothing but time to redo.
    database = ctm_db.get_database(config, target_db)

    # The verbatim source record for each trial, keyed on the same trial_hash as
    # its normalization so the two collections join exactly. Kept so a
    # normalization can be re-derived, or a parser change re-run, without going
    # back to the source system — which for DDOTS costs a rate-limited request.
    ctm_db.replace_collection(
        database,
        ctm_db.RAW_COLLECTION,
        [ctm_db.stamp({"entity": t.get("entity"),
                       "protocol_no": t.get("protocol_no"),
                       "nct_id": t.get("nct_id"),
                       "trial_hash": t["trial_hash"],
                       "_raw": t.get("_raw", {})},
                      "ctm-mm trials", run_date)
         for t in trials],
        ctm_db.DIFF_UNIQUE_KEY,
        ctm_db.DIFF_LOOKUP_KEYS,
    )
    print(f"Stored {len(trials)} doc(s) → {target_db}.{ctm_db.RAW_COLLECTION}",
          file=sys.stderr)

    ctm_db.replace_collection(
        database,
        ctm_db.NORMALIZED_COLLECTION,
        [ctm_db.stamp(t, "ctm-mm trials", run_date) for t in trials],
        ctm_db.DIFF_UNIQUE_KEY,
        ctm_db.DIFF_LOOKUP_KEYS,
    )
    print(f"Stored {len(trials)} doc(s) → {target_db}.{ctm_db.NORMALIZED_COLLECTION}",
          file=sys.stderr)

    if args.disk:
        out_path = Path(args.out)
        out_path.write_text(json.dumps(trials, indent=2, default=str))
        print(f"Saved {len(trials)} trial(s) → {out_path}", file=sys.stderr)


def _read_trials_json(path: Path, what: str) -> list[dict]:
    """Read a trials JSON file, failing with a readable message rather than a traceback.

    A truncated or empty file is a plausible real failure — an interrupted write, a
    stray `/dev/null` — and json.loads' JSONDecodeError says nothing about which
    file or which argument was at fault.
    """
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        print(f"Error: {what} file {path} is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)


def _read_new_trials(args, config, target_db) -> tuple[list[dict], str]:
    """The fresh normalization, from --new if given or else 01_normalized_trials."""
    from ctm import db as ctm_db

    if args.new:
        new_path = Path(args.new)
        if not new_path.exists():
            print(f"Error: file not found: {new_path}", file=sys.stderr)
            sys.exit(1)
        return _read_trials_json(new_path, "--new"), str(new_path)

    trials = ctm_db.read_collection(
        ctm_db.get_database(config, target_db), ctm_db.NORMALIZED_COLLECTION,
        keep_metadata=True,
    )
    described = f"{target_db}.{ctm_db.NORMALIZED_COLLECTION}"
    if not trials:
        print(f"Error: no trials in {described}. Run ctm-mm trials first, or pass --new.",
              file=sys.stderr)
        sys.exit(1)
    return trials, described


def _read_master(args, config) -> tuple[list[dict], str]:
    """The previous master plus a human-readable description of where it came from.

    A file given via --master wins over the master collection. A --master path
    that does not exist is an error rather than an empty master: the old
    `if exists() else []` fallback meant a typo silently routed every trial to
    `changed`, re-running full curation.
    """
    from ctm import db as ctm_db

    if args.master:
        master_path = Path(args.master)
        if not master_path.exists():
            print(f"Error: master file not found: {master_path}", file=sys.stderr)
            sys.exit(1)
        return _read_trials_json(master_path, "--master"), str(master_path)

    db_name = args.master_db or config["master_dbname"]
    collection = args.master_collection or config["master_collection"]
    trials = ctm_db.read_collection(ctm_db.get_database(config, db_name), collection)
    return trials, f"{db_name}.{collection}"


def _cmd_trials_diff(args) -> None:
    from ctm import db as ctm_db
    from ctm.trials_lifecycle import split_by_eligibility

    # Validated up front: a missing variable should fail before any files are
    # written, not after. MONGO_MASTER_DBNAME is only required when the master
    # comes from Mongo and --master-db has not already named the database.
    _check_disk_args(args, "--out-prefix")

    config = ctm_db.mongo_config(require_master=not args.master and not args.master_db)
    target_db = args.db or config["dbname"]

    new_trials, new_source = _read_new_trials(args, config, target_db)
    print(f"New: {len(new_trials)} trial(s) from {new_source}", file=sys.stderr)
    master_trials, master_source = _read_master(args, config)

    if not master_trials and not args.allow_empty_master:
        print(
            f"Error: master is empty ({master_source}). Every trial would route to "
            "'changed', re-running full curation. Pass --allow-empty-master if this "
            "really is the first-ever run.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Master: {len(master_trials)} trial(s) from {master_source}", file=sys.stderr)

    # --run-date wins; otherwise inherit from the normalized documents so this stage
    # stays on the run that produced them. A JSON file carries no run to inherit
    # from, so there today is the only honest answer.
    run_date = args.run_date or ctm_db.inherited_run_date(
        new_trials, fallback=date.today().isoformat())
    print(f"Target: {target_db}.{ctm_db.DIFF_COLLECTION} (run_date {run_date})", file=sys.stderr)

    # Upstream provenance has done its job now that run_date is known; it must not
    # reach the JSON files, and each stage re-stamps rather than inherits.
    new_trials = [ctm_db.strip_metadata(t) for t in new_trials]
    master_trials = [ctm_db.strip_metadata(t) for t in master_trials]

    unchanged, changed, deleted = split_by_eligibility(new_trials, master_trials)

    print(f"{len(unchanged)} unchanged, {len(changed)} changed, {len(deleted)} deleted", file=sys.stderr)

    # Files before Mongo when asked for: a standalone mongod is not a replica
    # set, so there is no transaction to roll back a partial write, and complete
    # files on disk keep the command safely re-runnable.
    if args.disk:
        prefix = args.out_prefix
        Path(f"{prefix}-unchanged.json").write_text(json.dumps(unchanged, indent=2, default=str))
        Path(f"{prefix}-changed.json").write_text(json.dumps(changed, indent=2, default=str))
        Path(f"{prefix}-deleted.json").write_text(json.dumps(deleted, indent=2, default=str))
        print(f"Saved → {prefix}-unchanged.json, {prefix}-changed.json, {prefix}-deleted.json",
              file=sys.stderr)

    # Stamped copies, so the files above stay byte-identical to pre-Mongo output.
    docs = [
        ctm_db.stamp(trial, "ctm-mm trials-diff", run_date, diff_status=status)
        for status, bucket in (("unchanged", unchanged), ("changed", changed), ("deleted", deleted))
        for trial in bucket
    ]
    ctm_db.replace_collection(
        ctm_db.get_database(config, target_db),
        ctm_db.DIFF_COLLECTION,
        docs,
        ctm_db.DIFF_UNIQUE_KEY,
        ctm_db.DIFF_LOOKUP_KEYS,
    )
    print(f"Stored {len(docs)} doc(s) → {target_db}.{ctm_db.DIFF_COLLECTION}", file=sys.stderr)


def _cmd_trials_curate(args) -> None:
    """DEPRECATED shim forwarding to `ctm-llm biomarkers`.

    Rebuilds the equivalent argv rather than calling the implementation directly,
    so the alias exercises exactly the same code path a real `ctm-llm biomarkers`
    invocation does and cannot drift from it. Removed in 2.0.0.
    """
    from ctm.llm_cli import main as llm_main

    print(
        "DEPRECATED: `ctm-mm trials-curate` is now `ctm-llm biomarkers` and will be "
        "removed in 2.0.0. Forwarding...",
        file=sys.stderr,
    )

    argv = ["biomarkers"]
    for flag, value in (("--trials", args.trials), ("--out", args.out),
                        ("--cache", args.cache), ("--kb", args.kb),
                        ("--db", args.db), ("--run-date", args.run_date)):
        if value:
            argv += [flag, str(value)]
    if not args.disk:
        argv.append("--no-disk")
    llm_main(argv)


def _cmd_trials_confidence_split(args) -> None:
    from ctm.transformers.confidence_split import load_cache, save_cache, split_by_confidence

    trials = json.loads(Path(args.trials).read_text())
    allowed_types = {t.strip() for t in args.allowed_biomarker_types.split(",") if t.strip()}

    client = cache = valid_oncotree = None
    cache_file = Path(args.cache) if args.cache else cache_path(_DIAGNOSIS_CACHE)

    if args.recover_diagnosis:
        from ctm.transformers.eligibility_to_ctml import build_client, fetch_oncotree_names
        client = build_client()
        valid_oncotree = fetch_oncotree_names()
        cache = load_cache(cache_file)

    high_confidence, needs_curation = split_by_confidence(
        trials, allowed_types,
        recover_diagnosis=args.recover_diagnosis,
        client=client, cache=cache, valid_oncotree=valid_oncotree,
    )

    if args.recover_diagnosis:
        save_cache(cache, cache_file)

    Path(args.high_confidence_out).write_text(json.dumps(high_confidence, indent=2, default=str))
    Path(args.needs_curation_out).write_text(json.dumps(needs_curation, indent=2, default=str))
    print(f"{len(high_confidence)} high-confidence, {len(needs_curation)} needs curation", file=sys.stderr)
    print(f"Saved → {args.high_confidence_out}, {args.needs_curation_out}", file=sys.stderr)


def _cmd_trials_merge(args) -> None:
    from ctm.trials_lifecycle import merge_master

    unchanged = json.loads(Path(args.unchanged).read_text())
    changed = json.loads(Path(args.changed).read_text())

    master = merge_master(unchanged, changed)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(master, indent=2, default=str))
    print(f"Saved {len(master)} trial(s) → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
