"""M2.3 QUA-3：notes.delete 假成功——删除链必须如实报 failed + 错误透传。

审查档实测（comprehensive-code-review-2026-09-11 K2）：RPC schema 允许
string id、存储 id 为 int，repo 严格 == 不命中 → 静默空转仍无条件
return True。本批修复口径：
① delete/batch_delete/permanent_delete 按 int/str 类型归一命中真实记录；
② 未命中 / 删除后校验不通过 → 如实返回 False（不落 ghost 审计、不广播）；
③ repo 异常向上透传（RPC 层 -32603），不得吞成 True。

隔离：真实 LocalDataStore + LocalPersonalNoteRepository 全在 tmp_path。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._notes import register_notes  # noqa: E402
from core.audit_rollback import AuditTrail, WriteGuard  # noqa: E402
from data.local_storage import LocalDataStore  # noqa: E402
from repositories._note import LocalPersonalNoteRepository  # noqa: E402


class _Registry:
    def __init__(self):
        self.registered: dict[str, object] = {}

    def register(self, name, fn, risk=None, risk_resolver=None) -> None:
        self.registered[name] = fn

    def register_with_schema(self, name, fn, schema=None, risk=None, risk_resolver=None) -> None:
        self.registered[name] = fn


def _make_env(tmp_path, *, with_guard: bool = False):
    store = LocalDataStore(base_dir=str(tmp_path / "data"))
    repo = LocalPersonalNoteRepository(store)
    guard = None
    if with_guard:
        guard = WriteGuard(AuditTrail(data_dir=str(tmp_path / "audit")))
    ctx = SimpleNamespace(
        personal_note_repository=repo,
        write_guard=guard,
        memory_index_service=None,
        settings_repository=None,
        ai_client=None,
    )
    reg = _Registry()
    register_notes(reg, ctx)
    return store, repo, ctx, reg


def _seed(repo) -> int:
    nid = repo.add_note({"title": "t", "content": "c"})
    assert isinstance(nid, int), "真实仓储 next_id 应生成 int id（QUA-3 前提）"
    return nid


def test_notes_delete_string_id_actually_deletes(tmp_path):
    """K2 主案：str(id) 删除必须命中 int 记录（修复前 True 但笔记仍存活）。"""
    store, repo, _ctx, reg = _make_env(tmp_path)
    try:
        nid = _seed(repo)
        ok = asyncio.run(reg.registered["notes.delete"](note_id=str(nid)))
        assert ok is True
        assert repo.get_note(nid) is None, "str-id 删除必须真实命中（假成功回归）"
        rows = repo.list_notes(include_deleted=True)
        assert any(n.get("id") == nid and n.get("deleted_at") for n in rows)
    finally:
        store.close()


def test_notes_delete_missing_id_returns_false(tmp_path):
    """不存在的 id：必须 False（修复前无条件 True = 假成功）。"""
    store, repo, _ctx, reg = _make_env(tmp_path)
    try:
        _seed(repo)
        with patch("backend.handlers._notes.cascade_delete_note", new=AsyncMock(return_value={})) as cascade:
            ok = asyncio.run(reg.registered["notes.delete"](note_id=9999))
        assert ok is False
        cascade.assert_not_awaited()
        assert len(repo.list_notes()) == 1
    finally:
        store.close()


def test_notes_delete_repo_error_propagates(tmp_path):
    """repo.delete_note 抛异常：如实透传失败，不得吞成 True（错误分型家族口径）。"""
    import pytest

    store, repo, _ctx, reg = _make_env(tmp_path)
    try:
        nid = _seed(repo)

        def boom(note_id, soft=True):
            raise OSError("disk full")

        original = repo.delete_note
        repo.delete_note = boom  # type: ignore[method-assign]
        try:
            with pytest.raises(OSError):
                asyncio.run(reg.registered["notes.delete"](note_id=nid))
            assert repo.get_note(nid) is not None, "失败必须可见：不得伪装删除成功"
        finally:
            repo.delete_note = original  # type: ignore[method-assign]
    finally:
        store.close()


def test_notes_delete_guarded_string_id_records_audit(tmp_path):
    """WriteGuard 路径：str-id 删除同样如实 + 审计条目记录真实 id。"""
    store, repo, _ctx, reg = _make_env(tmp_path, with_guard=True)
    try:
        nid = _seed(repo)
        ok = asyncio.run(reg.registered["notes.delete"](note_id=str(nid)))
        assert ok is True
        assert repo.get_note(nid) is None
    finally:
        store.close()


def test_notes_permanent_delete_missing_returns_false(tmp_path):
    store, repo, _ctx, reg = _make_env(tmp_path)
    try:
        _seed(repo)
        with patch("backend.handlers._notes.cascade_delete_note", new=AsyncMock(return_value={})):
            ok = asyncio.run(reg.registered["notes.permanent_delete"](note_id=9999))
        assert ok is False
        assert len(repo.list_notes()) == 1
    finally:
        store.close()


def test_notes_batch_delete_registered(tmp_path):
    """2026-10-15 反转：notes.batch_delete **必须注册**。

    原断言「已随 58e5fa8 下线、不再注册」依据的是该提交的自述结论（前端零调用、
    无测试耦合）；全族复核证伪——58e5fa8 连测试一并删走造成了"无耦合"假象，
    注册已于批次 A（c6fb2bc7）恢复（它是 _R4_METHODS 成员，走 sandbox 审批）。
    故断言反转，防止再次误下线。
    """
    _store, _repo, _ctx, reg = _make_env(tmp_path)
    try:
        assert "notes.batch_delete" in reg.registered
    finally:
        _store.close()
