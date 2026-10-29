"""Unified error grading and log anchors for startup, sync, bridge failures.

Every structured log anchor is emitted as a JSON line via the standard
logging module, using the StructuredLogger pattern so that downstream
JSON formatters can pick up structured fields.

Severity levels (from observability.py):
    BLOCKING = "blocking"   '?crash, fatal, startup failure  鈫? logger.error
    DEGRADED = "degraded"   '?timeout, fallback, sync failure 鈫? logger.warning
    INFO     = "info"       '?normal operation               鈫? logger.info
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.observability import SEVERITY_BLOCKING, SEVERITY_DEGRADED, SEVERITY_INFO

logger = logging.getLogger("siyi.error_grading")

# Domain prefixes for log anchors
DOMAIN_STARTUP = "STARTUP"
DOMAIN_SYNC = "SYNC"
DOMAIN_BRIDGE = "BRIDGE"

# Anchor names '?startup
ANCHOR_STARTUP_BEGIN = "startup.begin"
ANCHOR_STARTUP_OK = "startup.ok"
ANCHOR_STARTUP_SETTINGS_MIGRATE = "startup.settings_migrate"
ANCHOR_STARTUP_DB_INIT = "startup.db_init"
ANCHOR_STARTUP_RECOVERY = "startup.recovery"
ANCHOR_STARTUP_OBSERVABILITY = "startup.observability"
ANCHOR_STARTUP_BRIDGE = "startup.bridge"
ANCHOR_STARTUP_SYNC = "startup.sync"

# Anchor names '?sync
ANCHOR_SYNC_BATCH_BEGIN = "sync.batch.begin"
ANCHOR_SYNC_BATCH_OK = "sync.batch.ok"
ANCHOR_SYNC_BATCH_DEGRADED = "sync.batch.degraded"
ANCHOR_SYNC_RESOURCE_BEGIN = "sync.resource.begin"
ANCHOR_SYNC_RESOURCE_OK = "sync.resource.ok"
ANCHOR_SYNC_RESOURCE_FAILED = "sync.resource.failed"
ANCHOR_SYNC_RESOURCE_CONFLICT = "sync.resource.conflict"
ANCHOR_SYNC_PULL_FAILED = "sync.pull.failed"
ANCHOR_SYNC_PUSH_FAILED = "sync.push.failed"

# Anchor names '?bridge
ANCHOR_BRIDGE_START = "bridge.start"
ANCHOR_BRIDGE_START_OK = "bridge.start.ok"
ANCHOR_BRIDGE_START_FAILED = "bridge.start.failed"
ANCHOR_BRIDGE_STOP = "bridge.stop"
ANCHOR_BRIDGE_REQUEST_FAILED = "bridge.request.failed"
ANCHOR_BRIDGE_AUTH_FAILED = "bridge.auth.failed"
ANCHOR_BRIDGE_TIMEOUT = "bridge.timeout"

# Severity 鈫?Python log level mapping
_SEVERITY_TO_LEVEL = {
    SEVERITY_BLOCKING: logging.ERROR,
    SEVERITY_DEGRADED: logging.WARNING,
    SEVERITY_INFO: logging.INFO,
}


def log_anchor(
    domain: str,
    severity: str,
    anchor: str,
    message: str = "",
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit a structured log anchor as a JSON line.

    The log record includes structured fields (domain, severity, anchor, message)
    passed via ``extra`` so that downstream JSON formatters (e.g. automation-hub's
    ``JSONFormatter``) can render them as top-level keys.

    If the logging system is not configured with a JSON formatter, the human-readable
    fallback format is ``[DOMAIN] severity=SEVERITY anchor=ANCHOR_NAME message=<msg>``.
    """
    extra_fields: dict[str, Any] = {
        "domain": domain,
        "severity": severity,
        "anchor": anchor,
    }
    if message:
        extra_fields["anchor_message"] = message
    if extra:
        extra_fields.update(extra)

    # Human-readable fallback message for non-JSON loggers
    parts = [
        f"[{domain}]",
        f"severity={severity}",
        f"anchor={anchor}",
    ]
    if message:
        parts.append(f"message={message}")
    if extra:
        for k, v in extra.items():
            parts.append(f"{k}={v}")
    fallback_text = " ".join(parts)

    log_level = _SEVERITY_TO_LEVEL.get(severity, logging.INFO)
    logger.log(log_level, fallback_text, extra=extra_fields)


def startup_anchor(severity: str, anchor: str, message: str = "", **extra: Any) -> None:
    """Log anchor in the STARTUP domain."""
    log_anchor(DOMAIN_STARTUP, severity, anchor, message, extra=extra or None)


def sync_anchor(severity: str, anchor: str, message: str = "", **extra: Any) -> None:
    """Log anchor in the SYNC domain."""
    log_anchor(DOMAIN_SYNC, severity, anchor, message, extra=extra or None)


def bridge_anchor(severity: str, anchor: str, message: str = "", **extra: Any) -> None:
    """Log anchor in the BRIDGE domain."""
    log_anchor(DOMAIN_BRIDGE, severity, anchor, message, extra=extra or None)
