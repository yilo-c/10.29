"""单元测试：news/inbox/memory/settings 4 个新同步辅助模块。

覆盖：
- fingerprint 稳定性 / 区分度
- ensure_*_sync_metadata 默认补齐 + mark_dirty 行为
- build_*_sync_payload 字段完整 + 深拷贝隔离
- settings_sync 黑名单 + 子串过滤 + 不可同步项返回 None
"""
from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from sync import inbox_sync, memory_sync, news_sync, settings_sync


# ---------- 辅助 ----------
class _FakeDataStore:
    """模拟 LocalDataStore，仅提供 get_or_create_local_device_id 所需接口。"""

    def __init__(self, device_id: str = "test-device-xyz"):
        self._device_id = device_id
        self._docs = {"settings": {"siyi_device_id": device_id}}

    def load_json(self, name, default=None):
        return dict(self._docs.get(name, default if default is not None else {}))

    def save_json(self, name, data):
        self._docs[name] = dict(data) if isinstance(data, dict) else data
        return True


def _ds() -> _FakeDataStore:
    return _FakeDataStore()


# ====== news_sync ======

def test_news_fingerprint_stable_and_distinct():
    a = {"url": "https://x.com/a", "title": "T", "published_at": "2026-01-01"}
    b = {"url": "https://x.com/a", "title": "T", "published_at": "2026-01-01"}
    c = {"url": "https://x.com/a", "title": "T2", "published_at": "2026-01-01"}
    assert news_sync.build_news_fingerprint(a) == news_sync.build_news_fingerprint(b)
    assert news_sync.build_news_fingerprint(a) != news_sync.build_news_fingerprint(c)
    # None safe
    assert isinstance(news_sync.build_news_fingerprint(None), str)


def test_news_ensure_metadata_defaults_and_mark_dirty():
    item = {"title": "hello", "url": "u", "remote_id": None}
    changed = news_sync.ensure_news_sync_metadata(item, _ds())
    assert changed is True
    assert item["device_id"]
    assert item["version"] == 1
    assert item["sync_state"] == "local_only"
    assert item["fingerprint"]
    assert item["last_synced_at"] is None
    assert item["deleted_at"] is None

    # 二次调用应稳定（指纹不变 → 无脏 → False）
    assert news_sync.ensure_news_sync_metadata(item, _ds()) is False

    # 标记 dirty：remote_id 为空 → 仍是 local_only
    news_sync.ensure_news_sync_metadata(item, _ds(), mark_dirty=True)
    assert item["sync_state"] == "local_only"

    # remote_id 存在时，mark_dirty 应改为 dirty
    item["remote_id"] = "rid-1"
    item["sync_state"] = "synced"
    news_sync.ensure_news_sync_metadata(item, _ds(), mark_dirty=True)
    assert item["sync_state"] == "dirty"


def test_news_payload_isolation():
    src = {
        "id": 1, "title": "A", "url": "u", "tags": ["x"],
        "summary": "s", "source": "hn", "category": "tech",
        "published_at": "2026-01-01", "fetched_at": "2026-01-02",
        "starred": True, "read": False,
    }
    payload = news_sync.build_news_sync_payload(src)
    payload["tags"].append("mutated")
    assert src["tags"] == ["x"]
    assert payload["starred"] is True
    assert payload["read"] is False


# ====== inbox_sync ======

def test_inbox_fingerprint_distinct_on_source():
    a = {"content": "c", "captured_at": "t", "source_device_id": "d1", "source_type": "phone"}
    b = {"content": "c", "captured_at": "t", "source_device_id": "d2", "source_type": "phone"}
    assert inbox_sync.build_inbox_fingerprint(a) != inbox_sync.build_inbox_fingerprint(b)


def test_inbox_ensure_metadata_includes_is_processed():
    entry = {"content": "x", "captured_at": "2026-06-01T00:00:00Z"}
    inbox_sync.ensure_inbox_sync_metadata(entry, _ds())
    assert entry["is_processed"] is False
    assert entry["version"] == 1
    assert entry["fingerprint"]
    assert entry["device_id"]


def test_inbox_payload_defaults():
    entry = {"content": "hi"}
    inbox_sync.ensure_inbox_sync_metadata(entry, _ds())
    payload = inbox_sync.build_inbox_sync_payload(entry)
    assert payload["content_type"] == "text"
    assert payload["is_processed"] is False
    assert isinstance(payload["tags"], list)
    assert isinstance(payload["extra"], dict)
    # 隔离
    payload["tags"].append("z")
    assert entry.get("tags") in (None, [])


