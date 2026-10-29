"""T3.6 能力路由遥测单元测试。

覆盖 plugins.capability_router.CapabilityRouter 的 plugin/fallback 命中计数，
以及公共别名 dedup_items / title_similarity 的行为一致性。
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from plugins.capability_router import CapabilityRouter  # noqa: E402


def _make_router() -> CapabilityRouter:
    router = CapabilityRouter()
    router.reset_capability_stats()
    return router


def test_plugin_hit_counted():
    """plugin invoker 成功时应累计 plugin_hits。"""
    router = _make_router()
    router.register_plugin_invoker("info.demo", lambda params: {"ok": True})

    assert router.invoke("info.demo", {}) == {"ok": True}

    stats = router.get_capability_stats()
    assert stats["info.demo"]["plugin_hits"] == 1
    assert stats["info.demo"].get("fallback_hits", 0) == 0
    assert stats["info.demo"].get("plugin_errors", 0) == 0


def test_plugin_error_falls_back_and_counts():
    """plugin 抛错时应回退到 fallback，并分别累计 plugin_errors 与 fallback_hits。"""
    router = _make_router()

    def _boom(params):
        raise RuntimeError("plugin broke")

    router.register_plugin_invoker("info.demo", _boom)
    router.register_fallback("info.demo", lambda **params: {"fallback": True})

    assert router.invoke("info.demo", {}) == {"fallback": True}

    stats = router.get_capability_stats()
    assert stats["info.demo"]["plugin_errors"] == 1
    assert stats["info.demo"]["fallback_hits"] == 1
    assert stats["info.demo"].get("plugin_hits", 0) == 0


def test_fallback_only_counted():
    """无 plugin、仅有 fallback 时应只累计 fallback_hits。"""
    router = _make_router()
    router.register_fallback("info.demo", lambda **params: {"fallback": True})

    assert router.invoke("info.demo", {}) == {"fallback": True}

    stats = router.get_capability_stats()
    assert stats["info.demo"]["fallback_hits"] == 1
    assert stats["info.demo"].get("plugin_hits", 0) == 0


def test_no_plugin_no_fallback_raises():
    """既无 plugin 又无 fallback 时应抛 RuntimeError（不计数）。"""
    router = _make_router()
    try:
        router.invoke("info.unknown", {})
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")
    assert "info.unknown" not in router.get_capability_stats()


def test_reset_clears_stats():
    """reset_capability_stats 应清空计数。"""
    router = _make_router()
    router.register_plugin_invoker("info.demo", lambda params: {"ok": True})
    router.invoke("info.demo", {})
    assert router.get_capability_stats()

    router.reset_capability_stats()
    assert router.get_capability_stats() == {}


def test_public_aliases_match_private_behavior():
    """公共别名 dedup_items / title_similarity 应与原私有实现行为一致。"""
    from services.info_collector_service import dedup_items, title_similarity

    items = [
        {"url": "https://a.com/x", "title": "同一标题示例文章"},
        {"url": "https://a.com/x", "title": "同一标题示例文章副本"},
        {"url": "https://b.com/y", "title": "完全不同主题的内容"},
    ]
    deduped = dedup_items(items)
    # 同 url 去重 → 2 条
    assert len(deduped) == 2

    # 高度相似标题（长度 >= 8，超过 _title_similarity 的短串阈值）→ 相似度 > 0
    assert title_similarity("今天下午的天气真的很好啊", "今天下午的天气真的还不错啊") > 0.0
    # 极短字符串（< 8）→ 0
    assert title_similarity("ab", "cd") == 0.0
