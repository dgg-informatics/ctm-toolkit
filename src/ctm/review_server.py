"""``ctm-review``'s server: find a trial in 06, apply a batch of staged actions, serve the page.

Apply never writes trials to Mongo itself. It builds the edited trials, hands them to
``ctm-apply-edits`` (05 → 06 → 07, stamped ``curated_by: "manual-edit"``) as a subprocess,
and writes the ``curation_reviews`` log only when that exits 0. So the log holds only changes
that reached the master.
"""
import hashlib
import json
import logging
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ctm.logging_config import log_event
from ctm.review import (
    MARKER,
    apply_actions,
    build_review_docs,
    iter_leaves,
    link_source,
    validate_node,
)
from ctm.transformers.filter_trials import ENTITY_PRECEDENCE
from ctm.trials_lifecycle import trial_key

log = logging.getLogger(__name__)

REVIEWS_COLLECTION = "curation_reviews"
STDERR_TAIL_LINES = 20
REVIEW_PAGE = Path(__file__).parent / "static" / "review.html"


def run_apply_edits(path: Path, reviewer: str, db: str | None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "ctm.apply_edits_cli", "--file", str(path),
           "--curated-by-user", reviewer]
    if db:
        cmd += ["--db", db]
    return subprocess.run(cmd, capture_output=True, text=True)


@dataclass
class ReviewContext:
    master: object          # 06_master_trials collection
    reviews: object         # curation_reviews collection
    reviewer: str
    db: str | None
    runner: Callable[[Path, str, str | None], subprocess.CompletedProcess] = run_apply_edits


def to_json(obj) -> bytes:
    return json.dumps(obj, default=str).encode()


