"""健康检查增强模块 — 提供详细的系统指标暴露。

功能：
- 系统资源监控（CPU、内存、磁盘）
- 服务状态检查
- 数据库连接检查
- AI 服务可用性检查
- 性能指标收集

P2-4 改造要点：
- **探针无副作用**：``_check_data_storage`` / ``_check_ai_service`` 不再自行
  ``LocalDataStore()`` 构造（那会触发 ``_legacy_migrate_once`` / 建库 / schema
  迁移）。data_store 必须由外部通过 ``__init__(data_store=...)`` 注入；未注入时
  探针返回 ``unknown``，绝不创建文件或目录。
- **启动门禁**：:func:`run_startup_gate` 在后端启动前检查关键子系统，失败时
  抛 :class:`StartupGateError` 并给出可操作信息。
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("siyi.core.health_check")


@dataclass
class HealthStatus:
    """健康状态"""
    status: str  # "healthy", "degraded", "unhealthy"
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SystemMetrics:
    """系统指标"""
    cpu_percent: float = 0.0
    memory_total_mb: float = 0.0
    memory_used_mb: float = 0.0
    memory_percent: float = 0.0
    disk_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_free_gb: float = 0.0
    disk_percent: float = 0.0
    python_version: str = ""
    platform: str = ""
    pid: int = 0
    uptime_seconds: float = 0.0


@dataclass
class ServiceHealth:
    """服务健康状态"""
    name: str
    status: str  # "up", "down", "degraded"
    latency_ms: float = 0.0
    message: str = ""
    last_check: Optional[datetime] = None


@dataclass
class PerformanceMetrics:
    """性能指标"""
    request_count: int = 0
    error_count: int = 0
    avg_response_time_ms: float = 0.0
    p95_response_time_ms: float = 0.0
    p99_response_time_ms: float = 0.0
    active_connections: int = 0
    cache_hit_rate: float = 0.0


class HealthChecker:
    """健康检查器。
    
    使用示例：
        checker = HealthChecker()
        
        # 获取完整健康报告
        report = await checker.get_health_report()
        
        # 检查特定服务
        status = await checker.check_service("database")
    """
    
    def __init__(self, data_store: Any = None):
        """初始化健康检查器。

        Args:
            data_store: 已构造好的 LocalDataStore 实例（必须由调用方在外部
                完成首启迁移/建库后再注入）。P2-4 之前此参数为 None 时探针
                会自行 ``LocalDataStore()`` 构造，触发 ``_legacy_migrate_once``
                和建库副作用；P2-4 起探针绝不自行构造，未注入时返回 ``unknown``。
        """
        self._start_time = time.monotonic()
        self._services: Dict[str, ServiceHealth] = {}
        self._metrics = PerformanceMetrics()
        # P1 优化：使用 deque(maxlen=1000) 自动淘汰旧记录，避免列表切片重建的 O(n) 开销
        self._response_times: deque[float] = deque(maxlen=1000)
        self._lock = asyncio.Lock()
        # P2-4: data_store 必须由外部注入，探针不再自行构造（避免副作用）
        self._data_store = data_store
        # psutil cpu_percent 预热：首次调用返回 0.0，需先调一次让基线建立
        try:
            import psutil  # type: ignore[import-untyped]
            psutil.cpu_percent(interval=0)
        except ImportError:
            pass
    
    async def get_health_report(self) -> Dict[str, Any]:
        """获取完整健康报告。
        
        Returns:
            包含系统状态、服务健康、性能指标的字典
        """
        system = await self._get_system_metrics()
        services = await self._check_all_services()
        
        # 确定整体状态
        overall_status = "healthy"
        if any(s.status == "down" for s in services.values()):
            overall_status = "unhealthy"
        elif any(s.status == "degraded" for s in services.values()):
            overall_status = "degraded"
        
        return {
            "status": overall_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system": {
                "cpu_percent": system.cpu_percent,
                "memory": {
                    "total_mb": round(system.memory_total_mb, 2),
                    "used_mb": round(system.memory_used_mb, 2),
                    "percent": round(system.memory_percent, 2),
                },
                "disk": {
                    "total_gb": round(system.disk_total_gb, 2),
                    "used_gb": round(system.disk_used_gb, 2),
                    "free_gb": round(system.disk_free_gb, 2),
                    "percent": round(system.disk_percent, 2),
                },
                "python_version": system.python_version,
                "platform": system.platform,
                "pid": system.pid,
                "uptime_seconds": round(system.uptime_seconds, 2),
            },
            "services": {
                name: {
                    "status": s.status,
                    "latency_ms": round(s.latency_ms, 2),
                    "message": s.message,
                    "last_check": s.last_check.isoformat() if s.last_check else None,
                }
                for name, s in services.items()
            },
            "performance": {
                "request_count": self._metrics.request_count,
                "error_count": self._metrics.error_count,
                "error_rate": round(
                    self._metrics.error_count / max(1, self._metrics.request_count) * 100,
                    2,
                ),
                "avg_response_time_ms": round(self._metrics.avg_response_time_ms, 2),
                "p95_response_time_ms": round(self._metrics.p95_response_time_ms, 2),
                "p99_response_time_ms": round(self._metrics.p99_response_time_ms, 2),
                "active_connections": self._metrics.active_connections,
                "cache_hit_rate": round(self._metrics.cache_hit_rate * 100, 2),
            },
        }
    
    async def _get_system_metrics(self) -> SystemMetrics:
        """获取系统指标。"""
        metrics = SystemMetrics()
        metrics.python_version = sys.version.split()[0]
        metrics.platform = platform.platform()
        metrics.pid = os.getpid()
        metrics.uptime_seconds = time.monotonic() - self._start_time
        
        # P3 优化：将 psutil 阻塞调用移到线程池，避免阻塞事件循环
        try:
            import psutil  # type: ignore[import-untyped]
            
            def _collect_psutil() -> None:
                # interval=0 使用上次调用以来的平均值，非阻塞
                metrics.cpu_percent = psutil.cpu_percent(interval=0)
                
                mem = psutil.virtual_memory()
                metrics.memory_total_mb = mem.total / 1024 / 1024
                metrics.memory_used_mb = mem.used / 1024 / 1024
                metrics.memory_percent = mem.percent
                
                # 获取数据目录所在磁盘
                data_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                try:
                    disk = psutil.disk_usage(data_dir)
                except Exception:
                    disk = psutil.disk_usage("/")
                metrics.disk_total_gb = disk.total / 1024 / 1024 / 1024
                metrics.disk_used_gb = disk.used / 1024 / 1024 / 1024
                metrics.disk_free_gb = disk.free / 1024 / 1024 / 1024
                metrics.disk_percent = disk.percent
            
            await asyncio.to_thread(_collect_psutil)
                
        except ImportError:
            logger.debug("psutil not available, using basic metrics")
        except Exception as e:
            logger.warning("Failed to get system metrics: %s", e)
        
        return metrics
    
    async def _check_all_services(self) -> Dict[str, ServiceHealth]:
        """检查所有注册的服务。"""
        # 检查内置服务
        await self._check_data_storage()
        await self._check_ai_service()
        
        return dict(self._services)
    
    async def _check_data_storage(self) -> None:
        """检查数据存储。

        P2-4: 探针无副作用 — 不再自行 ``LocalDataStore()`` 构造。
        data_store 未注入时返回 ``unknown``，绝不触发建库/迁移。
        """
        name = "data_storage"
        start = time.monotonic()

        if self._data_store is None:
            # P2-4: 未注入 data_store 时报告 unknown，不自行构造
            self._services[name] = ServiceHealth(
                name=name,
                status="unknown",
                latency_ms=0,
                message="data_store not injected (probe is side-effect-free)",
                last_check=datetime.now(timezone.utc),
            )
            return

        try:
            await asyncio.to_thread(self._data_store.load_json, "settings", {})

            latency = (time.monotonic() - start) * 1000
            self._services[name] = ServiceHealth(
                name=name,
                status="up",
                latency_ms=latency,
                message="数据存储正常",
                last_check=datetime.now(timezone.utc),
            )
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            self._services[name] = ServiceHealth(
                name=name,
                status="down",
                latency_ms=latency,
                message=f"数据存储异常: {e}",
                last_check=datetime.now(timezone.utc),
            )
    
    async def _check_ai_service(self) -> None:
        """检查 AI 服务。

        P2-4: 探针无副作用 — 不再自行 ``LocalDataStore()`` 构造。
        data_store 未注入时返回 ``unknown``，绝不触发建库/迁移。
        """
        name = "ai_service"
        start = time.monotonic()

        if self._data_store is None:
            # P2-4: 未注入 data_store 时报告 unknown，不自行构造
            self._services[name] = ServiceHealth(
                name=name,
                status="unknown",
                latency_ms=0,
                message="data_store not injected (probe is side-effect-free)",
                last_check=datetime.now(timezone.utc),
            )
            return

        try:
            settings = await asyncio.to_thread(self._data_store.load_json, "settings", {})

            # P1.8: 通过 SecretResolver 统一读取 LLM key（含环境变量优先级 + 解密）
            # 同时检查 provider_configs，避免漏报已通过 provider 配置 api_key 的场景
            from core.secret_resolver import LLM_FLAT_KEY_ENV_MAP, get_default_resolver
            api_key = ""
            # 1) 先从 provider_configs 检查（首选配置源）
            providers = settings.get("provider_configs", []) if isinstance(settings, dict) else []
            for p in providers:
                if isinstance(p, dict) and p.get("enabled") and not p.get("is_local") and p.get("api_key"):
                    api_key = p.get("api_key", "")
                    break
            # 2) 再走扁平键 + 环境变量
            if not api_key:
                resolver = get_default_resolver()
                # 手动遍历环境变量映射（settings 是 dict 而非 repository，无法直接用 resolver.resolve）
                for env_name in LLM_FLAT_KEY_ENV_MAP.get("llm_api_key", []):
                    if os.environ.get(env_name):
                        api_key = os.environ[env_name]
                        break
                if not api_key:
                    api_key = settings.get("llm_api_key", "") if isinstance(settings, dict) else ""
            if not api_key:
                self._services[name] = ServiceHealth(
                    name=name,
                    status="degraded",
                    latency_ms=0,
                    message="AI API key 未配置",
                    last_check=datetime.now(timezone.utc),
                )
                return
            
            # 简单检查 API 可用性（不实际调用）
            latency = (time.monotonic() - start) * 1000
            self._services[name] = ServiceHealth(
                name=name,
                status="up",
                latency_ms=latency,
                message="AI 服务配置正常",
                last_check=datetime.now(timezone.utc),
            )
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            self._services[name] = ServiceHealth(
                name=name,
                status="degraded",
                latency_ms=latency,
                message=f"AI 服务检查异常: {e}",
                last_check=datetime.now(timezone.utc),
            )
    
    def record_request(self, response_time_ms: float, is_error: bool = False) -> None:
        """记录请求指标。
        
        Args:
            response_time_ms: 响应时间（毫秒）
            is_error: 是否为错误请求
        """
        self._metrics.request_count += 1
        if is_error:
            self._metrics.error_count += 1
        
        # deque(maxlen=1000) 自动淘汰旧记录，无需手动切片
        self._response_times.append(response_time_ms)
        
        # 更新平均响应时间
        if self._response_times:
            self._metrics.avg_response_time_ms = sum(self._response_times) / len(self._response_times)
            
            # 更新百分位响应时间
            sorted_times = sorted(self._response_times)
            n = len(sorted_times)
            self._metrics.p95_response_time_ms = sorted_times[int(n * 0.95)]
            self._metrics.p99_response_time_ms = sorted_times[int(n * 0.99)]
    
    def set_active_connections(self, count: int) -> None:
        """设置活跃连接数。"""
        self._metrics.active_connections = count
    
    def set_cache_hit_rate(self, rate: float) -> None:
        """设置缓存命中率。"""
        self._metrics.cache_hit_rate = max(0.0, min(1.0, rate))
    
    def register_service(self, name: str) -> None:
        """注册服务。
        
        Args:
            name: 服务名称
        """
        if name not in self._services:
            self._services[name] = ServiceHealth(
                name=name,
                status="unknown",
                message="服务已注册，待检查",
            )
    
    def update_service_status(
        self,
        name: str,
        status: str,
        message: str = "",
        latency_ms: float = 0.0,
    ) -> None:
        """更新服务状态。
        
        Args:
            name: 服务名称
            status: 状态 ("up", "down", "degraded")
            message: 状态消息
            latency_ms: 延迟（毫秒）
        """
        self._services[name] = ServiceHealth(
            name=name,
            status=status,
            latency_ms=latency_ms,
            message=message,
            last_check=datetime.now(timezone.utc),
        )
    
    def get_quick_status(self) -> Dict[str, str]:
        """获取快速状态摘要。
        
        Returns:
            包含各服务状态的字典
        """
        return {
            name: s.status
            for name, s in self._services.items()
        }

    # B5 修复：提供公开 API 替代直接访问私有属性 _metrics / _get_system_metrics

    def get_performance_metrics(self) -> PerformanceMetrics:
        """获取性能指标快照（公开 API）。

        用于替代外部直接访问 `self._metrics` 私有属性。
        """
        return self._metrics

    async def get_system_metrics(self) -> SystemMetrics:
        """获取系统指标（公开 API）。

        用于替代外部直接访问 `self._get_system_metrics()` 私有方法。
        """
        return await self._get_system_metrics()


# 全局健康检查器实例
_health_checker: Optional[HealthChecker] = None


def get_health_checker(data_store: Any = None, *, reset: bool = False) -> HealthChecker:
    """获取全局健康检查器实例。

    Args:
        data_store: 已构造好的 LocalDataStore 实例。首次创建时注入；
            后续调用传入的 data_store 会被忽略（除非 ``reset=True``）。
        reset: 强制重建单例（用于测试或 data_store 切换）。
    """
    global _health_checker
    if _health_checker is None or reset:
        _health_checker = HealthChecker(data_store=data_store)
    return _health_checker


async def get_health_report() -> Dict[str, Any]:
    """获取健康报告的便捷函数。"""
    return await get_health_checker().get_health_report()


# ════════════════════════════════════════════════════════════════════
# P2-4: 启动门禁
# ════════════════════════════════════════════════════════════════════


class StartupGateError(RuntimeError):
    """启动门禁失败异常。

    属性：
        failures: 阻塞启动的子系统失败列表，每项为 ``(subsystem_name, message)``
        report:   完整健康报告 dict（供日志/前端展示）
    """

    def __init__(self, failures: list[tuple[str, str]], report: dict[str, Any] | None = None) -> None:
        self.failures = failures
        self.report = report or {}
        # 构造可操作错误信息
        lines = [f"  - {name}: {msg}" for name, msg in failures]
        super().__init__(
            "startup gate failed — critical subsystem(s) unavailable:\n" + "\n".join(lines)
        )


# 启动门禁视为"必须可用"的子系统。注意：ai_service 不在此列——
# 思忆集可以在没有 AI key 的情况下启动（本地 Ollama 兜底 + 笔记/任务/词库
# 等非 AI 功能仍可用），只在 data_storage 完全不可用时阻塞。
CRITICAL_SUBSYSTEMS_FOR_STARTUP: tuple[str, ...] = ("data_storage",)


async def run_startup_gate(
    data_store: Any = None,
    *,
    critical_subsystems: tuple[str, ...] = CRITICAL_SUBSYSTEMS_FOR_STARTUP,
    allow_unknown: bool = True,
) -> dict[str, Any]:
    """P2-4 启动门禁：检查关键子系统，失败时抛 :class:`StartupGateError`。

    设计要点：
    1. **无副作用**：使用临时 ``HealthChecker(data_store=...)`` 实例，
       不污染全局单例，也不触发建库/迁移（data_store 未注入时探针返回 unknown）。
    2. **critical_subsystems**：必须为 ``up`` 才能通过。``unknown`` 在
       ``allow_unknown=True`` 时视为通过（适用于首次启动尚未注入 data_store
       的场景）；``allow_unknown=False`` 时 unknown 也视为失败。
    3. **非关键子系统**（如 ai_service）的 ``down``/``degraded`` 不会阻塞
       启动，但会记录到 report 里供前端展示。

    Args:
        data_store: 已构造好的 LocalDataStore 实例（必须由调用方在外部
            完成首启迁移后再传入）。
        critical_subsystems: 必须可用的子系统名元组。
        allow_unknown: unknown 状态是否视为通过（默认 True）。

    Returns:
        完整健康报告 dict。

    Raises:
        StartupGateError: 任一关键子系统不可用时抛出。
    """
    # 用临时 checker，不复用全局单例（避免缓存脏数据）
    checker = HealthChecker(data_store=data_store)
    report = await checker.get_health_report()

    services = report.get("services", {})
    failures: list[tuple[str, str]] = []
    for name in critical_subsystems:
        svc = services.get(name)
        if svc is None:
            # 关键子系统未被探针覆盖（理论不应发生）
            failures.append((name, "subsystem not probed"))
            continue
        status = svc.get("status", "unknown")
        if status == "up":
            continue
        if status == "unknown" and allow_unknown:
            continue
        failures.append((name, svc.get("message", f"status={status}")))

    if failures:
        raise StartupGateError(failures=failures, report=report)

    return report