# ====== memory_sync ======

def test_memory_fingerprint_uses_text_or_content():
    a = {"text": "记忆 A", "memory_type": "fact", "tags": ["a", "b"]}
    b = {"content": "记忆 A", "memory_type": "fact", "tags": ["b", "a"]}  # content 兼容 + 顺序无关
    assert memory_sync.build_memory_fingerprint(a) == memory_sync.build_memory_fingerprint(b)

    c = {"text": "记忆 A", "memory_type": "fact", "tags": ["a"]}
    assert memory_sync.build_memory_fingerprint(a) != memory_sync.build_memory_fingerprint(c)


def test_memory_ensure_metadata_full():
    entry = {"text": "x", "memory_type": "fact", "tags": ["t1"]}
    changed = memory_sync.ensure_memory_sync_metadata(entry, _ds())
    assert changed is True
    assert entry["device_id"]
    assert entry["sync_state"] == "local_only"
    # 二次幂等
    assert memory_sync.ensure_memory_sync_metadata(entry, _ds()) is False


def test_memory_payload_text_fallback():
    entry = {"content": "fallback", "memory_type": "fact"}
    payload = memory_sync.build_memory_sync_payload(entry)
    assert payload["text"] == "fallback"
    assert isinstance(payload["tags"], list)
    assert isinstance(payload["metadata"], dict)


# ====== settings_sync ======

def test_settings_blocklist_and_substring_filter():
    # 显式黑名单
    assert settings_sync.is_syncable_settings_key("llm_api_key") is False
    assert settings_sync.is_syncable_settings_key("siyi_device_id") is False
    assert settings_sync.is_syncable_settings_key("cloud_sync_token") is False
    assert settings_sync.is_syncable_settings_key("encryption_master_key") is False

    # 子串过滤
    assert settings_sync.is_syncable_settings_key("some_api_key_x") is False
    assert settings_sync.is_syncable_settings_key("My_Secret") is False
    assert settings_sync.is_syncable_settings_key("oauth_token_v2") is False
    assert settings_sync.is_syncable_settings_key("user_password_hash") is False

    # 普通可同步项
    assert settings_sync.is_syncable_settings_key("theme") is True
    assert settings_sync.is_syncable_settings_key("ui_language") is True
    assert settings_sync.is_syncable_settings_key("notes.default_folder") is True

    # 边界
    assert settings_sync.is_syncable_settings_key("") is False
    assert settings_sync.is_syncable_settings_key(None) is False  # type: ignore[arg-type]


def test_settings_filter_strip_sensitive():
    raw = {
        "theme": "dark",
        "llm_api_key": "sk-xxx",
        "deepseek_api_key": "sk-yyy",
        "ui_language": "zh-CN",
        "some_secret_value": 123,
    }
    filtered = settings_sync.filter_settings_for_sync(raw)
    assert filtered == {"theme": "dark", "ui_language": "zh-CN"}


def test_settings_ensure_metadata_skips_blocked():
    entry_blocked = {"key": "llm_api_key", "value": "sk-xxx"}
    changed = settings_sync.ensure_setting_sync_metadata(entry_blocked, _ds())
    assert changed is False
    # 被阻挡的项不会写入任何 sync 元字段
    assert "device_id" not in entry_blocked
    assert "fingerprint" not in entry_blocked

    entry_ok = {"key": "theme", "value": "dark"}
    changed_ok = settings_sync.ensure_setting_sync_metadata(entry_ok, _ds())
    assert changed_ok is True
    assert entry_ok["device_id"]
    assert entry_ok["version"] == 1
    assert entry_ok["fingerprint"]


def test_settings_payload_returns_none_for_blocked():
    assert settings_sync.build_setting_sync_payload({"key": "llm_api_key", "value": "x"}) is None
    assert settings_sync.build_setting_sync_payload({"key": "deepseek_api_key", "value": "x"}) is None

    payload = settings_sync.build_setting_sync_payload({"key": "theme", "value": "dark"})
    assert payload is not None
    assert payload["key"] == "theme"
    assert payload["value"] == "dark"
    assert payload["scope"] == "user"
    assert payload["id"] == "theme"  # 默认用 key 作为 id


def test_settings_fingerprint_changes_with_value():
    a = {"key": "theme", "value": "dark"}
    b = {"key": "theme", "value": "light"}
    assert settings_sync.build_settings_fingerprint(a) != settings_sync.build_settings_fingerprint(b)
