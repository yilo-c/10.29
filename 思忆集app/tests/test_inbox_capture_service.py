"""InboxCaptureService 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from services.inbox_capture_service import InboxCaptureService
from domain.inbox_models import InboxEntry, SOURCE_BROWSER, SOURCE_MANUAL


class _FakeWorkbenchService:
    def __init__(self, *, fail=False):
        self.captured = []
        self._fail = fail

    def capture_quick_entry(self, text, source="manual", title=None, tags=None, metadata=None):
        if self._fail:
            raise ValueError("模拟写入失败")
        entry = {
            "id": len(self.captured) + 1,
            "title": title or text[:40],
            "content": text,
            "source": source,
            "tags": list(tags or []),
            "metadata": dict(metadata or {}),
        }
        self.captured.append(entry)
        return entry


class _FakeInboxRepository:
    def __init__(self):
        self.entries = []

    def add_entry(self, entry_info):
        entry_id = len(self.entries) + 1
        item = dict(entry_info)
        item["id"] = entry_id
        self.entries.append(item)
        return entry_id

    def get_entry(self, entry_id):
        for e in self.entries:
            if e["id"] == entry_id:
                return e
        return None


class _FakeObservability:
    def __init__(self):
        self.events = []

    def track_event(self, name, payload):
        self.events.append((name, payload))


class _FakeGuardResult:
    def __init__(self, allowed=True, reason="", warnings=None):
        self.allowed = allowed
        self.reason = reason
        self.warnings = list(warnings or [])


class _FakeActionGuard:
    def __init__(self, *, allow=True, reason="", warnings=None):
        self._allow = allow
        self._reason = reason
        self._warnings = list(warnings or [])

    def validate(self, action_id=None, payload=None, source=None):
        return _FakeGuardResult(self._allow, self._reason, self._warnings)


# ── 基本收件 ──

def test_capture_success_via_workbench():
    wb = _FakeWorkbenchService()
    obs = _FakeObservability()
    svc = InboxCaptureService(workbench_service=wb, observability_service=obs)

    result = svc.capture("今天学到了新知识", source="manual", title="学习笔记")
    assert result["ok"] is True
    assert result["entry"]["title"] == "学习笔记"
    assert len(wb.captured) == 1
    assert any("success" in e[0] for e in obs.events)


def test_capture_empty_text_rejected():
    svc = InboxCaptureService(workbench_service=_FakeWorkbenchService())
    result = svc.capture("   ")
    assert result["ok"] is False
    assert "没有" in result["reason"]


def test_capture_fallback_to_inbox_repository():
    repo = _FakeInboxRepository()
    svc = InboxCaptureService(inbox_repository=repo)

    result = svc.capture("测试内容", source="browser")
    assert result["ok"] is True
    assert len(repo.entries) == 1
    # source "browser" is standardized to "browser_extension" by InboxEntry.normalize
    assert repo.entries[0]["source"] == "browser_extension"


def test_capture_no_backend_returns_failure():
    svc = InboxCaptureService()
    result = svc.capture("无服务可用")
    assert result["ok"] is False
    assert "无法" in result["reason"]


def test_capture_workbench_error_returns_failure():
    wb = _FakeWorkbenchService(fail=True)
    svc = InboxCaptureService(workbench_service=wb)
    result = svc.capture("会触发异常")
    assert result["ok"] is False
    assert "模拟写入失败" in result["reason"]


# ── Guard 鉴权 ──

def test_capture_blocked_by_guard():
    guard = _FakeActionGuard(allow=False, reason="频率超限")
    svc = InboxCaptureService(workbench_service=_FakeWorkbenchService(), action_guard=guard)
    result = svc.capture("被拦截的内容")
    assert result["ok"] is False
    assert "频率超限" in result["reason"]


def test_capture_skip_guard():
    guard = _FakeActionGuard(allow=False, reason="不该看到")
    wb = _FakeWorkbenchService()
    svc = InboxCaptureService(workbench_service=wb, action_guard=guard)
    result = svc.capture("跳过 guard", skip_guard=True)
    assert result["ok"] is True


# ── 批量收件 ──

def test_capture_batch():
    wb = _FakeWorkbenchService()
    svc = InboxCaptureService(workbench_service=wb)
    items = [
        {"text": "条目一", "title": "T1"},
        {"text": "条目二", "title": "T2"},
        {"text": "", "title": "空"},
    ]
    result = svc.capture_batch(items, source="batch")
    assert result["total"] == 3
    assert result["succeeded"] == 2
    assert len(wb.captured) == 2


# ── InboxEntry 模型标准化 ──

def test_inbox_entry_normalize_standardizes_sources():
    """不同来源的原始 source 值被映射到标准值。"""
    assert InboxEntry.normalize({"source": "browser"})["source"] == SOURCE_BROWSER
    assert InboxEntry.normalize({"source": "manual"})["source"] == SOURCE_MANUAL
    assert InboxEntry.normalize({"source": "browser_extension"})["source"] == SOURCE_BROWSER
    assert InboxEntry.normalize({"source": "unknown_source"})["source"] == SOURCE_MANUAL


def test_inbox_entry_normalize_ensures_consistent_fields():
    """标准化确保所有条目含相同的字段结构，即使输入不完整。"""
    result = InboxEntry.normalize({})
    assert set(result.keys()) == {"title", "content", "source", "status", "layer", "tags", "metadata", "created_at", "updated_at", "daily_at", "insights_at"}
    assert result["tags"] == []
    assert result["metadata"] == {}
    assert result["status"] == "new"


def test_inbox_entry_normalize_preserves_browser_metadata():
    """浏览器来源的额外元数据（url、content_kind）在标准化后保留。"""
    raw = {
        "source": "browser_extension",
        "title": "示例页面",
        "content": "页面内容",
        "metadata": {
            "url": "https://example.com/page",
            "content_kind": "article",
            "selection_length": 42,
        },
    }
    result = InboxEntry.normalize(raw)
    assert result["metadata"]["url"] == "https://example.com/page"
    assert result["metadata"]["content_kind"] == "article"
    assert result["metadata"]["selection_length"] == 42


def test_inbox_entry_to_dict_and_from_dict_roundtrip():
    """InboxEntry 序列化和反序列化应保持字段一致。"""
    original = InboxEntry(
        title="测试条目",
        content="测试内容",
        source=SOURCE_BROWSER,
        status="new",
        tags=["browser", "web"],
        metadata={"url": "https://example.com", "content_kind": "article"},
        created_at="2026-05-02T00:00:00",
        updated_at="2026-05-02T00:00:00",
    )
    data = original.to_dict()
    restored = InboxEntry.from_dict(data)
    assert restored.title == original.title
    assert restored.content == original.content
    assert restored.source == original.source
    assert restored.tags == original.tags
    assert restored.metadata == original.metadata
