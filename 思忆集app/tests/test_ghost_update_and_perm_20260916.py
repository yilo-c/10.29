# -*- coding: utf-8 -*-
"""S0-1 ghost update 假成功 + S0-2 permanent_delete 三重死 回归测试（2026-09-16 批）。

背景（两条已定案缺陷，本轮修复）：

S0-1 ghost update「空写记成功」：更新不存在的 id 时——
  ① 仓储层 ``update_note`` 对未命中静默返回 None（无命中信号）；
  ② handler 无条件 ``return True``，还触发 WriteGuard 生成 before=None 的
     假审计条目并广播不存在的变更。实测 ``notes.update(note_id=99999999)``
     →True、``inbox.update(不存在 id)`` →``{"success":true}``。
  同文件 delete/restore 早已按 QUA-3 做存在性归一（``_resolve_note_id``），
  唯独 update 漏网。修法：入口先 ``_resolve_note_id`` 探活；``inbox_update``
  先 ``get_entry`` 探活；治本——``update_note`` 返回 bool（命中 True/未命中
  False，含运行时真实适配器 ``desknotes_note_adapter`` 的未知 uuid 分支）。

S0-2 permanent_delete 三重死：``_resolve_note_id`` 靠 ``get_note`` 探活，而
  运行时真实适配器 ``desknotes_note_adapter.py:352``（与 ``repositories/_note.py``
  同款）对 ``deleted_at`` 非空**故意**返回 None（防幽灵读，语义不可改）→
  回收站里的软删笔记 resolve 恒不命中 → ``notes.permanent_delete`` 恒 False。
  修法：``_resolve_note_id``/``_find_note_row`` 增 ``include_deleted`` 走
  ``list_notes(include_deleted=True)`` 直查；``notes_permanent_delete`` 以
  ``include_deleted=True`` 探活，且**只对软删笔记生效**（对活笔记拒绝——
  顺带修复修复前「唯一能删的是活笔记」的倒挂行为）。

断言均用真实 LocalDataStore + LocalPersonalNoteRepository（tmp_path 隔离，
绝不触碰真实数据目录）；ghost 断言额外核查「无新增审计条目」。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._inbox_crud import register_inbox_crud  # noqa: E402
from backend.handlers._notes import register_notes  # noqa: E402
from core.audit_rollback import AuditTrail, WriteGuard  # noqa: E402
from data._hot_tables import ensure_hot_tables  # noqa: E402
from data.local_storage import LocalDataStore  # noqa: E402
from repositories._inbox import LocalPersonalInboxRepository  # noqa: E402
from repositories._note import LocalPersonalNoteRepository  # noqa: E402


# ───────────────────────────── 公共 fixture ─────────────────────────────
# 真实 LocalDataStore + LocalPersonalNoteRepository 全在 tmp_path，
# 与 test_qua3_notes_delete.py 同款隔离。
class _Registry:
    def __init__(self) -> None:
        self.registered: dict[str, object] = {}

    def register(self, name, fn, risk=None, risk_resolver=None) -> None:
        self.registered[name] = fn

    def register_with_schema(self, name, fn, schema=None, risk=None, risk_resolver=None) -> None:
        self.registered[name] = fn


def _make_notes_env(tmp_path: Path, *, with_guard: bool = False):
    store = LocalDataStore(base_dir=str(tmp_path / "data"))
    repo = LocalPersonalNoteRepository(store)
    guard = None
    trail = None
    if with_guard:
        trail = AuditTrail(data_dir=str(tmp_path / "audit"))
        guard = WriteGuard(trail)
    ctx = SimpleNamespace(
        personal_note_repository=repo,
        write_guard=guard,
        memory_index_service=None,
        settings_repository=None,
        ai_client=None,
    )
    reg = _Registry()
    register_notes(reg, ctx)
    return store, repo, ctx, reg, trail


# ───────────────────────────── S0-1: ghost update ─────────────────────────────
def test_notes_update_missing_id_returns_false(tmp_path):
    """ghost update 主案：不存在 id 必须 False（修复前 True = 假成功）。"""
    store, repo, _ctx, reg, _trail = _make_notes_env(tmp_path)
    try:
        nid = repo.add_note({"title": "t", "content": "c"})
        assert isinstance(nid, int), "真实仓储 next_id 应生成 int id（QUA-3 前提）"
        # 未探到 → False；且列表长度不变（不得 ghost-write 出副本）
        ok = asyncio.run(reg.registered["notes.update"](note_id=99999999, content="ghost"))
        assert ok is False, "ghost update 不得再返回 True（S0-1 主案）"
        assert len(repo.list_notes(include_deleted=True)) == 1
        assert repo.get_note(nid)["content"] == "c", "真实笔记不得被 ghost 写污染"
    finally:
        store.close()


def test_notes_update_string_id_hits_real_row(tmp_path):
    """合法 str-id 更新必须真实命中（不得因探活改造反被误伤）。"""
    store, repo, _ctx, reg, _trail = _make_notes_env(tmp_path)
    try:
        nid = repo.add_note({"title": "t", "content": "c"})
        ok = asyncio.run(reg.registered["notes.update"](note_id=str(nid), content="hit"))
        assert ok is True, "合法 id 更新不得被 S0-1 探活误伤"
        assert repo.get_note(nid)["content"] == "hit"
    finally:
        store.close()


def test_notes_update_ghost_no_audit_entry(tmp_path):
    """ghost update 不得留下 WriteGuard before=None 的假审计条目（S0-1 副案）。"""
    store, repo, _ctx, reg, trail = _make_notes_env(tmp_path, with_guard=True)
    try:
        nid = repo.add_note({"title": "t", "content": "c"})
        # 记录基线条目数
        before = len(trail.list_entries(limit=1000))
        ok = asyncio.run(reg.registered["notes.update"](note_id=99999999, content="ghost"))
        assert ok is False
        after = len(trail.list_entries(limit=1000))
        assert after == before, "ghost update 不得触发 WriteGuard 落审计（S0-1：before=None 假条目家族）"
    finally:
        store.close()


def test_notes_update_guarded_real_hit_records_audit(tmp_path):
    """WriteGuard 路径命中真实笔记必须正常落审计（探活改造不得吞掉合法写）。"""
    store, repo, _ctx, reg, trail = _make_notes_env(tmp_path, with_guard=True)
    try:
        nid = repo.add_note({"title": "t", "content": "c"})
        before = len(trail.list_entries(limit=1000))
        ok = asyncio.run(reg.registered["notes.update"](note_id=str(nid), content="hit"))
        assert ok is True
        after = len(trail.list_entries(limit=1000))
        assert after > before, "合法写入必须有审计条目"
        # 且必须是命中真实笔记的那一条（before_snapshot 非 None）
        last = trail.list_entries(limit=1, action_id="notes.update")[-1]
        assert last.get("before_snapshot") is not None
    finally:
        store.close()


def test_local_repo_update_note_returns_bool(tmp_path):
    """治本：LocalPersonalNoteRepository.update_note 命中 True / 未命中 False。"""
    store = LocalDataStore(base_dir=str(tmp_path / "data"))
    repo = LocalPersonalNoteRepository(store)
    try:
        nid = repo.add_note({"title": "t", "content": "c"})
        hit = repo.update_note(nid, {"content": "x"})
        miss = repo.update_note(99999999, {"content": "y"})
        assert hit is True, "命中必须 True（协议扩展：bool 返回）"
        assert miss is False, "未命中必须 False（旧实现返回 None = ghost 假成功温床）"
        # 关键：False 分支绝不能污染存储
        assert repo.get_note(nid)["content"] == "x"
    finally:
        store.close()


def test_inbox_update_missing_returns_success_false(tmp_path):
    """inbox.update 不存在 id → {success:False, error:...}（修复前无条件 success:True）。"""
    store = LocalDataStore(base_dir=str(tmp_path / "data"))
    ensure_hot_tables(store._sqlite._connect())
    repo = LocalPersonalInboxRepository(store)
    ctx = SimpleNamespace(
        personal_inbox_repository=repo,
        settings_repository=None,
        ai_client=None,
        assistant_inbox_service=None,
    )
    reg = _Registry()
    register_inbox_crud(reg, ctx)
    try:
        out = asyncio.run(reg.registered["inbox.update"](entry_id=99999999, content="ghost"))
        assert out.get("success") is False, f"ghost inbox.update 不得再 success:True → {out}"
        assert "error" in out
        # 合法 id 更新必须仍 success（改造不误伤）
        eid = repo.add_entry({"title": "t", "content": "c"})
        ok = asyncio.run(reg.registered["inbox.update"](entry_id=eid, content="real"))
        assert ok.get("success") is True
        assert repo.get_entry(eid)["content"] == "real"
    finally:
        store.close()


# ───────────────────────────── S0-2: permanent_delete 三重死 ─────────────────────────────
def test_permanent_delete_soft_deleted_note_succeeds(tmp_path):
    """回收站中的软删笔记 permanent_delete 必须 True 并真硬删（修复前恒 False）。

    关键：真实 LocalPersonalNoteRepository.get_note 对 deleted_at 非空返回 None
    （防幽灵读，与运行时 desknotes_note_adapter.py:352 同款语义）——旧
    ``_resolve_note_id`` 走 get_note 探活 → 恒不命中 → 恒 False。
    """
    store, repo, _ctx, reg, _trail = _make_notes_env(tmp_path)
    try:
        nid = repo.add_note({"title": "t", "content": "c"})
        # 软删（notes.delete）：get_note 从此返回 None，list_notes(include_deleted=True) 命中
        asyncio.run(reg.registered["notes.delete"](note_id=nid))
        assert repo.get_note(nid) is None, "前置：软删后 get_note 应返回 None（幽灵读防护）"
        rows = repo.list_notes(include_deleted=True)
        assert any(n["id"] == nid and n.get("deleted_at") for n in rows)
        # 主案：permanent_delete 探活 include_deleted=True → 命中软删行 → 真硬删
        with patch("backend.handlers._notes.cascade_delete_note", new=AsyncMock(return_value={})):
            ok = asyncio.run(reg.registered["notes.permanent_delete"](note_id=str(nid)))
        assert ok is True, "S0-2 三重死：回收站笔记 permanent_delete 不得再恒 False"
        # 底层删除真的发生（list 全量已空）
        assert not repo.list_notes(include_deleted=True), "底层 delete_note(hard) 必须真实落盘"
    finally:
        store.close()


def test_permanent_delete_rejects_live_note(tmp_path):
    """活笔记（未进回收站）permanent_delete 必须 False（顺带修复旧倒挂行为）。"""
    store, repo, _ctx, reg, _trail = _make_notes_env(tmp_path)
    try:
        nid = repo.add_note({"title": "t", "content": "c"})
        with patch("backend.handlers._notes.cascade_delete_note", new=AsyncMock(return_value={})) as cascade:
            ok = asyncio.run(reg.registered["notes.permanent_delete"](note_id=nid))
        assert ok is False, "对活笔记 perm 拒绝——它没进回收站，语义错误"
        assert repo.get_note(nid) is not None, "活笔记不得被误硬删"
        cascade.assert_not_awaited()
    finally:
        store.close()


def test_permanent_delete_ghost_still_returns_false(tmp_path):
    """不存在的 id 走 include_deleted 探活也不得误命中。"""
    store, repo, _ctx, reg, _trail = _make_notes_env(tmp_path)
    try:
        _ = repo.add_note({"title": "t", "content": "c"})
        with patch("backend.handlers._notes.cascade_delete_note", new=AsyncMock(return_value={})):
            ok = asyncio.run(reg.registered["notes.permanent_delete"](note_id=99999999))
        assert ok is False
        assert len(repo.list_notes(include_deleted=True)) == 1
    finally:
        store.close()


def test_permanent_delete_string_id_hits_trashed(tmp_path):
    """str 传参 + 软删笔记：_resolve_note_id(int→str) 归一必须命中真实行。"""
    store, repo, _ctx, reg, _trail = _make_notes_env(tmp_path)
    try:
        nid = repo.add_note({"title": "t", "content": "c"})
        asyncio.run(reg.registered["notes.delete"](note_id=nid))
        with patch("backend.handlers._notes.cascade_delete_note", new=AsyncMock(return_value={})):
            # 关键：以 str 传，走 candidates [str, int(str)] 归一 → 命中 int id
            ok = asyncio.run(reg.registered["notes.permanent_delete"](note_id=str(nid)))
        assert ok is True
        assert not repo.list_notes(include_deleted=True)
    finally:
        store.close()


# ───────────────────────────── 结构层：bool 契约 ─────────────────────────────
def test_desknotes_adapter_update_note_returns_bool(tmp_path):
    """运行时真实适配器（desknotes_note_adapter）：未知 uuid 必须 False（S0-1 治本）。

    装配路径命中它，比 _note.py 更要紧——修一半 = 生产上仍是 ghost 假成功。
    """
    from data.desknotes_id_map import IDMapper
    from data.repositories.desknotes_note_adapter import DeskNotesNoteAdapter

    ddir = tmp_path / "desknotes-data"
    ddir.mkdir(parents=True, exist_ok=True)
    mapper = IDMapper(ddir)
    adapter = DeskNotesNoteAdapter(ddir, mapper)
    uuid_str = "aaaa-bbbb-cccc"
    nid = adapter.add_note({"remote_id": uuid_str, "title": "t", "content": "c"})
    assert isinstance(nid, int)
    hit = adapter.update_note(nid, {"content": "x"})
    assert hit is True, "命中真实 uuid → True（bool 契约）"
    miss = adapter.update_note(99999999, {"content": "y"})
    assert miss is False, "未知 uuid → False（修复前仅 logger.warning 静默 return）"


# ───────────────────────────── 影响面：调用点消费 bool ─────────────────────────────
def test_notes_update_consumes_bool_false_from_repo(tmp_path):
    """探活与写入之间笔记被并发删除 → 消费 update_note 返回 False，如实失败（不广播）。"""
    store, repo, _ctx, reg, _trail = _make_notes_env(tmp_path)
    try:
        nid = repo.add_note({"title": "t", "content": "c"})
        # 用 monkey-patched repo 模拟竞态：_resolve_note_id 命中 → update_note 报 False
        original_update = repo.update_note

        def racing_update(note_id, new_info, *a, **kw):
            # 假装此时笔记已被并发硬删 → False
            return False

        repo.update_note = racing_update  # type: ignore[method-assign]
        try:
            with patch("backend.handlers._notes.event_bus") as fake_bus:
                fake_bus.emit.side_effect = AssertionError("不应广播 notes.changed")
                ok = asyncio.run(reg.registered["notes.update"](note_id=nid, content="race"))
            assert ok is False, "S0-1：repo 报 False 时 handler 不得再无条件 return True"
        finally:
            repo.update_note = original_update  # type: ignore[method-assign]
    finally:
        store.close()


def test_notes_update_guarded_path_consumes_bool(tmp_path):
    """WriteGuard 路径同样必须消费 bool False（不得只在回退路径修好）。"""
    store, repo, _ctx, reg, _trail = _make_notes_env(tmp_path, with_guard=True)
    try:
        nid = repo.add_note({"title": "t", "content": "c"})
        original_update = repo.update_note
        repo.update_note = lambda *a, **kw: False  # type: ignore[method-assign]
        try:
            with patch("backend.handlers._notes.event_bus") as fake_bus:
                fake_bus.emit.side_effect = AssertionError("不应广播")
                ok = asyncio.run(reg.registered["notes.update"](note_id=nid, content="x"))
            assert ok is False, "WriteGuard 路径的 ghost 竞态同样必须消费 False"
        finally:
            repo.update_note = original_update  # type: ignore[method-assign]
    finally:
        store.close()
