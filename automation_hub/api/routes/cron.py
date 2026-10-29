# -*- coding: utf-8 -*-
"""Cron 任务检视路由。

设计说明
--------
思忆集 Automation Hub 的**真正调度器不在 API 层**：定时任务的注册与执行由
ARQ Worker（``worker/arq_settings.py`` 中的 ``build_worker_kwargs`` 通过
``arq.cron`` 注册，例如每 5 分钟回收 stale run 的看门狗）负责。

本路由只提供**只读检视**端点，暴露当前已注册的 cron job 列表与状态，供运维
查看，不重复造调度器。这与 ``cronjob`` 角色（scope=``tool:execute``+``tool:read``，
由 ``api/middleware/scope.py`` 强制只能访问 ``/tools/`` 路径）的设计意图一致：
定时任务通过 token 触发工具执行，调度元数据在此集中可见。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["cron"])


def _format_schedule(job: object) -> str:
    """从 arq CronJob 的 minute/hour/day/month/weekday 字段生成人类可读调度描述。

    arq 的 ``CronJob`` 实例字段名（实测）：``minute``/``hour``/``day``/
    ``month``/``weekday`` 为 ``None`` 或集合；``run_at_startup`` 为布尔。
    """
    minute = getattr(job, "minute", None)
    hour = getattr(job, "hour", None)
    day = getattr(job, "day", None)
    month = getattr(job, "month", None)
    weekday = getattr(job, "weekday", None)
    run_at_startup = getattr(job, "run_at_startup", False)

    parts: list[str] = []
    if hour is None and day is None and month is None and weekday is None:
        if isinstance(minute, (set, frozenset)) and minute:
            # 例如 {0,5,10,...} 表示每 5 分钟
            step = _infer_step(minute)
            parts.append(f"every {step} minute(s)" if step else f"minutes={sorted(minute)}")
        elif minute is None:
            parts.append("every minute")
        else:
            parts.append(f"minutes={sorted(minute) if isinstance(minute, (set, frozenset)) else minute}")
    else:
        parts.append(
            f"month={month} day={day} weekday={weekday} hour={hour} minute={sorted(minute) if isinstance(minute, (set, frozenset)) else minute}"
        )
    if run_at_startup:
        parts.append("run_at_startup=true")
    return " ".join(parts)


def _infer_step(values) -> int | None:
    """若集合是等差数列，返回步长；否则返回 None。"""
    if not isinstance(values, (set, frozenset)) or len(values) < 2:
        return None
    s = sorted(values)
    diffs = {b - a for a, b in zip(s, s[1:])}
    return diffs.pop() if len(diffs) == 1 else None


def _collect_cron_jobs() -> list[dict[str, str]]:
    """从 ARQ Worker 配置中收集已注册的 cron job 元数据。

    返回每个 cron job 的简要描述。若 worker 模块在当前环境不可用
    （例如缺少 Redis 依赖的导入链），则安全降级返回空列表，确保路由
    始终可挂载、``import api.main`` 不崩溃。
    """
    try:
        from worker.arq_settings import build_worker_kwargs

        kwargs = build_worker_kwargs()
        cron_jobs = kwargs.get("cron_jobs") or []
    except Exception:
        # 调度器依赖（Redis/arq）在纯 API 测试环境中可能未就绪；
        # 检视端点降级为空，不影响应用启动与契约测试。
        return []

    jobs: list[dict[str, str]] = []
    for job in cron_jobs:
        # arq.CronJob 实例暴露 .name（"cron:<func>"）与 .coroutine（函数对象）
        raw_name = getattr(job, "name", None) or ""
        name = raw_name.split(":", 1)[-1] if raw_name.startswith("cron:") else raw_name
        jobs.append(
            {
                "name": name,
                "schedule": _format_schedule(job),
            }
        )
    return jobs


@router.get("/cron")
async def list_cron_jobs():
    """列出当前 Automation Hub 注册的所有 cron 定时任务。"""
    return {
        "count": len(_collect_cron_jobs()),
        "jobs": _collect_cron_jobs(),
    }


@router.get("/cron/{job_name}")
async def get_cron_job(job_name: str):
    """查看单个 cron 任务的详情（按 function 名匹配）。"""
    for job in _collect_cron_jobs():
        if job["name"] == job_name:
            return {"found": True, **job}
    return {"found": False, "name": job_name}
