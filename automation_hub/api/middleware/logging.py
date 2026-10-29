# -*- coding: utf-8 -*-
"""HTTP 请求日志中间件 — 结构化 JSON 日志"""

from __future__ import annotations

import time
import logging
from typing import Any

from fastapi import Request

from ..routes.metrics import observe_request_duration

logger = logging.getLogger("automation_hub.http")


def _sanitize_path(path: str) -> str:
    """Remove sensitive query parameters from logged paths."""
    return path.split("?")[0]


async def request_logging_middleware(request: Request, call_next):
    """记录每个 HTTP 请求的方法、路径、耗时和状态码。

    Outputs structured JSON via the configured JSONFormatter, including
    extra fields: method, path, status_code, duration_ms
    """
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_s = time.perf_counter() - start
    elapsed_ms = elapsed_s * 1000

    # Record Prometheus histogram observation
    observe_request_duration(
        method=request.method,
        path=_sanitize_path(request.url.path),
        status_code=response.status_code,
        duration_seconds=elapsed_s,
    )

    extra_fields: dict[str, Any] = {
        "method": request.method,
        "path": _sanitize_path(request.url.path),
        "status_code": response.status_code,
        "duration_ms": round(elapsed_ms, 1),
    }

    if response.status_code >= 500:
        logger.error(
            f"{request.method} {extra_fields['path']} → {response.status_code} ({elapsed_ms:.1f}ms)",
            extra=extra_fields,
        )
    elif response.status_code >= 400:
        logger.warning(
            f"{request.method} {extra_fields['path']} → {response.status_code} ({elapsed_ms:.1f}ms)",
            extra=extra_fields,
        )
    else:
        logger.info(
            f"{request.method} {extra_fields['path']} → {response.status_code} ({elapsed_ms:.1f}ms)",
            extra=extra_fields,
        )

    return response
