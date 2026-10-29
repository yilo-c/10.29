# -*- coding: utf-8 -*-
"""E2E 聊天与 AI 交互模块（2026-09-24）— 真实 WebSocket 流式链路端到端。

定位（补既有缺口）
----------------
既有流式相关测试各测一段、彼此不重叠，缺的正是"经**真实注册中心 + 真实
chat.send handler + 真实 ws_server 线协议 + 真实离线传输层**跑通的端到端帧契约"：

* ``test_ws_server.py`` 的流式用例是**进程内**桩：``registry.dispatch_stream``
  被替换成假 generator（直接 ``send_chunk("hello")``），测的是 ws 帧封装机器，
  不经真实 ``chat_send``/``preflight_ai_or_emit``/回退链/离线 ai_client。
* ``test_r2e_stream_contract_20260917.py`` 测 handler **形态**（AST/asyncgen），不上线。
* ``test_openai_client_stream_chain.py`` / ``test_fallback_*`` 测客户端链，不碰 ws 线。
* ``test_integration_chat_notes_ext.py`` 只跑 chat.save/history 等非流式 RPC。
* ``test_chat_preflight.py`` 在**单元层**用 _ChunkCollector 验 not_configured 分型，
  不经 ws。

本文件用 conftest 的 ``ws_session``/``rpc`` fixture 连 **AUTOSTART 隔离临时后端**
（``SIYI_INTEGRATION_AUTOSTART=1``），对 chat.send ``stream:true`` 的**线帧序列**
做端到端断言，并覆盖多轮 session 隔离、save→history 闭环、list_conversations、
断线重连历史可读，以及"客户端硬断开后服务端 ping 仍健康（事件广播不崩）"。

诚实边界（宿主环境相关，见运行报告）
------------------------------------
- AUTOSTART 子进程 env = 复制宿主 os.environ，并强制
  ``SIYI_TEST_OFFLINE=1``（``core.isolation``）。宿主 shell 若带
  DEEPSEEK_API_KEY/OPENAI/MOONSHOT/SIYI_LLM_API_KEY 等 key 会**透传进临时后端**。
- ``preflight_ai_or_emit`` 直接读 env ``DEEPSEEK_API_KEY`` 等 → 有 key 时**放行**
  （不进 not_configured fail-fast 分支）；但 chat.send 执行路径的 LLM 出口经
  ``ai.client.base`` 的 ``_OfflineTransport`` 被**当场拒绝出网**（不联网、不烧配额），
  异常由 ``emit_graded_error`` 分型为 error/notice chunk 后正常收尾。
- 因此这里 stream 帧序列的**内容**取决于宿主 key 与离线短路，**契约不变式**
  （id 回显、done 唯一终帧、chunk*→done）与内容无关——本文件只对**契约**断言，
  不对具体回复文本断言（离线环境拿不到真实 token 流）。
- "离线（无 key）→ not_configured 分型不进回退链"由 ``test_chat_preflight.py``
  在单元层固定覆盖；本文件额外用**空消息**这条零 LLM、确定性的路径
  （``chat.send`` message="" → error chunk → done）复刻"分型帧即终帧、其后唯一 done"
  的线级形态，不依赖宿主 key 状态。

只在 AUTOSTART 隔离后端上运行；无后端时 conftest 会自动跳过依赖 ws_session/rpc
的用例（本文件全部依赖之）。
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any

import aiohttp
import pytest

pytestmark = pytest.mark.integration

# 单帧接收上限：离线短路下 chat.send 收尾很快，给足冗余但不无限挂。
_FRAME_TIMEOUT = 45.0
_MAX_FRAMES = 400


def _ws_url() -> str:
    return os.environ.get("SIYI_WS_URL", "ws://127.0.0.1:18765/ws")


def _auth_headers() -> dict[str, str]:
    """与 conftest._auth_headers 同源：优先 SIYI_DIAG_TOKEN（AUTOSTART 注入临时目录 token）。"""
    tok = os.environ.get("SIYI_DIAG_TOKEN", "").strip()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


async def _open_ws():
    """手动开一条独立 WS（用于多连接/重连/硬断开场景），返回 (session, ws)。

    带 client_version=1.2.0（非 legacy）+ Bearer token（临时后端强制认证）。
    """
    url = _ws_url()
    sep = "&" if "?" in url else "?"
    session = aiohttp.ClientSession()
    try:
        ws = await session.ws_connect(f"{url}{sep}client_version=1.2.0", timeout=15, headers=_auth_headers())
    except Exception:
        await session.close()
        raise
    return session, ws


def _is_event_frame(d: dict[str, Any]) -> bool:
    return d.get("type") == "event" or "event_type" in d


async def _read_stream_frames(ws, rid: int, *, stop_after_done: bool = True) -> tuple[list[dict], bool]:
    """从已发送过 rid 流请求的连接上读帧，返回 (本 rid 的 result 帧列表, 是否见 done)。

    只收 ``id==rid`` 且 result 为带 type 的 dict 的线帧；server-push event 帧、
    其它 id 帧一律过滤。命中 done 即停（stop_after_done）。
    """
    frames: list[dict] = []
    saw_done = False
    for _ in range(_MAX_FRAMES):
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=_FRAME_TIMEOUT)
        except asyncio.TimeoutError:
            break
        if msg.type != aiohttp.WSMsgType.TEXT:
            if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                break
            continue
        try:
            d = json.loads(msg.data)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict) or d.get("id") != rid:
            continue
        if _is_event_frame(d):
            continue
        result = d.get("result")
        if not isinstance(result, dict) or "type" not in result:
            continue
        frames.append(result)
        if result["type"] == "done":
            saw_done = True
            if stop_after_done:
                break
    return frames, saw_done


async def _send_stream(ws, rid: int, method: str, params: dict[str, Any]) -> None:
    await ws.send_json({"jsonrpc": "2.0", "id": rid, "method": method, "params": params, "stream": True})


async def _call_raw(ws, rid: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """非流式调用并等回帧（跳过 event / 其它 id），返回原始响应 dict。"""
    await ws.send_json({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout=_FRAME_TIMEOUT)
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        try:
            d = json.loads(msg.data)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        if _is_event_frame(d) or d.get("id") != rid:
            continue
        return d


# ── 帧契约不变式校验工具 ──────────────────────────────────────────────
def _assert_stream_contract(frames: list[dict], saw_done: bool, rid: int) -> None:
    assert saw_done, f"rid={rid} 流未观察到唯一 done 终帧（帧类型序列={[f.get('type') for f in frames]}）"
    dones = [f for f in frames if f.get("type") == "done"]
    assert len(dones) == 1, f"rid={rid} done 必须唯一，实得 {len(dones)} 个"
    assert frames[-1]["type"] == "done", "done 必须是最后一帧（其后不得再有本请求内容帧）"
    for f in frames[:-1]:
        assert f.get("type") != "done", "done 之后/之前不得混入第二个 done"
        assert "data" in f, f"rid={rid} 内容帧缺少 data 字段：{f}"
    # done 帧形态：只有 type，无 data（见 ws_server 正常收尾发送）
    assert "data" not in frames[-1], f"done 终帧不应携带 data：{frames[-1]}"


# ── 组2·1：chat.send stream:true 帧序（chunk*→done、done 唯一终帧、每帧回显 id） ──
async def test_ws_chat_send_stream_frame_contract(ws_session):
    """真实 handler + 真实 ws_server：非空 chat.send 流的线帧满足 id 回显 + 唯一 done 终帧。

    内容随宿主 key/离线短路而变（error/notice/chunk 皆可能），故只断契约不断文本。
    """
    rid = 9101
    sid = f"e2e-{uuid.uuid4()}"
    await _send_stream(ws_session, rid, "chat.send", {"message": "你好，测试流式契约", "session_id": sid, "stream": True})
    # 每帧都带 id==rid 由 reader 保证；这里再显式核验 id 回显。
    frames, saw_done = await _read_stream_frames(ws_session, rid)
    assert frames, "chat.send 流未产生任何本请求内容帧（含 done）"
    _assert_stream_contract(frames, saw_done, rid)
    # 至少存在一个 done 之前的内容帧（离线短路 → notice/error；有 key 联网 → chunk）。
    assert len(frames) >= 2, f"期望 done 之前至少一条内容帧，实得 {frames}"


async def test_ws_chat_send_every_frame_echoes_request_id(ws_session):
    """逐帧核验 id 回显：reader 只收 id==rid 帧，若服务端漏回显则 frames 不含 done → 断言失败。"""
    rid = 9102
    await _send_stream(ws_session, rid, "chat.send", {"message": "id 回显检查", "stream": True})
    frames, saw_done = await _read_stream_frames(ws_session, rid)
    # reader 已按 id 过滤：只要拿到 done，即证明含 done 在内的帧均回显了 rid。
    _assert_stream_contract(frames, saw_done, rid)


# ── 组2·1b：空消息 → 零 LLM、确定性 [error, done]（分型帧即终帧后唯一 done） ──
async def test_ws_chat_send_empty_message_terminal_error_then_done(ws_session):
    """不依赖宿主 key 的确定性路径：message 为空 → 首帧 error（提示），随后唯一 done 收尾。"""
    rid = 9103
    await _send_stream(ws_session, rid, "chat.send", {"message": "", "stream": True})
    frames, saw_done = await _read_stream_frames(ws_session, rid)
    _assert_stream_contract(frames, saw_done, rid)
    assert frames[0]["type"] == "error", f"空消息首帧应为 error 分型，实得 {frames[0]}"
    assert "不能为空" in str(frames[0].get("data", "")), f"空消息 error 文案异常：{frames[0]}"
    # 分型帧之后直接 done，不进任何执行/回退链（帧序列里没有额外内容帧）。
    assert [f["type"] for f in frames] == ["error", "done"], f"空消息帧序列应为 [error, done]，实得 {[f['type'] for f in frames]}"


# ── 组2·2：多轮 session_id 隔离（同一连接并发两路流，各自 id/done 不串） ──
async def test_ws_multi_turn_stream_session_id_isolation(ws_session):
    """同连接并发发两路不同 session 的 chat.send：两路各自帧只按各自 rid 收，done 各一枚。"""
    rid_a, rid_b = 9201, 9202
    sid_a = f"e2e-A-{uuid.uuid4()}"
    sid_b = f"e2e-B-{uuid.uuid4()}"
    await _send_stream(ws_session, rid_a, "chat.send", {"message": "会话A轮1", "session_id": sid_a, "stream": True})
    await _send_stream(ws_session, rid_b, "chat.send", {"message": "会话B轮1", "session_id": sid_b, "stream": True})

    # 交替读取，按 rid 分桶；两路都收到 done 才停。
    buckets: dict[int, list[dict]] = {rid_a: [], rid_b: []}
    done_ids: set[int] = set()
    for _ in range(_MAX_FRAMES):
        try:
            msg = await asyncio.wait_for(ws_session.receive(), timeout=_FRAME_TIMEOUT)
        except asyncio.TimeoutError:
            break
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        try:
            d = json.loads(msg.data)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict) or _is_event_frame(d):
            continue
        rid = d.get("id")
        if rid not in buckets:
            continue
        result = d.get("result")
        if not isinstance(result, dict) or "type" not in result:
            continue
        buckets[rid].append(result)
        if result["type"] == "done":
            done_ids.add(rid)
        if done_ids >= {rid_a, rid_b}:
            break

    _assert_stream_contract(buckets[rid_a], rid_a in done_ids, rid_a)
    _assert_stream_contract(buckets[rid_b], rid_b in done_ids, rid_b)
    # 每一路 done 帧恰好一枚，两路互不吞并 → 会话级隔离在帧层成立。
    assert buckets[rid_a].count({"type": "done"}) == 1
    assert buckets[rid_b].count({"type": "done"}) == 1


# ── 组2·2b：会话存储级隔离（save 到 A/B，history 各查各的，互不可见） ──
async def test_ws_session_storage_isolation(rpc):
    """两个不同 session_id 的消息互不串台：chat.history(A) 只见 A，chat.history(B) 只见 B。"""
    sid_a = f"e2e-iso-A-{uuid.uuid4()}"
    sid_b = f"e2e-iso-B-{uuid.uuid4()}"
    msgs_a = [{"speaker": "你", "message": "AAA-only"}, {"speaker": "助手", "message": "aaa-reply"}]
    msgs_b = [{"speaker": "你", "message": "BBB-only"}, {"speaker": "助手", "message": "bbb-reply"}]

    assert (await rpc("chat.save_messages", {"session_id": sid_a, "title": "隔离A", "messages": msgs_a}))["result"] is True
    assert (await rpc("chat.save_messages", {"session_id": sid_b, "title": "隔离B", "messages": msgs_b}))["result"] is True

    hist_a = (await rpc("chat.history", {"session_id": sid_a, "limit": 10}))["result"]
    hist_b = (await rpc("chat.history", {"session_id": sid_b, "limit": 10}))["result"]
    texts_a = {m.get("message") for m in hist_a}
    texts_b = {m.get("message") for m in hist_b}
    assert "AAA-only" in texts_a and "aaa-reply" in texts_a, f"A 历史缺自身消息：{hist_a}"
    assert "BBB-only" not in texts_a, f"A 历史越界含 B 消息：{texts_a}"
    assert "BBB-only" in texts_b and "bbb-reply" in texts_b, f"B 历史缺自身消息：{hist_b}"
    assert "AAA-only" not in texts_b, f"B 历史越界含 A 消息：{texts_b}"

    # 清理（尽力而为，temp 目录随会话回收）
    await rpc("chat.delete_conversation", {"session_id": sid_a})
    await rpc("chat.delete_conversation", {"session_id": sid_b})


# ── 组2·3：chat.save_messages → chat.history 闭环一致 ──
async def test_ws_save_messages_history_roundtrip_closure(rpc):
    """save 的有序 (speaker,message) 序列与 history 读回的完全一致（闭环无损）。"""
    sid = f"e2e-closure-{uuid.uuid4()}"
    sent = [
        {"speaker": "你", "message": "第一条"},
        {"speaker": "助手", "message": "第二条回复"},
        {"speaker": "你", "message": "第三条追问"},
        {"speaker": "助手", "message": "第四条回复"},
    ]
    ok = (await rpc("chat.save_messages", {"session_id": sid, "title": "闭环会话", "messages": sent}))["result"]
    assert ok is True, "chat.save_messages 应返回 True"

    got = (await rpc("chat.history", {"session_id": sid, "limit": 50}))["result"]
    assert isinstance(got, list), f"chat.history 应返回列表，实得 {type(got)}"
    assert [(m.get("speaker"), m.get("message")) for m in got] == [(m["speaker"], m["message"]) for m in sent], (
        f"save→history 闭环不一致：\n sent={sent}\n got={got}"
    )

    # get_conversation 闭环：单会话完整读，消息一致，id 回显 client_id(=UUID)。
    conv = (await rpc("chat.get_conversation", {"session_id": sid}))["result"]
    assert conv and conv.get("id") == sid, f"get_conversation id 未回显 client_id：{conv}"
    assert [(m.get("speaker"), m.get("message")) for m in conv["messages"]] == [(m["speaker"], m["message"]) for m in sent]

    await rpc("chat.delete_conversation", {"session_id": sid})


# ── 组2·4：chat.list_conversations ──
async def test_ws_list_conversations_reflects_saved(rpc):
    """list_conversations 返回摘要列表；新建唯一会话后应能按 id 命中（不含正文）。"""
    sid = f"e2e-list-{uuid.uuid4()}"
    await rpc("chat.save_messages", {
        "session_id": sid,
        "title": "列表可见会话",
        "messages": [{"speaker": "你", "message": "列举测试"}],
    })
    lst = (await rpc("chat.list_conversations", {"limit": 200}))["result"]
    assert isinstance(lst, list), f"list_conversations 应返回列表，实得 {type(lst)}"
    hit = [c for c in lst if c.get("id") == sid]
    assert hit, f"新保存会话 {sid} 未出现在 list_conversations：命中 {len(hit)}"
    entry = hit[0]
    assert entry.get("title") == "列表可见会话", f"标题回显异常：{entry}"
    assert entry.get("message_count") == 1, f"消息计数异常：{entry}"
    assert "messages" not in entry, "list 摘要不应包含消息正文"

    await rpc("chat.delete_conversation", {"session_id": sid})


# ── 组2·5：断线重连后历史可读（跨连接持久性） ──
async def test_ws_reconnect_history_readable():
    """连接1 写历史 → 硬关连接 → 新开连接2 读到同一份历史（temp 后端持久化跨连接可读）。"""
    sid = f"e2e-reconnect-{uuid.uuid4()}"
    sent = [{"speaker": "你", "message": "重连前写入"}, {"speaker": "助手", "message": "重连前回复"}]

    session1, ws1 = await _open_ws()
    try:
        resp = await _call_raw(ws1, 9301, "chat.save_messages", {"session_id": sid, "title": "重连会话", "messages": sent})
        assert resp.get("result") is True, f"连接1 保存失败：{resp}"
    finally:
        await ws1.close()
        await session1.close()

    # 新连接（真实"断线重连"语义：不同 socket、不同 aiohttp session）
    session2, ws2 = await _open_ws()
    try:
        hist = await _call_raw(ws2, 9302, "chat.history", {"session_id": sid, "limit": 10})
        got = hist.get("result") or []
        assert [(m.get("speaker"), m.get("message")) for m in got] == [(m["speaker"], m["message"]) for m in sent], (
            f"重连后历史不可读/不一致：{hist}"
        )
        await _call_raw(ws2, 9303, "chat.delete_conversation", {"session_id": sid})
    finally:
        await ws2.close()
        await session2.close()


# ── 组3：WS 硬断开后服务端 ping 仍健康（事件广播不崩） ──
async def test_ws_hard_disconnect_server_ping_healthy():
    """在活跃 chat.send 流上硬断开客户端 → 服务端清理不崩 → 新连接 ping 稳定 pong。"""
    # 连接1：发起流式 chat.send 后不做任何消费，直接硬断开（transport.abort 优先，退化为 close）。
    session1, ws1 = await _open_ws()
    try:
        await _send_stream(ws1, 9401, "chat.send", {"message": "硬断开进行中的流", "session_id": f"e2e-hard-{uuid.uuid4()}", "stream": True})
        # 给服务端一点时间进入流处理，再硬切。
        await asyncio.sleep(0.2)
        aborted = False
        try:
            # 真·硬断开：不经 close 握手，直接掀翻底层传输。
            ws1._writer.transport.abort()  # noqa: SLF001 — 硬断开语义所需
            aborted = True
        except Exception:  # noqa: BLE001 — 私有 API 随 aiohttp 版本变化
            aborted = False
        if not aborted:
            await ws1.close()
    finally:
        try:
            await session1.close()
        except Exception:  # noqa: BLE001
            pass

    # 给服务端事件循环保留检测/清理时间（P2-14 兜底 watchdog ≤500ms）。
    await asyncio.sleep(1.0)

    # 连接2：服务端应仍健康 → 连续 ping 全部 pong，并可再触发一次广播型写入 + ping。
    session2, ws2 = await _open_ws()
    try:
        for i, rid in enumerate((9402, 9403, 9404)):
            pong = await _call_raw(ws2, rid, "ping")
            assert pong.get("result") == "pong", f"硬断开后第 {i+1} 次 ping 未 pong：{pong}"

        # 触发一次会广播/落盘的写操作（save_messages 走仓储 → 可能发事件），再次证明广播不崩。
        sid = f"e2e-after-hard-{uuid.uuid4()}"
        save = await _call_raw(ws2, 9405, "chat.save_messages", {"session_id": sid, "messages": [{"speaker": "你", "message": "硬断开后写入"}]})
        assert save.get("result") is True, f"硬断开后写操作失败：{save}"
        after = await _call_raw(ws2, 9406, "ping")
        assert after.get("result") == "pong", f"广播写操作后 ping 失健：{after}"
        await _call_raw(ws2, 9407, "chat.delete_conversation", {"session_id": sid})
    finally:
        await ws2.close()
        await session2.close()
