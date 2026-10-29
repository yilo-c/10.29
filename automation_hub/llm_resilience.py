from __future__ import annotations

import email.utils
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


RATE_LIMIT_MARKERS = (
    "429",
    "rate limit",
    "too many requests",
    "resource exhausted",
    "quota",
    "retry after",
)

SERVER_ERROR_MARKERS = (
    "500",
    "502",
    "503",
    "504",
    "server error",
    "temporarily unavailable",
    "overloaded",
)

NETWORK_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "connection reset",
    "connection aborted",
    "network",
)


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 4
    min_delay_seconds: float = 1.0
    max_delay_seconds: float = 8.0
    jitter_ratio: float = 0.1
    retry_after_cap_seconds: float = 30.0


@dataclass(frozen=True)
class ErrorClassification:
    kind: str
    transient: bool
    status: int | None = None
    retry_after_seconds: float | None = None
    message: str = ""


def extract_error_text(raw_text: str | None) -> str:
    body = (raw_text or "").strip()
    if not body:
        return ""
    try:
        payload = json.loads(body)
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                msg = err.get("message")
                if isinstance(msg, str) and msg.strip():
                    return msg.strip()
            msg = payload.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
    except Exception:
        pass
    return body


def extract_error_text_from_response(response: Any) -> str:
    if response is None:
        return ""
    try:
        return extract_error_text(getattr(response, "text", ""))
    except Exception:
        return ""


def parse_retry_after_seconds(retry_after_header: str | None, error_text: str = "") -> float | None:
    if isinstance(retry_after_header, str) and retry_after_header.strip():
        raw = retry_after_header.strip()
        try:
            seconds = float(raw)
            if seconds >= 0:
                return seconds
        except ValueError:
            try:
                dt = email.utils.parsedate_to_datetime(raw)
                if dt is not None:
                    now = datetime.now(dt.tzinfo or timezone.utc)
                    delta = (dt - now).total_seconds()
                    if delta >= 0:
                        return delta
            except Exception:
                pass

    text = (error_text or "").strip()
    if not text:
        return None

    for pattern, scale in (
        (r"retry[_-]?after\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", 1.0),
        (r"retry[_-]?after[_-]?ms\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", 0.001),
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            value = float(match.group(1)) * scale
            if value >= 0:
                return value
        except ValueError:
            continue

    return None


def classify_error(status: int | None, error_text: str, retry_after_seconds: float | None = None) -> ErrorClassification:
    lowered = (error_text or "").lower()

    if status in (401, 403):
        return ErrorClassification("auth_error", transient=False, status=status, retry_after_seconds=retry_after_seconds, message=error_text)

    if status == 400:
        return ErrorClassification("bad_request", transient=False, status=status, retry_after_seconds=retry_after_seconds, message=error_text)

    if status == 429 or any(marker in lowered for marker in RATE_LIMIT_MARKERS):
        return ErrorClassification("rate_limit", transient=True, status=status, retry_after_seconds=retry_after_seconds, message=error_text)

    if status is not None and 500 <= status <= 599:
        return ErrorClassification("server_error", transient=True, status=status, retry_after_seconds=retry_after_seconds, message=error_text)

    if any(marker in lowered for marker in SERVER_ERROR_MARKERS):
        return ErrorClassification("server_error", transient=True, status=status, retry_after_seconds=retry_after_seconds, message=error_text)

    if any(marker in lowered for marker in NETWORK_MARKERS):
        return ErrorClassification("network_error", transient=True, status=status, retry_after_seconds=retry_after_seconds, message=error_text)

    return ErrorClassification("unknown_error", transient=False, status=status, retry_after_seconds=retry_after_seconds, message=error_text)


def compute_retry_delay_seconds(*, attempt: int, policy: RetryPolicy, retry_after_seconds: float | None = None) -> float:
    base_delay = min(policy.min_delay_seconds * (2 ** (attempt - 1)), policy.max_delay_seconds)
    jitter = base_delay * max(0.0, policy.jitter_ratio) * random.random()
    local_delay = base_delay + jitter

    if retry_after_seconds is None:
        return local_delay

    cap = policy.retry_after_cap_seconds if policy.retry_after_cap_seconds > 0 else retry_after_seconds
    return max(local_delay, min(retry_after_seconds, cap))


def build_observability_tags(
    *,
    provider: str,
    model: str | None,
    status: int | None,
    transient: bool,
    retry_after_seconds: float | None,
    fallback_stage: str,
    cache_hit: bool,
    error_kind: str,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "status": status,
        "transient": bool(transient),
        "retry_after": retry_after_seconds,
        "fallback_stage": fallback_stage,
        "cache_hit": bool(cache_hit),
        "error_kind": error_kind,
    }