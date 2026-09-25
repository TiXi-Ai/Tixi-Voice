"""Structured logging with privacy-preserving redaction.

Diagnostic logs are useful for support; user *content* is not.  By default the
application never writes recognised or synthesised text, clipboard contents or
audio data to disk.  Call :func:`set_content_logging` to opt in explicitly.

Every record may carry structured ``extra`` fields (``event``, ``model``,
``duration_ms`` ...) which are rendered as ``key=value`` pairs so the log stays
greppable while remaining plain text (no telemetry, no network).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import platform
import sys
import threading
import traceback
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from .paths import paths

LOG_FORMAT: Final = "%(asctime)s %(levelname)-7s %(name)-34s %(message)s"
DATE_FORMAT: Final = "%Y-%m-%d %H:%M:%S"

#: Keys whose values are considered private user content.
SENSITIVE_KEYS: Final = frozenset(
    {
        "text",
        "source_text",
        "output_text",
        "transcript",
        "transcription",
        "clipboard",
        "content",
        "pasted_text",
        "recognised_text",
        "normalized_text",
        "diacritized_text",
        "audio_bytes",
    }
)

_CONTENT_LOGGING = False
_RING: deque[str] = deque(maxlen=2000)
_RING_LOCK = threading.Lock()
_STATS: dict[str, int] = {"errors": 0, "warnings": 0}


def content_logging_enabled() -> bool:
    return _CONTENT_LOGGING


def set_content_logging(enabled: bool) -> None:
    """Opt in/out of writing recognised or synthesised text into the log."""
    global _CONTENT_LOGGING
    _CONTENT_LOGGING = bool(enabled)


def recent_log_lines(limit: int = 400) -> list[str]:
    """Return the most recent log lines held in memory (for the UI)."""
    with _RING_LOCK:
        return list(_RING)[-limit:]


def error_counts() -> dict[str, int]:
    return dict(_STATS)


def redact(value: object, key: str | None = None) -> object:
    """Replace private content with a length-preserving placeholder."""
    if key and key.lower() in SENSITIVE_KEYS and not _CONTENT_LOGGING:
        if isinstance(value, str):
            return f"<redacted {len(value)} chars>"
        return "<redacted>"
    return value


class StructuredFormatter(logging.Formatter):
    """Human-readable formatter that renders structured ``extra`` fields."""

    RESERVED: Final = frozenset(
        logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
    ) | {"message", "asctime", "taskName"}

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        record.message = record.getMessage()
        extras: dict[str, Any] = {
            key: redact(value, key)
            for key, value in record.__dict__.items()
            if key not in self.RESERVED and not key.startswith("_")
        }
        base = f"{self.formatTime(record, DATE_FORMAT)} {record.levelname:<7} {record.name:<34} {record.message}"
        if extras:
            rendered = " ".join(f"{key}={_render(value)}" for key, value in sorted(extras.items()))
            base = f"{base} | {rendered}"
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        return datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone().strftime(
            datefmt or DATE_FORMAT
        )


def _render(value: object) -> str:
    if isinstance(value, str):
        return value if " " not in value else json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float, bool)) or value is None:
        return str(value)
    if isinstance(value, Path):
        return str(value).replace(" ", "\\ ")
    return json.dumps(str(value), ensure_ascii=False)


class RingBufferHandler(logging.Handler):
    """Keeps the last N records in memory so the UI can show them."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:  # pragma: no cover - never break logging
            return
        with _RING_LOCK:
            _RING.append(line)
        if record.levelno >= logging.ERROR:
            _STATS["errors"] += 1
        elif record.levelno >= logging.WARNING:
            _STATS["warnings"] += 1


class LoggerAdapter(logging.LoggerAdapter):  # noqa: D101 - convenience type
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = {**self.extra, **kwargs.get("extra", {})}
        kwargs["extra"] = extra
        return msg, kwargs


def get_logger(name: str = "tixi", **context: Any) -> logging.Logger | LoggerAdapter:
    """Return a logger, optionally bound to structured context fields."""
    logger = logging.getLogger(name)
    if context:
        return LoggerAdapter(logger, context)
    return logger


def setup_logging(
    level: int | str = logging.INFO,
    *,
    log_file: Path | None = None,
    console: bool = True,
    max_bytes: int = 2 * 1024 * 1024,
    backups: int = 3,
) -> Path | None:
    """Configure root logging. Returns the log file path (or ``None``)."""
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        with _suppress(Exception):
            handler.close()

    formatter = StructuredFormatter(LOG_FORMAT, DATE_FORMAT)

    target = log_file or paths().log_file
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            target, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root.addHandler(file_handler)
        written: Path | None = target
    except OSError:
        written = None

    ring = RingBufferHandler()
    ring.setFormatter(formatter)
    ring.setLevel(logging.DEBUG)
    root.addHandler(ring)

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        stream.setLevel(level)
        root.addHandler(stream)

    # Silence chatty third-party loggers.
    for noisy in ("urllib3", "requests", "PIL", "matplotlib", "numba", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    log = logging.getLogger("tixi.app")
    log.info(
        "logging initialised",
        extra={
            "event": "logging_started",
            "level": logging.getLevelName(level),
            "file": written,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "pid": os.getpid(),
        },
    )
    return written


def log_exception(logger: logging.Logger, message: str, exc: BaseException | None = None) -> None:
    """Log an exception with a compact, actionable traceback."""
    exc = exc or sys.exc_info()[1]
    if exc is None:
        logger.error(message)
        return
    logger.error(
        f"{message}: {type(exc).__name__}: {exc}",
        exc_info=exc,
        extra={"event": "exception", "error_type": type(exc).__name__},
    )


def crash_context() -> str:
    """A compact environment summary attached to unhandled-exception reports."""
    return "\n".join(
        [
            f"Tixi Voice: {_app_version()}",
            f"Python: {sys.version.split()[0]}",
            f"Platform: {platform.platform()}",
            f"Executable: {sys.executable}",
            f"Frozen: {getattr(sys, 'frozen', False)}",
        ]
    )


def _app_version() -> str:
    from .config import APP_VERSION

    return APP_VERSION


def install_excepthook(crash_log: Path | None = None) -> None:
    """Write unhandled exceptions to ``crashes.log`` in addition to the log."""
    target = crash_log or paths().crash_file

    def _hook(exc_type, exc_value, exc_tb) -> None:  # type: ignore[no-untyped-def]
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logging.getLogger("tixi.crash").critical(
            "unhandled exception", extra={"event": "crash"}, exc_info=(exc_type, exc_value, exc_tb)
        )
        with _suppress(OSError):
            target.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with target.open("a", encoding="utf-8") as handle:
                handle.write(f"\n===== {stamp} =====\n{crash_context()}\n{text}")

    sys.excepthook = _hook

    def _thread_hook(args) -> None:  # type: ignore[no-untyped-def]
        _hook(args.exc_type, args.exc_value, args.exc_traceback)

    if hasattr(threading, "excepthook"):
        threading.excepthook = _thread_hook  # type: ignore[assignment]


class _suppress:  # noqa: N801 - tiny local contextlib.suppress clone to avoid import cycle
    def __init__(self, *exceptions: type[BaseException]) -> None:
        self.exceptions = exceptions

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, self.exceptions)
