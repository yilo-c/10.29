from __future__ import annotations

import logging
import threading
from typing import Any, TypeVar

logger = logging.getLogger("siyi.app_runtime.service_registry")

__all__ = [
    "ServiceRegistry",
    "ServiceNotFoundError",
    "AmbiguousServiceError",
    "service_registry",
]

T = TypeVar("T")


class ServiceNotFoundError(LookupError):
    """Raised when a requested service is not registered."""

    def __init__(self, service_type: type, name: str | None = None) -> None:
        if name:
            msg = f"Service not found: type={service_type.__name__!r}, name={name!r}"
        else:
            msg = f"Service not found: type={service_type.__name__!r}"
        super().__init__(msg)
        self.service_type = service_type
        self.service_name = name


class AmbiguousServiceError(LookupError):
    """Raised when resolve() matches multiple services and no disambiguating name was given."""

    def __init__(self, service_type: type, count: int) -> None:
        msg = (
            f"Multiple services ({count}) match type={service_type.__name__!r}. "
            f"Use resolve_all() or provide a name to disambiguate."
        )
        super().__init__(msg)
        self.service_type = service_type
        self.match_count = count


class ServiceRegistry:
    """Thread-safe registry for service instances keyed by ``(type, optional name)``.

    Supports type-hierarchy resolution: when a subtype is registered and the
    parent type is requested, the subtype instance is returned.

    Typical usage::

        registry = ServiceRegistry()
        registry.register(Database, PostgresDatabase())
        db = registry.resolve(Database)          # → PostgresDatabase instance
        all_db = registry.resolve_all(Database)   # → [PostgresDatabase, ...]
    """

    def __init__(self) -> None:
        self._services: dict[tuple[type, str | None], Any] = {}
        self._lock = threading.Lock()
        self._listeners: dict[tuple[type, str | None], list[Any]] = {}

    # ── public API ─────────────────────────────────────────────

    def register(self, service_type: type[T], instance: T, name: str | None = None) -> None:
        """Register *instance* under *service_type* with an optional *name*.

        Raises ``TypeError`` when *instance* is not an instance of *service_type*.
        Re-registering the same ``(type, name)`` pair silently overwrites the
        previous entry.
        """
        if not isinstance(instance, service_type):
            raise TypeError(
                f"Cannot register instance of type {type(instance).__name__!r} "
                f"as {service_type.__name__!r}"
            )
        with self._lock:
            self._services[(service_type, name)] = instance
        self._notify_change(service_type, name, "registered", instance)

    def resolve(self, service_type: type[T], name: str | None = None) -> T:
        """Return the single service matching *service_type* and optional *name*.

        Resolution order:
        1. Exact ``(service_type, name)`` key match.
        2. Walk all entries and find those whose registered type is the same
           as or a subclass of *service_type* (and whose name matches, if
           *name* is given).

        Raises ``ServiceNotFoundError`` when no match is found.
        Raises ``AmbiguousServiceError`` when *name* is ``None`` and multiple
        candidates exist (use ``resolve_all()`` or supply a *name*).
        """
        with self._lock:
            # 1. Exact key match
            key = (service_type, name)
            if key in self._services:
                return self._services[key]

            # 2. Walk by type hierarchy
            matches: list[T] = []
            for (reg_type, reg_name), instance in self._services.items():
                if name is not None and reg_name != name:
                    continue
                if reg_type is service_type or issubclass(reg_type, service_type):
                    matches.append(instance)

            if not matches:
                raise ServiceNotFoundError(service_type, name)
            if len(matches) > 1:
                raise AmbiguousServiceError(service_type, len(matches))

            return matches[0]

    def resolve_all(self, service_type: type[T]) -> list[T]:
        """Return **all** services whose registered type matches or is a
        subclass of *service_type*."""
        with self._lock:
            return [
                instance
                for (reg_type, _), instance in self._services.items()
                if reg_type is service_type or issubclass(reg_type, service_type)
            ]

    def has(self, service_type: type, name: str | None = None) -> bool:
        """Return ``True`` if at least one service matches *service_type*
        (and *name*, if given)."""
        with self._lock:
            if (service_type, name) in self._services:
                return True
            for reg_type, reg_name in self._services:
                if name is not None and reg_name != name:
                    continue
                if reg_type is service_type or issubclass(reg_type, service_type):
                    return True
            return False

    def unregister(self, service_type: type, name: str | None = None) -> None:
        """Remove the service at the exact key ``(service_type, name)``.

        Raises ``ServiceNotFoundError`` when the key does not exist.
        (Subtype lookups are **not** performed here; use the exact type
        under which the service was registered.)
        """
        with self._lock:
            key = (service_type, name)
            if key not in self._services:
                raise ServiceNotFoundError(service_type, name)
            del self._services[key]
        self._notify_change(service_type, name, "unregistered", None)

    def clear(self) -> None:
        """Remove every registered service."""
        with self._lock:
            self._services.clear()

    # ── 依赖等待 / 变更通知（DSH inject 语义）────────────────────────

    def on_register(
        self,
        service_type: type,
        callback: Any,
        name: str | None = None,
    ) -> None:
        """注册变更监听：匹配服务 register/unregister 时调用回调。

        回调签名: ``callback(registered_type, instance, name, action)``，
        其中 action ∈ {"registered", "unregistered"}。注册同名同类型服务会
        覆盖并再次触发 "registered"。回调在锁外执行，避免死锁。
        """
        key = (service_type, name)
        with self._lock:
            self._listeners.setdefault(key, []).append(callback)

    def on_available(
        self,
        service_type: type,
        callback: Any,
        name: str | None = None,
    ) -> None:
        """依赖等待：服务可用时立即回调一次（一次性）。

        若服务当前已存在则同步回调；否则注册一次性监听，首次
        "registered" 时回调并自动移除。回调签名: ``callback(instance)``。
        """
        try:
            existing: Any = self.resolve(service_type, name)

        except (ServiceNotFoundError, AmbiguousServiceError):
            existing = None
        if existing is not None:
            callback(existing)
            return

        def _once(reg_type: type, instance: Any, reg_name: str | None, action: str) -> None:
            if action != "registered":
                return
            if name is not None and reg_name != name:
                return
            if not (reg_type is service_type or issubclass(reg_type, service_type)):
                return
            with self._lock:
                callbacks = self._listeners.get((service_type, name))
                if callbacks and _once in callbacks:
                    callbacks.remove(_once)
            callback(instance)

        self.on_register(service_type, _once, name)

    async def await_available(
        self,
        service_type: type,
        name: str | None = None,
        timeout: float = 30.0,
    ) -> Any:
        """异步等待服务可用（inject 语义）。

        Returns:
            服务可用时返回该服务实例；超时抛 ``ServiceNotFoundError``。
        """
        import asyncio

        try:
            existing: Any = self.resolve(service_type, name)

        except (ServiceNotFoundError, AmbiguousServiceError):
            existing = None
        if existing is not None:
            return existing

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()

        def _on_available(instance: Any) -> None:
            if not future.done():
                future.set_result(instance)

        self.on_available(service_type, _on_available, name)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            raise ServiceNotFoundError(service_type, name) from None

    def _notify_change(
        self,
        service_type: type,
        name: str | None,
        action: str,
        instance: Any,
    ) -> None:
        """在锁外分发匹配的监听回调。"""
        with self._lock:
            targets: list[tuple[Any, tuple[type, str | None]]] = []
            for (listen_type, listen_name), callbacks in list(self._listeners.items()):
                if listen_name is not None and listen_name != name:
                    continue
                if listen_type is service_type or issubclass(service_type, listen_type):
                    for cb in callbacks:
                        targets.append((cb, (listen_type, listen_name)))
        for cb, key in targets:
            try:
                cb(service_type, instance, name, action)
            except Exception:
                logger.exception(
                    "service change listener failed for %s/%s",
                    getattr(service_type, "__name__", service_type),
                    name,
                )

    def registered_types(self) -> list[type]:
        """Return a deduplicated list of registered types (insertion order
        is preserved but not guaranteed)."""
        with self._lock:
            seen: set[int] = set()
            result: list[type] = []
            for t, _ in self._services:
                tid = id(t)
                if tid not in seen:
                    seen.add(tid)
                    result.append(t)
            return result


# 全局单例 — 桌面端通过 AppContext 注册服务，其他模块通过 resolve 获取
service_registry = ServiceRegistry()
