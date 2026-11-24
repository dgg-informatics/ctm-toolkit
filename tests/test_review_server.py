"""ctm-review's lookup, batch apply and HTTP API (src/ctm/review_server.py)."""
import copy
import json
import subprocess
from datetime import UTC, datetime

import pytest
from bson import ObjectId
from mongo_doubles import FakeCollection

from ctm.review import MARKER
from ctm.review_server import ReviewContext, apply_batch, find_trial, fingerprint, trial_payload

LEAF = {"genomic": {"hugo_symbol": "KRAS", MARKER: "auto"}}


def _row(**over):
    row = {
        "_id": ObjectId(), "entity": "sparrow-api", "trial_key": "NCT01234567",
        "nct_id": "NCT01234567", "protocol_no": None, "run_date": "2026-11-20",
        "processed_with": "ctm-mm trials-merge 3.0.0", "trial_hash": "a" * 64,
        "curated_by": "auto", "curated_by_user": "dgg-mipro",
        "curated_at": datetime(2026, 11, 20, 7, tzinfo=UTC),
        "_raw": {"brief_title": "secret-ish raw blob"},
        "_llm_curation": {"_ctml_suggestions": [], "stage_runs": {}},
        "treatment_list": {"step": [{"match": [{"and": [copy.deepcopy(LEAF)]}]}]},
    }
    row.update(over)
    return row


def _amc():
    return _row(entity="amc", trial_key="2021.070", protocol_no="2021.070", nct_id="NCT09999999")


class FakeRunner:
    def __init__(self, returncode=0, stderr=""):
        self.returncode, self.stderr = returncode, stderr
        self.calls, self.written = [], None

    def __call__(self, path, reviewer, db):
        self.calls.append((path, reviewer, db))
        self.written = json.loads(path.read_text())
        return subprocess.CompletedProcess(["ctm-apply-edits"], self.returncode, "", self.stderr)


def _ctx(runner=None, rows=None):
    return ReviewContext(master=FakeCollection(rows if rows is not None else [_row(), _amc()]),
                         reviews=FakeCollection(), reviewer="dane", db=None,
                         runner=runner or FakeRunner())


def _item(key="NCT01234567", actions=None, entity="sparrow-api", trial_hash="a" * 64, fp=None):
    return {"trial_key": key, "entity": entity, "trial_hash": trial_hash,
            "fingerprint": fp or fingerprint(_row()["treatment_list"]),
            "actions": actions if actions is not None else
            [{"action": "verified", "step_index": 0, "path": "match[0].and[0]", "note": "ok"}]}


# ── lookup ───────────────────────────────────────────────────────────────────

def test_find_trial_variants():
    master = FakeCollection([_row(), _amc()])
    for q in (" nct01234567 ", "NCT01234567"):
        assert find_trial(master, q)["trial_key"] == "NCT01234567"
    assert find_trial(master, "2021.070")["entity"] == "amc"
    assert find_trial(master, "NCT09999999")["entity"] == "amc"
    assert find_trial(master, "NCT00000000") is None
    assert find_trial(master, "   ") is None
    assert "_id" not in find_trial(master, "NCT01234567")


def test_trial_payload_drops_raw_and_lists_leaves():
    master = FakeCollection([_row()])
    payload = trial_payload(find_trial(master, "NCT01234567"), master)
    assert "_raw" not in payload["trial"]
    assert payload["fingerprint"] == fingerprint(_row()["treatment_list"])
    assert payload["leaves"] == [{"step_index": 0, "path": "match[0].and[0]", "node": LEAF,
                                  "marker": "auto", "source_field": None, "source_text": None}]


# ── apply ────────────────────────────────────────────────────────────────────

