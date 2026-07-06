"""ctm-meta — meta-analysis across normalized trial JSON files.

Usage:
  ctm-meta --amc amc.json --sparrow sparrow.json --west west.json --out report.csv

Each input file is the output of `ctm-mm trials`. Produces a multi-section
CSV summarising coverage, overlap, status, phase, age group, data completeness,
and trials with conflicting status across sources.
"""
import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path


def _load(path: str) -> tuple[str, list[dict]]:
    name = Path(path).stem
    with open(path) as f:
        return name, json.load(f)


def _nct(t: dict) -> str | None:
    return t.get("nct_id") or None


def _status(t: dict) -> str:
    return (t.get("status") or "unknown").strip()


def _phase(t: dict) -> str:
    s = t.get("_summary", {})
    return (s.get("phase") or "Unknown").strip() or "Unknown"


def _age(t: dict) -> str:
    s = t.get("_summary", {})
    return (s.get("age") or "Unknown").strip() or "Unknown"


def _has_eligibility(t: dict) -> bool:
    e = t.get("eligibility", {})
    return bool(e.get("inclusion") or e.get("exclusion"))


def _has_drugs(t: dict) -> bool:
    s = t.get("_summary", {})
    return bool(s.get("drugs"))


def _has_match_beyond_age(t: dict) -> bool:
    steps = t.get("treatment_list", {}).get("step", [])
    for step in steps:
        match = step.get("match", [])
        if len(match) > 1:
            return True
        if len(match) == 1:
            clinical = match[0].get("clinical", {})
            keys = set(clinical.keys()) - {"age_numerical"}
            if keys or match[0].get("genomic"):
                return True
    return False


def _write_section(writer, title: str, rows: list[list]):
    writer.writerow([])
    writer.writerow([f"## {title}"])
    for row in rows:
        writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(prog="ctm-meta", description="Meta-analysis across trial sources")
    parser.add_argument("--amc",    metavar="JSON")
    parser.add_argument("--sparrow", metavar="JSON")
    parser.add_argument("--west",   metavar="JSON")
    parser.add_argument("--out",    metavar="CSV", required=True)
    args = parser.parse_args()

    sources: dict[str, list[dict]] = {}
    for flag, path in [("amc", args.amc), ("sparrow", args.sparrow), ("west", args.west)]:
        if path:
            name, trials = _load(path)
            sources[flag] = trials

    if not sources:
        print("Error: provide at least one source (--amc, --sparrow, --west)", file=sys.stderr)
        sys.exit(1)

    source_names = list(sources.keys())

    # NCT sets per source
    nct_sets: dict[str, set] = {
        name: {_nct(t) for t in trials if _nct(t)}
        for name, trials in sources.items()
    }
    all_ncts = set().union(*nct_sets.values())

    # Map NCT → {source: trial}
    nct_map: dict[str, dict[str, dict]] = defaultdict(dict)
    for name, trials in sources.items():
        for t in trials:
            n = _nct(t)
            if n:
                nct_map[n][name] = t

    with open(args.out, "w", newline="") as f:
        writer = csv.writer(f)

        # ── 1. Source Summary ─────────────────────────────────────────────────
        rows = [["Source", "Total Trials", "With NCT ID", "Without NCT ID"]]
        for name, trials in sources.items():
            has = sum(1 for t in trials if _nct(t))
            rows.append([name, len(trials), has, len(trials) - has])
        rows.append(["UNIQUE (all sources)", len(all_ncts), len(all_ncts), ""])
        _write_section(writer, "Source Summary", rows)

        # ── 2. Overlap Matrix ─────────────────────────────────────────────────
        rows = [[""] + source_names]
        for a in source_names:
            row = [a]
            for b in source_names:
                row.append(len(nct_sets[a] & nct_sets[b]) if a != b else len(nct_sets[a]))
            rows.append(row)
        _write_section(writer, "Overlap Matrix (NCT ID)", rows)

        # ── 3. Exclusive to each source ───────────────────────────────────────
        rows = [["Source", "Exclusive Trials (not in any other source)"]]
        for name in source_names:
            others = set().union(*(nct_sets[n] for n in source_names if n != name))
            rows.append([name, len(nct_sets[name] - others)])
        _write_section(writer, "Trials Exclusive to Each Source", rows)

        # ── 4. Status Breakdown ───────────────────────────────────────────────
        all_statuses = sorted({_status(t) for trials in sources.values() for t in trials})
        rows = [["Status"] + source_names]
        for status in all_statuses:
            row = [status]
            for name, trials in sources.items():
                row.append(sum(1 for t in trials if _status(t) == status))
            rows.append(row)
        _write_section(writer, "Status Breakdown", rows)

        # ── 5. Phase Distribution ─────────────────────────────────────────────
        all_phases = sorted({_phase(t) for trials in sources.values() for t in trials})
        rows = [["Phase"] + source_names]
        for phase in all_phases:
            row = [phase]
            for name, trials in sources.items():
                row.append(sum(1 for t in trials if _phase(t) == phase))
            rows.append(row)
        _write_section(writer, "Phase Distribution", rows)

        # ── 6. Age Group Breakdown ────────────────────────────────────────────
        all_ages = sorted({_age(t) for trials in sources.values() for t in trials})
        rows = [["Age Group"] + source_names]
        for age in all_ages:
            row = [age]
            for name, trials in sources.items():
                row.append(sum(1 for t in trials if _age(t) == age))
            rows.append(row)
        _write_section(writer, "Age Group Breakdown", rows)

        # ── 7. Data Completeness ──────────────────────────────────────────────
        metrics = [
            ("Has eligibility criteria", _has_eligibility),
            ("Has drug interventions",   _has_drugs),
            ("Has match tree beyond age", _has_match_beyond_age),
        ]
        rows = [["Metric"] + source_names + ["Notes"]]
        for label, fn in metrics:
            row = [label]
            for name, trials in sources.items():
                n = sum(1 for t in trials if fn(t))
                row.append(f"{n}/{len(trials)}")
            row.append("match tree beyond age = manually curated criteria exist")
            rows.append(row)
        _write_section(writer, "Data Completeness", rows)

        # ── 8. Sponsor / Cooperative Group ───────────────────────────────────
        rows = [["Sponsor", "Count (all sources, deduplicated by NCT)"]]
        sponsor_counter: Counter = Counter()
        for nct, src_map in nct_map.items():
            # Use any source that has summary.sponsor
            for t in src_map.values():
                sponsor = (t.get("_summary", {}).get("sponsor") or "Unknown").strip()
                if sponsor:
                    sponsor_counter[sponsor] += 1
                    break
        for sponsor, count in sponsor_counter.most_common(20):
            rows.append([sponsor, count])
        _write_section(writer, "Top Sponsors (unique trials, top 20)", rows)

        # ── 9. Conflicting Status ─────────────────────────────────────────────
        conflicts = []
        for nct, src_map in nct_map.items():
            if len(src_map) > 1:
                statuses = {s: _status(t) for s, t in src_map.items()}
                if len(set(statuses.values())) > 1:
                    conflicts.append((nct, statuses))

        rows = [["NCT ID"] + source_names]
        for nct, statuses in sorted(conflicts):
            row = [nct]
            for name in source_names:
                row.append(statuses.get(name, "—"))
            rows.append(row)
        if not conflicts:
            rows.append(["No conflicts found"])
        _write_section(writer, "Conflicting Status Across Sources", rows)

    print(f"Saved → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
