"""Tests for ctm.logging_config — presets, redaction, structured output.

The redaction tests are the load-bearing ones: they are what make it safe to
write a run log to a shared server.
"""
import json
import logging
import sys

import pytest

from ctm import logging_config as lc


@pytest.fixture(autouse=True)
def _reset_logging(monkeypatch):
    """Undo whatever a test configured.

    Logging is global process state, so without this one test's handlers and
    record factory leak into the next and the failures are baffling.
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_factory = logging.getLogRecordFactory()
    for name in ("CTM_LOG_ENV", "CTM_LOG_LEVEL", "CTM_LOG_FILE", "CTM_LOG_DIR",
                 "CTM_LOG_FILE_LEVEL", "CTM_LOG_FORMAT", "CTM_LOG_CONFIG",
                 "CTM_LOG_THIRDPARTY_LEVEL", "CTM_RUN_ID", "MONGO_DBNAME"):
        monkeypatch.delenv(name, raising=False)
    yield
    root.handlers = saved_handlers
    root.setLevel(saved_level)
    logging.setLogRecordFactory(saved_factory)


def _ctm_handlers():
    return [h for h in logging.getLogger().handlers if getattr(h, "_ctm_handler", False)]


# ── Presets ──────────────────────────────────────────────────────────────────

def test_dev_is_console_only_at_info():
    lc.configure_logging(env="dev")
    handlers = _ctm_handlers()
    assert len(handlers) == 1
    assert handlers[0].level == logging.INFO
    assert handlers[0].stream is sys.stderr


def test_staging_adds_a_debug_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    lc.configure_logging(env="staging")
    levels = sorted(h.level for h in _ctm_handlers())
    assert levels == [logging.DEBUG, logging.INFO]


def test_prod_console_is_warning_file_is_info(tmp_path, monkeypatch):
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    lc.configure_logging(env="prod")
    console, file_handler = (
        sorted(_ctm_handlers(), key=lambda h: isinstance(h, logging.FileHandler))
    )
    assert console.level == logging.WARNING
    assert file_handler.level == logging.INFO


def test_unknown_preset_falls_back_to_dev():
    lc.configure_logging(env="banana")
    assert _ctm_handlers()[0].level == logging.INFO


def test_env_var_overrides_preset(monkeypatch):
    monkeypatch.setenv("CTM_LOG_LEVEL", "DEBUG")
    lc.configure_logging(env="prod")
    console = next(h for h in _ctm_handlers() if not isinstance(h, logging.FileHandler))
    assert console.level == logging.DEBUG


def test_verbose_and_quiet_win_over_everything(monkeypatch):
    monkeypatch.setenv("CTM_LOG_LEVEL", "INFO")
    lc.configure_logging(env="dev", verbosity=1)
    assert _ctm_handlers()[0].level == logging.DEBUG
    lc.configure_logging(env="dev", verbosity=-1)
    assert _ctm_handlers()[0].level == logging.ERROR


def test_naming_a_log_dir_enables_the_file_on_dev(tmp_path, monkeypatch):
    """Pointing at a directory is itself a request for a file."""
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    lc.configure_logging(env="dev")
    assert any(isinstance(h, logging.FileHandler) for h in _ctm_handlers())


def test_configure_twice_does_not_duplicate_handlers(tmp_path, monkeypatch):
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    lc.configure_logging(env="staging")
    lc.configure_logging(env="staging")
    assert len(_ctm_handlers()) == 2


def test_third_party_loggers_are_pinned(monkeypatch):
    logging.getLogger("pymongo").setLevel(logging.DEBUG)
    lc.configure_logging(env="dev")
    assert logging.getLogger("pymongo").level == logging.WARNING


def test_ctm_log_config_overrides_everything(tmp_path, monkeypatch):
    config = tmp_path / "logging.json"
    config.write_text(json.dumps({
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"null": {"class": "logging.NullHandler"}},
        "root": {"handlers": ["null"], "level": "CRITICAL"},
    }))
    monkeypatch.setenv("CTM_LOG_CONFIG", str(config))
    lc.configure_logging(env="prod")
    assert logging.getLogger().level == logging.CRITICAL


# ── The log file ─────────────────────────────────────────────────────────────

def test_file_is_named_for_the_run_not_the_command(tmp_path, monkeypatch):
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("MONGO_DBNAME", "2026-09-28_dev")
    assert lc.log_file_path() == tmp_path / "2026-09-28_dev.log"


def test_explicit_file_wins_over_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CTM_LOG_FILE", str(tmp_path / "explicit.log"))
    assert lc.log_file_path() == tmp_path / "explicit.log"


def test_run_id_prefers_ctm_run_id(monkeypatch):
    monkeypatch.setenv("MONGO_DBNAME", "from_db")
    monkeypatch.setenv("CTM_RUN_ID", "from_run_id")
    assert lc.run_id() == "from_run_id"


def test_run_id_falls_back_to_a_date(monkeypatch):
    assert lc.run_id().count("-") == 2


def test_stages_sharing_a_run_id_share_one_file(tmp_path, monkeypatch):
    """The property that replaces the wrapper's `exec` redirect."""
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CTM_RUN_ID", "2026-09-28_dev")
    for stage in ("trials", "trials-diff"):
        lc.configure_logging(env="staging")
        logging.getLogger("ctm.test").info("ran %s", stage)
    for handler in _ctm_handlers():
        handler.flush()
    assert len(list(tmp_path.glob("*.log"))) == 1
    body = (tmp_path / "2026-09-28_dev.log").read_text()
    assert "ran trials" in body and "ran trials-diff" in body


