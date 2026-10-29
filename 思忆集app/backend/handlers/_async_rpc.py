# -*- coding: utf-8 -*-
"""S3-2 慢同步 RPC → 任务队列异步化的共享骨架（第 N 例照抄第 1 例）。

样板：``backend/handlers/_job_hunter.py`` 的 ``scrape_run`` / ``scrape_status``
（P2 风险册，rpc-census-2026-08-31）。那些方法的真实执行体经
``services/cron_actions.py`` 注册表在 ``queue_name="cron"`` 的 worker 里跑，
完成后 ``event_bus.emit(AppEvent("<name>.completed"))``，客户端轮询
``task_queue.get`` 拿终态。

本模块把「入队 + 轮询」两条重复逻辑抽成两个 helper，供 knowledge.brief /
personal.review / report.daily_summary / evolution.scan_and_report /
translate.text|full 复用。**刻意与样板保持完全一致的返回形**：

- 入队成功：``{"success": True, "job_id", "task_id", "status": "queued", "queue"}``
  （不泄漏 capability_id 之类内部键，避免与 status 查询结果的键集混淆）
- 队列不可用：``None`` → 调用方降级执行旧的同步实现（与 scrape_run 降级同语义）
- 入队异常：``{"success": False, "error"}``（旧 handler 各分支都带 ``success``，
  新路径同样带，不破坏调用方对 shape 的假设）

关于「每次调用都要独立 job_id」：样板 job_hunter 由 cron 周期派发，同参数去重
是想要的（``PersistentTaskQueue`` 对 pending/running 的 (queue_name,
capability_id, payload_hash) 唯一）。本模块服务的 6 处都是**用户按需点击**，
所以 RPC 侧预生成 ``_job_id`` 塞进 payload（``enqueue`` 支持自定义 task_id）
→ payload 每次都不同 → 不去重，连点两次就是两个任务。这是期望语义，
代价是 ``task_queue`` 记录里多一个 ``_job_id`` 键（worker 侧 pop 掉再执行）。

**S3-2 回退状态（2026-09-16 架构复审 audit §1）**：上述六处的 RPC *入口* 当前
**不再调用本模块的 ``queued_rpc``** —— 已回退为同步直调各自 ``run_*``（前端消费方
从未适配轮询，信封化导致 translate/evolution/personal 用户可见坏死，详见
``deliverables/architecture-conformance-audit-2026-09-16.md``）。本模块整体作为
异步底座**保留**：``async_rpc_worker``（cron_actions 六组 worker 注册仍引用）、
``poll_job_result`` + ``POLLER_SCHEMA``（六个 ``*.status`` 轮询器仍注册，无人入队
即零成本）、``*.completed`` 事件，待 S3-2b 先建前端轮询消费层后再翻转入口。
回退批的契约回归测试见 tests/test_async_rpc_s32_20260916.py（``_assert_not_envelope``）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

logger = logging.getLogger("siyi.backend.handlers.async_rpc")

__all__ = ["queued_rpc", "poll_job_result", "async_rpc_worker", "POLLER_SCHEMA"]


# S3-2 遗留债（2026-09-16 schema_coverage_ratchet 抓出）：*.status 轮询器
# 全部转发到 poll_job_result，参数契约一致，schema 收敛到这里统一维护。
# id 字段放宽为 string|integer —— poll_job_result 内部 str() 归一，容忍
# 客户端把 job_id 传成 int（见 test_poll_int_job_id_does_not_crash 回归）。
POLLER_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {"type": ["string", "integer"]},
        "task_id": {"type": ["string", "integer"]},
        "wait_seconds": {"type": "number"},
    },
}

# completed 结果落库后在内存里保留多久（前端轮询窗口），以及最多保留几条
_RESULT_TTL_SECONDS = 600.0
_RESULT_MAX_ENTRIES = 64
# job_id → (monotonic 到期时间, result)
_completed_results: dict[str, tuple[float, dict[str, Any]]] = {}


def _remember_result(job_id: str, result: dict[str, Any]) -> None:
    """记录 completed 任务的执行结果，供 poll 侧在短窗口内取回。

    ``task_queue`` 的 ``TaskRecord`` 只有 ``last_error`` 没有 result 列，且队列层
    ``purge_completed`` 会清掉 completed 记录；不落内存的话「秒回 job_id」之后
    就再也取不到真结果。重启后内存清空 → poll 返回 not found，可接受
    （前端每次操作只在本次会话内轮询）。
    """
    if not job_id:
        return
    now = time.monotonic()
    _completed_results[job_id] = (now + _RESULT_TTL_SECONDS, result)
    stale = [k for k, (exp, _) in _completed_results.items() if exp <= now]
    for k in stale:
        _completed_results.pop(k, None)
    while len(_completed_results) > _RESULT_MAX_ENTRIES:
        oldest = min(_completed_results.items(), key=lambda kv: kv[1][0])[0]
        _completed_results.pop(oldest, None)


def _recall_result(job_id: str) -> dict[str, Any] | None:
    entry = _completed_results.get(job_id)
    if entry is None:
        return None
    exp, result = entry
    if exp <= time.monotonic():
        _completed_results.pop(job_id, None)
        return None
    return result


# ── worker 侧：把「执行 + 发事件 + 记结果」收敛成一个装饰器 ──────────────


def async_rpc_worker(action_name: str, *, runner: Callable[[Any, dict], dict[str, Any]]):
    """构造 ``cron_actions`` 注册表用的同步 handler（装饰器形态）。

    行为（与 ``_register_job_hunter_scrape`` 内联实现一致）：

    - 调 ``runner(context, payload)`` 做真活（内部可用 ``_run_coroutine_sync``
      驱动 async 服务），把返回值原样作为「投递给客户端的结果」
    - 无论业务结果成败都 ``event_bus.emit(AppEvent("<action_name>.completed"))``，
      payload 带 ``job_id`` 供客户端匹配自己那次调用
    - 成功返回时把结果记进内存（``_remember_result``），poll 侧可取回
    - runner 抛异常 → 返回 ``{"success": False, "error"}``，让 worker 走正常的
      ``fail()`` 退避重试路径，而不是把异常抛出去炸掉 worker 主循环

    **业务失败 ≠ 任务失败**：runner 正常返回的 dict（哪怕内含
    ``{"success": False, "error": "ai returned non-json"}``）一律按「任务已完成」
    落 completed，把错误原文交给客户端。理由：这类结果是用户要看的答案，
    重试无意义；若映射成任务失败，按 max_retries=0 会立刻进 dead_letter，
    等于把 S2-3 刚清理完的死信面重新灌垃圾（job_hunter 样板可以那么映射，
    因为它由 cron 周期派发、失败重试有意义）。

    ``job_id`` 从 payload 里取（``execute_cron_action`` 的签名只有
    (context, payload)，拿不到 task_id）；入队侧会在 payload 里塞
    ``_job_id``——``PersistentTaskQueue.enqueue`` 允许额外键，worker 侧
    pop 掉后再交给 runner。
    """

    def _handler(context: Any, payload: dict) -> dict:
        payload = dict(payload or {})
        job_id = str(payload.pop("_job_id", "") or "")
        try:
            result = runner(context, payload)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s 执行失败: %s", action_name, exc, exc_info=True)
            _emit(action_name, job_id, {"job_id": job_id, "success": False, "error": str(exc)})
            # 抛异常 = 任务真失败 → 交回 worker 走 fail()/重试/dead_letter
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        result = result if isinstance(result, dict) else {"result": result}
        # 任务执行成功（即便 result 内含业务 success=False）→ 记结果 + 落 completed，
        # 把原文交给客户端；不让业务失败污染任务终态（见上方 docstring）。
        _remember_result(job_id, result)
        _emit(action_name, job_id, {"job_id": job_id, "result": result})
        return {"success": True, "status": "completed", "result": result}

    return _handler


def _emit(action_name: str, job_id: str, payload: dict) -> None:
    try:
        from core.events import AppEvent, event_bus

        event_bus.emit(AppEvent(f"{action_name}.completed", payload=payload))
    except Exception:  # noqa: BLE001
        logger.debug("%s.completed 事件发送失败", action_name, exc_info=True)


# ── RPC 侧：入队 / 轮询 ──────────────────────────────────────────────


async def queued_rpc(
    context: Any,
    *,
    capability_id: str,
    payload: dict[str, Any],
    queue_name: str = "cron",
    max_retries: int = 0,
    with_job_id_in_payload: bool = True,
) -> dict[str, Any] | None:
    """把一次慢 RPC 转成队列任务，秒回 job_id。

    返回 ``None`` 表示队列不可用 —— 调用方应降级执行原来的同步实现，
    这样后端没起 task_queue（测试/精简启动）时行为不退化。

    ``with_job_id_in_payload``：worker 侧 ``execute_cron_action(context, payload)``
    拿不到 task_id，事件要带 job_id 只能由 RPC 侧预生成并塞进 payload
    （``enqueue`` 支持自定义 task_id）。副作用是 payload 每次都不同 → 队列层
    「同参数活跃去重」失效：连点两次=两个任务。对按需触发的 LLM RPC 这是
    期望语义（用户再点就是要再跑一遍），样板 job_hunter 保留去重是因为它由
    cron 周期派发。
    """
    q = getattr(context, "task_queue", None)
    if q is None:
        return None
    import uuid

    job_id = uuid.uuid4().hex[:16]
    payload = dict(payload)
    if with_job_id_in_payload:
        payload["_job_id"] = job_id
    else:
        job_id = ""
    try:
        task_id = await asyncio.to_thread(
            q.enqueue,
            capability_id=capability_id,
            payload=payload,
            queue_name=queue_name,
            max_retries=max_retries,
            **({"task_id": job_id} if job_id else {}),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s enqueue 失败: %s", capability_id, exc)
        return {"success": False, "error": f"enqueue failed: {exc}"}
    return {
        "success": True,
        "job_id": task_id,
        "task_id": task_id,
        "status": "queued",
        "queue": queue_name,
    }


_STATUS_MAP = {
    "pending": "queued",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
    "dead_letter": "failed",
}


async def poll_job_result(
    context: Any,
    *,
    job_id: str = "",
    task_id: str = "",
    capability_id: str = "",
    wait_seconds: float = 0.0,
    poll_interval: float = 0.1,
    fetch: Callable[[dict], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """查询一次异步 RPC 的状态 / 结果。

    - 任务未完成：``{"success": True, "status": "queued|running", "job_id", ...}``
      （``wait_seconds > 0`` 时先在该状态上阻塞等待这么久再返回，供希望"尽量一次
      拿全"的旧调用方使用；异步客户端应传 0 自行轮询）
    - 已完成且有结果：``result`` 的内容 + ``job_id`` / ``status="completed"``
    - 已完成但结果取不到（服务重启/记录被清理）：
      ``{"success": False, "error": ..., "status": "completed"}``
    - 失败/死信：``{"success": False, "error", "status": "failed", "retries", "attempts"}``
    """
    tid = str(job_id or task_id or "").strip()
    if not tid:
        return {"success": False, "error": "job_id required"}
    q = getattr(context, "task_queue", None)
    if q is None:
        return {"success": False, "error": "task_queue service unavailable", "job_id": tid}
    deadline = time.monotonic() + max(0.0, float(wait_seconds or 0.0))
    rec: Any = None
    while True:
        try:
            rec = await asyncio.to_thread(q.get, tid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("poll %s 查询失败: %s", tid, exc)
            return {"success": False, "error": str(exc), "job_id": tid}
        if rec is None:
            return {"success": False, "error": "task not found", "job_id": tid}
        if rec.status in ("completed", "dead_letter", "failed") or time.monotonic() >= deadline:
            break
        await asyncio.sleep(max(0.01, float(poll_interval)))

    d = rec.to_dict()
    display = _STATUS_MAP.get(d.get("status"), d.get("status"))
    base = {"job_id": tid, "task_id": tid, "status": display, "task": d}
    if capability_id and d.get("capability_id") != capability_id:
        logger.warning("job %s capability 不匹配（期望 %s 实际 %s）", tid, capability_id, d.get("capability_id"))

    if display in ("queued", "running"):
        return {"success": True, **base}

    if display == "completed":
        stored = _recall_result(tid)
        if stored is None and fetch is not None:
            try:
                stored = fetch(dict(d.get("payload") or {}))
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s 结果补取失败: %s", capability_id, exc)
                stored = None
        if stored is None:
            return {
                "success": False,
                "error": "result unavailable (backend restarted or record purged) — 请重新发起",
                **base,
            }
        # 业务失败但任务 completed：runner 可能只返回 {"error": ...} 而不带
        # success 键（run_evolution_scan_and_report / run_translate_full 即如此）。
        # 若不推导，下面的合并会保留 success=True，客户端无法判断这次异步 RPC
        # 到底成没成。规则：stored 无显式 success 且含 error → 视为业务失败。
        if "success" not in stored and stored.get("error"):
            stored = {**stored, "success": False}
        return {"success": True, **base, **stored}

    # failed / dead_letter
    err = str(d.get("last_error") or "job failed")
    attempts = int(d.get("attempts") or 0)
    retries_left = max(0, int(d.get("max_retries") or 0) - max(0, attempts - 1))
    return {"success": False, "error": err, "retries": retries_left, **base}
