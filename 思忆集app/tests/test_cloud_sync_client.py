"""CloudSyncClient 单元测试。

覆盖：
- _collect_notes / _collect_todos / _collect_settings（字段映射、过滤已删除）
- _merge_notes / _merge_todos / _merge_settings（LWW 合并策略）
- is_configured / get_status
- register_device / push_local_data / pull_remote_data（mock HTTP）
- sync_once / sync_loop 生命周期
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from sync.cloud_sync_client import (
    CloudSyncAuthError,
    CloudSyncClient,
    CloudSyncConfigError,
    CloudSyncVersionError,
    RESOURCE_NOTES,
    RESOURCE_TODOS,
    SyncBridge,
    encrypt_data,
)
from sync._cloud_sync_crypto import SYNCABLE_SETTING_KEYS


# ── 测试 fixtures ──


def _make_note_repo(notes=None):
    repo = SimpleNamespace()
    repo._notes = list(notes or [])
    repo.list_notes = lambda include_deleted=False: list(repo._notes)
    repo.add_note = lambda data: repo._notes.append(data)
    repo.update_note = lambda nid, data: _update_in_list(repo._notes, nid, data)
    return repo


def _make_task_repo(tasks=None):
    repo = SimpleNamespace()
    repo._tasks = list(tasks or [])
    repo.list_tasks = lambda include_deleted=False: list(repo._tasks)
    repo.add_task = lambda data: repo._tasks.append(data)
    repo.update_task = lambda tid, data: _update_in_list(repo._tasks, tid, data)
    return repo


def _make_settings_repo(data=None):
    repo = SimpleNamespace()
    repo._data = dict(data or {})
    repo.get_setting = lambda key, default=None: repo._data.get(key, default)
    repo.set_setting = lambda key, value: repo._data.__setitem__(key, value)
    return repo


def _update_in_list(items, item_id, new_data):
    for i, item in enumerate(items):
        if item.get("id") == item_id:
            items[i] = {**item, **new_data}
            return
    items.append(new_data)


def _make_client(**overrides):
    defaults = dict(
        worker_url="https://sync.example.workers.dev",
        sync_token="test-token",
        device_id="desktop-test",
        register_secret="reg-secret",
        note_repo=None,
        task_repo=None,
        settings_repo=None,
    )
    defaults.update(overrides)
    return CloudSyncClient(**defaults)


# ── is_configured ──


def test_is_configured_complete():
    client = _make_client()
    assert client.is_configured() is True


def test_is_configured_missing_url():
    client = _make_client(worker_url="")
    assert client.is_configured() is False


def test_is_configured_missing_token():
    client = _make_client(sync_token="")
    assert client.is_configured() is False


def test_require_config_raises_when_not_configured():
    client = _make_client(worker_url="")
    with pytest.raises(CloudSyncConfigError):
        client._require_config()


def test_worker_url_trailing_slash_stripped():
    client = _make_client(worker_url="https://sync.example.workers.dev/")
    assert client.worker_url == "https://sync.example.workers.dev"


def test_default_device_id_generated_when_empty():
    client = _make_client(device_id="")
    assert client.device_id.startswith("desktop-")
    assert len(client.device_id) > len("desktop-")


# ── _collect_notes ──


def test_collect_notes_maps_fields():
    notes = [
        {
            "id": "n1",
            "title": "笔记1",
            "content": "内容",
            "type": "note",
            "folder": "work",
            "tags": ["a"],
            "created_at": 1000,
            "updated_at": 2000,
        },
    ]
    client = _make_client(note_repo=_make_note_repo(notes))
    result = client._collect_notes()
    assert len(result) == 1
    n = result[0]
    assert n["id"] == "n1"
    assert n["title"] == "笔记1"
    assert n["content"] == "内容"
    assert n["folderId"] == "work"
    assert n["tags"] == ["a"]
    assert n["createdAt"] == 1000
    assert n["updatedAt"] == 2000


def test_collect_notes_includes_deleted_with_flag():
    # M24: 软删除条目应带 deleted=True 推送到远端，让其他设备同步删除，
    # 而不是在 collect 阶段被静默过滤掉（否则删数据会在 pull 时复活）。
    notes = [
        {"id": "n1", "title": "alive", "deleted_at": None},
        {"id": "n2", "title": "dead", "deleted_at": "2026-01-01T00:00:00+00:00"},
        {"id": "n3", "title": "also alive"},  # 无 deleted_at 字段
    ]
    client = _make_client(note_repo=_make_note_repo(notes))
    result = client._collect_notes()
    by_id = {n["id"]: n for n in result}
    assert set(by_id.keys()) == {"n1", "n2", "n3"}
    assert by_id["n1"]["deleted"] is False
    assert by_id["n2"]["deleted"] is True
    assert by_id["n3"]["deleted"] is False


def test_collect_notes_no_repo_returns_empty():
    client = _make_client(note_repo=None)
    assert client._collect_notes() == []


def test_collect_notes_repo_error_returns_empty():
    repo = SimpleNamespace()
    repo.list_notes = MagicMock(side_effect=RuntimeError("db locked"))
    client = _make_client(note_repo=repo)
    assert client._collect_notes() == []


# ── _collect_todos ──


def test_collect_todos_maps_fields():
    tasks = [
        {
            "id": "t1",
            "title": "任务1",
            "completed": True,
            "category": "work",
            "priority": "high",
            "due_date": "2026-01-01",
            "created_at": 1000,
            "updated_at": 2000,
        },
    ]
    client = _make_client(task_repo=_make_task_repo(tasks))
    result = client._collect_todos()
    assert len(result) == 1
    t = result[0]
    assert t["id"] == "t1"
    assert t["text"] == "任务1"
    assert t["done"] is True
    assert t["category"] == "work"
    assert t["priority"] == "high"
    assert t["dueDate"] == "2026-01-01"
    assert t["createdAt"] == 1000
    assert t["updatedAt"] == 2000


def test_collect_todos_includes_deleted_with_flag():
    # M24: 软删除任务应带 deleted=True 推送到远端。
    tasks = [
        {"id": "t1", "title": "alive", "deleted_at": None},
        {"id": "t2", "title": "dead", "deleted_at": "2026-01-01T00:00:00+00:00"},
    ]
    client = _make_client(task_repo=_make_task_repo(tasks))
    result = client._collect_todos()
    by_id = {t["id"]: t for t in result}
    assert set(by_id.keys()) == {"t1", "t2"}
    assert by_id["t1"]["deleted"] is False
    assert by_id["t2"]["deleted"] is True


# ── _collect_settings ──


def test_collect_settings_only_whitelist():
    settings_data = {
        "ui_theme": "dark",
        "cloud_ai_enabled": True,
        "llm_api_key": "secret-should-not-sync",  # 不在白名单
        "knowledge_base_enabled": False,
    }
    client = _make_client(settings_repo=_make_settings_repo(settings_data))
    result = client._collect_settings()
    assert "ui_theme" in result
    assert "cloud_ai_enabled" in result
    assert "knowledge_base_enabled" in result
    assert "llm_api_key" not in result  # 敏感字段不同步


def test_collect_settings_no_repo_returns_empty():
    client = _make_client(settings_repo=None)
    assert client._collect_settings() == {}


def test_collect_settings_excludes_none_values():
    settings_data = {"ui_theme": "dark", "cloud_ai_enabled": None}
    client = _make_client(settings_repo=_make_settings_repo(settings_data))
    result = client._collect_settings()
    assert "ui_theme" in result
    assert "cloud_ai_enabled" not in result


def test_syncable_setting_keys_excludes_sensitive():
    """安全检查：确保敏感键不在同步白名单中。"""
    sensitive = {"llm_api_key", "llm_fallback_api_key", "cloud_sync_token", "cloud_sync_register_secret", "youdao_app_secret"}
    for key in sensitive:
        assert key not in SYNCABLE_SETTING_KEYS, f"{key} 不应出现在同步白名单"


# ── _merge_notes (LWW) ──


def test_merge_notes_remote_newer_updates():
    local = [{"id": "n1", "title": "old", "updated_at": 1000}]
    repo = _make_note_repo(local)
    client = _make_client(note_repo=repo)
    remote = [{"id": "n1", "title": "new", "updatedAt": 2000, "createdAt": 1000}]
    merged = client._merge_notes(remote)
    assert merged == 1
    assert repo._notes[0]["title"] == "new"


def test_merge_notes_local_newer_skips():
    local = [{"id": "n1", "title": "local", "updated_at": 2000}]
    repo = _make_note_repo(local)
    client = _make_client(note_repo=repo)
    remote = [{"id": "n1", "title": "remote", "updatedAt": 1000, "createdAt": 1000}]
    merged = client._merge_notes(remote)
    assert merged == 0
    assert repo._notes[0]["title"] == "local"


def test_merge_notes_local_missing_adds():
    repo = _make_note_repo([])
    client = _make_client(note_repo=repo)
    remote = [{"id": "n1", "title": "new note", "updatedAt": 2000, "createdAt": 1000}]
    merged = client._merge_notes(remote)
    assert merged == 1
    assert repo._notes[0]["id"] == "n1"
    assert repo._notes[0]["title"] == "new note"


def test_merge_notes_equal_timestamp_skips():
    """remote_time <= local_time 时跳过（等于也跳过，本地优先）。"""
    local = [{"id": "n1", "title": "local", "updated_at": 2000}]
    repo = _make_note_repo(local)
    client = _make_client(note_repo=repo)
    remote = [{"id": "n1", "title": "remote", "updatedAt": 2000, "createdAt": 1000}]
    merged = client._merge_notes(remote)
    assert merged == 0
    assert repo._notes[0]["title"] == "local"


def test_merge_notes_no_repo_returns_zero():
    client = _make_client(note_repo=None)
    assert client._merge_notes([{"id": "n1"}]) == 0


def test_merge_notes_skips_items_without_id():
    repo = _make_note_repo([])
    client = _make_client(note_repo=repo)
    remote = [{"title": "no id", "updatedAt": 2000}, {"id": "n1", "title": "has id", "updatedAt": 2000}]
    merged = client._merge_notes(remote)
    assert merged == 1


# ── _merge_todos (LWW) ──


def test_merge_todos_remote_newer_updates():
    local = [{"id": "t1", "title": "old", "completed": False, "updated_at": 1000}]
    repo = _make_task_repo(local)
    client = _make_client(task_repo=repo)
    remote = [{"id": "t1", "text": "new", "done": True, "updatedAt": 2000, "createdAt": 1000}]
    merged = client._merge_todos(remote)
    assert merged == 1
    assert repo._tasks[0]["title"] == "new"
    assert repo._tasks[0]["completed"] is True


def test_merge_todos_local_newer_skips():
    local = [{"id": "t1", "title": "local", "updated_at": 2000}]
    repo = _make_task_repo(local)
    client = _make_client(task_repo=repo)
    remote = [{"id": "t1", "text": "remote", "updatedAt": 1000, "createdAt": 1000}]
    merged = client._merge_todos(remote)
    assert merged == 0


def test_merge_todos_local_missing_adds():
    repo = _make_task_repo([])
    client = _make_client(task_repo=repo)
    remote = [{"id": "t1", "text": "new", "done": False, "updatedAt": 2000, "createdAt": 1000}]
    merged = client._merge_todos(remote)
    assert merged == 1
    assert repo._tasks[0]["id"] == "t1"


# ── _merge_settings ──


def test_merge_settings_only_whitelist():
    repo = _make_settings_repo({"ui_theme": "light"})
    client = _make_client(settings_repo=repo)
    remote = {"ui_theme": "dark", "llm_api_key": "hacked"}
    merged = client._merge_settings(remote)
    assert merged == 1
    assert repo._data["ui_theme"] == "dark"
    assert "llm_api_key" not in repo._data


def test_merge_settings_no_repo_returns_zero():
    client = _make_client(settings_repo=None)
    assert client._merge_settings({"ui_theme": "dark"}) == 0


def test_merge_settings_non_dict_returns_zero():
    repo = _make_settings_repo({})
    client = _make_client(settings_repo=repo)
    assert client._merge_settings("not a dict") == 0
    assert client._merge_settings(None) == 0
    assert client._merge_settings([]) == 0


# ── get_status ──


def test_get_status_initial_state():
    client = _make_client()
    status = client.get_status()
    assert status["configured"] is True
    assert status["device_id"] == "desktop-test"
    assert status["worker_url"] == "https://sync.example.workers.dev"
    assert status["last_sync_at"] == 0.0
    assert status["last_sync_ok"] is False
    assert status["cursor"] == 0
    assert status["push_count"] == 0
    assert status["pull_count"] == 0
    assert status["loop_running"] is False


# ── register_device (mock HTTP) ──


@pytest.mark.asyncio
async def test_register_device_success():
    client = _make_client()
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value="ok")
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_ctx)
    mock_session.closed = False

    client._session = mock_session
    ok = await client.register_device("My Desktop")
    assert ok is True


@pytest.mark.asyncio
async def test_register_device_invalid_secret_returns_false():
    client = _make_client()
    mock_resp = AsyncMock()
    mock_resp.status = 401
    mock_resp.text = AsyncMock(return_value="unauthorized")
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_ctx)
    mock_session.closed = False
    client._session = mock_session

    with pytest.raises(CloudSyncAuthError):
        await client.register_device()


@pytest.mark.asyncio
async def test_register_device_403_invalid_register_secret():
    """Worker 对无效 register_secret 返回 403（非 401），客户端应同样抛 CloudSyncAuthError。"""
    client = _make_client()
    mock_resp = AsyncMock()
    mock_resp.status = 403
    mock_resp.text = AsyncMock(return_value='{"error":"invalid register_secret"}')
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_ctx)
    mock_session.closed = False
    client._session = mock_session

    with pytest.raises(CloudSyncAuthError):
        await client.register_device()


@pytest.mark.asyncio
async def test_register_device_missing_secret_raises():
    client = _make_client(register_secret="")
    with pytest.raises(CloudSyncConfigError):
        await client.register_device()


@pytest.mark.asyncio
async def test_register_device_not_configured_raises():
    client = _make_client(worker_url="")
    with pytest.raises(CloudSyncConfigError):
        await client.register_device()


# ── push_local_data (mock HTTP) ──


@pytest.mark.asyncio
async def test_push_local_data_success():
    note_repo = _make_note_repo([{"id": "n1", "title": "note"}])
    task_repo = _make_task_repo([{"id": "t1", "title": "task"}])
    settings_repo = _make_settings_repo({"ui_theme": "dark"})
    client = _make_client(note_repo=note_repo, task_repo=task_repo, settings_repo=settings_repo)

    # mock _upload_resource 全部成功
    client._upload_resource = AsyncMock(return_value=True)
    ok = await client.push_local_data()
    assert ok is True
    assert client._push_count == 1
    # P1-4: push 不再用本地钟推进游标（Worker SQL 已按 device_id 过滤自己）
    assert client._cursor == 0
    # 3 个资源都应被上传
    assert client._upload_resource.call_count == 3


@pytest.mark.asyncio
async def test_push_local_data_partial_failure():
    client = _make_client(note_repo=_make_note_repo([{"id": "n1"}]))
    # notes 成功，todos 失败，settings 成功
    client._upload_resource = AsyncMock(side_effect=[True, False, True])
    ok = await client.push_local_data()
    assert ok is False


@pytest.mark.asyncio
async def test_push_local_data_not_configured_raises():
    client = _make_client(worker_url="")
    with pytest.raises(CloudSyncConfigError):
        await client.push_local_data()


# ── pull_remote_data (mock HTTP) ──


@pytest.mark.asyncio
async def test_pull_remote_data_empty_items():
    client = _make_client()
    client._download = AsyncMock(return_value=[])
    ok = await client.pull_remote_data()
    assert ok is True
    assert client._pull_count == 1
    assert client._last_sync_ok is True


@pytest.mark.asyncio
async def test_pull_remote_data_merges_notes():
    note_repo = _make_note_repo([])
    client = _make_client(note_repo=note_repo)

    # 构造加密的 notes 数据
    notes_data = [{"id": "n1", "title": "remote note", "updatedAt": 2000, "createdAt": 1000}]
    encrypted = encrypt_data(json.dumps(notes_data), "test-token", "desktop-test")
    items = [{"resource": RESOURCE_NOTES, "data_encrypted": encrypted, "updated_at": 2000}]

    client._download = AsyncMock(return_value=items)
    ok = await client.pull_remote_data()
    assert ok is True
    assert len(note_repo._notes) == 1
    assert note_repo._notes[0]["title"] == "remote note"
    assert client._cursor == 2000


@pytest.mark.asyncio
async def test_pull_remote_data_decrypt_failure_skips_item():
    client = _make_client()
    items = [
        {"resource": RESOURCE_NOTES, "data_encrypted": "!!!invalid!!!", "updated_at": 1000},
        {"resource": RESOURCE_NOTES, "data_encrypted": "", "updated_at": 2000},
    ]
    client._download = AsyncMock(return_value=items)
    ok = await client.pull_remote_data()
    # V2.1 批次3（P1-1）：解密失败条目必须重试而非静默跳过 → 整体失败 + 游标暂停
    assert ok is False
    assert client._cursor == 0  # 游标未越过失败条目（下次 pull 重试）
    assert client._pull_failures == 1  # 失败累计（死信告警依据）


# ── B-01: merge/collect 必须 to_thread（不阻塞事件循环）──


@pytest.mark.asyncio
async def test_pull_merge_does_not_block_event_loop_b01():
    """B-01: pull 的 merge 必须经 asyncio.to_thread 执行。

    慢 repo：list_notes / update_note 各 sleep 50ms。若 merge 直接跑事件循环，
    单条笔记合并 = list(50ms) + update(50ms) ≈ 100ms 同步阻塞，会反映在
    「并发心跳任务」的最大间隔上；to_thread 化后事件循环空闲，心跳保持亚 50ms。
    """
    import asyncio
    import time

    note_repo = SimpleNamespace()
    note_repo._notes = []

    def _slow_list(include_deleted=False):
        time.sleep(0.05)
        return [{"id": "n1", "title": "t", "content": "c", "created_at": 1000, "updated_at": 1000}]

    def _slow_update(nid, data):
        time.sleep(0.05)
        _update_in_list(note_repo._notes, nid, data)

    note_repo.list_notes = _slow_list
    note_repo.update_note = _slow_update

    client = _make_client(note_repo=note_repo)
    notes_data = [{"id": "n1", "title": "remote", "updatedAt": 2000, "createdAt": 1000}]
    encrypted = encrypt_data(json.dumps(notes_data), "test-token", "desktop-test")
    items = [{"resource": RESOURCE_NOTES, "data_encrypted": encrypted, "updated_at": 2000}]
    client._download = AsyncMock(return_value=items)

    loop = asyncio.get_running_loop()
    max_gap = 0.0
    stop = asyncio.Event()

    async def heartbeat():
        nonlocal max_gap
        prev = loop.time()
        while not stop.is_set():
            await asyncio.sleep(0.005)
            now = loop.time()
            if now - prev > max_gap:
                max_gap = now - prev
            prev = now

    hb = asyncio.create_task(heartbeat())
    try:
        ok = await client.pull_remote_data()
        assert ok is True
        assert len(note_repo._notes) == 1
    finally:
        stop.set()
        await hb

    # 修复前（merge 同步阻塞）：心跳间隔 ≥ ~100ms；修复后（to_thread）：< 80ms
    assert max_gap < 0.08, f"pull 期间事件循环最大延迟 {max_gap * 1000:.0f}ms ≥ 80ms （merge 的 SQLite I/O 阻塞了事件循环，违反 to_thread 铁律）"


@pytest.mark.asyncio
async def test_pull_remote_data_auth_error_propagates():
    client = _make_client()
    client._download = AsyncMock(side_effect=CloudSyncAuthError("bad token"))
    with pytest.raises(CloudSyncAuthError):
        await client.pull_remote_data()


# ── sync_once ──


@pytest.mark.asyncio
async def test_sync_once_success():
    client = _make_client()
    client.push_local_data = AsyncMock(return_value=True)
    client.pull_remote_data = AsyncMock(return_value=True)
    ok = await client.sync_once()
    assert ok is True
    assert client._last_sync_ok is True
    assert client._last_error is None
    assert client._last_sync_at > 0


@pytest.mark.asyncio
async def test_sync_once_success_emits_completed_event():
    client = _make_client()
    client.push_local_data = AsyncMock(return_value=True)
    client.pull_remote_data = AsyncMock(return_value=True)
    client._push_count = 2
    client._pull_count = 3
    client._cursor = 1234

    with patch("sync.cloud_sync_client.event_bus.emit") as mock_emit:
        ok = await client.sync_once()

    assert ok is True
    event = mock_emit.call_args.args[0]
    assert event.event_type == "sync.completed"
    assert event.payload == {
        "device_id": "desktop-test",
        "push_ok": True,
        "pull_ok": True,
        "push_count": 2,
        "pull_count": 3,
        "cursor": 1234,
    }


@pytest.mark.asyncio
async def test_sync_once_push_fails():
    client = _make_client()
    client.push_local_data = AsyncMock(return_value=False)
    client.pull_remote_data = AsyncMock(return_value=True)
    ok = await client.sync_once()
    assert ok is False
    assert client._last_sync_ok is False


@pytest.mark.asyncio
async def test_sync_once_partial_failure_emits_failed_event():
    client = _make_client()
    client.push_local_data = AsyncMock(return_value=False)
    client.pull_remote_data = AsyncMock(return_value=True)
    client._push_count = 1
    client._pull_count = 4
    client._cursor = 4321

    with patch("sync.cloud_sync_client.event_bus.emit") as mock_emit:
        ok = await client.sync_once()

    assert ok is False
    event = mock_emit.call_args.args[0]
    assert event.event_type == "sync.failed"
    assert event.payload == {
        "device_id": "desktop-test",
        "push_ok": False,
        "pull_ok": True,
        "push_count": 1,
        "pull_count": 4,
        "cursor": 4321,
    }


@pytest.mark.asyncio
async def test_sync_once_not_configured_returns_false():
    client = _make_client(worker_url="")
    ok = await client.sync_once()
    assert ok is False


@pytest.mark.asyncio
async def test_sync_once_auth_error_stops_loop():
    client = _make_client()
    client.push_local_data = AsyncMock(side_effect=CloudSyncAuthError("bad"))
    mock_task = MagicMock()
    mock_task.done.return_value = False
    client._sync_task = mock_task
    with patch("sync.cloud_sync_client.event_bus.emit") as mock_emit:
        ok = await client.sync_once()
    assert ok is False
    assert client._last_error is not None
    # stop_sync_loop 会 cancel 后置 None，所以检查捕获的 mock 引用
    mock_task.cancel.assert_called_once()
    event = mock_emit.call_args.args[0]
    assert event.event_type == "sync.failed"
    assert event.payload["device_id"] == "desktop-test"
    assert event.payload["error"] == "bad"
    assert event.payload["fatal"] is True


# ── P2-5-A：426 版本协商 → CloudSyncVersionError fatal 化 ──


def _mock_session_status(client, status: int, body: str = ""):
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.text = AsyncMock(return_value=body)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_ctx)
    mock_session.get = MagicMock(return_value=mock_ctx)
    mock_session.closed = False
    client._session = mock_session


@pytest.mark.asyncio
async def test_upload_426_raises_version_error():
    client = _make_client()
    _mock_session_status(client, 426, '{"code":"SYNC_VERSION_TOO_OLD"}')
    with pytest.raises(CloudSyncVersionError):
        await client._upload_resource(RESOURCE_NOTES, {"items": []})


@pytest.mark.asyncio
async def test_upload_500_still_returns_false_not_fatal():
    """非 401/426 的失败仍走旧「返回 False」路径——不升级为 fatal。"""
    client = _make_client()
    _mock_session_status(client, 500, "boom")
    ok = await client._upload_resource(RESOURCE_NOTES, {"items": []})
    assert ok is False


@pytest.mark.asyncio
async def test_download_426_raises_version_error():
    client = _make_client()
    _mock_session_status(client, 426, '{"code":"SYNC_VERSION_TOO_OLD"}')
    with pytest.raises(CloudSyncVersionError):
        await client._download(0)


@pytest.mark.asyncio
async def test_sync_once_version_error_stops_loop_and_is_fatal():
    client = _make_client()
    client.push_local_data = AsyncMock(side_effect=CloudSyncVersionError("version too old"))
    mock_task = MagicMock()
    mock_task.done.return_value = False
    client._sync_task = mock_task
    with patch("sync.cloud_sync_client.event_bus.emit") as mock_emit:
        ok = await client.sync_once()
    assert ok is False
    assert client._last_error is not None and client._last_error.startswith("version:")
    mock_task.cancel.assert_called_once()
    event = mock_emit.call_args.args[0]
    assert event.event_type == "sync.failed"
    assert event.payload["fatal"] is True
    assert event.payload["reason"] == "version_too_old"
    assert event.payload["cursor"] == client._cursor  # 游标不推进


# ── sync loop 生命周期 ──


@pytest.mark.asyncio
async def test_pull_same_second_items_both_reachable_b09():
    """B-09: worker `>= since` 语义下，同秒两条目两次 pull 均可达。

    场景（audit-report B-09）：设备 A 在 T 秒 push notes、设备 B 同 T 秒 push
    todos。桌面第一次 pull(since=0) 拿到 A（cursor=T）；第二次 pull(since=T)
    在 `>=` 语义下仍返回同秒条目 → B todos 必须可达（原 `>` 会被永久跳过）。
    客户端按 (resource, device_id) 去重 + LWW 幂等，重拉的 A 不重复合并。
    """
    note_repo = _make_note_repo([])
    task_repo = _make_task_repo([])
    client = _make_client(note_repo=note_repo, task_repo=task_repo)

    # T 秒两条目：A 的 notes、B 的 todos（worker 返回带 device_id）
    T = 1718900000
    notes_data = [{"id": "n1", "title": "A note", "updatedAt": T, "createdAt": T}]
    todos_data = [{"id": "t1", "text": "B todo", "done": False, "updatedAt": T, "createdAt": T}]
    enc_notes = encrypt_data(json.dumps(notes_data), "test-token", "desktop-test")
    enc_todos = encrypt_data(json.dumps(todos_data), "test-token", "desktop-test")

    # 第一次 pull：只返回 A 的 notes（模拟 A 先 push）
    client._download = AsyncMock(
        return_value=[
            {"resource": RESOURCE_NOTES, "device_id": "device-A", "data_encrypted": enc_notes, "updated_at": T},
        ]
    )
    ok1 = await client.pull_remote_data()
    assert ok1 is True
    assert len(note_repo._notes) == 1
    assert client._cursor == T, "第一次 pull 游标推进到最后成功条目 T"

    # 第二次 pull：worker `>= T` 返回 A notes（重拉）+ B todos（同秒新增）
    client._download = AsyncMock(
        return_value=[
            {"resource": RESOURCE_NOTES, "device_id": "device-A", "data_encrypted": enc_notes, "updated_at": T},
            {"resource": RESOURCE_TODOS, "device_id": "device-B", "data_encrypted": enc_todos, "updated_at": T},
        ]
    )
    ok2 = await client.pull_remote_data()
    assert ok2 is True
    # B todos 必须可达（原 `>` 会在此处永久跳过 → B 丢失）
    assert len(task_repo._tasks) == 1, "同秒第二条目（B todos）必须被拉到并合并"
    assert task_repo._tasks[0]["title"] == "B todo"
    # A notes 重拉被 LWW 幂等吸收：不重复、不倒退
    assert len(note_repo._notes) == 1, "重拉的 A notes 不得重复合并"
    assert note_repo._notes[0]["title"] == "A note"
    assert client._cursor == T, "游标不得后退（同秒条目不推进游标）"


@pytest.mark.asyncio
async def test_start_sync_loop_creates_task():
    client = _make_client()
    client.start_sync_loop(interval_seconds=3600)
    assert client._sync_task is not None
    assert not client._sync_task.done()
    client.stop_sync_loop()
    assert client._sync_task is None or client._sync_task.done()


@pytest.mark.asyncio
async def test_start_sync_loop_idempotent():
    client = _make_client()
    client.start_sync_loop(interval_seconds=3600)
    task1 = client._sync_task
    client.start_sync_loop(interval_seconds=3600)
    assert client._sync_task is task1  # 不应创建新任务
    client.stop_sync_loop()


def test_stop_sync_loop_without_task():
    client = _make_client()
    client.stop_sync_loop()  # 不应抛异常
    assert client._sync_task is None


@pytest.mark.asyncio
async def test_close_releases_session():
    client = _make_client()
    mock_session = AsyncMock()
    mock_session.closed = False
    mock_session.close = AsyncMock()
    client._session = mock_session
    await client.close()
    mock_session.close.assert_called_once()
    assert client._session is None


@pytest.mark.asyncio
async def test_close_when_no_session():
    client = _make_client()
    await client.close()  # 不应抛异常
    assert client._session is None


# ── P1-4 回归：游标单位 / 持久化 / 时钟偏移防御 ──


@pytest.mark.asyncio
async def test_push_does_not_advance_cursor_p1_4():
    """P1-4: push 不再用本地钟推进 _cursor。

    旧逻辑会把 _cursor 设为 _now_seconds()，本地超前 Worker 时会跳过其他
    设备在 [worker_now, local_now] 区间内的更新；落后时会反复拉取已合并的
    条目。Worker SQL 已经按 device_id 过滤掉自己，没必要本地推进。
    """
    client = _make_client(initial_cursor=1700000000)
    client._upload_resource = AsyncMock(return_value=True)
    before = client._cursor
    ok = await client.push_local_data()
    assert ok is True
    # 游标不变
    assert client._cursor == before == 1700000000


@pytest.mark.asyncio
async def test_pull_advances_cursor_to_worker_updated_at_p1_4():
    """P1-4: 游标只在 pull 时由 Worker 返回的 max(updated_at) 推进。"""
    note_repo = _make_note_repo([])
    client = _make_client(note_repo=note_repo)
    encrypted = encrypt_data(json.dumps([{"id": "n1", "title": "x", "updatedAt": 1700001234}]), "test-token", "desktop-test")
    items = [
        # B-09：worker 主键 = (device_id, resource)，同 resource 不同 device 各自返回
        {"resource": RESOURCE_NOTES, "device_id": "device-A", "data_encrypted": encrypted, "updated_at": 1700001100},
        {"resource": RESOURCE_NOTES, "device_id": "device-B", "data_encrypted": encrypted, "updated_at": 1700001234},
    ]
    client._download = AsyncMock(return_value=items)
    ok = await client.pull_remote_data()
    assert ok is True
    assert client._cursor == 1700001234


@pytest.mark.asyncio
async def test_pull_normalizes_millisecond_updated_at_p1_4():
    """P1-4: 若 Worker 异常返回毫秒级 updated_at，应折算回秒，避免下次 since 偏大。"""
    note_repo = _make_note_repo([])
    client = _make_client(note_repo=note_repo)
    encrypted = encrypt_data(json.dumps([{"id": "n1"}]), "test-token", "desktop-test")
    # 1700001234000 ms = 1700001234 s
    items = [{"resource": RESOURCE_NOTES, "data_encrypted": encrypted, "updated_at": 1700001234000}]
    client._download = AsyncMock(return_value=items)
    await client.pull_remote_data()
    assert client._cursor == 1700001234


@pytest.mark.asyncio
async def test_pull_does_not_regress_cursor_p1_4():
    """P1-4: 若 Worker 返回的 max(updated_at) <= 当前 _cursor，游标不应倒退。"""
    note_repo = _make_note_repo([])
    client = _make_client(note_repo=note_repo, initial_cursor=2000)
    encrypted = encrypt_data(json.dumps([{"id": "n1"}]), "test-token", "desktop-test")
    items = [{"resource": RESOURCE_NOTES, "data_encrypted": encrypted, "updated_at": 1500}]
    client._download = AsyncMock(return_value=items)
    await client.pull_remote_data()
    # max_updated 起始为 since=2000，远端 1500 不会替换它
    assert client._cursor == 2000


@pytest.mark.asyncio
async def test_pull_persists_cursor_via_callback_p1_4():
    """P1-4: 游标推进时通过 cursor_persister 回调落盘。"""
    saved: list[int] = []
    note_repo = _make_note_repo([])
    client = CloudSyncClient(
        worker_url="https://sync.example.workers.dev",
        sync_token="test-token",
        device_id="desktop-test",
        register_secret="reg",
        note_repo=note_repo,
        initial_cursor=0,
        cursor_persister=saved.append,
    )
    encrypted = encrypt_data(json.dumps([{"id": "n1"}]), "test-token", "desktop-test")
    items = [{"resource": RESOURCE_NOTES, "data_encrypted": encrypted, "updated_at": 1700002000}]
    client._download = AsyncMock(return_value=items)
    await client.pull_remote_data()
    assert saved == [1700002000]

    # 再次 pull 但无新数据 → 游标不变 → 不再重复落盘
    client._download = AsyncMock(return_value=[])
    await client.pull_remote_data()
    assert saved == [1700002000]  # 仍只 1 次


@pytest.mark.asyncio
async def test_pull_persister_failure_does_not_break_sync_p1_4():
    """P1-4: cursor_persister 抛异常不应中断同步。"""

    def bad_persister(value: int) -> None:
        raise RuntimeError("disk full")

    note_repo = _make_note_repo([])
    client = CloudSyncClient(
        worker_url="https://sync.example.workers.dev",
        sync_token="test-token",
        device_id="desktop-test",
        register_secret="reg",
        note_repo=note_repo,
        cursor_persister=bad_persister,
    )
    encrypted = encrypt_data(json.dumps([{"id": "n1"}]), "test-token", "desktop-test")
    items = [{"resource": RESOURCE_NOTES, "data_encrypted": encrypted, "updated_at": 1700003000}]
    client._download = AsyncMock(return_value=items)
    ok = await client.pull_remote_data()
    assert ok is True
    assert client._cursor == 1700003000  # 内存游标仍推进


def test_initial_cursor_restored_from_settings_p1_4():
    """P1-4: SyncBridge.build_client 从 settings 读出 cursor 注入 client。"""
    from sync.cloud_sync_client import SyncBridge

    settings = _make_settings_repo(
        {
            "cloud_sync_worker_url": "https://sync.example.workers.dev",
            "cloud_sync_token": "test-token",
            "cloud_sync_cursor": 1700004000,
            "cloud_sync_device_id": "desktop-test",
        }
    )
    ctx = SimpleNamespace(
        settings_repository=settings,
        personal_note_repository=None,
        personal_task_repository=None,
    )
    bridge = SyncBridge(ctx)
    client = bridge.build_client()
    assert client is not None
    assert client._cursor == 1700004000


def test_initial_cursor_normalizes_legacy_millisecond_value_p1_4():
    """P1-4: 旧版若误把毫秒写入 settings，build_client 应归一为秒。"""
    from sync.cloud_sync_client import SyncBridge

    settings = _make_settings_repo(
        {
            "cloud_sync_worker_url": "https://sync.example.workers.dev",
            "cloud_sync_token": "test-token",
            # 旧代码 _now_seconds() 实际是秒，但若手机端 setCursor(Date.now()) 历史回写就是 ms
            "cloud_sync_cursor": 1700004000000,
            "cloud_sync_device_id": "desktop-test",
        }
    )
    ctx = SimpleNamespace(
        settings_repository=settings,
        personal_note_repository=None,
        personal_task_repository=None,
    )
    bridge = SyncBridge(ctx)
    client = bridge.build_client()
    assert client is not None
    assert client._cursor == 1700004000


@pytest.mark.asyncio
async def test_bridge_persister_writes_back_to_settings_p1_4():
    """P1-4: pull 推进游标后通过 SyncBridge._save_cursor 写回 settings。"""
    from sync.cloud_sync_client import SyncBridge

    settings = _make_settings_repo(
        {
            "cloud_sync_worker_url": "https://sync.example.workers.dev",
            "cloud_sync_token": "test-token",
            "cloud_sync_cursor": 0,
            "cloud_sync_device_id": "desktop-test",
        }
    )
    note_repo = _make_note_repo([])
    ctx = SimpleNamespace(
        settings_repository=settings,
        personal_note_repository=note_repo,
        personal_task_repository=None,
    )
    bridge = SyncBridge(ctx)
    client = bridge.build_client()
    assert client is not None

    encrypted = encrypt_data(json.dumps([{"id": "n1"}]), "test-token", "desktop-test")
    items = [{"resource": RESOURCE_NOTES, "data_encrypted": encrypted, "updated_at": 1700005555}]
    client._download = AsyncMock(return_value=items)
    await client.pull_remote_data()

    assert settings._data["cloud_sync_cursor"] == 1700005555


# ── B-18: reset_cursor 逃生舱（内存 + 持久层双清）──


def test_reset_cursor_clears_memory_and_persists_b18():
    """B-18: reset_cursor 必须同时清内存游标与持久层（运行中生效）。"""
    saved: list[int] = []
    client = _make_client(initial_cursor=42, cursor_persister=saved.append)
    assert client._cursor == 42

    result = client.reset_cursor()

    assert result == 0
    assert client._cursor == 0, "内存游标必须立即清零（运行中 client 下次 pull 以 since=0 全量重拉）"
    assert saved == [0], "持久层必须收到 0（settings cloud_sync_cursor 双清）"


def test_reset_cursor_without_persister_only_clears_memory_b18():
    """B-18: 无 cursor_persister 时 reset_cursor 仍清内存（不抛异常）。"""
    client = _make_client(initial_cursor=42)  # 未注入 persister
    result = client.reset_cursor()
    assert result == 0
    assert client._cursor == 0


# ── 独立推送函数与 v3/v4 加密协议（P0.1）──────────────────────────


def test_get_worker_config_env_only(monkeypatch):
    from sync import cloud_sync_client as csc

    monkeypatch.setenv("SIYI_WORKER_URL", "https://w.example/")
    monkeypatch.setenv("SIYI_SYNC_TOKEN", "tok")
    monkeypatch.setenv("SIYI_REGISTER_SECRET", "rs")
    assert csc._get_worker_config(None) == ("https://w.example/", "tok", "rs")


def test_get_worker_config_settings_override(monkeypatch):
    from sync import cloud_sync_client as csc

    monkeypatch.delenv("SIYI_WORKER_URL", raising=False)
    monkeypatch.delenv("SIYI_SYNC_TOKEN", raising=False)
    monkeypatch.delenv("SIYI_REGISTER_SECRET", raising=False)
    values = {
        "cloud_sync_worker_url": "https://s.example",
        "cloud_sync_token": "t2",
        "cloud_sync_register_secret": "r2",
    }
    settings = SimpleNamespace(get_setting=lambda k, d="": values.get(k, d))
    assert csc._get_worker_config(settings) == ("https://s.example", "t2", "r2")


def test_get_worker_config_settings_error_falls_back_to_env(monkeypatch):
    from sync import cloud_sync_client as csc

    monkeypatch.setenv("SIYI_WORKER_URL", "https://env.example")
    monkeypatch.delenv("SIYI_SYNC_TOKEN", raising=False)
    monkeypatch.delenv("SIYI_REGISTER_SECRET", raising=False)
    broken = SimpleNamespace(get_setting=MagicMock(side_effect=RuntimeError("boom")))
    assert csc._get_worker_config(broken) == ("https://env.example", "", "")


def test_derive_sync_group_id_stable_and_scoped():
    from sync import cloud_sync_client as csc

    assert csc._derive_sync_group_id("") == ""
    gid = csc._derive_sync_group_id("secret-a")
    assert len(gid) == 16
    assert gid == csc._derive_sync_group_id("secret-a")  # 同 secret 稳定
    assert gid != csc._derive_sync_group_id("secret-b")  # 跨 secret 隔离


def test_group_salt_prefix_and_group():
    from sync import cloud_sync_client as csc

    salt = csc._group_salt("abc123")
    assert salt.startswith(csc.CRYPTO_SALT_V4_PREFIX)
    assert salt.endswith("abc123")


def test_encrypt_v4_roundtrip_and_cross_group_rejected():
    from sync import cloud_sync_client as csc

    gid = csc._derive_sync_group_id("register-secret-1")
    ct = csc._encrypt_v4("token-x", "hello v4", gid)
    assert csc.decrypt_data(ct, "token-x", "desktop-any", sync_group_id=gid) == "hello v4"
    # 跨组（不同 group_id）派生不同 salt，应解不开
    with pytest.raises(csc.CloudSyncCryptoError):
        csc.decrypt_data(ct, "token-x", "desktop-any", sync_group_id="other-group")


def test_encrypt_v3_roundtrip(monkeypatch):
    from sync import cloud_sync_client as csc

    monkeypatch.setenv("SIYI_DEVICE_ID", "desktop-test")
    ct = csc._encrypt_v3("token-y", "hello v3")
    assert csc.decrypt_data(ct, "token-y", "desktop-test") == "hello v3"


def test_decrypt_data_error_paths():
    from sync import cloud_sync_client as csc

    with pytest.raises(csc.CloudSyncCryptoError):
        csc.decrypt_data("!!!not-base64!!!", "t", "d")
    with pytest.raises(csc.CloudSyncCryptoError):
        csc.decrypt_data("", "t", "d")  # 空密文
    # v4 信封但缺 sync_group_id
    import base64 as _b64

    envelope = bytes([csc.VERSION_BYTE_V4]) + b"\x00" * 20
    with pytest.raises(csc.CloudSyncCryptoError):
        csc.decrypt_data(_b64.b64encode(envelope).decode(), "t", "d")
    # v4 信封过短
    with pytest.raises(csc.CloudSyncCryptoError):
        csc.decrypt_data(
            _b64.b64encode(bytes([csc.VERSION_BYTE_V4]) + b"\x01\x02").decode(),
            "t",
            "d",
            sync_group_id="g",
        )


def test_normalize_ts_string_branches():
    from sync.cloud_sync_client import _normalize_ts

    assert _normalize_ts(None) == 0.0
    assert _normalize_ts("") == 0.0
    assert _normalize_ts("   ") == 0.0
    assert _normalize_ts("not-a-date") == 0.0
    assert _normalize_ts("1719064800") == 1719064800.0
    assert _normalize_ts("1719064800000") == 1719064800.0  # 毫秒字符串
    iso_z = _normalize_ts("2026-06-22T12:00:00Z")
    iso_naive = _normalize_ts("2026-06-22T12:00:00")
    assert iso_z > 0
    assert iso_z == iso_naive  # naive 按 UTC 处理


async def test_push_info_brief_standalone_not_configured(monkeypatch):
    from sync import cloud_sync_client as csc

    monkeypatch.delenv("SIYI_WORKER_URL", raising=False)
    monkeypatch.delenv("SIYI_SYNC_TOKEN", raising=False)
    assert await csc.push_info_brief_standalone("overview", {}, "full", 0) is False


async def test_push_info_sections_standalone_not_configured(monkeypatch):
    from sync import cloud_sync_client as csc

    monkeypatch.delenv("SIYI_WORKER_URL", raising=False)
    monkeypatch.delenv("SIYI_SYNC_TOKEN", raising=False)
    assert await csc.push_info_sections_standalone([{"name": "x"}]) is False


async def test_push_standalone_missing_config_returns_false():
    from sync import cloud_sync_client as csc

    assert await csc._push_standalone("", "", RESOURCE_NOTES, {}) is False
    assert await csc._push_standalone("https://w.example", "", RESOURCE_NOTES, {}) is False


async def test_push_standalone_network_failure_returns_false(monkeypatch):
    from sync import cloud_sync_client as csc

    # 指向不可达地址：捕获异常后返回 False（不抛出）
    ok = await csc._push_standalone(
        "http://127.0.0.1:1",
        "tok",
        RESOURCE_NOTES,
        {"k": "v"},
    )
    assert ok is False


async def test_push_standalone_sends_client_version_t7(monkeypatch):
    """T7 缺口 2：standalone 推送 payload 必须携带 client_version。

    Worker 对缺失/过低 client_version 的 upload 恒 426（SYNC_VERSION_TOO_OLD）；
    本测试捕获实际 POST 的 payload，断言版本字段存在、格式合法、resource 键名
    与 AES-GCM v4 信封可解（同组互解约定）。
    """
    import json as _json
    import re as _re

    import aiohttp as _aiohttp
    from sync import cloud_sync_client as csc

    captured = {}

    class _FakeResp:
        status = 200

        async def text(self):
            return ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, json=None, **kwargs):
            captured["url"] = url
            captured["payload"] = json
            return _FakeResp()

    monkeypatch.setattr(_aiohttp, "ClientSession", _FakeSession)

    ok = await csc._push_standalone(
        "https://w.example",
        "tok",
        csc.RESOURCE_INFO_BRIEF,
        {"k": "v"},
        register_secret="reg-secret",
    )
    assert ok is True
    payload = captured["payload"]
    # 缺口 2 主断言：client_version 必须随 standalone upload 上报（否则 426）
    assert payload["client_version"] == csc.SYNC_CLIENT_VERSION
    # 版本格式须满足 Worker isVersionAtLeast 的语义化解析（1 / 1.2 / 1.2.3）
    assert _re.fullmatch(r"\d+(\.\d+){0,2}", csc.SYNC_CLIENT_VERSION)
    # 类型键名约定：resource 使用 RESOURCE_INFO_* 键名（与 Worker 白名单一致）
    assert payload["resource"] == csc.RESOURCE_INFO_BRIEF
    assert payload["checksum"] == csc._sha256_hex(_json.dumps({"k": "v"}, ensure_ascii=False))
    # AES-GCM v4 信封约定：同组（同 register_secret 派生 group_id）可解
    gid = csc._derive_sync_group_id("reg-secret")
    plain = csc.decrypt_data(payload["data_encrypted"], "tok", "desktop-x", sync_group_id=gid)
    assert _json.loads(plain) == {"k": "v"}


# ── SyncBridge（管理器入口）测试：拆分前安全网（T1.3）──
# 覆盖 build_client / is_enabled / is_configured / get_status 的纯逻辑分支，
# 不依赖真实网络。SyncBridge 是后续拆分子模块时的顶层入口，必须先有测试网。


class _FakeSettingsRepo:
    def __init__(self, data: dict | None = None):
        self._data = dict(data or {})

    def get_setting(self, key, default=None):
        return self._data.get(key, default)

    def set_setting(self, key, value):
        self._data[key] = value


def _make_bridge(settings: dict | None = None):
    ctx = SimpleNamespace(settings_repository=_FakeSettingsRepo(settings))
    return SyncBridge(ctx)


def test_sync_bridge_disabled_by_default():
    bridge = _make_bridge()
    assert bridge.is_enabled() is False
    assert bridge.is_configured() is False


def test_sync_bridge_enabled_flag():
    bridge = _make_bridge({"cloud_sync_enabled": True})
    assert bridge.is_enabled() is True


def test_sync_bridge_build_client_returns_none_when_unconfigured():
    # 缺少 worker_url 或 token → build_client 返回 None（不抛异常）
    bridge = _make_bridge({"cloud_sync_enabled": True})
    assert bridge.build_client() is None


def test_sync_bridge_build_client_returns_client_when_configured():
    settings = {
        "cloud_sync_enabled": True,
        "cloud_sync_worker_url": "https://sync.example.com",
        "cloud_sync_token": "tok-123",
        "cloud_sync_register_secret": "sec",
    }
    bridge = _make_bridge(settings)
    client = bridge.build_client()
    assert isinstance(client, CloudSyncClient)
    assert client.is_configured() is True


def test_sync_bridge_get_status_before_start():
    bridge = _make_bridge({"cloud_sync_enabled": True})
    status = bridge.get_status()
    assert set(status.keys()) >= {"enabled", "configured", "running", "message"}
    assert status["running"] is False
    assert status["enabled"] is True
