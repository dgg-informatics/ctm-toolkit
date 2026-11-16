"""The weekly trials report: what one ``ctm-pipeline`` run changed.

Pure: :func:`build_report` turns the collections a run left behind into a
summary dict, and :func:`render` turns that into the plain text that cron mails
on Monday morning. Gathering the inputs from Mongo is ``trials_report_cli``'s job.

Inputs (all already written by the pipeline):

* the run database's collection names: which stage it reached;
* its ``02_diff_trials``: new, changed, deleted, unchanged;
* its ``04_autofill_trials``: whether a match clause was built, and from what;
* its ``07_filtered_trials`` snapshot: totals and each trial's ``match_level``;
* the previous dated run's ``06_master_trials`` snapshot: a changed trial whose
  key was not there is new, and one stamped ``manual-edit`` there had a hand fix
  that this run replaced.
"""
from ctm.db import CURATED_MANUAL_EDIT
from ctm.trials_lifecycle import trial_key

STAGE_ORDER = (
    "00_raw_trials", "01_normalized_trials", "02_diff_trials",
    "03c_clinical_trials", "03g_genomic_trials", "04_autofill_trials",
    "05_manual_curated_trials", "06_master_trials", "07_filtered_trials",
)
LEVELS = (3, 2, 1, 0)
TITLE_WIDTH = 42


def _key(doc: dict) -> str:
    return doc.get("trial_key") or trial_key(doc)


def _leaves(node) -> tuple[int, int]:
    """(all leaves, genomic leaves) in a match clause."""
    if isinstance(node, list):
        counts = [_leaves(n) for n in node]
        return sum(c[0] for c in counts), sum(c[1] for c in counts)
    if not isinstance(node, dict):
        return 0, 0
    total = genomic = 0
    for k, v in node.items():
        if k in ("clinical", "genomic"):
            total += 1
            genomic += k == "genomic"
        else:
            t, g = _leaves(v)
            total, genomic = total + t, genomic + g
    return total, genomic


def _match(doc: dict):
    steps = (doc.get("treatment_list") or {}).get("step") or []
    return steps[0].get("match") if steps else None


def build_report(data: dict) -> dict:
    collections = data["collections"]
    present = [c for c in STAGE_ORDER if collections and c in collections]
    status = {"ok": "07_filtered_trials" in present,
              "last_stage": present[-1] if present else None}

    previous = {_key(t): t for t in data["previous_master"]}
    have_previous = data["previous_db"] is not None
    autofill = {_key(t): t for t in data["autofill"]}
    levels_by_key = {_key(t): t.get("match_level") for t in data["filtered"]}

    by_status: dict[str, list[dict]] = {}
    for doc in data["diff"]:
        by_status.setdefault(doc.get("diff_status"), []).append(doc)
    changed_docs = by_status.get("changed", [])

    rows = []
    for doc in changed_docs:
        key = _key(doc)
        drafted = autofill.get(key, {})
        report = (drafted.get("_llm_curation") or {}).get("autofill") or {}
        leaves, genomic = _leaves(_match(drafted) or [])
        rows.append({
            "key": key,
            "kind": "NEW" if have_previous and key not in previous else "CHG",
            "entity": doc.get("entity"),
            "title": doc.get("long_title") or doc.get("short_title") or "",
            "level": levels_by_key.get(key),
            "filled": report.get("filled"),
            "reason": report.get("reason"),
            "leaves": leaves,
            "genomic": genomic,
        })
    rows.sort(key=lambda r: (r["kind"] != "NEW", r["key"]))

    new = sum(r["kind"] == "NEW" for r in rows)
    level_counts = dict.fromkeys(LEVELS, 0)
    for level in levels_by_key.values():
        if level in level_counts:
            level_counts[level] += 1

    return {
        "db": data["db"],
        "db_exists": collections is not None,
        "status": status,
        "previous_db": data["previous_db"],
        "totals": {"trials": len(data["filtered"]), "previous": data["previous_filtered"],
                   "levels": level_counts},
        "week": {"new": new, "changed": len(rows) - new,
                 "deleted": len(by_status.get("deleted", [])),
                 "unchanged": len(by_status.get("unchanged", [])),
                 "filled": sum(bool(r["filled"]) for r in rows if r["key"] in autofill),
                 "annotated": sum(r["key"] in autofill for r in rows)},
        "rows": rows,
        "deleted": sorted(_key(d) for d in by_status.get("deleted", [])),
        "look": {
            "manual_edit_replaced": [r["key"] for r in rows
                                     if previous.get(r["key"], {}).get("curated_by")
                                     == CURATED_MANUAL_EDIT],
            "level_0": [r["key"] for r in rows if r["level"] == 0],
            "not_filled": [(r["key"], r["reason"]) for r in rows
                           if r["key"] in autofill and not r["filled"]],
        },
    }


def _short(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[:width - 1] + "…"


def render(report: dict) -> str:
    db, status, totals, week = report["db"], report["status"], report["totals"], report["week"]
    lines = [f"CTM weekly trials — {db}"]

    if not report["db_exists"]:
        lines.append(f"pipeline: DID NOT RUN — no database {db}")
        return "\n".join(lines) + "\n"
    if status["ok"]:
        lines.append("pipeline: OK")
    else:
        lines.append("pipeline: INCOMPLETE — last stage written: "
                     f"{status['last_stage'] or 'nothing'}")
    lines.append("")

    if totals["previous"] is not None:
        since = f" ({totals['trials'] - totals['previous']:+d} vs {report['previous_db']})"
    else:
        since = ""
    levels = "   ".join(f"{lvl}: {n}" for lvl, n in totals["levels"].items())
    lines += [
        f"Totals     07_filtered_trials: {totals['trials']} trials{since}",
        f"           match_level  {levels}",
        "",
        f"This week  new {week['new']} · changed {week['changed']} · "
        f"deleted {week['deleted']} · unchanged {week['unchanged']}",
        f"           match clauses built: {week['filled']} of {week['annotated']}",
        "",
    ]

    if report["rows"]:
        lines.append("New / changed")
        for r in report["rows"]:
            level = "filtered out" if r["level"] is None else f"level {r['level']}"
            if r["filled"] is None:
                built = "not annotated"
            elif r["filled"]:
                built = f"filled  {r['leaves']} leaves ({r['genomic']} genomic)"
            else:
                built = f"SKIPPED {r['reason']}"
            lines.append(f"  {r['kind']:<4} {r['key']:<13} {_short(r['title'], TITLE_WIDTH):<{TITLE_WIDTH}} "
                         f"{(r['entity'] or ''):<6} {level:<12} {built}")
        lines.append("")
    else:
        lines += ["No new or changed trials this week.", ""]

    if report["deleted"]:
        lines += ["Deleted", "  " + ", ".join(report["deleted"]), ""]

    look = report["look"]
    flags = [f"  ! manual edit replaced: {k} (source changed; re-check)"
             for k in look["manual_edit_replaced"]]
    flags += [f"  ! level 0 after AI: {k}" for k in look["level_0"]]
    flags += [f"  ! no match built: {k} ({reason})" for k, reason in look["not_filled"]]
    if not status["ok"]:
        flags.insert(0, "  ! the pipeline did not finish: check the run log and resume "
                        "with ctm-pipeline --start <stage> --db " + db)
    lines += ["Needs a look", *(flags or ["  nothing"])]
    return "\n".join(lines) + "\n"