def fingerprint(treatment_list) -> str:
    """Content hash of a trial's match tree: the "changed since loaded" token.

    Neither ``run_date`` (re-stamped on every row by every merge) nor ``trial_hash``
    (hash of ``_raw`` only) changes when someone edits the match, so the tree itself
    is what's compared.
    """
    serialized = json.dumps(treatment_list or {}, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def find_rows(master, q: str, entity: str | None = None) -> list[dict]:
    """Every 06 row for an NCT ID or AMC protocol number, highest 07 precedence first.

    06 holds one row per (entity, trial_key), and several sources can hold the same
    NCT (AMC keys it by protocol_no, the others by nct_id). Only the highest-precedence
    one survives into 07 (filter_trials), so that is the one worth reviewing; the
    rest are listed on the page.
    """
    q = (q or "").strip()
    if not q:
        return []
    entities = [entity] if entity is not None else list(ENTITY_PRECEDENCE)
    for value in dict.fromkeys((q, q.upper())):
        rows = []
        for ent in entities:
            for field in ("trial_key", "nct_id"):
                row = master.find_one({"entity": ent, field: value})
                if row is not None:
                    rows.append(row)
                    break
        if not rows and entity is None:   # an entity not in ENTITY_PRECEDENCE
            for field in ("trial_key", "nct_id"):
                row = master.find_one({field: value})
                if row is not None:
                    rows.append(row)
                    break
        if rows:
            return [{k: v for k, v in r.items() if k != "_id"} for r in rows]
    return []


def find_trial(master, q: str, entity: str | None = None) -> dict | None:
    rows = find_rows(master, q, entity)
    return rows[0] if rows else None


def trial_payload(trial: dict, master=None) -> dict:
    curation = trial.get("_llm_curation")
    leaves = []
    for i, step in enumerate((trial.get("treatment_list") or {}).get("step") or []):
        for path, leaf in iter_leaves(step.get("match")):
            source, text = link_source(leaf, curation)
            leaves.append({"step_index": i, "path": path, "node": leaf,
                           "marker": next(iter(leaf.values())).get(MARKER),
                           "source_field": source, "source_text": text})
    entities = ([r.get("entity") for r in find_rows(master, trial.get("nct_id") or trial_key(trial))]
                if master is not None else [trial.get("entity")])
    return {"trial": {k: v for k, v in trial.items() if k != "_raw"}, "leaves": leaves,
            "fingerprint": fingerprint(trial.get("treatment_list")),
            "entities": entities or [trial.get("entity")]}


def _fail(error: str, stale=None) -> dict:
    return {"ok": False, "error": error, "stale": stale or []}


def _log_docs(ctx, rows, resolved_per_trial) -> tuple[str, list[dict]]:
    from ctm.db import toolkit_version

    batch_id = str(uuid.uuid4())
    reviewed_at = datetime.now(UTC)
    processed_with = f"ctm-review {toolkit_version()}"
    docs = [doc for row, resolved in zip(rows, resolved_per_trial, strict=True)
            for doc in build_review_docs(row, resolved, reviewer=ctx.reviewer,
                                         reviewed_at=reviewed_at, batch_id=batch_id,
                                         processed_with=processed_with)]
    return batch_id, docs


def apply_batch(ctx: ReviewContext, batch: list[dict]) -> dict:
    """Apply every staged action, all or nothing. See the module docstring."""
    from ctm.db import strip_metadata

    if not batch:
        return _fail("nothing staged")

    rows = []
    for item in batch:
        row = find_trial(ctx.master, item.get("trial_key", ""), entity=item.get("entity"))
        if row is None:
            return _fail(f"{item.get('trial_key')!r} ({item.get('entity')}) is not in the master")
        rows.append(row)

    stale = [item["trial_key"] for item, row in zip(batch, rows, strict=True)
             if (row.get("trial_hash"), fingerprint(row.get("treatment_list")))
             != (item.get("trial_hash"), item.get("fingerprint"))]
    if stale:
        return _fail("changed in the master since you loaded it — remove its staged actions, "
                     "reload it and redo them: " + ", ".join(stale), stale)

    edited, resolved_per_trial = [], []
    for item, row in zip(batch, rows, strict=True):
        try:
            new_tl, resolved = apply_actions(row.get("treatment_list") or {}, item.get("actions") or [])
        except ValueError as exc:
            return _fail(f"{item['trial_key']}: {exc}")
        edited.append({**strip_metadata(row), "treatment_list": new_tl})
        resolved_per_trial.append(resolved)

    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", prefix="ctm-review-", delete=False)
    path = Path(tmp.name)
    keys = [item["trial_key"] for item in batch]
    try:
        with tmp:
            json.dump(edited, tmp, default=str)
        proc = ctx.runner(path, ctx.reviewer, ctx.db)
    finally:
        path.unlink(missing_ok=True)

    failure = None
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").splitlines()[-STDERR_TAIL_LINES:])
        failure = f"ctm-apply-edits failed (exit {proc.returncode}):\n{tail}"
        log_event(log, "review.apply_failed", "ctm-apply-edits failed (exit %d) for %d trial(s)",
                  proc.returncode, len(keys), level=logging.ERROR,
                  returncode=proc.returncode, trial_keys=keys)
        # A later stage (07) can fail after 06 was written. If the master now holds the
        # edits, they happened, and the log must say so.
        reached = all(
            (now := find_trial(ctx.master, item["trial_key"], entity=item.get("entity"))) is not None
            and fingerprint(now.get("treatment_list")) == fingerprint(e["treatment_list"])
            for item, e in zip(batch, edited, strict=True))
        if not reached:
            return _fail(failure)

    batch_id, docs = _log_docs(ctx, rows, resolved_per_trial)
    try:
        if docs:
            ctx.reviews.insert_many(docs)
    except Exception as exc:   # the master already changed; say so instead of hanging the page
        log_event(log, "review.log_failed", "06 updated but curation_reviews insert failed: %s",
                  type(exc).__name__, level=logging.ERROR, batch_id=batch_id, trial_keys=keys)
        return _fail(f"06 was updated, but writing curation_reviews failed ({exc}). "
                     "Do not re-apply; the edits are in the master.")
    if failure:
        return _fail(f"06 was updated and {len(docs)} action(s) logged, but a later stage "
                     f"failed — check 07:\n{failure}")
    log_event(log, "review.applied", "applied %d action(s) on %d trial(s), batch %s",
              len(docs), len(keys), batch_id, batch_id=batch_id, trial_keys=keys,
              actions=len(docs))
    return {"ok": True, "batch_id": batch_id, "trials": keys, "logged": len(docs)}


# ── HTTP ─────────────────────────────────────────────────────────────────────

def make_handler(ctx: ReviewContext) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            log.debug("ctm-review http: " + format, *args)

        def _send(self, status: int, body: bytes, content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj):
            self._send(status, to_json(obj))

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                self._send(200, REVIEW_PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/trial":
                params = parse_qs(url.query)
                q = params.get("q", [""])[0]
                trial = find_trial(ctx.master, q, entity=params.get("entity", [None])[0] or None)
                if trial is None:
                    self._json(404, {"error": "not found"})
                else:
                    self._json(200, trial_payload(trial, ctx.master))
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                self._json(400, {"error": "body must be a JSON object"})
                return
            if self.path == "/api/validate-node":
                try:
                    self._json(200, {"ok": True, "node": validate_node(body.get("text", ""))})
                except ValueError as exc:
                    self._json(200, {"ok": False, "error": str(exc)})
            elif self.path == "/api/apply":
                self._json(200, apply_batch(ctx, body.get("batch") or []))
            else:
                self._json(404, {"error": "not found"})

    return Handler


def serve(ctx: ReviewContext, port: int) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(ctx))
    log_event(log, "review.serving", "ctm-review on http://127.0.0.1:%d as %s — from your laptop: "
              "ssh -L %d:localhost:%d <server>, then open http://localhost:%d",
              port, ctx.reviewer, port, port, port, port=port, reviewer=ctx.reviewer)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("ctm-review stopped; anything not applied is discarded")
    finally:
        httpd.server_close()
