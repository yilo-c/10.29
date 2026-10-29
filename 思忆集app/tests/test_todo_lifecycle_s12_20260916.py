# -*- coding: utf-8 -*-
"""S1-2（AN-2 todo 终生命周期）+ AM-2（title 静默丢弃）回归测试（2026-09-16 批）。

四条已定案缺陷（运行时适配器 desknotes_task_adapter + kv 侧 _task.py 同步修）：

AN-2① 软删顺手置 done：``delete_task(soft=True)`` 同时写 ``done=True``——
  「进回收站」与「已完成」是正交状态位，混写导致回收站恢复后任务永远
  是"已完成"。修法：软删只置 deletedAt。
AN-2② 无 tasks.permanent_delete：todo 进回收站后永无物理摘除路径。
  修法：适配器 permanent_delete_task（store.json 摘除 + id_map 清映射，
  一并消解八轮 §2.1 的 id_map 孤儿生成机制）+ handler
  ``tasks.permanent_delete``（RISK_R4，对照 notes.permanent_delete）。
AN-2③ 回收站可编辑：update_task 对软删任务照常编辑。修法：拒绝，返 False。
AM-2  update_task 白名单静默丢弃 title（add_task 有 title→name 映射，
  更新面漏了——云端 merge 的 remote title 也被丢弃）。修法：白名单补归一。

断言均用真实 DeskNotesTaskAdapter + IDMapper（tmp_path 隔离，绝不触碰
%LOCALAPPDATA%\\desk-notes\\ 真实数据）。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._tasks import register_tasks  # noqa: E402
from data.desknotes_id_map import IDMapper  # noqa: E402
from data.repositories.desknotes_task_adapter import DeskNotesTaskAdapter  # noqa: E402


# ───────────────────────────── fixture ─────────────────────────────


def _make_adapter(tmp_path: Path) -> tuple[DeskNotesTaskAdapter, IDMapper, Path]:
    ddir = tmp_path / "desknotes-data"
    ddir.mkdir(parents=True, exist_ok=True)
    mapper = IDMapper(ddir)
    adapter = DeskNotesTaskAdapter(ddir, mapper)
    return adapter, mapper, ddir


def _raw_todos(ddir: Path) -> list[dict]:
    store = json.loads((ddir / "store.json").read_text(encoding="utf-8"))
    return (store.get("state") or {}).get("todos") or []


class _Registry:
    def __init__(self) -> None:
        self.registered: dict[str, object] = {}
        self.risks: dict[str, str] = {}

    def register(self, name, fn, *, risk=None, **kw) -> None:
        self.registered[name] = fn
        if risk:
            self.risks[name] = risk

    def register_with_schema(self, name, fn, schema=None, *, risk=None, **kw) -> None:
        self.registered[name] = fn
        if risk:
            self.risks[name] = risk


def _make_tasks_env(tmp_path: Path):
    adapter, mapper, ddir = _make_adapter(tmp_path)
    ctx = SimpleNamespace(
        personal_task_repository=adapter,
        write_guard=None,
        task_manager=None,
        cron_service=None,
    )
    reg = _Registry()
    register_tasks(reg, ctx)
    return adapter, mapper, ddir, ctx, reg


# ─────────────── ① AN-2①：软删不得伪造完成态 ───────────────


def test_soft_delete_does_not_mark_done(tmp_path):
    """软删（进回收站）只置 deletedAt，done 保持原值——恢复后不得变"已完成"。"""
    adapter, mapper, ddir = _make_adapter(tmp_path)
    tid = adapter.add_task({"name": "未完成的活", "is_completed": False})
    assert adapter.get_task(tid)["is_completed"] is False
    adapter.delete_task(tid, soft=True)
    raw = _raw_todos(ddir)
    row = next(t for t in raw if t["id"] == mapper.to_uuid(tid))
    assert row.get("deletedAt"), "前置：软删墓碑位必须写入"
    assert row.get("done") is False, f"修复前缺陷：软删顺手 done=True → 回收站恢复后永远'已完成'。实际 row={row}"
    # list_trashed 视角（经 deleted_at）仍能看到回收站条目
    trashed = [t for t in adapter.list_tasks(include_deleted=True) if t.get("deleted_at")]
    assert len(trashed) == 1
    assert trashed[0]["is_completed"] is False, "回收站条目的完成态必须忠实于删除前"


def test_soft_delete_preserves_already_done_task_state(tmp_path):
    """已完成任务软删后 done 仍为 True——正交状态位不得被删除动作翻转。"""
    adapter, _mapper, ddir = _make_adapter(tmp_path)
    tid = adapter.add_task({"name": "已完成的活", "is_completed": True})
    adapter.delete_task(tid, soft=True)
    raw = _raw_todos(ddir)
    assert next(t for t in raw)["done"] is True


# ─────────────── ② AN-2②：permanent_delete 只杀软删的、返 bool ───────────────


def test_permanent_delete_kills_soft_deleted_and_returns_bool(tmp_path):
    adapter, _mapper, ddir = _make_adapter(tmp_path)
    # 显式 remote_id：既有 add_task 的 uuid 是秒级粒度（siyi_task_{epoch_sec}），
    # 同一秒内两条会碰撞——非本任务范围，测试侧规避。
    tid_live = adapter.add_task({"name": "活任务", "remote_id": "live-uuid-1"})
    tid_trash = adapter.add_task({"name": "回收站任务", "remote_id": "trash-uuid-1"})
    assert tid_live != tid_trash, "前置：两条任务必须是不同 int id"
    adapter.delete_task(tid_trash, soft=True)
    # 活任务拒绝（与 S0-2 notes rejects_live_note 语义严格对齐）
    assert adapter.permanent_delete_task(tid_live) is False, "活任务不得被 perm 删除"
    assert adapter.get_task(tid_live) is not None
    # 回收站任务放行，返回 True
    assert adapter.permanent_delete_task(tid_trash) is True
    assert not any(t["text"] == "回收站任务" for t in _raw_todos(ddir)), "store.json 必须物理摘除"
    assert adapter.get_task(tid_live) is not None, "活任务不受波及"
    # ghost：未知 id → False
    assert adapter.permanent_delete_task(99999999) is False


def test_permanent_delete_removes_id_map_entry(tmp_path):
    """③ perm 后 desknotes_id_map 无残留——消解八轮 §2.1 id_map 孤儿生成机制。"""
    adapter, mapper, ddir = _make_adapter(tmp_path)
    tid = adapter.add_task({"name": "将被终删"})
    uuid = mapper.to_uuid(tid)
    assert uuid and mapper.to_int(uuid) == tid, "前置：映射存在"
    adapter.delete_task(tid, soft=True)
    assert adapter.permanent_delete_task(tid) is True
    # 重载 mapper（盘上真值，而非仅内存态）
    fresh = IDMapper(ddir)
    fresh._load_or_init()
    assert fresh.to_uuid(tid) is None, f"int→uuid 映射残留：id={tid}"
    assert fresh.to_int(uuid) is None, f"uuid→int 映射残留：uuid={uuid}"
    map_raw = json.loads((ddir / "desknotes_id_map.json").read_text(encoding="utf-8"))
    assert uuid not in map_raw.get("note_map", {}), "落盘 note_map 不得残留孤儿 uuid"


def test_hard_delete_via_flag_also_purges_map(tmp_path):
    """delete_task(soft=False) 直删路径同样清映射——孤儿生成的另一半入口。"""
    adapter, mapper, ddir = _make_adapter(tmp_path)
    tid = adapter.add_task({"name": "直删"})
    uuid = mapper.to_uuid(tid)
    adapter.delete_task(tid, soft=False)
    assert not _raw_todos(ddir)
    fresh = IDMapper(ddir)
    fresh._load_or_init()
    assert fresh.to_int(uuid) is None and fresh.to_uuid(tid) is None


# ─────────────── ④ AN-2③：软删任务 update 被拒 ───────────────


def test_update_soft_deleted_task_rejected(tmp_path):
    adapter, _mapper, ddir = _make_adapter(tmp_path)
    tid = adapter.add_task({"name": "原名"})
    adapter.delete_task(tid, soft=True)
    hit = adapter.update_task(tid, {"name": "偷改"})
    assert hit is False, "回收站任务编辑必须如实 False（修复前静默改成功）"
    row = _raw_todos(ddir)[0]
    assert row["text"] == "原名", "拒绝后盘上内容不得被污染"
    assert row.get("deletedAt"), "拒绝不得顺带清除墓碑位"


def test_update_returns_bool_hit_and_miss(tmp_path):
    """S0-1 同款 bool 契约（task 面）：命中 True / 未知 id False。"""
    adapter, _mapper, _ddir = _make_adapter(tmp_path)
    tid = adapter.add_task({"name": "a"})
    assert adapter.update_task(tid, {"name": "b"}) is True
    assert adapter.update_task(99999999, {"name": "c"}) is False, "未知 uuid 不得静默空转"
    assert adapter.get_task(tid)["name"] == "b"


# ─────────────── ⑤ AM-2：title 更新真实生效 ───────────────


def test_update_task_title_lands_in_store(tmp_path):
    adapter, _mapper, ddir = _make_adapter(tmp_path)
    tid = adapter.add_task({"name": "旧标题"})
    # handler 契约用 title——修复前白名单只认 name，title 被静默丢弃
    hit = adapter.update_task(tid, {"title": "新标题"})
    assert hit is True
    assert _raw_todos(ddir)[0]["text"] == "新标题", "title 必须归一写入 todo.text"
    assert adapter.get_task(tid)["name"] == "新标题"


def test_kv_repo_update_task_title_and_lifecycle(tmp_path):
    """kv 侧 LocalPersonalTaskRepository 同步修：bool 契约 + 回收站拒编 + title 归一。"""
    from data.local_storage import LocalDataStore
    from repositories._task import LocalPersonalTaskRepository

    store = LocalDataStore(base_dir=str(tmp_path / "kv"))
    repo = LocalPersonalTaskRepository(store)
    try:
        tid = repo.add_task({"name": "kv旧"})
        assert repo.get_task(tid)["title"] == "kv旧", "add 时 title 回填（归一往返一致）"
        assert repo.update_task(tid, {"title": "kv新"}) is True
        assert repo.get_task(tid)["name"] == "kv新" and repo.get_task(tid)["title"] == "kv新"
        assert repo.update_task(99999999, {"name": "ghost"}) is False
        # 软删 → 拒编辑
        repo.delete_task(tid, soft=True)
        t = repo.update_task(tid, {"name": "偷改"})
        assert t is False, "kv 侧回收站任务编辑必须 False"
        assert repo.list_tasks(include_deleted=True)[0]["name"] == "kv新"
        # kv 侧无 permanent_delete_task：delete_task(soft=False) 仍为摘除语义
        repo.delete_task(tid, soft=False)
        assert repo.list_tasks(include_deleted=True) == []
    finally:
        store.close()


# ─────────────── handler 面：tasks.permanent_delete RPC ───────────────


def test_rpc_permanent_delete_only_soft_deleted(tmp_path):
    adapter, _mapper, ddir, _ctx, reg = _make_tasks_env(tmp_path)
    tid_live = adapter.add_task({"name": "活", "remote_id": "live-uuid-rpc"})
    tid_trash = adapter.add_task({"name": "trash", "remote_id": "trash-uuid-rpc"})
    assert tid_live != tid_trash
    asyncio.run(reg.registered["tasks.delete"](task_id=tid_trash))
    with patch("backend.handlers._tasks.cascade_delete_task", new=AsyncMock()) as cascade:
        # 活任务拒绝
        ok = asyncio.run(reg.registered["tasks.permanent_delete"](task_id=tid_live))
        assert ok is False, "对活任务 perm 拒绝（S0-2 同款语义）"
        # str 传参归一 + 回收站放行
        ok = asyncio.run(reg.registered["tasks.permanent_delete"](task_id=str(tid_trash)))
        assert ok is True, "回收站任务 perm 必须 True（修复前无此 RPC）"
        assert tid_trash not in [t["id"] for t in adapter.list_tasks(include_deleted=True)]
        cascade.assert_awaited()
        # ghost
        ok = asyncio.run(reg.registered["tasks.permanent_delete"](task_id=99999999))
        assert ok is False
    assert reg.risks["tasks.permanent_delete"] == "R4", "注册 risk 对齐 notes.permanent_delete"


def test_rpc_update_rejects_ghost_and_trashed(tmp_path):
    adapter, _mapper, _ddir, _ctx, reg = _make_tasks_env(tmp_path)
    tid = adapter.add_task({"name": "x"})
    ok = asyncio.run(reg.registered["tasks.update"](task_id=99999999, title="ghost"))
    assert ok is False, "S0-1 同款：ghost update 不得再假成功"
    asyncio.run(reg.registered["tasks.delete"](task_id=tid))
    ok = asyncio.run(reg.registered["tasks.update"](task_id=tid, title="偷改"))
    assert ok is False, "RPC 面回收站编辑必须 False（AN-2③）"


def test_rpc_update_title_roundtrip(tmp_path):
    adapter, _mapper, ddir, _ctx, reg = _make_tasks_env(tmp_path)
    tid = adapter.add_task({"name": "旧"})
    ok = asyncio.run(reg.registered["tasks.update"](task_id=tid, title="新"))
    assert ok is True
    assert _raw_todos(ddir)[0]["text"] == "新", "AM-2：RPC 面 title 必须落库"
