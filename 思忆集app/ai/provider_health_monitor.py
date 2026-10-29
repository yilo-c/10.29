"""Provider 健康监控 — 自动降级与恢复 (B2)。

在后台周期性调用 ``ProviderConfig.test_connectivity()`` 探测各 Provider，
连续失败超过阈值后标记为 down（自动降级），并在恢复探测成功后重新标记为 up。

特性：
- 周期性检查：``check_interval`` 检查未降级 provider；``recovery_check_interval``
  单独的更慢节奏重试已降级 provider。
- 事件：``provider.degraded`` / ``provider.recovered`` 通过 ``core.events.event_bus``
  广播，EventBridge 会转发到所有 WebSocket 客户端。
- 持久化：状态写入 settings 键 ``provider_health_state``，重启后恢复。
- 与 FallbackOpenAIClient 集成：``is_available(name)`` 用于在调用前跳过已降级 provider。

注意：本模块是**主动**监控 + 降级管理（周期探测、状态持久化、事件广播），
不是被动的健康状态缓存查询；仓库中不存在第二种 provider 健康实现。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import core.events
from core.events import event_bus

logger = logging.getLogger("siyi.ai.provider_health_monitor")

DEFAULT_CHECK_TIMEOUT = 10


@dataclass
class ProviderHealthState:
    """单个 Provider 的健康状态记录。"""
    name: str = ""
    consecutive_failures: int = 0
    last_check_at: float = 0.0
    last_check_wall_at: float = 0.0
    is_marked_down: bool = False
    last_error: str = ""
    last_check_ok: bool = True

    def to_dict(self) -> "dict[str, Any]":
        return {
            "name": self.name,
            "consecutive_failures": self.consecutive_failures,
            "last_check_at": self.last_check_at,
            "is_marked_down": self.is_marked_down,
            "last_error": self.last_error,
            "last_check_ok": self.last_check_ok,
        }

    @classmethod
    def from_dict(cls, d: "dict[str, Any]") -> "ProviderHealthState":
        return cls(
            name=str(d.get("name", "")),
            consecutive_failures=int(d.get("consecutive_failures", 0)),
            last_check_at=float(d.get("last_check_at", 0.0)),
            is_marked_down=bool(d.get("is_marked_down", False)),
            last_error=str(d.get("last_error", "")),
            last_check_ok=bool(d.get("last_check_ok", True)),
        )


class ProviderHealthMonitor:
    """Provider 健康监控器。

    在后台周期检查各 Provider 连接性，连续失败超过阈值自动降级，
    恢复后自动标记可用，并持久化状态供重启恢复。
    """

    def __init__(
        self,
        providers: "list[Any] | None" = None,
        check_interval: int = 300,
        failure_threshold: int = 3,
        recovery_check_interval: int = 600,
        settings_repository: "Any | None" = None,
        event_bus: "Any | None" = None,
        check_timeout: float = DEFAULT_CHECK_TIMEOUT,
    ):
        self._providers: "list[Any]" = list(providers or [])
        self.check_interval = max(5, int(check_interval))
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_check_interval = max(5, int(recovery_check_interval))
        self.check_timeout = float(check_timeout)
        self._settings_repository = settings_repository
        self._event_bus = event_bus or core.events.event_bus

        self._states: "dict[str, ProviderHealthState]" = {}
        self._task: "asyncio.Task | None" = None
        self._stop_event: "asyncio.Event | None" = None
        self._lock = asyncio.Lock()
        self._states_lock = threading.Lock()

        self._load_persisted_state()

    @property
    def providers(self) -> "list[Any]":
        """当前监控的 provider 列表。"""
        return list(self._providers)

    def set_providers(self, providers: "list[Any]") -> None:
        """替换监控的 provider 集合，保留已有状态。"""
        new_names = {getattr(p, "name", "") for p in providers}
        with self._states_lock:
            for name in list(self._states.keys()):
                if name not in new_names:
                    del self._states[name]
            for p in providers:
                name = getattr(p, "name", "")
                if name and name not in self._states:
                    self._states[name] = ProviderHealthState(name=name)
        self._providers = list(providers)

    def is_available(self, name: str) -> bool:
        """判断 provider 是否可用（未降级）。未知 provider 视为可用。"""
        state = self._states.get(name)
        if state is None:
            return True
        return not state.is_marked_down

    def get_status(self) -> "list[dict[str, Any]]":
        """返回所有已跟踪 provider 的状态快照列表。"""
        with self._states_lock:
            return [s.to_dict() for s in self._states.values()]

    def get_provider_status(self, name: str) -> "dict[str, Any] | None":
        """返回单个 provider 的状态，未跟踪返回 None。"""
        with self._states_lock:
            s = self._states.get(name)
            if s is None:
                return None
            return s.to_dict()

    async def check_provider(self, provider: "Any") -> "dict[str, Any]":
        """检查单个 provider 并更新状态。返回该 provider 的状态快照。

        provider 参数可以是 ProviderConfig 或同形状对象，必须实现
        ``async test_connectivity(timeout) -> (bool, str)``。
        """
        name = getattr(provider, "name", "") or ""
        if not name:
            return {"name": "", "error": "provider missing name", "is_marked_down": False}

        ok: bool = False
        message: str = ""
        try:
            ok, message = await provider.test_connectivity(timeout=self.check_timeout)
        except TimeoutError:
            ok, message = False, "健康检查超时"
        except Exception as exc:  # noqa: BLE001
            ok, message = False, f"检查异常: {exc}"

        if not ok:
            # 失败必须可见：状态里记 last_error 之外再落一条 warning，
            # 否则后台循环/面板看起来"一直正常"。
            logger.warning("provider %r 健康检查失败: %s", name, message or "unknown error")

        return await self._apply_check_result(name, ok, message)

    async def check_all(self) -> "list[dict[str, Any]]":
        """并发检查所有 enabled provider。返回各 provider 状态快照列表。"""
        enabled = [p for p in self._providers if getattr(p, "enabled", True)]
        if not enabled:
            return []
        results = await asyncio.gather(
            *[self.check_provider(p) for p in enabled],
            return_exceptions=False,
        )
        return list(results)

    async def _apply_check_result(self, name: str, ok: bool, message: str) -> "dict[str, Any]":
        async with self._lock:
            state = self._states.get(name)
            if state is None:
                state = ProviderHealthState(name=name)
                self._states[name] = state

            now_wall = time.time()

            was_down = state.is_marked_down

            state.last_check_at = now_wall
            state.last_check_wall_at = now_wall
            state.last_check_ok = bool(ok)
            state.last_error = "" if ok else (message or "unknown error")

            if ok:
                state.consecutive_failures = 0
            else:
                state.consecutive_failures += 1

            degraded = False
            recovered = False
            if not ok and state.consecutive_failures >= self.failure_threshold and not was_down:
                state.is_marked_down = True
                degraded = True
            elif was_down and ok:
                state.is_marked_down = False
                recovered = True

        if degraded:
            self._emit_event(
                "provider.degraded",
                {
                    "name": name,
                    "consecutive_failures": state.consecutive_failures,
                    "error": message or "",
                },
            )
            self._persist_state()
        elif recovered:
            self._emit_event(
                "provider.recovered",
                {
                    "name": name,
                    "consecutive_failures": state.consecutive_failures,
                },
            )
            self._persist_state()
        elif ok:
            self._persist_state()
        elif not degraded and not recovered:
            self._persist_state()

        return self.get_provider_status(name) or {
            "name": name,
            "error": "state missing",
        }

    async def reset_provider(self, name: str) -> bool:
        """重置 provider 的失败计数并标记为可用。返回是否找到并重置。"""
        async with self._lock:
            state = self._states.get(name)
            if state is None:
                return False

            was_down = state.is_marked_down

            state.consecutive_failures = 0
            state.is_marked_down = False
            state.last_error = ""
            state.last_check_ok = True

        if was_down:
            self._emit_event(
                "provider.recovered",
                {
                    "name": name,
                    "last_check_at": state.last_check_at,
                    "manual_reset": True,
                },
            )
            self._persist_state()
        return True

    def _emit_event(self, event_type: str, payload: "dict[str, Any]") -> None:
        """通过 event_bus 广播事件（异常静默处理）。"""
        try:
            from core.events import AppEvent

            self._event_bus.emit(
                AppEvent(
                    event_type=event_type,
                    source="ai.provider_health_monitor",
                    payload=payload,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("emit provider health event failed: %s", exc)

    def _persist_state(self) -> None:
        """将状态持久化到 settings（异常静默处理）。"""
        try:
            if self._settings_repository is None:
                return
            from core.settings_keys import KEY_PROVIDER_HEALTH_STATE

            data = {name: s.to_dict() for name, s in self._states.items()}
            self._settings_repository.set_setting(KEY_PROVIDER_HEALTH_STATE, data)
        except Exception as exc:  # noqa: BLE001
            logger.debug("persist provider health state failed: %s", exc)

    def _load_persisted_state(self) -> None:
        """从 settings 恢复状态。"""
        try:
            if self._settings_repository is None:
                return
            from core.settings_keys import KEY_PROVIDER_HEALTH_STATE

            data = self._settings_repository.get_setting(KEY_PROVIDER_HEALTH_STATE, {})
            if not isinstance(data, dict):
                return

            for name, raw in data.items():
                if not isinstance(raw, dict):
                    continue
                state = ProviderHealthState.from_dict(raw)
                if not state.name:
                    state.name = str(name)
                state.last_check_at = 0.0
                self._states[state.name] = state

            if self._states:
                logger.info("恢复 %d 个 provider 健康状态", len(self._states))
        except Exception as exc:  # noqa: BLE001
            logger.debug("load provider health state failed: %s", exc)

    async def start(self) -> None:
        """启动后台监控循环。"""
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "provider health monitor started (check=%ds, recovery=%ds, threshold=%d, providers=%d)",
            self.check_interval,
            self.recovery_check_interval,
            self.failure_threshold,
            len(self._providers),
        )

    async def stop(self) -> None:
        """停止后台监控循环。"""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                self._task.cancel()
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.debug("stop provider health monitor failed: %s", exc)
        self._task = None
        logger.info("provider health monitor stopped")

    async def _run_loop(self) -> None:
        """后台监控主循环。"""
        while True:
            tick = max(5, min(self.check_interval, self.recovery_check_interval))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=tick)
                break
            except asyncio.TimeoutError:
                pass
            try:
                await self._tick_once()
            except Exception as exc:  # noqa: BLE001
                logger.warning("provider health tick failed: %s", exc)

    async def _tick_once(self) -> None:
        """执行一次检查：对到期（或已降级需恢复）的 provider 检查。"""
        now = time.time()

        def _check_needed(name: str) -> bool:
            state = self._states.get(name)
            if state is None:
                return True
            if state.is_marked_down:
                return now - state.last_check_at >= self.recovery_check_interval
            return now - state.last_check_at >= self.check_interval

        due_normal = [
            p for p in self._providers
            if getattr(p, "name", "") not in self._states
            or not self._states[getattr(p, "name", "")].is_marked_down
        ]
        due_recovery = [
            p for p in self._providers
            if getattr(p, "name", "") in self._states
            and self._states[getattr(p, "name", "")].is_marked_down
        ]

        due_all = [p for p in self._providers if _check_needed(getattr(p, "name", ""))]
        if not due_all:
            return
        await asyncio.gather(
            *[self.check_provider(p) for p in due_all],
            return_exceptions=False,
        )
