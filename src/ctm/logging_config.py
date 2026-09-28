"""One place that configures logging for every ``ctm-*`` command.

The toolkit writes its own run log; the bash wrappers do not have to redirect
anything. Two handlers, deliberately shaped for two different readers:

* **console** (stderr) — what a curator sees. Format is the message alone, so a
  terminal looks the way it always has. ``WARNING``/``ERROR`` regain the
  ``Warning: ``/``Error: `` prefixes the old ``print()`` calls carried in their
  text, which is why call sites pass the bare message.
* **file** — JSON lines, one object per record, carrying the structured fields a
  dashboard needs: which CLI, which run, which source, how many documents.

Configuration is environment-first so the same wheel behaves correctly on a
laptop and on a server without a code change::

    CTM_LOG_ENV=dev       console INFO, no file          (default)
    CTM_LOG_ENV=staging   console INFO, file DEBUG
    CTM_LOG_ENV=prod      console WARNING, file INFO

Individual ``CTM_LOG_*`` variables override the preset; ``CTM_LOG_CONFIG``
(a path to a ``logging.config.dictConfig`` JSON document) overrides everything
and is the escape hatch for a setup this module did not anticipate.

Library modules do ``log = logging.getLogger(__name__)`` and nothing else. Only
``main()`` calls :func:`configure_logging`.

Note the name: **not** ``CTM_ENV``. The server's ``ctm-pre-curate`` already uses
that variable for the path to its env file.
"""
from __future__ import annotations

import json
import logging
import logging.config
import os
import re
import socket
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

DEFAULT_LOG_DIR = "/var/lib/ctm/logs"
DEFAULT_ENV = "dev"

#: console level, whether a file is written, and at what level.
PRESETS: dict[str, dict[str, object]] = {
    "dev": {"console_level": "INFO", "file_enabled": False, "file_level": "DEBUG"},
    "staging": {"console_level": "INFO", "file_enabled": True, "file_level": "DEBUG"},
    "prod": {"console_level": "WARNING", "file_enabled": True, "file_level": "INFO"},
}

# Libraries that are chatty at DEBUG. Pinned to WARNING independently of our own
# level, because `CTM_LOG_LEVEL=DEBUG` is the moment someone is trying to debug
# our code — pymongo logging every command, or fontTools every glyph, makes that
# impossible and the feature goes unused.
THIRD_PARTY_LOGGERS = (
    "pymongo", "openai", "httpx", "httpcore", "urllib3",
    "weasyprint", "fontTools", "PIL", "openpyxl", "livereload",
)

# Env vars whose *values* are scrubbed from every record, whatever the shape of
# the message that leaked them. Belt to the regexes' braces.
SECRET_ENV_VARS = (
    "UMGPT_API_KEY", "DDOTS_API_KEY", "DDOTS_SECRET_KEY",
    "MONGO_PASSWORD", "MONGO_RO_PASSWORD", "MONGO_URI",
)

# Below this length a "secret" is more likely to be a common substring than a
# credential, and blind replacement would corrupt unrelated messages.
_MIN_SECRET_LEN = 6

_MONGO_URI_RE = re.compile(r"(mongodb(?:\+srv)?://[^:/\s]+:)([^@\s]+)(@)", re.IGNORECASE)
# The leading `[A-Za-z0-9_]*` matters: `\b` will not match inside `DDOTS_SECRET_KEY`
# or `UMGPT_API_KEY`, because `_` is a word character — so a bare `\bsecret` misses
# exactly the variable names this codebase uses.
_SECRET_KV_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_]*(?:api[_-]?key|secret[_-]?key|secret|password|passwd|pwd"
    r"|token|authorization))(\s*[=:]\s*)(\"?)([^\s,;\"')]+)"
)
# PHI that must never reach a log file. `pt_uuid` is deliberately absent — it is
# the PHI-free join key and the thing call sites are supposed to log instead.
_PHI_KV_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_]*(?:mrn|first_name|last_name|patient_name|dob|birth_date))"
    r"(\s*[=:]\s*)(\"?)([^\s,;\"')]+)"
)

_REDACTED = "***"

# Standard LogRecord attributes. Anything else a call site passes via `extra`
# is a structured field and gets serialized into the JSON payload.
_RESERVED_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


def _env(name: str) -> str | None:
    """An environment variable, treating empty string as unset.

    Same idiom as ``paths.py``: ``CTM_LOG_DIR=`` in a ``.env`` should mean "I did
    not set this", not "write logs to the current directory".
    """
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


# ── Redaction ────────────────────────────────────────────────────────────────

