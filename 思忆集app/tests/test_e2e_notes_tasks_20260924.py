# -*- coding: utf-8 -*-
"""E2E 笔记与任务管理模块（2026-09-24 战役）—— WS 真调闭环 + 诚实契约 + 波次②收敛链.

只测不改源码。覆盖矩阵（全部对 conftest AUTOSTART 隔离临时后端**实调 WS**）：

- E2E-1 notes.create → get → update → list → delete（R4 pending→approve）闭环，
  全程诚实布尔；删后 get 必须诚实 None、list 不可见、list_trashed 可见（软删）
- E2E-2 tasks.create → get → update → toggle → delete（R4）→ list 闭环
- E2E-3 跨连接持久化：A 连接写入，B 连接读回一致；B 写 A 读对称
- E2E-4 双连接并发 update 同一笔记：无 torn state（终值必须是某写家的完整值）
- E2E-5 不存在 id 诚实错误面：notes.get/update/delete、tasks.get/update 须如实
- E2E-6/7 【已知缺口 · xfail(strict=False)】tasks.toggle（_tasks.py:299）与
  tasks.delete（_tasks.py:203）对不存在 id 假 True（handler 无探活、repo 层
  toggle/delete_task 返回 None，直接无条件 return True）。本波只记录不修源码。
- E2E-8 波次② C-1 收敛链实调：A 套 ``AssistantCapabilityRouter.dispatch``
  原生执行 notes.create / tasks.create（参数名差异：A 套 tasks.create 用
  ``name``，WS 面用 ``title``——两处都按各自契约传参锁定）。

已知坑（本文件实测口径）：R4 方法（notes.delete / tasks.delete）经新客户端
（client_version=1.2.0，ws_session fixture 已带）返回 pending 信封，须
sandbox.approve 重放才真执行；重放回执在信封 ``result`` 一层。
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

import aiohttp
import pytest

pytestmark = pytest.mark.integration

TAG = "e2e-nt-20260924"
MISSING_NOTE_ID = 99_999_999
MISSING_TASK_ID = 99_999_998


def _task_title(row: dict[str, Any]) -> str | None:
    """读回标题兼容两形态：桥形态 DeskNotesTaskAdapter 归一为 ``name``
    （_todo_to_task: todo.text → task.name），纯 kv 形态原样存 ``title``。
    写入面统一用 ``title``（WS 契约），读回面两键任一命中即诚实。"""
    return row.get("title") or row.get("name")


def _result(resp: dict[str, Any]) -> Any:
    assert "error" not in resp, f"RPC 层错误: {resp.get('error')}"
    return resp.get("result")


async def _call_approved(rpc, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """R4 链实调：dispatch → pending 信封 → sandbox.approve → 重放执行。

    返回 sandbox.approve 的回执 dict（{"success", "pending_id", "action_id",
    "result": <handler 真回执>}）；若方法未触发审批则返回原响应。
    """
    resp = await rpc(method, params)
    result = _result(resp)
    if isinstance(result, dict) and result.get("pending"):
        assert result.get("requires_approval") is True, f"{method} pending 信封缺 requires_approval: {result}"
        pid = result.get("pending_id")
        assert pid, f"{method} pending 信封缺 pending_id: {result}"
        ack = await rpc("sandbox.approve", {"pending_id": pid})
        return _result(ack)
    return resp


# ── 第二条 WS 连接（跨连接持久化 / 双连接并发用）─────────────────────────


def _ws_url() -> str:
    # AUTOSTART 会把随机端口的临时后端 URL 与 token 回写进进程 env（conftest :426-446），
    # 运行时读取即可与 ws_session fixture 连到同一后端。
    return os.environ.get("SIYI_WS_URL", "ws://127.0.0.1:18765/ws")


def _auth_headers() -> dict[str, str]:
    tok = os.environ.get("SIYI_DIAG_TOKEN", "").strip()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


class _WsClient:
    """轻量 RPC 客户端（与 conftest.rpc 同协议：跳事件帧、按 id 匹配）。"""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._ws = ws
        self._n = 0

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._n += 1
        rid = self._n
        await self._ws.send_json({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        while True:
            msg = await asyncio.wait_for(self._ws.receive(), timeout=60)
            if msg.type != aiohttp.WSMsgType.TEXT:
                raise RuntimeError(f"WS 帧异常: {msg.type}")
            data = json.loads(msg.data)
            if data.get("type") == "event" or "event_type" in data:
                continue
            if data.get("id") == rid:
                return data


@asynccontextmanager
async def second_connection():
    url = _ws_url()
    sep = "&" if "?" in url else "?"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            f"{url}{sep}client_version=1.2.0", timeout=10, headers=_auth_headers()
        ) as ws:
            yield _WsClient(ws)


# ── E2E-1 notes 闭环：create → get → update → list → delete(R4) → 诚实读回 ──


async def test_e2e1_notes_full_loop_honest(rpc):
    uniq = f"{TAG}-notes-{uuid.uuid4().hex[:8]}"

    # 1. create：返回 id + 回显 title/content（诚实载荷，不是 bare ack）
    created = _result(await rpc("notes.create", {"title": uniq, "content": "初稿内容", "tags": [TAG]}))
    assert isinstance(created, dict) and "id" in created, f"notes.create 回执异常: {created}"
    nid = created["id"]

    # 2. get：字段逐一对齐
    got = _result(await rpc("notes.get", {"note_id": nid}))
    assert got is not None and got.get("title") == uniq
    assert got.get("content") == "初稿内容"

    # 3. update：诚实布尔 True（不是 truthy dict）
    upd = _result(await rpc("notes.update", {"note_id": nid, "content": "改稿内容", "title": f"{uniq}-v2"}))
    assert upd is True, f"notes.update 应诚实 True，实得 {upd!r}"
    got2 = _result(await rpc("notes.get", {"note_id": nid}))
    assert got2["content"] == "改稿内容" and got2["title"] == f"{uniq}-v2"

    # 4. list：按 id 命中（同后端共享会话，用成员断言而非计数）
    listed = _result(await rpc("notes.list", {"limit": 500}))
    assert any(n.get("id") == nid for n in listed), "更新后的笔记未出现在 notes.list"

    # 5. delete：R4 pending → approve → 信封内层必须诚实 True
    ack = await _call_approved(rpc, "notes.delete", {"note_id": nid})
    assert ack.get("success") is True, f"sandbox.approve 失败: {ack}"
    assert ack.get("result") is True, f"notes.delete 重放回执应诚实 True: {ack}"

    # 6. 删后诚实：get=None、list 不可见、list_trashed 可见（软删语义）
    assert _result(await rpc("notes.get", {"note_id": nid})) is None, "删除后 notes.get 仍可读（幽灵读）"
    listed2 = _result(await rpc("notes.list", {"limit": 500}))
    assert all(n.get("id") != nid for n in listed2), "删除后仍在 notes.list"
    trashed = _result(await rpc("notes.list_trashed", {}))
    row = next((n for n in trashed if n.get("id") == nid), None)
    assert row is not None and row.get("deleted_at"), "软删未进回收站或无 deleted_at"


# ── E2E-2 tasks 闭环：create → get → update → toggle → delete(R4) → list ──


async def test_e2e2_tasks_full_loop(rpc):
    uniq = f"{TAG}-task-{uuid.uuid4().hex[:8]}"

    # WS 面参数名是 title（已知坑：A 套才用 name）
    created = _result(await rpc("tasks.create", {"title": uniq, "priority": "high"}))
    assert isinstance(created, dict) and "id" in created, f"tasks.create 回执异常: {created}"
    tid = created["id"]

    got = _result(await rpc("tasks.get", {"task_id": tid}))
    assert got is not None and _task_title(got) == uniq
    assert got.get("is_completed") is False

    upd = _result(await rpc("tasks.update", {"task_id": tid, "title": f"{uniq}-v2", "priority": "low"}))
    assert upd is True, f"tasks.update 应诚实 True，实得 {upd!r}"
    got2 = _result(await rpc("tasks.get", {"task_id": tid}))
    assert _task_title(got2) == f"{uniq}-v2"

    # toggle 正反两向 + 状态读回
    tg = _result(await rpc("tasks.toggle", {"task_id": tid, "completed": True}))
    assert tg is True
    done = _result(await rpc("tasks.get", {"task_id": tid}))
    assert done.get("is_completed") is True and done.get("completed") is True
    assert _result(await rpc("tasks.toggle", {"task_id": tid, "completed": False})) is True
    undone = _result(await rpc("tasks.get", {"task_id": tid}))
    assert undone.get("is_completed") is False

    listed = _result(await rpc("tasks.list", {"limit": 500}))
    assert any(t.get("id") == tid for t in listed), "任务未出现在 tasks.list"

    # tasks.delete 亦在 _R4_METHODS 集中清单 → pending → approve
    ack = await _call_approved(rpc, "tasks.delete", {"task_id": tid})
    assert ack.get("success") is True, f"tasks.delete 审批失败: {ack}"
    assert ack.get("result") is True
    assert _result(await rpc("tasks.get", {"task_id": tid})) is None, "删除后 tasks.get 幽灵读"
    listed2 = _result(await rpc("tasks.list", {"limit": 500}))
    assert all(t.get("id") != tid for t in listed2)


# ── E2E-3 跨连接持久化 ────────────────────────────────────────────────────


async def test_e2e3_cross_connection_persistence(rpc):
    uniq = f"{TAG}-xc-{uuid.uuid4().hex[:8]}"
    nid = _result(await rpc("notes.create", {"title": uniq, "content": "A连接写入"}))["id"]
    try:
        async with second_connection() as b:
            got_b = _result(await b.call("notes.get", {"note_id": nid}))
            assert got_b is not None and got_b.get("content") == "A连接写入", "B 连接读不到 A 连接笔记"
            # B 写 A 读（对称）
            upd_b = _result(await b.call("notes.update", {"note_id": nid, "content": "B连接改写"}))
            assert upd_b is True
            got_a = _result(await rpc("notes.get", {"note_id": nid}))
            assert got_a["content"] == "B连接改写", "A 连接未读回 B 连接写入"
    finally:
        await _call_approved(rpc, "notes.delete", {"note_id": nid})


# ── E2E-4 双连接并发 update 同一笔记：无 torn state ───────────────────────


async def test_e2e4_dual_conn_concurrent_update_no_torn(rpc):
    uniq = f"{TAG}-race-{uuid.uuid4().hex[:8]}"
    nid = _result(await rpc("notes.create", {"title": uniq, "content": "seed"}))["id"]
    val_a = "A" * 512
    val_b = "B" * 512
    try:
        async with second_connection() as b:
            resp_a, resp_b = await asyncio.gather(
                rpc("notes.update", {"note_id": nid, "content": val_a, "title": f"{uniq}-A"}),
                b.call("notes.update", {"note_id": nid, "content": val_b, "title": f"{uniq}-B"}),
            )
            # 两次 update 都须诚实 True（不允许假失败/异常吞没）
            assert _result(resp_a) is True and _result(resp_b) is True

            final = _result(await rpc("notes.get", {"note_id": nid}))
            assert final is not None
            # torn state 判据：content 必须逐字节等于其中一家的完整写入（无交错/截断）
            assert final.get("content") in (val_a, val_b), "并发更新后 content 撕裂（非任何写家完整值）"
            # 同字段族的 title 允许按字段级 last-write 归任一写家，但必须完整
            assert final.get("title") in (f"{uniq}-A", f"{uniq}-B"), "title 撕裂"
            assert int(final.get("version") or 0) >= 2, "双写后 version 未推进"
            # 两连接读回一致（同一存储真相）
            async with second_connection() as c:
                assert _result(await c.call("notes.get", {"note_id": nid})) == final
    finally:
        await _call_approved(rpc, "notes.delete", {"note_id": nid})


# ── E2E-5 不存在 id 诚实错误面（notes 全绿；tasks.get/update 全绿）────────


async def test_e2e5_missing_id_honesty_notes_and_safe_tasks(rpc):
    assert _result(await rpc("notes.get", {"note_id": MISSING_NOTE_ID})) is None, "notes.get 不存在 id 未如实 None"
    upd = _result(await rpc("notes.update", {"note_id": MISSING_NOTE_ID, "content": "x"}))
    assert upd is False, f"notes.update 不存在 id 应 False（S0-1），实得 {upd!r}"
    ack = await _call_approved(rpc, "notes.delete", {"note_id": MISSING_NOTE_ID})
    assert ack.get("result") is False, f"notes.delete 不存在 id 应 False（QUA-3），实得 {ack!r}"

    assert _result(await rpc("tasks.get", {"task_id": MISSING_TASK_ID})) is None
    tupd = _result(await rpc("tasks.update", {"task_id": MISSING_TASK_ID, "title": "ghost"}))
    assert tupd is False, f"tasks.update 不存在 id 应 False（S0-1 同款），实得 {tupd!r}"


# ── E2E-6/7 已知缺口：tasks.toggle / tasks.delete 假 True（_tasks.py:299/:203）──
# 源码确证：两 handler 无 _resolve_task_id 探活，repo 层 toggle_task_completion /
# delete_task 对不存在 id 静默空转返回 None，handler 仍无条件 return True。
# 本波只如实记录缺口（xfail strict=False：修复后自动转 XPASS 提示撤标），不修源码。


@pytest.mark.xfail(
    strict=False,
    reason="缺口 G-T1：_tasks.py:299 tasks.toggle 对不存在 id 无探活、无条件 True（假成功家族）",
)
async def test_e2e6_gap_tasks_toggle_missing_id_honest(rpc):
    before = len(_result(await rpc("tasks.list", {"limit": 500})))
    tg = _result(await rpc("tasks.toggle", {"task_id": MISSING_TASK_ID, "completed": True}))
    assert tg is False, f"不存在任务的 toggle 应诚实 False，实得 {tg!r}"
    assert len(_result(await rpc("tasks.list", {"limit": 500}))) == before


@pytest.mark.xfail(
    strict=False,
    reason="缺口 G-T2：_tasks.py:203 tasks.delete 对不存在 id 无探活、审批重放后仍 True（假成功家族）",
)
async def test_e2e7_gap_tasks_delete_missing_id_honest(rpc):
    before = len(_result(await rpc("tasks.list", {"limit": 500})))
    ack = await _call_approved(rpc, "tasks.delete", {"task_id": MISSING_TASK_ID})
    assert ack.get("success") is True, f"审批链本身应通: {ack}"
    assert ack.get("result") is False, f"不存在任务的 delete 重放应诚实 False，实得 {ack!r}"
    assert len(_result(await rpc("tasks.list", {"limit": 500}))) == before


# ── E2E-8 波次② C-1 收敛链：A 套 capability_router 实调 notes.create ─────


def _build_router(tmp_path):
    from assistant.capability_router import AssistantCapabilityRouter
    from data.local_storage import LocalDataStore
    from repositories._note import LocalPersonalNoteRepository
    from repositories._task import LocalPersonalTaskRepository

    store = LocalDataStore(base_dir=str(tmp_path / "runtime"))
    note_repo = LocalPersonalNoteRepository(store)
    task_repo = LocalPersonalTaskRepository(store)
    router = AssistantCapabilityRouter(
        data_store=store,
        settings_repository=None,
        observability_service=None,
        note_repository=note_repo,
        task_repository=task_repo,
        inbox_repository=None,
    )
    return router, store, note_repo, task_repo


async def test_e2e8_capability_router_notes_create_native_chain(tmp_path):
    """波次② 收敛后 A 套 router.dispatch 的原生执行体（不再 B 套转发/循环委托）。"""
    router, store, note_repo, task_repo = _build_router(tmp_path)
    try:
        uniq = f"{TAG}-router-{uuid.uuid4().hex[:8]}"
        res = await router.dispatch("notes.create", {"title": uniq, "content": "收敛链实调", "tags": "工作,周报"})
        # 信封保持 B 套形态 {"result":..., "note_id":...}（搬迁纪律）
        assert res.get("note_id") is not None, f"notes.create 收敛链回执异常: {res}"
        nid = res["note_id"]
        row = next((n for n in note_repo.list_notes() if n.get("id") == nid), None)
        assert row is not None and row["title"] == uniq
        assert row.get("tags") == ["工作", "周报"], f"逗号标签字符串应被拆列表: {row}"

        got = await router.dispatch("notes.get", {"note_id": nid})
        assert got.get("result", {}).get("content") == "收敛链实调"

        # 诚实缺参：title 空 → error 信封（不建条）
        bad = await router.dispatch("notes.create", {"title": "  "})
        assert "error" in bad and bad.get("note_id") is None

        # 已知坑对偶锁定：A 套 tasks.create 参数名 name（WS 面是 title）
        tres = await router.dispatch("tasks.create", {"name": f"{TAG}-router-task", "description": "A套面"})
        assert tres.get("task_id") is not None, f"A 套 tasks.create 回执异常: {tres}"
        assert task_repo.get_task(tres["task_id"]) is not None

        tbad = await router.dispatch("tasks.create", {"title": "错用 WS 面参数名"})
        assert "error" in tbad, f"A 套 tasks.create 用 title 应如实缺参报错: {tbad}"
    finally:
        store.close()
