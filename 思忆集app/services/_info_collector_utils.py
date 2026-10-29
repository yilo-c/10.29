"""Info Collector Utils — 从 info_collector_service.py 抽离的模块级纯函数与缓存状态。

包含：源健康度追踪、持久化缓存、XML/HTML 解析助手、标题相似度、
进度回调。不依赖 InfoCollectorService 类，可独立测试。
所有公开符号由 services/info_collector_service.py 透明 re-export。
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from difflib import SequenceMatcher
import xml.etree.ElementTree as ET
from typing import Any

import aiohttp

logger = logging.getLogger("siyi.services.info_collector")


# ── 超时常量（原在 info_collector_service.py 顶层，移此避免 fetchers mixin 循环导入）──
ITEM_TIMEOUT = aiohttp.ClientTimeout(total=10)
SHORT_TIMEOUT = aiohttp.ClientTimeout(total=6)
TOTAL_TIMEOUT = 90  # 45→90：给慢源更多时间


# 源健康度追踪: name → [bool, bool, ...] (最近 5 次)
_source_health: dict[str, list[bool]] = {}
_SOURCE_HEALTH_WINDOW = 5

_cache: dict[str, Any] = {"items": [], "brief": "", "cached_at": 0.0}
_CACHE_MAX_ITEMS = 150


def _resolve_persist_path() -> str:
    """持久化缓存路径：优先 SIYI_DATA_DIR（隔离实例），缺省回落源码树 data/。

    R2-C1：原先模块导入期即把路径固化为源码树相对路径，隔离实例（临时
    SIYI_DATA_DIR）的采集缓存会写穿生产数据。改为调用期解析，env 未设时
    路径与旧实现完全一致。
    """
    data_dir = os.environ.get("SIYI_DATA_DIR", "").strip()
    if data_dir:
        return os.path.join(data_dir, "info_cache.json")
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
        "info_cache.json",
    )


def _save_persisted_cache() -> None:
    """原子写入：先写 .tmp，成功后再 os.replace。"""
    persist_path = _resolve_persist_path()
    try:
        os.makedirs(os.path.dirname(persist_path), exist_ok=True)
        tmp_path = persist_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False)
        os.replace(tmp_path, persist_path)
    except Exception as exc:
        logger.debug("save persisted info cache failed: %s", exc)


def _record_source_health(name: str, success: bool) -> None:
    """记录源健康度（最近 _SOURCE_HEALTH_WINDOW 次）。"""
    if name not in _source_health:
        _source_health[name] = []
    _source_health[name].append(success)
    if len(_source_health[name]) > _SOURCE_HEALTH_WINDOW:
        _source_health[name] = _source_health[name][-_SOURCE_HEALTH_WINDOW:]


def _source_success_rate(name: str) -> float:
    """源的近期成功率（0.0-1.0）。"""
    history = _source_health.get(name, [])
    if not history:
        return 1.0  # 新源默认为健康
    return sum(1 for ok in history if ok) / len(history)


def _load_persisted_cache() -> None:
    global _cache
    persist_path = _resolve_persist_path()
    try:
        if os.path.exists(persist_path):
            with open(persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("items"):
                _cache = data
                logger.info(
                    "loaded persisted info cache: %d items, brief=%d chars",
                    len(_cache.get("items", [])),
                    len(_cache.get("brief", "")),
                )
    except Exception as exc:
        logger.debug("load persisted info cache failed: %s", exc)


_load_persisted_cache()


_ATOM_NS = "http://www.w3.org/2005/Atom"


def _e(element: ET.Element | None, path: str) -> str:
    if element is None:
        return ""
    found = element.find(path)
    if found is None:
        # Atom 源（如 simonwillison）的所有标签都位于 atom 命名空间，
        # 无前缀 find 匹配不到，需显式按命名空间 URI 查找。
        found = element.find(f"{{{_ATOM_NS}}}{path}")
    if found is None:
        if path == "link":
            link_el = element.find("link") or element.find(f"{{{_ATOM_NS}}}link")
            if link_el is not None:
                return link_el.get("href", "") or link_el.text or ""
        return ""
    return found.text or ""


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html.unescape(text)
    return text.strip()


def _title_similarity(a: str, b: str) -> float:
    a_clean = re.sub(r"[^\w\u4e00-\u9fff]", "", (a or "").lower())
    b_clean = re.sub(r"[^\w\u4e00-\u9fff]", "", (b or "").lower())
    if not a_clean or not b_clean:
        return 0.0
    shorter = min(len(a_clean), len(b_clean))
    if shorter < 8:
        return 0.0
    return SequenceMatcher(None, a_clean, b_clean).ratio()


# T3.6：公共别名，消除能力路由对私有方法的依赖
# （原调用方直接 import 了模块内的私有实现，现统一走公共别名）。
def title_similarity(a: str, b: str) -> float:
    """公开别名：标题相似度（实现见 _title_similarity）。"""
    return _title_similarity(a, b)


async def _maybe_call(callback: Any, event_type: str, payload: dict[str, Any]) -> None:
    """调用进度回调（同步或异步），并吞掉异常避免中断主流程。"""
    if callback is None:
        return
    try:
        result = callback(event_type, payload)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.debug("info progress callback failed", exc_info=True)