def test_apply_success_logs_and_runs_apply_edits():
    ctx = _ctx()
    result = apply_batch(ctx, [_item()])
    assert result["ok"] is True and result["logged"] == 1 and result["trials"] == ["NCT01234567"]
    (path, reviewer, db), = ctx.runner.calls
    assert reviewer == "dane" and db is None
    assert not path.exists()   # temp file cleaned up
    (written,) = ctx.runner.written
    assert "_id" not in written and "run_date" not in written and "trial_key" not in written
    assert written["curated_at"].startswith("2026-11-20")
    assert written["treatment_list"]["step"][0]["match"][0]["and"][0]["genomic"][MARKER] == "verified"
    (doc,) = ctx.reviews.inserted
    assert doc["action"] == "verified" and doc["batch_id"] == result["batch_id"]
    assert doc["note"] == "ok" and doc["processed_with"].startswith("ctm-review ")


def test_apply_failure_logs_nothing():
    ctx = _ctx(FakeRunner(3, "boom\n" * 50))
    result = apply_batch(ctx, [_item()])
    assert result["ok"] is False
    assert "exit 3" in result["error"] and "boom" in result["error"]
    assert result["error"].count("boom") == 20
    assert ctx.reviews.inserted == []


def test_apply_refuses_whole_batch_when_stale():
    ctx = _ctx()
    other = {"step": [{"match": [{"and": [copy.deepcopy(LEAF), copy.deepcopy(LEAF)]}]}]}
    result = apply_batch(ctx, [_item(fp=fingerprint(other)), _item("2021.070", entity="amc")])
    assert result["ok"] is False and result["stale"] == ["NCT01234567"]
    assert ctx.runner.calls == [] and ctx.reviews.inserted == []


def test_apply_refuses_when_match_changed_same_day():
    """run_date is re-stamped on every merge and trial_hash ignores treatment_list, so only
    the match content itself can tell that someone edited this trial since it was loaded."""
    edited = _row(treatment_list={"step": [{"match": []}]})
    ctx = _ctx(rows=[edited])
    result = apply_batch(ctx, [_item()])
    assert result["ok"] is False and result["stale"] == ["NCT01234567"]
    assert ctx.runner.calls == []


def test_apply_ignores_run_date_restamp():
    ctx = _ctx(rows=[_row(run_date="2026-11-23")])
    assert apply_batch(ctx, [_item()])["ok"] is True


def test_apply_partial_failure_logs_when_master_changed():
    """05 and 06 succeeded, then 07 failed: the master holds the edit, so the log must too."""
    class ReachedMaster(FakeRunner):
        def __call__(self, path, reviewer, db):
            result = super().__call__(path, reviewer, db)
            ctx.master.docs[0]["treatment_list"] = self.written[0]["treatment_list"]
            return result

    ctx = _ctx(ReachedMaster(1, "trials-filter failed\n"))
    result = apply_batch(ctx, [_item()])
    assert result["ok"] is False
    assert "06" in result["error"] and "trials-filter failed" in result["error"]
    assert len(ctx.reviews.inserted) == 1


def test_apply_log_insert_failure_reports():
    ctx = _ctx()

    def boom(docs):
        raise RuntimeError("mongo down")
    ctx.reviews.insert_many = boom
    result = apply_batch(ctx, [_item()])
    assert result["ok"] is False and "curation_reviews" in result["error"]


def test_find_trial_prefers_filter_precedence():
    west = _row(entity="west")
    amc = _row(entity="amc", trial_key="2021.001", protocol_no="2021.001")
    master = FakeCollection([west, amc])
    assert find_trial(master, "NCT01234567")["entity"] == "amc"
    assert find_trial(master, "NCT01234567", entity="west")["entity"] == "west"
    assert trial_payload(find_trial(master, "NCT01234567"), master)["entities"] == ["amc", "west"]


def test_apply_targets_the_loaded_entity():
    ctx = _ctx(rows=[_row(), _row(entity="west")])
    assert apply_batch(ctx, [_item(entity="west")])["ok"] is True
    (written,) = ctx.runner.written
    assert written["entity"] == "west"
    assert ctx.reviews.inserted[0]["entity"] == "west"