def _secret_values() -> list[str]:
    """Literal secret values to scrub, longest first so a URI containing a
    password is masked before the password alone is."""
    values = []
    for name in SECRET_ENV_VARS:
        value = _env(name)
        if value and len(value) >= _MIN_SECRET_LEN:
            values.append(value)
            # A connection URI also carries its password inline; scrub that too,
            # so `MONGO_URI` being set protects a bare password in a message.
            match = _MONGO_URI_RE.search(value)
            if match and len(match.group(2)) >= _MIN_SECRET_LEN:
                values.append(match.group(2))
    return sorted(set(values), key=len, reverse=True)


def redact(text: str, secrets: list[str] | None = None) -> str:
    """Mask credentials and PHI in an already-formatted message.

    A backstop, not the primary control. The primary control is the rule that
    call sites log ``pt_uuid`` and never an MRN or a name — a filter only catches
    the shapes it knows about. It exists because one ``log.debug("config=%s",
    mongo_config())`` would otherwise publish a Mongo password to a file that
    outlives the terminal.
    """
    for secret in secrets if secrets is not None else _secret_values():
        text = text.replace(secret, _REDACTED)
    text = _MONGO_URI_RE.sub(rf"\1{_REDACTED}\3", text)
    text = _SECRET_KV_RE.sub(rf"\1\2\3{_REDACTED}", text)
    return _PHI_KV_RE.sub(rf"\1\2\3{_REDACTED}", text)


class RedactingFilter(logging.Filter):
    """Collapse each record to its final message, redacted.

    Installed on every handler rather than on a logger: a logger's filters do not
    run for records that propagate up from child loggers, so a logger-level
    filter would miss almost everything. Records are shared between handlers, so
    the work is marked done to keep it idempotent and cheap.
    """

    def __init__(self) -> None:
        super().__init__()
        self._secrets = _secret_values()

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "_ctm_redacted", False):
            record.msg = redact(record.getMessage(), self._secrets)
            record.args = ()
            record._ctm_redacted = True
            for key, value in list(record.__dict__.items()):
                if key not in _RESERVED_RECORD_ATTRS and isinstance(value, str):
                    record.__dict__[key] = redact(value, self._secrets)
        return True


#: Bookkeeping events that belong in the run log but not in front of a curator.
#: They replace the wrapper's `stage()` echoes, which were never on the terminal
#: either — they went to the redirected log file.
CONSOLE_SUPPRESSED_EVENTS = frozenset({"command.begin", "command.end"})


