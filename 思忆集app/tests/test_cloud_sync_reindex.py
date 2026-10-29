"""Cloud sync memory reindex tests — 云同步合并后记忆索引对账（T5.4）。

覆盖：pull 合并远端笔记后触发 rebuild_index、无合并不触发、开关关闭不触发、
reindex 失败不阻塞同步主流程。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from sync.cloud_sync_client import CloudSyncClient, RESOURCE_NOTES, encrypt_data


def _make_note_repo(notes=None):
    repo = SimpleNamespace()
    repo._notes = list(notes or [])
    repo.list_notes = lambda include_deleted=False: list(repo._notes)
    repo.add_note = lambda data: repo._notes.append(data)
    repo.update_note = lambda nid, data: _update_in_list(repo._notes, nid, data)
    return repo


def _update_in_list(lst, nid, data):
    for i, item in enumerate(lst):
        if item.get("id") == nid:
            lst[i] = {**item, **data}
            return True
    return False


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


class _FakeMemoryIndex:
    """记录 rebuild_index 调用次数。"""

    def __init__(self):
        self.rebuild_calls = 0

    def rebuild_index(self):
        self.rebuild_calls += 1
        return {"entries": [{"entry_id": "note:n1"}], "built_at": "now"}


def _encrypted_note_data(note_id: str, title: str, ts: float) -> str:
    """构造一个经加密的 notes 远端数据（与 pull_remote_data 解密路径一致）。"""
    note_repo = _make_note_repo()
    client = _make_client(note_repo=note_repo)
    # 用测试 client 加密；pull 侧用相同 token/device 解密
    return encrypt_data(
        json.dumps([{"id": note_id, "title": title, "updated_at": ts, "content": "sync test"}]),
        "test-token", "desktop-test", client.sync_group_id,
    )


@pytest.mark.asyncio
async def test_pull_merges_and_reindexes(tmp_path: Path) -> None:
    """pull 合并远端笔记后应触发 rebuild_index。"""
    mi = _FakeMemoryIndex()
    client = _make_client(
        note_repo=_make_note_repo(),
        memory_index=mi,
    )

    remote_item = {
        "resource": RESOURCE_NOTES,
        "data_encrypted": _encrypted_note_data("n_sync", "来自远端的笔记", 2000.0),
        "updated_at": 2000,
    }
    with patch.object(client, "_download", new=AsyncMock(return_value=[remote_item])):
        ok = await client.pull_remote_data()

    assert ok is True
    assert mi.rebuild_calls == 1


@pytest.mark.asyncio
async def test_pull_no_changes_no_reindex(tmp_path: Path) -> None:
    """pull 无新数据（空 items）不应触发 rebuild_index。"""
    mi = _FakeMemoryIndex()
    client = _make_client(memory_index=mi)

    with patch.object(client, "_download", new=AsyncMock(return_value=[])):
        ok = await client.pull_remote_data()

    assert ok is True
    assert mi.rebuild_calls == 0


@pytest.mark.asyncio
async def test_pull_reindex_flag_off(tmp_path: Path) -> None:
    """reindex_after_pull=False 时即使有合并也不触发。"""
    mi = _FakeMemoryIndex()
    client = _make_client(
        note_repo=_make_note_repo(),
        memory_index=mi,
        reindex_after_pull=False,
    )

    remote_item = {
        "resource": RESOURCE_NOTES,
        "data_encrypted": _encrypted_note_data("n_sync2", "关闭开关", 2000.0),
        "updated_at": 2000,
    }
    with patch.object(client, "_download", new=AsyncMock(return_value=[remote_item])):
        ok = await client.pull_remote_data()

    assert ok is True
    assert mi.rebuild_calls == 0


@pytest.mark.asyncio
async def test_pull_reindex_failure_does_not_block(tmp_path: Path) -> None:
    """rebuild_index 抛异常不应让同步主流程失败。"""
    mi = _FakeMemoryIndex()

    def _boom():
        raise RuntimeError("chroma down")
        return {}

    mi.rebuild_index = _boom
    client = _make_client(
        note_repo=_make_note_repo(),
        memory_index=mi,
    )

    remote_item = {
        "resource": RESOURCE_NOTES,
        "data_encrypted": _encrypted_note_data("n_sync3", "索引故障", 2000.0),
        "updated_at": 2000,
    }
    with patch.object(client, "_download", new=AsyncMock(return_value=[remote_item])):
        ok = await client.pull_remote_data()

    assert ok is True  # 同步仍成功，reindex 失败被吞掉记 warning


@pytest.mark.asyncio
async def test_maybe_reindex_no_memory_index(tmp_path: Path) -> None:
    """未注入 memory_index 时 _maybe_reindex_after_pull 应安全返回。"""
    client = _make_client()
    await client._maybe_reindex_after_pull()  # 不应抛异常
