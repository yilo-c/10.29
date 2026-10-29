"""行为级测试：services.info_collector_service 的纯函数（去重 / 标题相似度 / HTML 清理）。

不依赖网络或数据库。覆盖 dedup_items 的 URL 归一、标题相似度去重、无 url 时回退 id，
以及 title_similarity / _strip_html 的边界，为后续拆分大文件提供安全网。
"""
from __future__ import annotations

import pytest

from services.info_collector_service import (
    _strip_html,
    dedup_items,
    title_similarity,
)

pytestmark = pytest.mark.unit


def _item(id_: str, title: str, url: str | None = None) -> dict:
    return {"id": id_, "title": title, "url": url}


# ── 去重 ────────────────────────────────────────────────────────

def test_dedup_removes_duplicate_url():
    items = [
        _item("1", "Same story", "https://example.com/a"),
        _item("2", "Totally different wording here", "https://example.com/a"),
    ]
    out = dedup_items(items)
    assert len(out) == 1
    assert out[0]["id"] == "1"


def test_dedup_normalizes_trailing_slash_and_query():
    items = [
        _item("1", "News headline", "https://example.com/a/"),
        _item("2", "News headline", "https://example.com/a?utm_source=x"),
    ]
    assert len(dedup_items(items)) == 1


def test_dedup_by_title_similarity():
    """不同 url 但标题高度相似（>0.72）→ 判为重。"""
    items = [
        _item("1", "Apple releases new iPhone 15 in autumn", "https://a.com/1"),
        _item("2", "Apple releases new iPhone 15 this autumn", "https://b.com/2"),
    ]
    assert len(dedup_items(items)) == 1


def test_dedup_keeps_distinct():
    items = [
        _item("1", "Quantum computing breakthrough announced today", "https://a.com/1"),
        _item("2", "Best pasta recipes for a summer dinner party", "https://b.com/2"),
    ]
    assert len(dedup_items(items)) == 2


def test_dedup_empty():
    assert dedup_items([]) == []


def test_dedup_falls_back_to_id_when_no_url():
    """无 url 时按 id 去重；标题不同以隔离 url 路径。"""
    items = [
        _item("x", "Title one alpha"),
        _item("x", "Title one beta"),
        _item("y", "Title two gamma"),
    ]
    out = dedup_items(items)
    assert len(out) == 2
    ids = {it["id"] for it in out}
    assert ids == {"x", "y"}


# ── 标题相似度 ──────────────────────────────────────────────────

def test_title_similarity_near_identical_above_threshold():
    s = title_similarity(
        "Apple releases new iPhone 15 in autumn",
        "Apple releases new iPhone 15 this autumn",
    )
    assert s > 0.72


def test_title_similarity_clearly_different_low():
    s = title_similarity(
        "Quantum computing breakthrough announced today",
        "Best pasta recipes for summer dinner",
    )
    assert s < 0.5


def test_title_similarity_short_returns_zero():
    """清理后短于 8 字符 → 直接判不相似，避免短标题误判。"""
    assert title_similarity("Hi", "Hi") == 0.0


def test_title_similarity_empty_returns_zero():
    assert title_similarity("", "anything") == 0.0


# ── HTML 清理 ───────────────────────────────────────────────────

def test_strip_html_removes_tags_and_unescapes():
    assert _strip_html("<p>Hello &amp; world</p>") == "Hello & world"


def test_strip_html_plain_text():
    assert _strip_html("just text") == "just text"