class ConsoleEventFilter(logging.Filter):
    """Keep command lifecycle records out of the console stream.

    Without this a failing command prints its real error *and* an
    ``Error: END ctm-mm patients (exit 1, 0.1s)`` line, which is noise the old
    print-based output never had.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "event", None) not in CONSOLE_SUPPRESSED_EVENTS


# ── Record enrichment ────────────────────────────────────────────────────────

def _command_name() -> str:
    """The CLI this process is, e.g. ``ctm-mm``. Under pytest or ``python -m``
    this is whatever ran, which is the honest answer."""
    return Path(sys.argv[0]).name or "ctm"


def run_id() -> str:
    """Correlation id shared by every stage of one pipeline run.

    ``CTM_RUN_ID`` → ``MONGO_DBNAME`` → today's date. The per-run database name is
    already the canonical name for a run, so four stages of one morning agree on
    it without the wrapper configuring anything.
    """
    return _env("CTM_RUN_ID") or _env("MONGO_DBNAME") or datetime.now(tz=UTC).strftime("%Y-%m-%d")


#: The factory in place before this module first touched it. Captured once and
#: always used as the base, so repeated `configure_logging()` calls replace our
#: factory instead of wrapping it — chained wrappers would leave the *earliest*
#: context winning, which silently stamps a second run with the first run's id.
_BASE_RECORD_FACTORY: object | None = None


def _install_record_factory() -> None:
    """Stamp every record with the fields a dashboard needs to group by."""
    global _BASE_RECORD_FACTORY
    if _BASE_RECORD_FACTORY is None:
        _BASE_RECORD_FACTORY = logging.getLogRecordFactory()
    base = _BASE_RECORD_FACTORY

    # Resolved once per configure call: these cannot change mid-run, and
    # `gethostname()` is not something to pay for per log line.
    context = {
        "command": _command_name(),
        "run_id": run_id(),
        "ctm_env": _env("CTM_LOG_ENV") or DEFAULT_ENV,
        "host": socket.gethostname(),
        "pid": os.getpid(),
    }

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = base(*args, **kwargs)
        # Set unconditionally: `extra=` is applied by Logger.makeRecord *after*
        # the factory runs, so a call site that passes its own `run_id` still wins.
        for key, value in context.items():
            setattr(record, key, value)
        return record

    logging.setLogRecordFactory(factory)


# ── Formatters ───────────────────────────────────────────────────────────────

class ConsoleFormatter(logging.Formatter):
    """The message alone, with the severity prefix the old prints carried.

    ``print(f"Error: no trials in {db}")`` became ``log.error("no trials in %s",
    db)``; this puts the ``Error: `` back, so a curator's terminal is unchanged
    while the stored message stays clean for grouping and search.
    """

    _PREFIXES: ClassVar[dict[int, str]] = {
        logging.WARNING: "Warning: ",
        logging.ERROR: "Error: ",
        logging.CRITICAL: "Error: ",
    }

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        prefix = self._PREFIXES.get(record.levelno, "")
        if prefix:
            # Detail lines are indented by two spaces to sit under their summary.
            # Keep that indent outside the prefix so it still reads as a subitem.
            stripped = message.lstrip(" ")
            indent = " " * (len(message) - len(stripped))
            message = f"{indent}{prefix}{stripped}"
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        return message


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for the dashboard ingestion path.

    Every structured field a call site passed via ``extra`` is included verbatim,
    so ``log.info("…", extra={"event": "trials.source", "source": "amc",
    "count": 279})`` is queryable without parsing English.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "command": getattr(record, "command", None),
            "run_id": getattr(record, "run_id", None),
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS or key.startswith("_") or key in payload:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-readable file format, for ``CTM_LOG_FORMAT=text``. Structured
    fields are appended as ``key=value`` so nothing is lost relative to JSON."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = " ".join(
            f"{key}={value}"
            for key, value in record.__dict__.items()
            if key not in _RESERVED_RECORD_ATTRS
            and not key.startswith("_")
            and key not in ("command", "run_id", "ctm_env", "host", "pid")
        )
        return f"{base}  {extras}" if extras else base


# ── Configuration ────────────────────────────────────────────────────────────

def log_file_path() -> Path:
    """Where this process writes, when a file handler is enabled.

    ``CTM_LOG_FILE`` wins outright. Otherwise ``<CTM_LOG_DIR>/<run_id>.log`` —
    named for the *run*, not the command, so ``ctm-mm trials``, ``ctm-mm
    trials-diff`` and the two ``ctm-llm`` stages of one morning append to a single
    file in the order they ran.
    """
    explicit = _env("CTM_LOG_FILE")
    if explicit:
        return Path(explicit).expanduser()
    safe_run_id = re.sub(r"[^A-Za-z0-9._-]", "_", run_id())
    return Path(_env("CTM_LOG_DIR") or DEFAULT_LOG_DIR).expanduser() / f"{safe_run_id}.log"


def _resolve(env: str | None, level: str | None, verbosity: int) -> dict[str, object]:
    """Preset, then environment overrides, then ``-v``/``-q``."""
    name = (env or _env("CTM_LOG_ENV") or DEFAULT_ENV).lower()
    preset = dict(PRESETS.get(name, PRESETS[DEFAULT_ENV]))
    preset["env"] = name if name in PRESETS else DEFAULT_ENV

    console_level = level or _env("CTM_LOG_LEVEL") or preset["console_level"]
    if verbosity > 0:
        console_level = "DEBUG"
    elif verbosity < 0:
        console_level = "ERROR"
    preset["console_level"] = str(console_level).upper()
    preset["file_level"] = str(_env("CTM_LOG_FILE_LEVEL") or preset["file_level"]).upper()
    # Naming a file is itself a request for one, on any preset.
    if _env("CTM_LOG_FILE") or _env("CTM_LOG_DIR"):
        preset["file_enabled"] = True
    return preset


def _file_handler(path: Path, level: str, fmt: str) -> logging.Handler | None:
    """A plain append-mode handler, or None (with a warning) if it cannot be made.

    Deliberately not ``TimedRotatingFileHandler``: several short-lived processes
    share one run file, and concurrent rotation is a known way to lose records.
    The filename changes when the run does, so retention belongs to a logrotate
    drop-in rather than to the application.

    Failure to open must never take down a pipeline stage — logging is not the
    job. The stage keeps running with console output alone.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    except OSError as exc:
        print(f"Warning: logging to {path} disabled ({exc})", file=sys.stderr)
        return None
    handler.setLevel(level)
    handler.setFormatter(TextFormatter() if fmt == "text" else JsonFormatter())
    handler.addFilter(RedactingFilter())
    return handler


