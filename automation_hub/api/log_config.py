"""Structured JSON logging configuration for Automation Hub.

Provides a custom JSON formatter that outputs all log records as JSON lines,
enabling consistent structured logging across all services.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs log records as JSON lines.

    Produces parseable JSON output with standard fields:
    - ts: ISO-8601 timestamp (UTC)
    - level: log level name
    - logger: logger name
    - message: the log message
    - Extra fields from the record's `extra` dict
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        log_entry: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Merge structured extra fields from the record
        for key, value in getattr(record, "extra_fields", {}).items():
            log_entry[key] = value

        return json.dumps(log_entry, ensure_ascii=False, default=str)


class StructuredLogger(logging.Logger):
    """Logger subclass that supports structured extra fields via `extra=`.

    Usage:
        logger.info("message", extra={"key": "value"})
    """

    def _log(
        self,
        level: int,
        msg: object,
        args: tuple[object, ...],
        exc_info: Any = None,
        extra: dict[str, Any] | None = None,
        stack_info: bool = False,
        stacklevel: int = 1,
    ) -> None:
        if extra is None:
            extra = {}
        extra_fields = {k: v for k, v in extra.items() if k != "extra_fields"}
        extra["extra_fields"] = extra_fields
        super()._log(level, msg, args, exc_info, extra, stack_info, stacklevel)


def setup_logging(
    level: int = logging.INFO,
    *,
    json_output: bool = True,
    log_to_stdout: bool = True,
) -> None:
    """Configure structured JSON logging for the application.

    Args:
        level: The logging level (default: INFO).
        json_output: If True, use JSON formatter; otherwise use plain text.
        log_to_stdout: If True, add a StreamHandler writing to stdout.
    """
    # Register StructuredLogger as the default logger class
    logging.setLoggerClass(StructuredLogger)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicate output
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    if log_to_stdout:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)

        if json_output:
            # Use our JSON formatter for structured output
            handler.setFormatter(JSONFormatter())
        else:
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
            )

        root_logger.addHandler(handler)

    # Set third-party library log levels to reduce noise
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.ERROR)
