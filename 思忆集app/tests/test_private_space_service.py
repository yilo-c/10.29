"""PrivateSpaceService 单元测试。

覆盖：
- 三层状态转换（inbox → daily → insights）
- 转换后 version / device_id / sync_state 保持不变
- 三种资源类型（notes / personal_tasks / saved_articles）
- list_by_space 空间筛选
- 无效参数处理
- 已删除条目不影响空间计数
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from services.private_space_service import (
    ALL_RESOURCE_TYPES,
    ALL_SPACES,
    PrivateSpaceService,
    SPACE_DAILY,
    SPACE_INBOX,
    SPACE_INSIGHTS,
)


class FakeDataStore:
    """模拟 LocalDataStore，使用内存 dict 而非文件 I/O。"""

    def __init__(self):
        self._storage: dict[str, list | dict] = {}

    def load_json(self, key: str, default):
        return self._storage.get(key, default)

    def save_json(self, key: str, data):
        self._storage[key] = data

    def update_json(self, key: str, default, updater):
        current = self._storage.get(key, default)
        updated = updater(current)
        self._storage[key] = updated
        return updated

    def next_id(self, items: list[dict]) -> int:
        return (max((item.get("id", 0) for item in items), default=0) + 1) or 1


# ── Helper ─────────────────────────────────────────────────────


def _make_item(item_id: int, **overrides) -> dict:
    """创建一个带同步元数据的标准条目。"""
    item = {
        "id": item_id,
        "title": f"测试条目 {item_id}",
        "content": f"内容 {item_id}",
        "version": 3,
        "device_id": "test-device-abc",
        "sync_state": "synced",
        "created_at": "2026-04-01T00:00:00",
        "updated_at": "2026-04-02T00:00:00",
        "deleted_at": None,
    }
    item.update(overrides)
    return item


def _make_service(data_store: FakeDataStore, resource_type: str, items: list[dict]) -> PrivateSpaceService:
    """用预设数据创建一个 PrivateSpaceService。"""
    key_map = {
        "notes": "notes",
        "personal_tasks": "tasks",
        "saved_articles": "news",
    }
    store_key = key_map[resource_type]
    data_store.save_json(store_key, list(items))
    return PrivateSpaceService(data_store=data_store)


# ── 基础功能测试 ────────────────────────────────────────────────


def test_set_space_transitions_item():
    """set_space 将条目从 inbox 转换到 daily。"""
    ds = FakeDataStore()
    items = [_make_item(1, space=SPACE_INBOX)]
    service = _make_service(ds, "notes", items)

    result = service.set_space("notes", 1, SPACE_DAILY)

    assert result is not None
    assert result["id"] == 1
    assert result["space"] == SPACE_DAILY


def test_set_space_preserves_version():
    """转换后 version 保持不变。"""
    ds = FakeDataStore()
    items = [_make_item(1, space=SPACE_INBOX, version=42)]
    service = _make_service(ds, "notes", items)

    result = service.set_space("notes", 1, SPACE_DAILY)

    assert result["version"] == 42


def test_set_space_preserves_device_id():
    """转换后 device_id 保持不变。"""
    ds = FakeDataStore()
    items = [_make_item(1, space=SPACE_INBOX, device_id="my-desktop")]
    service = _make_service(ds, "notes", items)

    result = service.set_space("notes", 1, SPACE_INSIGHTS)

    assert result["device_id"] == "my-desktop"


def test_set_space_preserves_sync_state():
    """转换后 sync_state 保持不变。"""
    ds = FakeDataStore()
    items = [_make_item(1, space=SPACE_INBOX, sync_state="dirty")]
    service = _make_service(ds, "notes", items)

    result = service.set_space("notes", 1, SPACE_DAILY)

    assert result["sync_state"] == "dirty"


def test_set_space_full_cycle():
    """条目完整经过 inbox → daily → insights 三步，同步元数据始终不变。"""
    ds = FakeDataStore()
    item = _make_item(1)
    items = [item]
    service = _make_service(ds, "notes", items)

    # inbox → daily
    r1 = service.set_space("notes", 1, SPACE_DAILY)
    assert r1["space"] == SPACE_DAILY
    assert r1["version"] == 3
    assert r1["device_id"] == "test-device-abc"
    assert r1["sync_state"] == "synced"

    # daily → insights
    r2 = service.set_space("notes", 1, SPACE_INSIGHTS)
    assert r2["space"] == SPACE_INSIGHTS
    assert r2["version"] == 3
    assert r2["device_id"] == "test-device-abc"
    assert r2["sync_state"] == "synced"

    # insights → inbox (回退也允许)
    r3 = service.set_space("notes", 1, SPACE_INBOX)
    assert r3["space"] == SPACE_INBOX
    assert r3["version"] == 3
    assert r3["device_id"] == "test-device-abc"
    assert r3["sync_state"] == "synced"


def test_set_space_nonexistent_item_returns_none():
    """不存在的条目返回 None。"""
    ds = FakeDataStore()
    items = [_make_item(1, space=SPACE_INBOX)]
    service = _make_service(ds, "notes", items)

    result = service.set_space("notes", 999, SPACE_DAILY)

    assert result is None


def test_set_space_invalid_resource_type_returns_none():
    """无效的资源类型返回 None。"""
    ds = FakeDataStore()
    service = _make_service(ds, "notes", [_make_item(1)])
    result = service.set_space("unknown_type", 1, SPACE_DAILY)
    assert result is None


def test_set_space_invalid_space_returns_none():
    """无效的空间值返回 None。"""
    ds = FakeDataStore()
    service = _make_service(ds, "notes", [_make_item(1)])
    result = service.set_space("notes", 1, "nonexistent_space")
    assert result is None


# ── 三种资源类型 ────────────────────────────────────────────────


def test_all_resource_types_support_transition():
    """notes / personal_tasks / saved_articles 三种资源都支持转换。"""
    for resource_type in ALL_RESOURCE_TYPES:
        ds = FakeDataStore()
        items = [_make_item(1, space=SPACE_INBOX)]
        service = _make_service(ds, resource_type, items)

        result = service.set_space(resource_type, 1, SPACE_DAILY)

        assert result is not None, f"{resource_type} should support transition"
        assert result["space"] == SPACE_DAILY, f"{resource_type} should transition to daily"
        assert result["version"] == 3, f"{resource_type} should preserve version"
        assert result["device_id"] == "test-device-abc", f"{resource_type} should preserve device_id"
        assert result["sync_state"] == "synced", f"{resource_type} should preserve sync_state"


def test_all_resource_types_support_list_by_space():
    """notes / personal_tasks / saved_articles 三种资源都支持按空间查询。"""
    for resource_type in ALL_RESOURCE_TYPES:
        ds = FakeDataStore()
        items = [
            _make_item(1, space=SPACE_INBOX, title="收件 A"),
            _make_item(2, space=SPACE_DAILY, title="日计划 B"),
            _make_item(3, space=SPACE_INSIGHTS, title="洞见 C"),
        ]
        service = _make_service(ds, resource_type, items)

        inbox_items = service.list_by_space(resource_type, SPACE_INBOX)
        daily_items = service.list_by_space(resource_type, SPACE_DAILY)
        insights_items = service.list_by_space(resource_type, SPACE_INSIGHTS)

        assert len(inbox_items) == 1, f"{resource_type}: inbox should have 1 item"
        assert len(daily_items) == 1, f"{resource_type}: daily should have 1 item"
        assert len(insights_items) == 1, f"{resource_type}: insights should have 1 item"


# ── 空间查询 ────────────────────────────────────────────────────


def test_list_by_space_filters_correctly():
    """list_by_space 只返回指定空间的条目。"""
    ds = FakeDataStore()
    items = [
        _make_item(1, space=SPACE_INBOX, title="待处理"),
        _make_item(2, space=SPACE_DAILY, title="今日任务"),
        _make_item(3, space=SPACE_DAILY, title="另一个任务"),
        _make_item(4, space=SPACE_INSIGHTS, title="已归档"),
    ]
    service = _make_service(ds, "notes", items)

    daily = service.list_by_space("notes", SPACE_DAILY)

    assert len(daily) == 2
    titles = {item["title"] for item in daily}
    assert titles == {"今日任务", "另一个任务"}


def test_list_by_space_excludes_deleted():
    """list_by_space 排除已删除条目。"""
    ds = FakeDataStore()
    items = [
        _make_item(1, space=SPACE_INBOX, title="正常"),
        _make_item(2, space=SPACE_INBOX, title="已删除", deleted_at="2026-04-03T00:00:00"),
    ]
    service = _make_service(ds, "notes", items)

    inbox = service.list_by_space("notes", SPACE_INBOX)

    assert len(inbox) == 1
    assert inbox[0]["title"] == "正常"


def test_list_by_space_defaults_to_inbox():
    """没有 space 字段的条目默认属于 inbox 空间。"""
    ds = FakeDataStore()
    items = [
        _make_item(1, title="无空间字段"),
    ]
    # 移除 space 字段
    items[0].pop("space", None)
    service = _make_service(ds, "notes", items)

    inbox = service.list_by_space("notes", SPACE_INBOX)
    daily = service.list_by_space("notes", SPACE_DAILY)

    assert len(inbox) == 1
    assert inbox[0]["title"] == "无空间字段"
    assert len(daily) == 0


def test_list_by_space_invalid_params_returns_empty():
    """无效参数返回空列表。"""
    ds = FakeDataStore()
    service = _make_service(ds, "notes", [_make_item(1)])
    assert service.list_by_space("unknown", SPACE_INBOX) == []
    assert service.list_by_space("notes", "void") == []


def test_get_space_returns_correct_space():
    """get_space 返回条目的当前空间。"""
    ds = FakeDataStore()
    items = [
        _make_item(1, space=SPACE_INBOX),
        _make_item(2, space=SPACE_DAILY),
        _make_item(3, space=SPACE_INSIGHTS),
    ]
    service = _make_service(ds, "notes", items)

    assert service.get_space("notes", 1) == SPACE_INBOX
    assert service.get_space("notes", 2) == SPACE_DAILY
    assert service.get_space("notes", 3) == SPACE_INSIGHTS


def test_get_space_nonexistent_returns_none():
    """不存在的条目返回 None。"""
    ds = FakeDataStore()
    service = _make_service(ds, "notes", [_make_item(1)])
    assert service.get_space("notes", 999) is None


# ── 计数 ───────────────────────────────────────────────────────


def test_count_by_space():
    """count_by_space 正确统计各空间条目数。"""
    ds = FakeDataStore()
    items = [
        _make_item(1, space=SPACE_INBOX),
        _make_item(2, space=SPACE_INBOX),
        _make_item(3, space=SPACE_DAILY),
        _make_item(4, space=SPACE_INSIGHTS),
        _make_item(5, space=SPACE_INSIGHTS),
        _make_item(6, space=SPACE_INSIGHTS),
    ]
    service = _make_service(ds, "notes", items)

    counts = service.count_by_space("notes")

    assert counts[SPACE_INBOX] == 2
    assert counts[SPACE_DAILY] == 1
    assert counts[SPACE_INSIGHTS] == 3


def test_count_by_space_excludes_deleted():
    """已删除条目不计入空间统计。"""
    ds = FakeDataStore()
    items = [
        _make_item(1, space=SPACE_INBOX),
        _make_item(2, space=SPACE_INBOX, deleted_at="2026-04-03T00:00:00"),
    ]
    service = _make_service(ds, "notes", items)

    counts = service.count_by_space("notes")

    assert counts[SPACE_INBOX] == 1


def test_list_all_spaces_returns_grouped():
    """list_all_spaces 返回以空间分组的结果。"""
    ds = FakeDataStore()
    items = [
        _make_item(1, space=SPACE_INBOX, title="A"),
        _make_item(2, space=SPACE_DAILY, title="B"),
        _make_item(3, space=SPACE_INSIGHTS, title="C"),
    ]
    service = _make_service(ds, "notes", items)

    grouped = service.list_all_spaces("notes")

    assert len(grouped[SPACE_INBOX]) == 1
    assert len(grouped[SPACE_DAILY]) == 1
    assert len(grouped[SPACE_INSIGHTS]) == 1
    assert grouped[SPACE_INBOX][0]["title"] == "A"


# ── 持久化验证 ──────────────────────────────────────────────────


def test_transition_persists_to_storage():
    """set_space 后 data_store 中已持久化新空间。"""
    ds = FakeDataStore()
    items = [_make_item(1, space=SPACE_INBOX)]
    service = _make_service(ds, "notes", items)

    service.set_space("notes", 1, SPACE_DAILY)

    stored = ds.load_json("notes", [])
    assert len(stored) == 1
    assert stored[0]["space"] == SPACE_DAILY


# ── 常量完整性 ──────────────────────────────────────────────────


def test_all_spaces_contains_three_values():
    """ALL_SPACES 包含三个标准空间。"""
    assert len(ALL_SPACES) == 3
    assert SPACE_INBOX in ALL_SPACES
    assert SPACE_DAILY in ALL_SPACES
    assert SPACE_INSIGHTS in ALL_SPACES


def test_all_resource_types_contains_three_types():
    """ALL_RESOURCE_TYPES 包含三种资源类型。"""
    assert len(ALL_RESOURCE_TYPES) == 3
    assert "notes" in ALL_RESOURCE_TYPES
    assert "personal_tasks" in ALL_RESOURCE_TYPES
    assert "saved_articles" in ALL_RESOURCE_TYPES
