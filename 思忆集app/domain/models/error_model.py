"""
Unified error model for the SiYiJi application.

Provides a single ``AppError`` dataclass that standardises how every layer
(network, database, AI, UI, system) represents, grades, and serialises
errors.  This replaces ad-hoc dict payloads and informal exception wrappers
with a consistent structure that can be logged, displayed, and retried
uniformly.

Usage
-----
>>> from domain.models.error_model import AppError
>>> err = AppError(
...     code="NETWORK_TIMEOUT",
...     message="Connection to remote API timed out",
...     severity="error",
...     source="network",
...     recoverable=True,
...     retry_allowed=True,
...     cause="HTTPSConnectionPool(host='api.example.com'): Read timed out",
...     context={"endpoint": "/v1/chat", "timeout_s": 30},
... )
>>> err.to_dict()
{
    "code": "NETWORK_TIMEOUT",
    "message": "Connection to remote API timed out",
    ...
}
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# ---------------------------------------------------------------------------
# Well-known error codes — kept in one place so they can be referenced
# without magic strings throughout the codebase.
# ---------------------------------------------------------------------------

# Network
ERR_NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
ERR_NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
ERR_NETWORK_DNS = "NETWORK_DNS"
ERR_NETWORK_SSL = "NETWORK_SSL"

# Database
ERR_DB_CONNECTION = "DB_CONNECTION"
ERR_DB_LOCK = "DB_LOCK"
ERR_DB_QUERY = "DB_QUERY"
ERR_DB_MIGRATION = "DB_MIGRATION"

# AI / LLM
ERR_AI_UNAVAILABLE = "AI_UNAVAILABLE"
ERR_AI_TIMEOUT = "AI_TIMEOUT"
ERR_AI_RATE_LIMIT = "AI_RATE_LIMIT"
ERR_AI_CONTEXT_LENGTH = "AI_CONTEXT_LENGTH"
ERR_AI_CONTENT_FILTER = "AI_CONTENT_FILTER"
ERR_AI_INVALID_RESPONSE = "AI_INVALID_RESPONSE"

# UI
ERR_UI_RENDER = "UI_RENDER"
ERR_UI_WIDGET_NOT_FOUND = "UI_WIDGET_NOT_FOUND"

# System / generic
ERR_SYSTEM_IO = "SYSTEM_IO"
ERR_SYSTEM_RESOURCE = "SYSTEM_RESOURCE"
ERR_SYSTEM_CONFIG = "SYSTEM_CONFIG"
ERR_SYSTEM_UNEXPECTED = "SYSTEM_UNEXPECTED"

# Startup / lifecycle
ERR_STARTUP_FAILED = "STARTUP_FAILED"
ERR_STARTUP_DEPENDENCY = "STARTUP_DEPENDENCY"

# Sync
ERR_SYNC_FAILED = "SYNC_FAILED"
ERR_SYNC_CONFLICT = "SYNC_CONFLICT"
ERR_SYNC_PULL = "SYNC_PULL"
ERR_SYNC_PUSH = "SYNC_PUSH"

# Bridge (IPC between desktop & mobile / MCP)
ERR_BRIDGE_DISCONNECTED = "BRIDGE_DISCONNECTED"
ERR_BRIDGE_AUTH = "BRIDGE_AUTH"
ERR_BRIDGE_TIMEOUT = "BRIDGE_TIMEOUT"

# Security / guard
ERR_GUARD_BLOCKED = "GUARD_BLOCKED"
ERR_GUARD_INVALID_INPUT = "GUARD_INVALID_INPUT"
ERR_GUARD_RISK_REJECTED = "GUARD_RISK_REJECTED"

# ---------------------------------------------------------------------------
# Well-known severity values (mirrors observability.SEVERITY_* constants so
# this module has zero external dependencies).
# ---------------------------------------------------------------------------

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"
SEVERITY_CRITICAL = "critical"

# ---------------------------------------------------------------------------
# Well-known source values
# ---------------------------------------------------------------------------

SOURCE_NETWORK = "network"
SOURCE_DATABASE = "database"
SOURCE_AI = "ai"
SOURCE_UI = "ui"
SOURCE_SYSTEM = "system"
SOURCE_BRIDGE = "bridge"
SOURCE_GUARD = "guard"
SOURCE_SYNC = "sync"
SOURCE_STARTUP = "startup"


@dataclass
class AppError:
    """A single, unified error object used across all application layers.

    Attributes
    ----------
    code
        Machine-readable error code, e.g. ``"NETWORK_TIMEOUT"``.  Prefer the
        ``ERR_*`` constants defined in this module.
    message
        Human-readable description suitable for end-user display or log files.
    severity
        One of ``"info"``, ``"warning"``, ``"error"``, ``"critical"``.
    source
        The subsystem that produced the error, e.g. ``"network"``, ``"ai"``.
    recoverable
        Whether the system can continue operating after this error (possibly
        with degraded functionality).
    retry_allowed
        Whether the failed operation can be retried as-is.
    cause
        Optional original exception message or lower-level error detail.
    context
        Optional key-value pairs providing additional context (endpoint,
        duration, request ID, etc.).
    """

    code: str
    message: str
    severity: str
    source: str
    recoverable: bool
    retry_allowed: bool
    cause: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    # ── serialisation ────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict representation, dropping internal-only fields.

        The returned dict is JSON-serialisable and suitable for telemetry
        payloads, structured log lines, or API responses.
        """
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "source": self.source,
            "recoverable": self.recoverable,
            "retry_allowed": self.retry_allowed,
            "cause": self.cause,
            "context": self.context,
        }

    def to_log_line(self) -> str:
        """Return a single-line string suitable for structured logging.

        The format is designed to be both human-readable at a glance and
        grep-friendly:
            [source] severity=SEVERITY code=CODE message=<msg> [cause=<cause>]
        """
        parts = [
            f"[{self.source}]",
            f"severity={self.severity}",
            f"code={self.code}",
            f"message={self.message}",
        ]
        if self.cause:
            parts.append(f"cause={self.cause}")
        if self.context:
            ctx_str = " ".join(f"{k}={v}" for k, v in self.context.items())
            parts.append(ctx_str)
        return " ".join(parts)

    def __bool__(self) -> bool:
        """An ``AppError`` is truthy — it represents an error that exists."""
        return True
