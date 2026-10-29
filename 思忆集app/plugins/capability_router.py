"""能力路由：plugin 优先、原生兜底。

核心能力渐进 plugin 化的统一接入层。业务侧（handler / service）通过本模块调用
「已 plugin 化」的能力，而不是直接 import services.*。

策略：
1. 若对应 capability 的 plugin 已注册且可调用 → 用 plugin 结果；
2. 否则 → 回退到 services.* 的原生实现（保证功能不丢）；
3. plugin 调用抛错 → 记 WARNING 并回退，不向上传播（兜底优先于 plugin 故障）。

注意：本模块不主动 import services.*，兜底函数以惰性回调方式注入，避免循环依赖。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("siyi.capability_router")


class CapabilityRouter:
    """按 capability 名路由到 plugin 或兜底实现。"""

    def __init__(self) -> None:
        # capability -> 兜底回调（惰性）
        self._fallbacks: dict[str, Callable[..., Any]] = {}
        # capability -> plugin 工具调用器（由 plugin 工具桥注册）
        self._plugin_invokers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        # T3.6 能力路由遥测：capability -> {"plugin_hits", "fallback_hits", "plugin_errors"}
        self._hits: dict[str, dict[str, int]] = {}
        self._hits_lock = threading.Lock()

    def register_fallback(self, capability: str, fn: Callable[..., Any]) -> None:
        self._fallbacks[capability] = fn

    def register_plugin_invoker(
        self, capability: str, invoker: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        self._plugin_invokers[capability] = invoker

    def has_plugin(self, capability: str) -> bool:
        return capability in self._plugin_invokers

    def invoke(self, capability: str, params: dict[str, Any]) -> Any:
        """路由调用：plugin 优先，失败/缺失回退。

        T3.6 遥测：分别累计 plugin 命中、plugin 调用失败（转兜底）、兜底命中，
        供 system.health 的 capability_routing 子系统透出。
        """
        invoker = self._plugin_invokers.get(capability)
        if invoker is not None:
            try:
                result = invoker(params)
                self._record_hit(capability, "plugin_hits")
                return result
            except Exception as exc:  # plugin 故障不阻断业务
                logger.warning(
                    "plugin invoker for %s failed, fallback: %s", capability, exc
                )
                self._record_hit(capability, "plugin_errors")
        fallback = self._fallbacks.get(capability)
        if fallback is None:
            raise RuntimeError(f"no plugin and no fallback for capability: {capability}")
        self._record_hit(capability, "fallback_hits")
        return fallback(**params)

    def _record_hit(self, capability: str, kind: str) -> None:
        with self._hits_lock:
            bucket = self._hits.setdefault(capability, {})
            bucket[kind] = bucket.get(kind, 0) + 1

    def get_capability_stats(self) -> dict[str, Any]:
        """T3.6：返回能力路由命中统计（线程安全快照）。

        结构：{capability: {plugin_hits, plugin_errors, fallback_hits}}。
        """
        with self._hits_lock:
            return {
                cap: dict(bucket) for cap, bucket in self._hits.items()
            }

    def reset_capability_stats(self) -> None:
        """T3.6：重置遥测计数（测试用）。"""
        with self._hits_lock:
            self._hits.clear()


# 模块级单例
_default_router = CapabilityRouter()


def get_capability_router() -> CapabilityRouter:
    return _default_router


def classify_item(item: dict[str, Any]) -> tuple[str, int]:
    """信息分类：plugin 优先，回退 services.info_collector_service.categorize_item。"""
    router = get_capability_router()
    if router.has_plugin("info.classify"):
        try:
            result = router.invoke("info.classify", {"action": "classify_item", "item": item})
            if isinstance(result, dict) and "category" in result:
                return result["category"], int(result.get("score", 0))
        except Exception:  # noqa: BLE001 - 路由内部已记 WARNING
            pass
    # 兜底（M1 后 categorize_item 迁至 info_categorizer）
    from services.info_categorizer import categorize_item

    return categorize_item(item)


def has_cjk(text: str) -> bool:
    router = get_capability_router()
    if router.has_plugin("info.has_cjk"):
        try:
            result = router.invoke("info.has_cjk", {"action": "has_cjk", "text": text})
            if isinstance(result, dict) and "has_cjk" in result:
                return bool(result["has_cjk"])
        except Exception:  # noqa: BLE001
            pass
    from services.info_constants import has_cjk as _has_cjk

    return _has_cjk(text)


def section_keywords() -> dict[str, list[str]]:
    router = get_capability_router()
    if router.has_plugin("info.section_keywords"):
        try:
            result = router.invoke("info.section_keywords", {"action": "section_keywords"})
            if isinstance(result, dict) and "keywords" in result:
                return result["keywords"]
        except Exception:  # noqa: BLE001
            pass
    from services.info_collector_service import get_section_keywords

    return get_section_keywords()


def dedup_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """信息去重：plugin 优先，回退 services.info_collector_service._dedup_items。"""
    router = get_capability_router()
    if router.has_plugin("info.dedup"):
        try:
            result = router.invoke("info.dedup", {"action": "dedup_items", "items": items})
            if isinstance(result, dict) and "items" in result:
                return result["items"]
        except Exception:  # noqa: BLE001
            pass
    from services.info_collector_service import dedup_items

    return dedup_items(list(items))


def title_similarity(a: str, b: str) -> float:
    """标题相似度：plugin 优先，回退 services.info_collector_service.title_similarity。"""
    router = get_capability_router()
    if router.has_plugin("info.title_similarity"):
        try:
            result = router.invoke(
                "info.title_similarity", {"action": "title_similarity", "a": a, "b": b}
            )
            if isinstance(result, dict) and "similarity" in result:
                return float(result["similarity"])
        except Exception:  # noqa: BLE001
            pass
    from services.info_collector_service import title_similarity

    return title_similarity(a, b)