def configure_logging(
    env: str | None = None,
    level: str | None = None,
    verbosity: int = 0,
    log_file: str | Path | None = None,
) -> None:
    """Install the toolkit's handlers on the root logger. Call once, from ``main()``.

    Idempotent: a second call replaces the handlers this module installed rather
    than stacking a duplicate set, which is the classic cause of every line
    appearing twice.

    ``verbosity`` is ``-v``/``-q``: positive forces DEBUG, negative forces ERROR.
    """
    config_path = _env("CTM_LOG_CONFIG")
    if config_path:
        # Full escape hatch. Whoever writes this file owns the outcome, including
        # redaction — so the filter is still attached afterwards.
        with open(config_path, encoding="utf-8") as handle:
            logging.config.dictConfig(json.load(handle))
        _install_record_factory()
        for handler in logging.getLogger().handlers:
            handler.addFilter(RedactingFilter())
        return

    if log_file is not None:
        os.environ["CTM_LOG_FILE"] = str(log_file)

    settings = _resolve(env, level, verbosity)
    _install_record_factory()

    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, "_ctm_handler", False)]:
        root.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(str(settings["console_level"]))
    console.setFormatter(ConsoleFormatter())
    console.addFilter(RedactingFilter())
    console.addFilter(ConsoleEventFilter())
    handlers: list[logging.Handler] = [console]

    if settings["file_enabled"]:
        handler = _file_handler(
            log_file_path(),
            str(settings["file_level"]),
            (_env("CTM_LOG_FORMAT") or "json").lower(),
        )
        if handler is not None:
            handlers.append(handler)

    for handler in handlers:
        handler._ctm_handler = True
        root.addHandler(handler)

    # The root logger must pass everything the most permissive handler wants.
    root.setLevel(min(h.level for h in handlers) or logging.DEBUG)

    third_party_level = (_env("CTM_LOG_THIRDPARTY_LEVEL") or "WARNING").upper()
    for name in THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(third_party_level)


def add_logging_arguments(parser: object) -> None:
    """``-v``/``-q`` on a CLI parser, resolved by :func:`verbosity_from_args`."""
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="More detail on the console (DEBUG)")
    parser.add_argument("-q", "--quiet", action="count", default=0,
                        help="Errors only on the console")


def verbosity_from_args(args: object) -> int:
    return getattr(args, "verbose", 0) - getattr(args, "quiet", 0)


# ── Call-site helpers ────────────────────────────────────────────────────────

def fail(message: str, *args: object, code: int = 1, **fields: object) -> None:
    """Log at ERROR and exit non-zero — the 35 ``print("Error: …"); sys.exit(1)``
    pairs, as one call that cannot drift apart.

    The non-zero exit is load-bearing: the cron wrapper runs under ``set -e`` and
    its ERR trap is the only unattended-failure alert. An error logged but exited
    0 would look like a clean run and let the next stage consume bad data.
    """
    logging.getLogger("ctm").error(message, *args, extra=_extra("error", fields))
    raise SystemExit(code)


def _extra(event: str | None, fields: dict[str, object]) -> dict[str, object]:
    """Structured fields for a record, keeping clear of LogRecord's own names."""
    extra = {k: v for k, v in fields.items() if k not in _RESERVED_RECORD_ATTRS}
    if event:
        extra["event"] = event
    return extra


def log_event(
    log: logging.Logger,
    event: str,
    message: str,
    *args: object,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """A message that is also a structured event.

    The dashboard groups on ``event`` and reads the numbers from ``fields``; the
    console just shows ``message``. Use it wherever a line answers "how many, from
    where" — trial counts per source, LLM cache hits, documents written::

        log_event(log, "trials.source", "  %s: %d trial(s)", src, n,
                  source=src, count=n)
    """
    log.log(level, message, *args, extra=_extra(event, fields))


@contextmanager
def command_context(log: logging.Logger, description: str | None = None) -> Iterator[None]:
    """Bracket a command with BEGIN/OK/FAILED lines carrying elapsed time.

    These used to come from the wrapper's ``stage()`` function. They live here
    now so a run log is self-describing regardless of what launched the command —
    cron, a curator's shell, or a future scheduler.

    At DEBUG on the console (a curator does not need it) but INFO to the file, via
    the levels; the failure line is ERROR either way.
    """
    label = description or " ".join(sys.argv) or _command_name()
    started = time.monotonic()
    log_event(log, "command.begin", "BEGIN %s", label, level=logging.DEBUG,
              argv=label, run_started=datetime.now(tz=UTC).isoformat())
    try:
        yield
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        level = logging.ERROR if code else logging.DEBUG
        log_event(log, "command.end", "END %s (exit %d, %.1fs)", label, code,
                  time.monotonic() - started, level=level,
                  exit_code=code, duration_s=round(time.monotonic() - started, 3))
        raise
    except BaseException:
        # exc_info, so the traceback reaches the run log. Python will also print
        # it to stderr on the way out, but stderr is now cron mail rather than a
        # file — without this the log would say FAILED and not why.
        elapsed = time.monotonic() - started
        log.error("FAILED %s (%.1fs)", label, elapsed, exc_info=True,
                  extra=_extra("command.end",
                               {"exit_code": 1, "duration_s": round(elapsed, 3)}))
        raise
    else:
        log_event(log, "command.end", "OK %s (%.1fs)", label,
                  time.monotonic() - started, level=logging.DEBUG,
                  exit_code=0, duration_s=round(time.monotonic() - started, 3))