def test_unwritable_log_file_does_not_kill_the_process(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CTM_LOG_FILE", str(tmp_path / "nope" / "x.log"))
    monkeypatch.setattr(lc.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))
    lc.configure_logging(env="prod")
    assert not any(isinstance(h, logging.FileHandler) for h in _ctm_handlers())
    assert "disabled" in capsys.readouterr().err


# ── Formatters ───────────────────────────────────────────────────────────────

def test_console_restores_the_error_prefix(capsys):
    lc.configure_logging(env="dev")
    logging.getLogger("ctm.test").error("no trials in %s", "2026-09-28_dev")
    assert capsys.readouterr().err.strip() == "Error: no trials in 2026-09-28_dev"


def test_console_restores_the_warning_prefix_keeping_indent(capsys):
    lc.configure_logging(env="dev")
    logging.getLogger("ctm.test").warning("  2 row(s) skipped")
    assert capsys.readouterr().err.strip("\n") == "  Warning: 2 row(s) skipped"


def test_console_info_is_the_bare_message(capsys):
    lc.configure_logging(env="dev")
    logging.getLogger("ctm.test").info("Stored %d doc(s)", 42)
    assert capsys.readouterr().err.strip() == "Stored 42 doc(s)"


def _read_json_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_json_file_carries_structured_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CTM_RUN_ID", "run-1")
    lc.configure_logging(env="staging")
    log = logging.getLogger("ctm.test")
    lc.log_event(log, "trials.source", "  %s: %d", "amc", 279, source="amc", count=279)
    for handler in _ctm_handlers():
        handler.flush()
    record = _read_json_lines(tmp_path / "run-1.log")[0]
    assert record["event"] == "trials.source"
    assert record["source"] == "amc"
    assert record["count"] == 279
    assert record["run_id"] == "run-1"
    assert record["level"] == "INFO"
    assert "time" in record and "command" in record


def test_json_file_carries_the_traceback(tmp_path, monkeypatch):
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CTM_RUN_ID", "run-2")
    lc.configure_logging(env="staging")
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("ctm.test").exception("render failed")
    for handler in _ctm_handlers():
        handler.flush()
    record = _read_json_lines(tmp_path / "run-2.log")[0]
    assert "ValueError: boom" in record["exception"]


def test_text_format_is_available(tmp_path, monkeypatch):
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CTM_RUN_ID", "run-3")
    monkeypatch.setenv("CTM_LOG_FORMAT", "text")
    lc.configure_logging(env="staging")
    lc.log_event(logging.getLogger("ctm.test"), "db.write", "Stored 12", count=12)
    for handler in _ctm_handlers():
        handler.flush()
    body = (tmp_path / "run-3.log").read_text()
    assert "Stored 12" in body and "count=12" in body and "event=db.write" in body


# ── Redaction ────────────────────────────────────────────────────────────────

def test_mongo_uri_password_is_masked():
    redacted = lc.redact("uri=mongodb://deemer:hunter2pass@localhost:27017/", [])
    assert "hunter2pass" not in redacted
    assert "mongodb://deemer:***@localhost" in redacted


def test_secret_key_values_are_masked():
    for text in ("api_key=sk-abcdef123456", "DDOTS_SECRET_KEY: DFuGa23UgOif0YS",
                 "password=hunter2pass", "token=abcdef123456"):
        assert "***" in lc.redact(text, [])


def test_phi_fields_are_masked():
    masked = lc.redact("pt_uuid=pt_0000016 mrn=123456789 last_name=Smith", [])
    assert "123456789" not in masked
    assert "Smith" not in masked
    assert "pt_uuid=pt_0000016" in masked, "the PHI-free join key must survive"


def test_secret_env_values_are_masked_in_any_shape(monkeypatch):
    monkeypatch.setenv("UMGPT_API_KEY", "32i6KhD6S1aWjEKcZAX3wSEpulZn")
    masked = lc.redact("calling gateway with 32i6KhD6S1aWjEKcZAX3wSEpulZn now")
    assert "32i6KhD6S1aWjEKcZAX3wSEpulZn" not in masked


