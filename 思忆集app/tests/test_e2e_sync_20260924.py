# -*- coding: utf-8 -*-
"""E2E 同步机制模块（2026-09-24 战役）——波次④（H-3 同步 SSOT）改后首次真调回归.

只测不改源码。本域刚经历 commit 9e0a9c5（删 cloud_push/delta_sync/services/channels/
file_sync_service 四岛、SyncState 枚举上提 core.sync_protocol、双栈互感守卫、
resource_vocab 锁），本文件是该域改后的**活体 WS 面**首次 E2E 回归。

覆盖矩阵（凡标 [WS] 者全部对 conftest AUTOSTART 隔离临时后端实调）：

- S1 [WS] sync.* 注册表 9 方法逐个冒烟（未配置态诚实面）+ conflicts.list 空态合法
- S2 [WS] 构造 sync_state=conflict 笔记 → conflicts.list 可见 → resolve choice=remote
  （R4 pending→approve 审批链）→ 复跑 list 消解 + sync_state=synced
- S3 [WS] 同上构冲突 → resolve choice=local → versions[0] 完整快照恢复 + dirty
- S4 [WS] resolve_conflict 诚实错误面（resource/id/choice 非法、非冲突态、无本地版本）
- S5 [WS] 手机↔桌面跨设备面的**同后端双 WS 客户端代理**：A 写 B 读、并发写、
  A 裁决 B 确认（真机跨端不可模拟，诚实边界见测试内注释）
- S6 [WS] Worker 不可达（sync.configure 指向 127.0.0.1:9 + enable）→ sync.failed
  事件上线（critical 前缀直通 WS）、非 fatal（payload 无 fatal 真值）不进通知管道
  （无 notification.delivered「同步失败」帧）、后端不崩、disable 可停循环；
  窗口内事件次数有界（P1-14 指数退避）→「瞬时失败重试不死循环」活体侧
- S7 （进程内）_sync_loop 退避序列确定性验证：失败 delay=interval·2^n、成功后复位、
  上限 1800s——防「死循环重试」把 Worker 打死（与 S6 活体侧互补）
- S8 （子进程实调）双栈互感守卫生产参数真跑：A 栈（RESOURCE_SPECS 全集）+
  B 栈（CLOUD_BRIDGE_SYNC_RESOURCES）同开 → stderr 出 "dual-stack sync overlap"
  WARN、诊断标记 overlap=["notes","tasks"]；单开无告警。构造点接线静态锁由
  tests/test_wave4c_dual_stack_and_vocab_20260924.py 负责，本处补**真函数活体执行**。

已知坑（本文件实测口径）：
- sync.resolve_conflict 在 R4 集中清单（backend/handlers/__init__.py:82），新客户端
  （client_version=1.2.0）走 pending→sandbox.approve→重放，回执在 approve 信封 result 一层。
- AUTOSTART 后端笔记走 DeskNotesNoteAdapter（桥模式）：sync_state/versions 落
  .sync-meta.json sidecar（S1-1），notes.update 白名单透传；任务面
  DeskNotesTaskAdapter.update_task 为字段白名单（**无 sync_state sidecar 路径**），
  桥模式下 todo 无法经 WS 构造 conflict——记入缺口表，单元层由
  tests/test_sync_conflict_resolution.py（Fake repos）覆盖。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiohttp
import pytest

pytestmark = pytest.mark.integration

APP_ROOT = Path(__file__).resolve().parents[1]

TAG = "e2e-sync-20260924"
UNREACHABLE_WORKER = "http://127.0.0.1:9"  # discard 端口：连接立即拒绝（模拟网络中断）

# sync.* 注册表全集（backend/handlers/_sync.py，9 个方法）
SYNC_METHODS = [
    "sync.status",
    "sync.sync_now",
    "sync.configure",
    "sync.enable",
    "sync.disable",
    "sync.register_device",
    "sync.conflicts.list",
    "sync.resolve_conflict",
    "sync.reset_watermark",
]


# ── 通用工具 ─────────────────────────────────────────────────────────────


def _result(resp: dict[str, Any]) -> Any:
    assert "error" not in resp, f"RPC 层错误: {resp.get('error')}"
    return resp.get("result")


async def _resolve_conflict(rpc, params: dict[str, Any]) -> dict[str, Any]:
    """R4 审批链实调 sync.resolve_conflict，返回 handler 真回执 dict。

    dispatch → pending 信封 → sandbox.approve → 重放执行；回执在
    approve 信封 ``result.result`` 一层（与 test_e2e_notes_tasks 同口径）。
    """
    resp = _result(await rpc("sync.resolve_conflict", params))
    if isinstance(resp, dict) and resp.get("pending"):
        pid = resp.get("pending_id")
        assert pid, f"sync.resolve_conflict pending 信封缺 pending_id: {resp}"
        ack = _result(await rpc("sandbox.approve", {"pending_id": pid}))
        inner = ack.get("result") if isinstance(ack, dict) else None
        assert isinstance(inner, dict), f"approve 回执缺内层 result: {ack}"
        return inner
    assert isinstance(resp, dict), f"sync.resolve_conflict 回执异常: {resp}"
    return resp


async def _make_conflict_note(rpc, *, with_versions: bool = True) -> tuple[Any, dict[str, Any]]:
    """创建笔记并打成 sync_state=conflict（模拟「远端覆盖、本地快照在 versions[0]」）。"""
    uniq = f"{TAG}-cf-{uuid.uuid4().hex[:8]}"
    snapshot = {
        "title": f"{uniq}-local",
        "content": f"本地冲突前版本-{uniq}",
        "tags": [TAG],
        "category": "",
        "updated_at": "2026-09-20T00:00:00Z",
    }
    created = _result(await rpc("notes.create", {"title": f"{uniq}-remote", "content": "远端覆盖后内容", "tags": [TAG]}))
    nid = created["id"]
    patch: dict[str, Any] = {"note_id": nid, "sync_state": "conflict"}
    if with_versions:
        patch["versions"] = [snapshot]
    upd = _result(await rpc("notes.update", patch))
    assert upd is True, f"notes.update 打冲突标记应诚实 True，实得 {upd!r}"
    got = _result(await rpc("notes.get", {"note_id": nid}))
    assert got is not None and got.get("sync_state") == "conflict", f"冲突标记未落 sidecar: {got}"
    return nid, snapshot


def _find_conflict(conflicts: list[dict[str, Any]], nid: Any) -> dict[str, Any] | None:
    return next((c for c in conflicts if str(c.get("id")) == str(nid)), None)


# ── 事件感知 WS 客户端（跨设备代理腿 / 事件流取证腿）────────────────────


def _ws_url() -> str:
    # AUTOSTART 把随机端口临时后端 URL + token 回写进程 env（conftest :426-446）
    return os.environ.get("SIYI_WS_URL", "ws://127.0.0.1:18765/ws")


def _auth_headers() -> dict[str, str]:
    tok = os.environ.get("SIYI_DIAG_TOKEN", "").strip()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


class _EventRpcClient:
    """与 conftest.rpc 同协议的响应匹配，但把 ``type=event`` 帧留存而非丢弃。"""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._ws = ws
        self._n = 0
        self.events: list[dict[str, Any]] = []

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
                self.events.append(data)
                continue
            if data.get("id") == rid:
                return data

    async def drain_events(self, seconds: float) -> None:
        """静收 seconds 秒事件帧（RPC id 计数隔离，响应帧一并消费不入事件表）。"""
        end = time.monotonic() + seconds
        while True:
            remain = end - time.monotonic()
            if remain <= 0:
                return
            try:
                msg = await asyncio.wait_for(self._ws.receive(), timeout=remain)
            except asyncio.TimeoutError:
                return
            if msg.type != aiohttp.WSMsgType.TEXT:
                return
            data = json.loads(msg.data)
            if data.get("type") == "event" or "event_type" in data:
                self.events.append(data)


@asynccontextmanager
async def event_connection():
    url = _ws_url()
    sep = "&" if "?" in url else "?"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            f"{url}{sep}client_version=1.2.0", timeout=10, headers=_auth_headers()
        ) as ws:
            yield _EventRpcClient(ws)


def _sync_failed_events(client: _EventRpcClient) -> list[dict[str, Any]]:
    return [e for e in client.events if e.get("event_type") == "sync.failed"]


# ── S1 sync.* 注册表逐个冒烟（未配置态诚实面）────────────────────────────


async def test_s1_sync_registry_surface(rpc):
    # 1) sync.status：未配置临时后端 → enabled/configured/running 全 False（诚实，不假开）
    status = _result(await rpc("sync.status", {}))
    assert isinstance(status, dict)
    assert status.get("enabled") is False and status.get("configured") is False
    assert status.get("running") is False
    assert "message" in status or "enabled" in status

    # 2) sync.conflicts.list：空态合法信封（conflicts 列表 + count 对齐；
    #    并与 notes.list 的 sync_state==conflict 真值交叉核对，跨会话残留也诚实）
    clist = _result(await rpc("sync.conflicts.list", {}))
    assert isinstance(clist.get("conflicts"), list)
    assert clist["count"] == len(clist["conflicts"])
    assert "error" not in clist, f"空态 conflicts.list 不应带错误: {clist}"
    notes = _result(await rpc("notes.list", {"limit": 0}))
    repo_conflicts = [n for n in notes if n.get("sync_state") == "conflict"]
    assert {str(c["id"]) for c in clist["conflicts"]} == {str(n["id"]) for n in repo_conflicts}, (
        "conflicts.list 与仓储真值不一致（P0-2 可见性面）"
    )

    # 3) sync.sync_now：桥未启动 → 诚实失败（波次④假成功反转项的反面锁）
    now = _result(await rpc("sync.sync_now", {}))
    assert now == {"success": False, "error": "cloud sync not started"} or now.get("success") is False

    # 4) sync.enable：未配置 → 诚实失败，不落 cloud_sync_enabled
    en = _result(await rpc("sync.enable", {}))
    assert en.get("success") is False and "worker_url and token" in en.get("error", "")
    st = _result(await rpc("sync.status", {}))
    assert st.get("enabled") is False

    # 5) sync.register_device：未配 register_secret → 诚实失败
    reg = _result(await rpc("sync.register_device", {"name": "e2e-desktop"}))
    assert reg.get("success") is False and "register_secret" in reg.get("error", "")

    # 6) sync.reset_watermark：client 未构建 → 诚实失败（B-18 生效化前置守卫面）
    rw = _result(await rpc("sync.reset_watermark", {}))
    assert rw.get("success") is False and "not initialized" in rw.get("error", "")

    # 7) sync.configure 空参：只读回显，success True，不改任何键
    cfg = _result(await rpc("sync.configure", {}))
    assert cfg.get("success") is True and cfg.get("worker_url") == "" and cfg.get("has_token") is False

    # 8) 注册表外 sync.* 方法 → MethodNotFound（-32601 或 error 信封）
    resp = await rpc("sync.nonexistent_20260924", {})
    assert "error" in resp, f"未注册方法必须回错误信封: {resp}"


# ── S2 冲突构态 → conflicts.list 可见 → remote 裁决 → 复跑消解 ────────────


async def test_s2_conflict_visible_and_resolved_remote(rpc):
    nid, _ = await _make_conflict_note(rpc)

    clist = _result(await rpc("sync.conflicts.list", {}))
    entry = _find_conflict(clist["conflicts"], nid)
    assert entry is not None, f"构冲突后 conflicts.list 未见笔记 {nid}: {clist}"
    assert entry["resource"] == "note" and entry["has_local_version"] is True

    res = await _resolve_conflict(rpc, {"resource": "note", "id": nid, "choice": "remote"})
    assert res.get("success") is True, f"remote 裁决失败: {res}"

    clist2 = _result(await rpc("sync.conflicts.list", {}))
    assert _find_conflict(clist2["conflicts"], nid) is None, "remote 裁决后冲突仍在列（未消解）"
    got = _result(await rpc("notes.get", {"note_id": nid}))
    assert got.get("sync_state") == "synced", f"remote 裁决应落 synced，实得 {got.get('sync_state')}"
    assert got.get("content") == "远端覆盖后内容", "remote=接受当前（远端覆盖后）内容"


# ── S3 local 裁决：versions[0] 完整快照恢复 + dirty ───────────────────────


async def test_s3_conflict_resolved_local_restores_snapshot(rpc):
    nid, snapshot = await _make_conflict_note(rpc)

    clist = _result(await rpc("sync.conflicts.list", {}))
    assert _find_conflict(clist["conflicts"], nid) is not None

    res = await _resolve_conflict(rpc, {"resource": "note", "id": nid, "choice": "local"})
    assert res.get("success") is True, f"local 裁决失败: {res}"

    got = _result(await rpc("notes.get", {"note_id": nid}))
    assert got.get("title") == snapshot["title"], f"local 裁决未恢复快照标题: {got.get('title')}"
    assert got.get("content") == snapshot["content"], f"local 裁决未恢复快照正文: {got.get('content')}"
    assert got.get("sync_state") == "dirty", f"local 裁决应标 dirty 待推回远端，实得 {got.get('sync_state')}"
    assert int(got.get("version") or 0) >= 2, "local 裁决应推进乐观锁版本号"

    clist2 = _result(await rpc("sync.conflicts.list", {}))
    assert _find_conflict(clist2["conflicts"], nid) is None, "local 裁决后冲突仍在列（未消解）"


# ── S4 resolve_conflict 诚实错误面 ───────────────────────────────────────


async def test_s4_resolve_conflict_honest_errors(rpc):
    bad_res = await _resolve_conflict(rpc, {"resource": "calendar", "id": "x", "choice": "remote"})
    assert bad_res.get("success") is False and "note" in bad_res["error"] and "todo" in bad_res["error"]

    bad_choice = await _resolve_conflict(rpc, {"resource": "note", "id": "x", "choice": "mine"})
    assert bad_choice.get("success") is False and "remote" in bad_choice["error"]

    missing_id = await _resolve_conflict(rpc, {"resource": "note", "id": "", "choice": "remote"})
    assert missing_id.get("success") is False and "missing id" in missing_id["error"]

    ghost = await _resolve_conflict(rpc, {"resource": "note", "id": 99_999_999, "choice": "remote"})
    assert ghost.get("success") is False and "not found" in ghost["error"], f"不存在 id 应诚实: {ghost}"

    # 非冲突态笔记 → 拒绝裁决
    created = _result(await rpc("notes.create", {"title": f"{TAG}-plain", "content": "普通笔记"}))
    plain_id = created["id"]
    not_cf = await _resolve_conflict(rpc, {"resource": "note", "id": plain_id, "choice": "remote"})
    assert not_cf.get("success") is False and "not in conflict" in not_cf["error"]

    # 冲突态但无本地版本快照 → local 选择诚实失败（不得凭空造版本）
    nid, _ = await _make_conflict_note(rpc, with_versions=False)
    no_ver = await _resolve_conflict(rpc, {"resource": "note", "id": nid, "choice": "local"})
    assert no_ver.get("success") is False and "no local version" in no_ver["error"], f"{no_ver}"
    # 收尾：消解掉这条，避免残留污染后续 list 交叉核对
    done = await _resolve_conflict(rpc, {"resource": "note", "id": nid, "choice": "remote"})
    assert done.get("success") is True


# ── S5 跨设备面代理：同后端双 WS 客户端并发（真机不可模拟，诚实边界）─────


async def test_s5_dual_ws_clients_cross_device_proxy(rpc):
    """手机↔桌面真机直连不可在 CI 模拟——以第二 WS 客户端连同一后端代理
    「对端设备」：A（本连接）写，B（新连接）必须读回一致；双端并发写互不丢；
    A 裁决冲突，B 立刻可见消解。跨进程真机链路（手机端 cloudSync.ts ↔
    Cloudflare Worker）不在本腿覆盖内，登记为诚实边界。"""
    assert _result(await rpc("sync.status", {})) is not None  # 复用 fixture 触发后端自启

    async with event_connection() as dev_b:
        # B1 跨连接读一致：A 写 B 读
        nid, _ = await _make_conflict_note(rpc)
        b_list = _result(await dev_b.call("sync.conflicts.list", {}))
        assert _find_conflict(b_list["conflicts"], nid) is not None, "B 端看不见 A 端构的冲突（跨连接不一致）"

        # B2 双端并发写（各 4 条），互不丢失
        uniq = uuid.uuid4().hex[:8]

        async def _write(client_call, who: str) -> list[Any]:
            ids = []
            for i in range(4):
                r = _result(await client_call("notes.create", {"title": f"{TAG}-dual-{uniq}-{who}{i}", "content": who}))
                ids.append(r["id"])
            return ids

        ids_a, ids_b = await asyncio.gather(_write(rpc, "a"), _write(dev_b.call, "b"))
        b_notes = _result(await dev_b.call("notes.list", {"limit": 0}))
        seen = {str(n["id"]) for n in b_notes}
        assert {str(x) for x in ids_a + ids_b} <= seen, "双 WS 并发写出现丢单"

        # B3 A 裁决 → B 立即可见消解
        res = await _resolve_conflict(rpc, {"resource": "note", "id": nid, "choice": "remote"})
        assert res.get("success") is True
        b_list2 = _result(await dev_b.call("sync.conflicts.list", {}))
        assert _find_conflict(b_list2["conflicts"], nid) is None


# ── S6 Worker 不可达：sync.failed 活体事件（非 fatal·不进通知·不崩）──────


async def test_s6_worker_unreachable_nonfatal_live(rpc):
    """真实 WS 面模拟网络中断：configure 指向不可达 worker + enable 起循环。

    锁四件事（波次④ web_bridge 假成功反转、通知分级门控的活体侧）：
    1) sync.failed 事件帧直达 WS（EventBridge critical 前缀 "sync." 直通）；
    2) payload 无 fatal 真值 → NotificationDispatcher 拒投通知管道
       （观测面：窗口内零「同步失败」notification.delivered 帧）；
    3) 后端不崩：全程 RPC 可用，disable 干净停循环且此后无新 sync.failed；
    4) 窗口内失败次数有界（5s 基础间隔 + P1-14 指数退避，非死循环）。
    """
    cfg = _result(await rpc("sync.configure", {
        "worker_url": UNREACHABLE_WORKER,
        "token": "e2e-outage-token",
        "register_secret": "e2e-outage-secret",
        "interval_seconds": 5,
    }))
    assert cfg.get("success") is True and cfg.get("worker_url") == UNREACHABLE_WORKER

    async with event_connection() as wire:
        en = await wire.call("sync.enable", {})
        res = _result(en)
        assert res.get("success") is True, f"已配置态 sync.enable 应成功起循环: {res}"
        assert res.get("status", {}).get("running") is True

        await wire.drain_events(13.0)

        failed = _sync_failed_events(wire)
        assert failed, "Worker 不可达窗口内未收到任何 sync.failed 事件帧（同步失败静默丢失）"
        for evt in failed:
            payload = evt.get("payload") or {}
            assert not payload.get("fatal"), f"网络中断应为非 fatal 抖动，实得: {payload}"
            assert payload.get("push_ok") is False

        # 通知管道门控（非 fatal 不投）：不得出现「同步失败」投递帧
        delivered = [
            e for e in wire.events
            if e.get("event_type") == "notification.delivered"
            and "同步失败" in json.dumps(e.get("payload") or {}, ensure_ascii=False)
        ]
        assert not delivered, f"非 fatal sync.failed 漏进了通知管道: {delivered}"

        # 有界重试（13s 窗口、基础间隔 5s：理论上界 <6）
        assert len(failed) <= 6, f"13s 窗口 sync.failed ×{len(failed)} 超出退避上界，疑死循环"

        # 后端活体健在 + 运行态可查 + 逃生舱生效（B-18：client 已构建）
        st = _result(await wire.call("sync.status", {}))
        assert st.get("running") is True and st.get("last_sync_ok") is False
        rw = _result(await wire.call("sync.reset_watermark", {}))
        assert rw.get("success") is True and rw.get("watermark") == 0
        now = _result(await wire.call("sync.sync_now", {}))
        assert now.get("success") is False, "不可达 worker 下 sync_now 不得假成功"

        dis = _result(await wire.call("sync.disable", {}))
        assert dis.get("success") is True
        st2 = _result(await wire.call("sync.status", {}))
        assert st2.get("running") is False and st2.get("enabled") is False

        # disable 后静默确认循环真的停了（再等 7s > 1 个基础间隔，不得再有增量事件）
        before = len(_sync_failed_events(wire))
        await wire.drain_events(7.0)
        after = len(_sync_failed_events(wire))
        assert after == before, f"sync.disable 后仍有新 sync.failed（循环未停：{before}→{after}）"

    # 跨连接健在收尾：主连接照常可 dispatch
    assert _result(await rpc("sync.status", {})).get("enabled") is False


# ── S7 进程内：退避序列确定性（重试不死循环的单元侧证据）─────────────────


async def test_s7_sync_loop_exponential_backoff_sequence(monkeypatch):
    import sync.cloud_sync_client as csc

    client = csc.CloudSyncClient(
        worker_url=UNREACHABLE_WORKER, sync_token="t", device_id="desktop-e2e-s7", register_secret="",
    )

    outcomes = [False, False, True, True]  # 两次瞬时失败后恢复
    seen_calls: list[int] = []

    async def fake_sync_once() -> bool:
        ok = outcomes[min(len(seen_calls), len(outcomes) - 1)]
        seen_calls.append(1)
        return ok

    monkeypatch.setattr(client, "sync_once", fake_sync_once)

    real_sleep = asyncio.sleep
    delays: list[float] = []

    async def fake_sleep(d: float) -> None:
        delays.append(d)
        await real_sleep(0)

    monkeypatch.setattr(csc.asyncio, "sleep", fake_sleep)  # csc.asyncio 即 asyncio 模块

    client.start_sync_loop(interval_seconds=5)
    deadline = time.monotonic() + 5.0
    while len(delays) < 4 and time.monotonic() < deadline:
        await real_sleep(0.01)
    client.stop_sync_loop()

    assert len(delays) >= 4, f"同步循环未按期迭代 4 次: delays={delays}"
    # 5(失败1: 5·2^0) → 10(失败2: 5·2^1) → 成功后复位 5 → 5（指数退避 + 复位，非固定/非死循环）
    assert delays[:4] == [5, 10, 5, 5], f"退避序列异常（P1-14 契约破坏）: {delays}"
    # 上限锁读源（30 分钟封顶在 _sync_loop 内 max_backoff=1800）
    src = Path(csc.__file__).read_text(encoding="utf-8")
    assert "max_backoff = 1800" in src


# ── S8 双栈互感守卫：子进程实调生产登记参数 ──────────────────────────────


_DUAL_STACK_SNIPPET = textwrap.dedent(
    """
    import logging, sys
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(levelname)s %(name)s %(message)s")
    from api.sync_orchestrator import (
        RESOURCE_SPECS, STACK_ORCHESTRATOR, STACK_CLOUD_BRIDGE,
        CLOUD_BRIDGE_SYNC_RESOURCES, note_stack_launch, get_dual_stack_diagnostic,
    )
    both = sys.argv[1] == "both"
    # 与两个生产构造点逐字同参（_tooling_provider.py:113 / headless_main.py:831）
    note_stack_launch(STACK_ORCHESTRATOR, list(RESOURCE_SPECS.keys()), enabled=True)
    note_stack_launch(STACK_CLOUD_BRIDGE, CLOUD_BRIDGE_SYNC_RESOURCES, enabled=both)
    print("DIAG=" + repr(get_dual_stack_diagnostic()))
    """
)


def test_s8_dual_stack_guard_live_subprocess():
    """真跑守卫函数（子进程隔离）：A+B 同开 → 启动 WARN；A 单开 → 静默。

    与 wave4c 静态锁互补：那边锁「构造点接线」，这里以生产构造点的**同一参数**
    执行真 note_stack_launch，验证 WARN 文案与诊断标记真实产出。
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(APP_ROOT)
    env["SIYI_TEST_OFFLINE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    def _run(mode: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", _DUAL_STACK_SNIPPET, mode],
            cwd=str(APP_ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=90,
        )

    both = _run("both")
    assert both.returncode == 0, f"双栈守卫子进程异常退出: {both.stderr[-800:]}"
    assert "dual-stack sync overlap" in both.stderr, f"A+B 同开未见启动 WARN: {both.stderr[-800:]}"
    assert "notes" in both.stderr and "tasks" in both.stderr, "WARN 文案须点名重叠资源（tasks↔todos 归一）"
    assert "DIAG=" in both.stdout and "'overlap': ['notes', 'tasks']" in both.stdout, both.stdout
    assert "'stacks': ['cloud_bridge', 'orchestrator']" in both.stdout, both.stdout

    single = _run("single")
    assert single.returncode == 0, f"单栈子进程异常退出: {single.stderr[-800:]}"
    assert "dual-stack" not in single.stderr, f"A 栈单开不得告警: {single.stderr[-800:]}"
    assert "DIAG=None" in single.stdout
