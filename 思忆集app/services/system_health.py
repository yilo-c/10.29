"""SystemHealthAggregator — 聚合各子系统健康状态，供前端统一状态面板展示。

设计要点：
- 10 个子系统并行采集（ThreadPoolExecutor, max_workers=8）
- 10 秒缓存 TTL，避免高频 RPC 调用压垮后端
- 严重度分级：healthy / degraded / down / unknown
- 单个子系统采集失败不影响其他子系统
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("siyi.services.system_health")

from services.agent_gateway.routing import resolve_chat_route, get_gateway

# 严重度等级
SEVERITY_HEALTHY = "healthy"
SEVERITY_DEGRADED = "degraded"
SEVERITY_DOWN = "down"
SEVERITY_UNKNOWN = "unknown"


@dataclass
class SubsystemDef:
    """子系统定义。"""

    key: str  # 唯一标识，如 "ai"
    name: str  # 显示名，如 "AI 服务"
    collector: Callable[[Any], dict[str, Any]]  # 采集函数 (context) -> snapshot dict
    timeout: float = 3.0  # 单子系统采集超时（秒）


@dataclass
class SubsystemStatus:
    """单个子系统采集结果。"""

    key: str
    name: str
    severity: str  # healthy / degraded / down / unknown
    available: bool  # 是否可用（资源存在且未抛异常）
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0  # 采集耗时
    error: str = ""  # 异常信息（若有）


@dataclass
class SystemHealthSnapshot:
    """整系统健康快照。"""

    timestamp: float  # Unix 时间戳
    overall_severity: str  # 全局严重度（取最差）
    subsystems: list[SubsystemStatus]
    summary: dict[str, int] = field(default_factory=dict)  # 各严重度计数


# ── 各子系统采集函数 ──


def _run_async_in_thread(coro, timeout: float = 5.0):
    """在线程池中安全运行一个异步协程并返回结果。"""
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
    finally:
        loop.close()


def _collect_ai(context: Any) -> dict[str, Any]:
    """AI 服务：检查 ai_client 是否就绪 + provider 配置与真实推理可用性。"""
    client = getattr(context, "ai_client", None)
    if client is None:
        return {"severity": SEVERITY_DOWN, "available": False, "message": "AI client not initialized"}
    providers = []
    try:
        from core.provider_config import load_providers, provider_to_safe_dict

        providers = load_providers(context.settings_repository)
    except Exception:
        pass
    if not providers:
        return {
            "severity": SEVERITY_DEGRADED,
            "available": True,
            "message": "no providers configured",
            "details": {"provider_count": 0, "enabled_count": 0, "providers": []},
        }
    enabled = [p for p in providers if p.enabled]
    valid_enabled = [p for p in enabled if p.is_valid()]

    # 对每个 enabled 且非本地 provider 做真实推理测试
    # N-F（2026-09-17）：并行探测。历史实现串行 test_connectivity（每次新建事件
    # 循环 + 真实 HTTP 往返，各最长 5s），N 个 provider 耗时线性累加 → system.health
    # 面板 2.3s+。改为线程池并行后总耗时 ≈ 单个最慢 provider。
    # 每个探测仍由 _run_async_in_thread 内的 asyncio.wait_for 保证上界，
    # 不会无限阻塞；shutdown(wait=False) 避免慢 provider 拖住聚合线程 join。
    inference_targets = [p for p in enabled if not p.is_local and p.is_valid()]
    inference_results: dict[str, tuple[bool, str]] = {}

    def _probe(p: Any) -> tuple[str, tuple[bool, str]]:
        try:
            return p.name, _run_async_in_thread(
                p.test_connectivity(),
                timeout=min(5.0, p.timeout or 5.0),
            )
        except Exception as exc:
            return p.name, (False, str(exc)[:120])

    if inference_targets:
        probe_ex = ThreadPoolExecutor(
            max_workers=min(8, len(inference_targets)),
            thread_name_prefix="ai-probe",
        )
        try:
            futs = [probe_ex.submit(_probe, p) for p in inference_targets]
            for fut in futs:
                try:
                    name, res = fut.result()
                    inference_results[name] = res
                except Exception as exc:  # pragma: no cover - 探测内部已兜底，防御性
                    logger.debug("provider probe future failed: %s", exc)
        finally:
            probe_ex.shutdown(wait=False)

    provider_details = []
    cloud_inference_ok_count = 0
    cloud_enabled_count = 0
    any_billing_error = False
    for p in providers:
        detail = {
            **provider_to_safe_dict(p),
            "valid": p.is_valid(),
        }
        if p.enabled and not p.is_local and p.is_valid():
            cloud_enabled_count += 1
            ok, msg = inference_results.get(p.name, (False, "未测试"))
            detail["inference_ok"] = ok
            detail["inference_error"] = None if ok else msg
            detail["billing_error"] = "账户欠费" in msg or "配额耗尽" in msg
            if ok:
                cloud_inference_ok_count += 1
            if detail["billing_error"]:
                any_billing_error = True
        provider_details.append(detail)

    if not enabled:
        severity = SEVERITY_DEGRADED
        message = "no enabled providers"
    elif not valid_enabled:
        severity = SEVERITY_DEGRADED
        message = "enabled providers invalid (missing api_base/key)"
    elif cloud_enabled_count == 0:
        # 只有本地模型，也视为可用但 degraded（功能受限）
        severity = SEVERITY_DEGRADED
        message = "仅本地模型可用"
    elif cloud_inference_ok_count == 0:
        severity = SEVERITY_DEGRADED
        message = "所有云端 provider 推理失败（可能欠费/不可用）"
    elif cloud_inference_ok_count < cloud_enabled_count:
        severity = SEVERITY_DEGRADED
        message = f"{cloud_inference_ok_count}/{cloud_enabled_count} 云端 provider 可用"
    else:
        severity = SEVERITY_HEALTHY
        message = f"{len(valid_enabled)} provider(s) ready"

    return {
        "severity": severity,
        "available": True,
        "message": message,
        "details": {
            "provider_count": len(providers),
            "enabled_count": len(enabled),
            "ready_count": len(valid_enabled),
            "cloud_inference_ok_count": cloud_inference_ok_count,
            "cloud_enabled_count": cloud_enabled_count,
            "any_billing_error": any_billing_error,
            "providers": provider_details,
        },
    }


def _collect_cost(context: Any) -> dict[str, Any]:
    """成本追踪：今日/本月花费 + 调用次数。"""
    tracker = getattr(context, "cost_tracker", None)
    if tracker is None:
        return {"severity": SEVERITY_UNKNOWN, "available": False, "message": "cost tracker not available"}
    snap = tracker.get_status_snapshot()
    return {"severity": SEVERITY_HEALTHY, "available": True, "message": "ok", "details": snap}


def _collect_cron(context: Any) -> dict[str, Any]:
    """定时任务：任务数 + 运行状态。"""
    cron = getattr(context, "cron_service", None)
    if cron is None:
        return {"severity": SEVERITY_UNKNOWN, "available": False, "message": "cron service not available"}
    snap = cron.get_status_snapshot()
    job_count = snap.get("job_count", 0)
    return {
        "severity": SEVERITY_HEALTHY,
        "available": True,
        "message": f"{job_count} job(s) registered",
        "details": snap,
    }


def _collect_memory(context: Any) -> dict[str, Any]:
    """记忆系统：检查 memory_index_service。"""
    svc = getattr(context, "memory_index_service", None)
    if svc is None:
        return {"severity": SEVERITY_DOWN, "available": False, "message": "memory index not initialized"}
    count = None
    try:
        if hasattr(svc, "count"):
            count = svc.count()
        elif hasattr(svc, "size"):
            count = svc.size()
    except Exception:
        pass
    details = {"memory_count": count} if count is not None else {}
    return {"severity": SEVERITY_HEALTHY, "available": True, "message": "ok", "details": details}


def _collect_knowledge_graph(context: Any) -> dict[str, Any]:
    """知识图谱：检查 knowledge_graph_store。"""
    store = getattr(context, "knowledge_graph_store", None)
    if store is None:
        return {"severity": SEVERITY_DOWN, "available": False, "message": "knowledge graph not initialized"}
    return {"severity": SEVERITY_HEALTHY, "available": True, "message": "ok"}


def _collect_sync(context: Any) -> dict[str, Any]:
    """云同步：检查 cloud_sync_bridge。"""
    bridge = getattr(context, "cloud_sync_bridge", None)
    if bridge is None:
        return {"severity": SEVERITY_UNKNOWN, "available": False, "message": "cloud sync not configured"}
    status = bridge.get_status()
    enabled = status.get("enabled", False)
    configured = status.get("configured", False)
    running = status.get("running", False)
    if not configured:
        return {"severity": SEVERITY_UNKNOWN, "available": False, "message": "not configured", "details": status}
    if enabled and running:
        return {"severity": SEVERITY_HEALTHY, "available": True, "message": "running", "details": status}
    if enabled and not running:
        return {"severity": SEVERITY_DEGRADED, "available": True, "message": "enabled but not running", "details": status}
    return {"severity": SEVERITY_UNKNOWN, "available": False, "message": "disabled", "details": status}


def _collect_vocabulary(context: Any) -> dict[str, Any]:
    """词库：检查 vocabulary_repository。"""
    repo = getattr(context, "vocabulary_repository", None)
    if repo is None:
        return {"severity": SEVERITY_DOWN, "available": False, "message": "vocabulary repo not initialized"}
    return {"severity": SEVERITY_HEALTHY, "available": True, "message": "ok"}


def _collect_inbox(context: Any) -> dict[str, Any]:
    """收件箱：检查 personal_inbox_repository。"""
    repo = getattr(context, "personal_inbox_repository", None)
    if repo is None:
        return {"severity": SEVERITY_DOWN, "available": False, "message": "inbox repo not initialized"}
    return {"severity": SEVERITY_HEALTHY, "available": True, "message": "ok"}


def _collect_notes(context: Any) -> dict[str, Any]:
    """笔记：检查 personal_note_repository。"""
    repo = getattr(context, "personal_note_repository", None)
    if repo is None:
        return {"severity": SEVERITY_DOWN, "available": False, "message": "note repo not initialized"}
    return {"severity": SEVERITY_HEALTHY, "available": True, "message": "ok"}


def _collect_tasks(context: Any) -> dict[str, Any]:
    """任务：检查 personal_task_repository。"""
    repo = getattr(context, "personal_task_repository", None)
    if repo is None:
        return {"severity": SEVERITY_DOWN, "available": False, "message": "task repo not initialized"}
    return {"severity": SEVERITY_HEALTHY, "available": True, "message": "ok"}


def _collect_error_rate(context: Any) -> dict[str, Any]:
    """S1: AI 调用错误率 — 最近 5 分钟内的错误数/总调用数。

    从 AggregationTelemetry 的 call_traces 表中统计。
    若 telemetry 未初始化或无数据，视为 unknown。
    """
    # 优先从 ai_manager.aggregation_telemetry 获取
    ai_manager = getattr(context, "ai_manager", None)
    telemetry = None
    if ai_manager is not None:
        telemetry = getattr(ai_manager, "aggregation_telemetry", None)
    # 兜底：直接从 context 获取
    if telemetry is None:
        telemetry = getattr(context, "aggregation_telemetry", None)
    if telemetry is None:
        return {
            "severity": SEVERITY_UNKNOWN,
            "available": False,
            "message": "telemetry not initialized",
        }
    try:
        stats = telemetry.error_rate(since_seconds=300.0)
    except Exception as exc:
        return {
            "severity": SEVERITY_UNKNOWN,
            "available": False,
            "message": f"error_rate query failed: {exc}",
        }
    total = stats.get("total_calls", 0)
    failed = stats.get("failed_calls", 0)
    rate = stats.get("error_rate", 0.0)
    if total == 0:
        return {
            "severity": SEVERITY_UNKNOWN,
            "available": True,
            "message": "no recent AI calls",
            "details": stats,
        }
    # 阈值：错误率 > 50% 视为 down，> 20% 视为 degraded
    if rate > 0.5:
        severity = SEVERITY_DOWN
        message = f"错误率 {rate:.0%}（{failed}/{total}，5分钟内）"
    elif rate > 0.2:
        severity = SEVERITY_DEGRADED
        message = f"错误率 {rate:.0%}（{failed}/{total}，5分钟内）"
    else:
        severity = SEVERITY_HEALTHY
        message = f"错误率 {rate:.0%}（{failed}/{total}，5分钟内）"
    return {
        "severity": severity,
        "available": True,
        "message": message,
        "details": stats,
    }


def _collect_backup(context: Any) -> dict[str, Any]:
    """S3: 自动备份状态 — 上次备份时间和备份文件数。

    从 data/backups/ 目录读取最近一次备份文件的时间戳。
    BackupService 创建的备份文件名为 YYYYMMDD_HHMMSS.json.gz 或 .json。
    """
    from pathlib import Path

    try:
        # LocalDataStore 使用 base_dir 属性（非 data_dir）
        data_dir = getattr(context, "data_dir", None)
        if data_dir is None:
            data_store = getattr(context, "data_store", None)
            if data_store is not None:
                data_dir = getattr(data_store, "base_dir", None) or getattr(data_store, "data_dir", None)
        if data_dir is None:
            return {
                "severity": SEVERITY_UNKNOWN,
                "available": False,
                "message": "data_dir not available",
            }
        backup_root = Path(data_dir) / "backups"
        if not backup_root.exists():
            return {
                "severity": SEVERITY_UNKNOWN,
                "available": True,
                "message": "backups dir not created yet",
                "details": {"last_backup_at": None, "backup_count": 0},
            }
        # BackupService 创建 .json.gz / .json 文件，文件名为 YYYYMMDD_HHMMSS
        backup_files = sorted(
            [f for f in backup_root.iterdir() if f.suffix in (".gz", ".json") and not f.name.startswith("backups.")],
            key=lambda f: f.name,
            reverse=True,
        )
        if not backup_files:
            return {
                "severity": SEVERITY_UNKNOWN,
                "available": True,
                "message": "no backup yet",
                "details": {"last_backup_at": None, "backup_count": 0},
            }
        latest = backup_files[0]
        # 从文件名解析 YYYYMMDD_HHMMSS 时间戳
        from datetime import datetime

        stem = latest.stem.split(".")[0]  # 去掉 .json / .gz 后缀
        try:
            latest_dt = datetime.strptime(stem, "%Y%m%d_%H%M%S")
            age_days = (datetime.now() - latest_dt).days
        except ValueError:
            age_days = 0
        if age_days > 14:
            severity = SEVERITY_DOWN
            message = f"上次备份于 {stem}（{age_days} 天前）"
        elif age_days > 7:
            severity = SEVERITY_DEGRADED
            message = f"上次备份于 {stem}（{age_days} 天前）"
        else:
            severity = SEVERITY_HEALTHY
            message = f"上次备份于 {stem}（{age_days} 天前）"
        return {
            "severity": severity,
            "available": True,
            "message": message,
            "details": {
                "last_backup_at": stem,
                "backup_count": len(backup_files),
                "latest_file_size": latest.stat().st_size,
            },
        }
    except Exception as exc:
        return {
            "severity": SEVERITY_UNKNOWN,
            "available": False,
            "message": f"backup check failed: {exc}",
        }


def _collect_task_queue(context: Any) -> dict[str, Any]:
    """P1.1: 持久任务队列 — 队列大小 + worker 状态 + 死信数量。"""
    tq = getattr(context, "task_queue", None)
    if tq is None:
        return {"severity": SEVERITY_UNKNOWN, "available": False, "message": "task queue not initialized"}
    try:
        # 修复：PersistentTaskQueue 只有 stats() 方法，调用 get_stats() 会抛
        # AttributeError 被 except 捕获，导致任务队列子系统永远显示 DOWN。
        # 优先用 stats()，回退 get_stats() 兼容旧版本/其他实现。
        stats = tq.stats() if hasattr(tq, "stats") else tq.get_stats()
        dq_count = stats.get("dead_letter", 0)
        pending_count = stats.get("pending", 0)
        severity = SEVERITY_HEALTHY
        if dq_count > 200:
            severity = SEVERITY_DOWN
        elif dq_count > 50:
            severity = SEVERITY_DEGRADED
        return {
            "severity": severity,
            "available": True,
            "message": f"pending={pending_count} dead_letter={dq_count}",
            "details": stats,
        }
    except Exception as exc:
        return {"severity": SEVERITY_DOWN, "available": False, "message": f"task queue check failed: {exc}"}


def _collect_feedback(context: Any) -> dict[str, Any]:
    """P2.8: 用户反馈学习 — 反馈数 + 学习样本数 + 聚合状态。"""
    svc = getattr(context, "feedback_service", None)
    if svc is None:
        return {"severity": SEVERITY_UNKNOWN, "available": False, "message": "feedback service not initialized"}
    try:
        svc.get_stats()
        count = svc.count_learning_samples()
        return {
            "severity": SEVERITY_HEALTHY,
            "available": True,
            "message": f"feedback samples={count}",
            "details": {"learning_samples": count},
        }
    except Exception as exc:
        return {"severity": SEVERITY_DEGRADED, "available": True, "message": f"feedback check failed: {exc}"}


def _collect_notification(context: Any) -> dict[str, Any]:
    """P2.9: 通知推送通道 — 订阅事件数 + 未读通知数。"""
    disp = getattr(context, "notification_dispatcher", None)
    if disp is None:
        return {"severity": SEVERITY_UNKNOWN, "available": False, "message": "notification dispatcher not initialized"}
    try:
        subs = getattr(disp, "_subscriptions", [])
        sub_count = len(subs) if subs else 0
        return {
            "severity": SEVERITY_HEALTHY,
            "available": True,
            "message": f"{sub_count} event(s) subscribed",
            "details": {"subscribed_events": sub_count},
        }
    except Exception as exc:
        return {"severity": SEVERITY_DEGRADED, "available": True, "message": f"notification check failed: {exc}"}


def _collect_kernel(context: Any) -> dict[str, Any]:
    """Flip: 执行内核（网关路径）健康度。

    反映当前 chat_route 生效路径 + 网关可用性，供前端内核状态徽章与
    system.health 统一面板使用：
    - chat_route=legacy / 网关 type=none：内核未启用（经典 butler 模式），
      severity=unknown，available=False。
    - 网关 type=dsh|claude 且 health_check 可达：healthy。
    - 网关不可达或注入缺失：degraded（dispatcher 自动回落 butler，非硬故障）。
    """
    route = resolve_chat_route(context)
    gw = get_gateway(context)
    gw_type = getattr(gw, "type", None) if gw is not None else None
    if gw is None or gw_type == "none":
        return {
            "severity": SEVERITY_UNKNOWN,
            "available": False,
            "message": "内核未启用（经典 butler 模式）" if gw is None else "内核已禁用（--no-gateway）",
            "details": {"chat_route": route, "gateway_type": gw_type},
        }
    try:
        ok, err = gw.health_check() if hasattr(gw, "health_check") else (True, None)
        if ok:
            return {
                "severity": SEVERITY_HEALTHY,
                "available": True,
                "message": f"内核可达（{gw_type}）",
                "details": {"chat_route": route, "gateway_type": gw_type},
            }
        return {
            "severity": SEVERITY_DEGRADED,
            "available": True,
            "message": f"内核不可达：{err or 'unknown'}",
            "details": {"chat_route": route, "gateway_type": gw_type},
        }
    except Exception as exc:
        return {
            "severity": SEVERITY_DEGRADED,
            "available": True,
            "message": f"内核健康探测异常：{exc}",
            "details": {"chat_route": route, "gateway_type": gw_type},
        }


# 严重度优先级（越大越严重）
_SEVERITY_ORDER = {
    SEVERITY_HEALTHY: 0,
    SEVERITY_UNKNOWN: 1,
    SEVERITY_DEGRADED: 2,
    SEVERITY_DOWN: 3,
}


def _collect_capability_routing(context: Any) -> dict[str, Any]:
    """T3.6: 能力路由遥测 — 各 capability 的 plugin / fallback 命中计数。

    数据来自 CapabilityRouter 的内存计数器（get_capability_stats）。
    该子系统本身不反映"故障"，故始终 healthy；仅透出计数供删 fallback 决策量化。
    """
    try:
        from plugins.capability_router import get_capability_router

        router = get_capability_router()
        stats = router.get_capability_stats()
    except Exception as exc:
        return {
            "severity": SEVERITY_UNKNOWN,
            "available": False,
            "message": f"capability router unavailable: {exc}",
        }

    total_plugin = sum(v.get("plugin_hits", 0) for v in stats.values())
    total_fallback = sum(v.get("fallback_hits", 0) for v in stats.values())
    total_plugin_errors = sum(v.get("plugin_errors", 0) for v in stats.values())

    # 已 plugin 化的 capability 数（有 plugin invoker 注册）
    try:
        plugin_caps = [
            cap
            for cap, inv in router._plugin_invokers.items()  # noqa: SLF001 - 只读快照
        ]
    except Exception:
        plugin_caps = []

    return {
        "severity": SEVERITY_HEALTHY,
        "available": True,
        "message": (f"plugin={total_plugin} fallback={total_fallback} plugin_errors={total_plugin_errors} caps={len(stats)}"),
        "details": {
            "total_plugin_hits": total_plugin,
            "total_fallback_hits": total_fallback,
            "total_plugin_errors": total_plugin_errors,
            "tracked_capabilities": len(stats),
            "plugin_enabled_capabilities": len(plugin_caps),
            "per_capability": stats,
        },
    }


def _collect_events(context: Any) -> dict[str, Any]:
    """复C2/C3（2026-08-29）：事件桥接可观测 — 丢失/挤出/超时/订阅方异常计数。

    数据源：backend.event_bridge.get_active_bridge().stats()（线程安全快照）。
    本收集器在线程池线程内执行（system.health handler 已统一以
    asyncio.to_thread 包裹整个聚合），直接调用同步 stats() 即可，无需再起协程。
    订阅方异常计数来自 core.events 全局 event_bus.stats()。

    计数为进程启动以来的累计值；事件桥本身不因累计丢失判 down/degraded
    （对齐 capability_routing 纯遥测口径——累计计数无法区分瞬时背压与持续
    故障，阈值待窗口化速率后再引入），丢失详情在 message/details 透出。
    """
    try:
        from backend.event_bridge import get_active_bridge

        bridge = get_active_bridge()
    except Exception:  # pragma: no cover - 防御性：隔离 import 失败
        bridge = None
    if bridge is None:
        return {
            "severity": SEVERITY_UNKNOWN,
            "available": False,
            "message": "event bridge not available",
        }
    try:
        snap = bridge.stats()
    except Exception as exc:
        return {
            "severity": SEVERITY_UNKNOWN,
            "available": False,
            "message": f"event bridge stats failed: {exc}",
        }
    subscriber_errors: int | None = None
    try:
        from core.events import event_bus

        subscriber_errors = int(event_bus.stats().get("handler_errors", 0))
    except Exception:
        subscriber_errors = None
    details = {
        "dropped_total": snap.get("dropped_total", 0),
        "evicted_total": snap.get("evicted_total", 0),
        "send_timeout_total": snap.get("send_timeout_total", 0),
        "by_type": snap.get("by_type", {}),
        "subscriber_errors": subscriber_errors,
    }
    message = f"events ok (dropped={details['dropped_total']}, evicted={details['evicted_total']}, send_timeout={details['send_timeout_total']})"
    if details["dropped_total"] + details["send_timeout_total"] > 0:
        message += " — 存在累计事件丢失（慢客户端背压）"
    return {
        "severity": SEVERITY_HEALTHY,
        "available": True,
        "message": message,
        "details": details,
    }


class SystemHealthAggregator:
    """聚合各子系统健康状态。

    用法：
        aggregator = SystemHealthAggregator(context)
        snapshot = aggregator.aggregate_all()  # 带 10s 缓存
    """

    def __init__(self, context: Any, cache_ttl: float = 10.0) -> None:
        self._context = context
        self._cache_ttl = cache_ttl
        self._cache: SystemHealthSnapshot | None = None
        self._cache_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=12, thread_name_prefix="health")
        self._subsystems: list[SubsystemDef] = [
            # N-F（2026-09-17）：ai 采集超时 3.0→6.0s，与 _collect_ai 内部 provider
            # 探测上界 min(5.0, p.timeout) 自洽。此前多 provider 冷启动时外层 3s
            # 会截断真实探测（误判 timeout/degraded）且后台线程仍在跑。并行化后
            # 典型 <1s，6.0 仅为最坏安全上界（受 10s 缓存兜底）。
            SubsystemDef("ai", "AI 服务", _collect_ai, timeout=6.0),
            SubsystemDef("cost", "成本追踪", _collect_cost),
            SubsystemDef("cron", "定时任务", _collect_cron),
            SubsystemDef("memory", "记忆系统", _collect_memory),
            SubsystemDef("knowledge_graph", "知识图谱", _collect_knowledge_graph),
            SubsystemDef("sync", "云同步", _collect_sync),
            SubsystemDef("vocabulary", "词库", _collect_vocabulary),
            SubsystemDef("inbox", "收件箱", _collect_inbox),
            SubsystemDef("notes", "笔记", _collect_notes),
            SubsystemDef("tasks", "任务", _collect_tasks),
            # S1+S3: 错误率追踪 + 自动备份
            SubsystemDef("error_rate", "AI 错误率", _collect_error_rate),
            SubsystemDef("backup", "自动备份", _collect_backup),
            # P1.1: 新增 3 个子系统（任务队列 + 反馈学习 + 通知推送）
            SubsystemDef("task_queue", "任务队列", _collect_task_queue),
            SubsystemDef("feedback", "反馈学习", _collect_feedback),
            SubsystemDef("notification", "通知推送", _collect_notification),
            # Flip: 执行内核网关路径健康度（gateway 不可用时标记 degraded）
            SubsystemDef("kernel", "执行内核", _collect_kernel),
            # 复C2/C3（2026-08-29）: 事件桥接可观测（dropped/evicted/send_timeout/by_type）
            SubsystemDef("events", "事件桥接", _collect_events),
            # T3.6: 能力路由遥测（plugin/fallback 命中计数，供删 fallback 决策）
            SubsystemDef("capability_routing", "能力路由遥测", _collect_capability_routing),
        ]

    def _submit_one(self, defn: SubsystemDef) -> tuple[SubsystemDef, Any, float]:
        """提交单个子系统采集到线程池，立即返回（不阻塞等待）。

        返回 (defn, future, start_monotonic) —— 等待由 _await_one 完成。
        拆分提交与等待是为了让 aggregate_all 能批量提交后并行等待，
        否则逐个 submit+result 会退化成串行。
        """
        start = time.monotonic()
        future = self._executor.submit(defn.collector, self._context)
        return defn, future, start

    def _await_one(self, defn: SubsystemDef, future: Any, start: float) -> SubsystemStatus:
        """等待单个已提交的 future 完成，构建 SubsystemStatus（带超时与异常兜底）。"""
        try:
            result = future.result(timeout=defn.timeout)
            latency = (time.monotonic() - start) * 1000.0
            severity = result.get("severity", SEVERITY_UNKNOWN)
            return SubsystemStatus(
                key=defn.key,
                name=defn.name,
                severity=severity,
                available=bool(result.get("available", False)),
                message=result.get("message", ""),
                details=result.get("details", {}),
                latency_ms=round(latency, 2),
            )
        except FuturesTimeoutError:
            latency = (time.monotonic() - start) * 1000.0
            return SubsystemStatus(
                key=defn.key,
                name=defn.name,
                severity=SEVERITY_DEGRADED,
                available=False,
                message=f"timeout ({defn.timeout}s)",
                latency_ms=round(latency, 2),
                error="timeout",
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000.0
            logger.debug("health collector %s failed: %s", defn.key, exc, exc_info=True)
            return SubsystemStatus(
                key=defn.key,
                name=defn.name,
                severity=SEVERITY_DOWN,
                available=False,
                message="collector error",
                latency_ms=round(latency, 2),
                error=str(exc),
            )

    def aggregate_all(self, force_refresh: bool = False) -> SystemHealthSnapshot:
        """采集所有子系统，返回健康快照。带 cache_ttl 缓存。"""
        with self._cache_lock:
            if not force_refresh and self._cache is not None and (time.time() - self._cache.timestamp) < self._cache_ttl:
                return self._cache

            # P1-13 修复：先批量提交所有 future，再统一等待，实现真正并行采集。
            # 历史问题：原实现用 [self._collect_one(d) for d in subsystems] 列表推导，
            # 每次循环内 submit 后立即 future.result() 阻塞，导致 10 个子系统串行执行，
            # 总耗时 ≈ 各子系统耗时之和（最坏 30s）。批量提交后总耗时 ≈ 单个最慢子系统的耗时。
            submitted = [self._submit_one(defn) for defn in self._subsystems]
            statuses = [self._await_one(defn, future, start) for defn, future, start in submitted]

            # 计算全局严重度（取最差）
            overall = SEVERITY_HEALTHY
            for s in statuses:
                if _SEVERITY_ORDER.get(s.severity, 0) > _SEVERITY_ORDER.get(overall, 0):
                    overall = s.severity

            # 统计各严重度数量
            summary: dict[str, int] = {
                SEVERITY_HEALTHY: 0,
                SEVERITY_DEGRADED: 0,
                SEVERITY_DOWN: 0,
                SEVERITY_UNKNOWN: 0,
            }
            for s in statuses:
                summary[s.severity] = summary.get(s.severity, 0) + 1

            snapshot = SystemHealthSnapshot(
                timestamp=time.time(),
                overall_severity=overall,
                subsystems=statuses,
                summary=summary,
            )
            self._cache = snapshot
            return snapshot

    def to_dict(self, force_refresh: bool = False) -> dict[str, Any]:
        """返回 JSON 可序列化的字典。"""
        snap = self.aggregate_all(force_refresh=force_refresh)
        return {
            "timestamp": snap.timestamp,
            "overall_severity": snap.overall_severity,
            "summary": snap.summary,
            "subsystems": [
                {
                    "key": s.key,
                    "name": s.name,
                    "severity": s.severity,
                    "available": s.available,
                    "message": s.message,
                    "details": s.details,
                    "latency_ms": s.latency_ms,
                    "error": s.error,
                }
                for s in snap.subsystems
            ],
        }

    def shutdown(self) -> None:
        """关闭线程池。"""
        try:
            self._executor.shutdown(wait=False)
        except Exception:
            logger.debug("health aggregator shutdown failed", exc_info=True)