def test_short_secrets_are_not_blindly_replaced(monkeypatch):
    """A two-character secret would corrupt unrelated messages."""
    monkeypatch.setenv("MONGO_PASSWORD", "ab")
    assert lc.redact("stored 12 trials in database ab-test") == "stored 12 trials in database ab-test"


def test_redaction_reaches_the_log_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CTM_RUN_ID", "run-4")
    monkeypatch.setenv("MONGO_PASSWORD", "hunter2pass")
    lc.configure_logging(env="staging")
    logging.getLogger("ctm.test").info(
        "connecting to %s", "mongodb://deemer:hunter2pass@localhost:27017/")
    for handler in _ctm_handlers():
        handler.flush()
    body = (tmp_path / "run-4.log").read_text()
    assert "hunter2pass" not in body
    assert "***" in body


def test_redaction_reaches_structured_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CTM_RUN_ID", "run-5")
    lc.configure_logging(env="staging")
    lc.log_event(logging.getLogger("ctm.test"), "patients.loaded", "loaded 1",
                 detail="mrn=123456789")
    for handler in _ctm_handlers():
        handler.flush()
    assert "123456789" not in (tmp_path / "run-5.log").read_text()


def test_redaction_is_idempotent_across_handlers(tmp_path, monkeypatch, capsys):
    """One record, two handlers — the message must not be double-processed."""
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CTM_RUN_ID", "run-6")
    lc.configure_logging(env="staging")
    logging.getLogger("ctm.test").info("count=12 for mongodb://u:secretpass@h/")
    for handler in _ctm_handlers():
        handler.flush()
    console = capsys.readouterr().err
    assert "count=12" in console
    assert "secretpass" not in console


# ── Helpers ──────────────────────────────────────────────────────────────────

def test_fail_logs_an_error_and_exits_nonzero(caplog):
    lc.configure_logging(env="dev")
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        lc.fail("no trials in %s", "somedb")
    assert excinfo.value.code == 1
    assert "no trials in somedb" in caplog.text


def test_command_context_brackets_a_run(tmp_path, monkeypatch):
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CTM_RUN_ID", "run-7")
    monkeypatch.setenv("CTM_LOG_FILE_LEVEL", "DEBUG")
    lc.configure_logging(env="staging")
    log = logging.getLogger("ctm.test")
    with lc.command_context(log, "ctm-mm trials"):
        log.info("working")
    for handler in _ctm_handlers():
        handler.flush()
    events = [r["event"] for r in _read_json_lines(tmp_path / "run-7.log") if "event" in r]
    assert events == ["command.begin", "command.end"]


def test_command_context_marks_a_crash_and_reraises(tmp_path, monkeypatch):
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CTM_RUN_ID", "run-8")
    lc.configure_logging(env="staging")
    log = logging.getLogger("ctm.test")
    with pytest.raises(ValueError), lc.command_context(log, "ctm-mm trials"):
        raise ValueError("boom")
    for handler in _ctm_handlers():
        handler.flush()
    end = [r for r in _read_json_lines(tmp_path / "run-8.log") if r.get("event") == "command.end"]
    assert end[0]["level"] == "ERROR"
    assert end[0]["exit_code"] == 1


def test_command_context_treats_clean_systemexit_as_success(tmp_path, monkeypatch):
    """`sys.exit(0)` after writing output is a normal end, not a failure."""
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CTM_RUN_ID", "run-9")
    monkeypatch.setenv("CTM_LOG_FILE_LEVEL", "DEBUG")
    lc.configure_logging(env="staging")
    log = logging.getLogger("ctm.test")
    with pytest.raises(SystemExit), lc.command_context(log, "ctm-mm trials"):
        raise SystemExit(0)
    for handler in _ctm_handlers():
        handler.flush()
    end = [r for r in _read_json_lines(tmp_path / "run-9.log") if r.get("event") == "command.end"]
    assert end[0]["level"] == "DEBUG"
    assert end[0]["exit_code"] == 0


def test_reconfiguring_updates_the_run_id(tmp_path, monkeypatch):
    """Regression: chained record factories left the first run's id on every
    later record, so a second stage's lines were filed under the first run."""
    monkeypatch.setenv("CTM_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CTM_RUN_ID", "first")
    lc.configure_logging(env="staging")
    monkeypatch.setenv("CTM_RUN_ID", "second")
    lc.configure_logging(env="staging")
    logging.getLogger("ctm.test").info("hello")
    for handler in _ctm_handlers():
        handler.flush()
    assert _read_json_lines(tmp_path / "second.log")[0]["run_id"] == "second"