def test_apply_refuses_when_trial_hash_changed():
    result = apply_batch(_ctx(), [_item(trial_hash="b" * 64)])
    assert result["ok"] is False and result["stale"] == ["NCT01234567"]


def test_apply_rejects_bad_action_before_running():
    ctx = _ctx()
    bad = [{"action": "edited", "step_index": 0, "path": "match[0].and[0]", "node": {}}]
    result = apply_batch(ctx, [_item(actions=bad)])
    assert result["ok"] is False and "NCT01234567" in result["error"]
    assert ctx.runner.calls == []


def test_apply_rejects_unknown_trial_and_empty_batch():
    assert apply_batch(_ctx(), [_item("NCT00000000")])["ok"] is False
    assert apply_batch(_ctx(), [])["ok"] is False


def test_apply_ignores_client_tree():
    ctx = _ctx()
    item = _item()
    item["treatment_list"] = {"step": [{"match": [{"clinical": {"junk": True}}]}]}
    assert apply_batch(ctx, [item])["ok"] is True
    (written,) = ctx.runner.written
    assert "junk" not in json.dumps(written["treatment_list"])


# ── HTTP API ─────────────────────────────────────────────────────────────────

@pytest.fixture
def server():
    import threading
    from http.server import ThreadingHTTPServer

    from ctm.review_server import make_handler

    ctx = _ctx()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctx))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield ctx, httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _req(port, method, path, body=None):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    conn.request(method, path, body=data, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, raw


def test_http_routes(server):
    ctx, port = server
    status, raw = _req(port, "GET", "/")
    assert status == 200 and b"<title>" in raw

    status, raw = _req(port, "GET", "/api/trial?q=nct01234567")
    assert status == 200 and json.loads(raw)["trial"]["trial_key"] == "NCT01234567"
    assert "_raw" not in json.loads(raw)["trial"]

    status, raw = _req(port, "GET", "/api/trial?q=NCT01234567&entity=sparrow-api")
    assert status == 200 and json.loads(raw)["fingerprint"]
    status, _ = _req(port, "GET", "/api/trial?q=NCT01234567&entity=west")
    assert status == 404

    status, raw = _req(port, "GET", "/api/trial?q=NCT00000000")
    assert status == 404 and json.loads(raw) == {"error": "not found"}

    status, raw = _req(port, "POST", "/api/validate-node", {"text": "{}"})
    assert status == 200 and json.loads(raw)["ok"] is False

    status, raw = _req(port, "POST", "/api/validate-node",
                       {"text": '{"genomic": {"hugo_symbol": "BRAF"}}'})
    assert json.loads(raw) == {"ok": True,
                               "node": {"genomic": {"hugo_symbol": "BRAF", MARKER: "manual"}}}

    status, raw = _req(port, "POST", "/api/apply", {"batch": [_item()]})
    assert status == 200 and json.loads(raw)["ok"] is True
    assert len(ctx.reviews.inserted) == 1

    status, _ = _req(port, "POST", "/api/apply", b"x{")
    assert status == 400

    status, _ = _req(port, "GET", "/nope")
    assert status == 404


def test_review_cli_defaults():
    from ctm.review_cli import build_parser

    args = build_parser().parse_args([])
    assert args.port == 8765 and args.db is None


def test_http_validate_group_only_for_add(server):
    _, port = server
    text = '{"and": [{"clinical": {"age_numerical": ">=18"}}]}'
    status, raw = _req(port, "POST", "/api/validate-node", {"text": text, "group": True})
    assert status == 200 and json.loads(raw) == {
        "ok": True, "node": {"and": [{"clinical": {"age_numerical": ">=18", MARKER: "manual"}}]}}
    status, raw = _req(port, "POST", "/api/validate-node", {"text": text})
    assert json.loads(raw)["ok"] is False
