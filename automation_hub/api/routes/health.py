# -*- coding: utf-8 -*-
"""健康检查路由 — 增强版，包含组件状态探针"""

from __future__ import annotations

import logging
import time
from datetime import datetime

import redis
from fastapi import APIRouter

from ..config import settings
from ..db.connection import get_db_connection
from ..schemas import ComponentHealth, HealthCheck

router = APIRouter(tags=["health"])
logger = logging.getLogger("automation_hub.health")


def _check_database() -> ComponentHealth:
    """Check DB connectivity by executing a trivial query."""
    start = time.perf_counter()
    try:
        with get_db_connection() as conn:
            conn.execute("SELECT 1").fetchone()
        latency = (time.perf_counter() - start) * 1000
        return ComponentHealth(
            name="database",
            status="ok",
            detail=f"sqlite3 at {settings.DATABASE_PATH.name}",
            latency_ms=round(latency, 1),
        )
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        logger.warning("Health check — database down", extra={"component": "health", "target": "database", "latency_ms": round(latency, 1), "error": str(exc)})
        return ComponentHealth(
            name="database",
            status="down",
            detail=str(exc)[:200],
            latency_ms=round(latency, 1),
        )


def _check_redis() -> ComponentHealth:
    """Check Redis connectivity by pinging."""
    start = time.perf_counter()
    try:
        conn = redis.from_url(settings.REDIS_URL)
        conn.ping()
        latency = (time.perf_counter() - start) * 1000
        return ComponentHealth(
            name="redis",
            status="ok",
            detail=f"connected at {settings.REDIS_URL}",
            latency_ms=round(latency, 1),
        )
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        logger.warning("Health check — redis down", extra={"component": "health", "target": "redis", "latency_ms": round(latency, 1), "error": str(exc)})
        return ComponentHealth(
            name="redis",
            status="down",
            detail=str(exc)[:200],
            latency_ms=round(latency, 1),
        )


@router.get("/health", response_model=HealthCheck)
async def health_check():
    """增强健康检查端点，返回各组件状态。

    Components:
    - database: sqlite3 连接检测
    - redis: Redis 连通性检测
    """
    db_health = _check_database()
    redis_health = _check_redis()

    components = [db_health, redis_health]

    # Determine overall status
    if any(c.status == "down" for c in components):
        overall = "down"
    elif any(c.status == "degraded" for c in components):
        overall = "degraded"
    else:
        overall = "ok"

    logger.info(
        "Health check result",
        extra={
            "component": "health",
            "status": overall,
            "database": db_health.status,
            "redis": redis_health.status,
        },
    )

    return HealthCheck(
        status=overall,
        timestamp=datetime.now(),
        components=components,
    )
