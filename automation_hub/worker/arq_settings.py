"""ARQ Worker 配置与 Redis 连接管理。

提供 ARQ Worker 参数、Redis 连接池创建、以及同步/异步入队辅助函数。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from arq.connections import ArqRedis, RedisSettings, create_pool

from automation_hub.api.config import settings

logger = logging.getLogger("automation_hub.arq")


# ---- Redis 连接池 ----

_pool: ArqRedis | None = None
_pool_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    return _pool_lock


async def get_redis_pool() -> ArqRedis:
    """获取 ARQ Redis 连接池（懒加载单例）。"""
    global _pool
    if _pool is None:
        async with _get_lock():
            if _pool is None:
                _pool = await create_pool(
                    RedisSettings.from_dsn(settings.REDIS_URL),
                )
    return _pool


async def close_redis_pool() -> None:
    """关闭 Redis 连接池。"""
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None


# ---- 异步入队 ----

async def enqueue_job(
    func_name: str,
    *args,
    _job_timeout: int | None = None,
    _queue_name: str | None = None,
    **kwargs,
) -> str | None:
    """异步入队一个 ARQ 任务。

    Args:
        func_name: 注册的函数名（如 ``run_tool_job``）。
        _job_timeout: 任务超时秒数。
        _queue_name: 队列名，默认由 ARQ 从函数名推断。

    Returns:
        任务 ID，失败返回 None。
    """
    pool = await get_redis_pool()
    job = await pool.enqueue_job(
        func_name,
        *args,
        _job_timeout=_job_timeout,
        _queue_name=_queue_name,
        **kwargs,
    )
    if job is None:
        logger.error("Failed to enqueue job %s: enqueue_job returned None", func_name)
        return None
    return job.job_id


# ---- 同步入队（供 sync FastAPI 端点使用） ----

async def _create_temp_pool() -> ArqRedis:
    return await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))


async def _enqueue_one_shot(func_name: str, *args, **kwargs) -> str | None:
    pool = await _create_temp_pool()
    try:
        job = await pool.enqueue_job(func_name, *args, **kwargs)
        if job is None:
            return None
        return job.job_id
    finally:
        pool.close()
        await pool.wait_closed()


def enqueue_job_sync(
    func_name: str,
    *args,
    job_timeout: int | None = None,
    queue_name: str | None = None,
    **kwargs,
) -> str | None:
    """从同步上下文入队 ARQ 任务。

    适用于 sync FastAPI 端点（tool_runs、approvals）。
    每次调用创建一次性连接。
    """
    try:
        return asyncio.run(
            _enqueue_one_shot(
                func_name,
                *args,
                _job_timeout=job_timeout,
                _queue_name=queue_name,
                **kwargs,
            ),
        )
    except Exception:
        logger.exception("Failed to enqueue job %s from sync context", func_name)
        return None


# ---- 队列长度（供 metrics 使用） ----

def queue_length() -> int:
    """获取当前队列中的任务数量（近似值）。"""
    try:
        import redis as sync_redis
        r = sync_redis.from_url(settings.REDIS_URL)
        # ARQ 将任务存储在 ``arq:queue:<queue_name>`` 的 Redis sorted set 中
        queue_name_actual = (settings.QUEUE_NAME.split(",", 1)[0] or "default").strip()
        return r.zcard(f"arq:queue:{queue_name_actual}") or 0
    except Exception:
        return -1


# ---- Worker 构造参数 ----

def build_worker_kwargs(**overrides) -> dict[str, Any]:
    """构建 ARQ Worker 关键字参数。

    返回一个 dict，可直接展开传递给 ``arq.Worker(**kwargs)``。
    支持 ``overrides`` 覆盖默认值，便于注入测试配置。
    """
    from worker.jobs_v2 import arq_run_tool_job, arq_reap_stale_runs
    from arq.cron import cron

    queue_name_actual = (settings.QUEUE_NAME.split(",", 1)[0] or "default").strip()

    # P0-4：每 5 分钟运行一次看门狗，回收超时的 running 状态 run（默认 30 min 超时）
    # run_at_startup=True 确保启动时立即清理一次上次进程崩溃遗留的 stale run
    watchdog_cron = cron(
        arq_reap_stale_runs,
        minute={i for i in range(0, 60, 5)},
        run_at_startup=True,
        timeout=60,
    )

    kwargs: dict[str, Any] = dict(
        functions=[arq_run_tool_job, arq_reap_stale_runs],
        cron_jobs=[watchdog_cron],
        redis_settings=RedisSettings.from_dsn(settings.REDIS_URL),
        max_jobs=10,
        job_timeout=300,
        keep_result=3600,
        keep_result_forever=False,
        poll_delay=0.5,
        queue_name=queue_name_actual,
        max_tries=5,
    )
    kwargs.update(overrides)
    return kwargs
