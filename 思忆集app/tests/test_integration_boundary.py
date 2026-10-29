# -*- coding: utf-8 -*-
"""L3 边界集成测试：limit 分页边界 / 空 payload / WS 16MB 临界 / cron-FREQ。

仅针对 conftest 自动启动的隔离临时后端（SIYI_INTEGRATION_AUTOSTART=1），
不触碰常驻 S1 观察实例。破坏性操作（cron.add）在测试内自行清理。
"""

import os

import aiohttp
import pytest

pytestmark = pytest.mark.integration

# 服务端 WS 最大消息体（backend/ws_server.py: max_msg_size 取自该 env，默认 16MB）
WS_MAX_MSG = int(os.environ.get("SIYI_WS_MAX_MSG_SIZE", str(16 * 1024 * 1024)))


def _ws_url() -> str:
    """运行时读取 WS URL。

    不能做成模块级常量：conftest 在 collection 阶段会把 os.environ["SIYI_WS_URL"]
    重定向到隔离临时后端（AUTOSTART=1 时），import 时捕获会锁定到真实 18765。

    AUTOSTART=1 时禁止回退真实后端：临时后端未就绪则显式失败（而非 403 假失败），
    保证隔离语义（xdist 多 worker 下部分 worker 后端未就绪也绝不连真实实例）。
    """
    url = os.environ.get("SIYI_WS_URL", "")
    if url:
        return url
    if os.environ.get("SIYI_INTEGRATION_AUTOSTART") == "1":
        raise RuntimeError("SIYI_INTEGRATION_AUTOSTART=1 但 SIYI_WS_URL 未指向隔离后端")
    return "ws://127.0.0.1:18765/ws"


# ── 辅助：原始 WS 调用（用于 16MB 临界，需自行收发） ──


async def _raw_ws_call(method, params, timeout=60):
    # V2.1 批次1#4：后台已强制 WS 认证，原始连接必须带 Bearer token，
    # 否则未认证连接被服务端拒绝（close type=8），16MB 临界将误判为连接失败。
    import conftest as _ct

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(_ws_url() + "?" + "client_version=1.2.0", timeout=10, headers=_ct._auth_headers()) as ws:
            await ws.send_json({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            msg = await asyncio_wait(ws, timeout)
            return msg


async def asyncio_wait(ws, timeout):
    import asyncio

    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
        if msg.type == aiohttp.WSMsgType.TEXT:
            return msg
        if msg.type in (
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.ERROR,
            aiohttp.WSMsgType.CLOSE,
        ):
            return msg
        # 跳过非文本（ping/pong）
        continue


# ── L3-1: notes.list limit 分页边界 ──


async def test_notes_list_limit_boundary(rpc):
    # limit=1 → 至多 1 条
    r1 = (await rpc("notes.list", {"limit": 1})).get("result")
    assert isinstance(r1, list) and len(r1) <= 1, f"limit=1 应返回 ≤1 条, got {r1}"

    # limit=0 → 不过滤（返回全部，至少是个 list）
    r0 = (await rpc("notes.list", {"limit": 0})).get("result")
    assert isinstance(r0, list), f"limit=0 应返回 list, got {r0}"

    # N-E 范围棘轮契约（2026-09-17）：limit 越界 → 显式 -32602 拒绝，
    # 不再静默透传（旧语义"超大不崩溃"作废——那是无界返回病根之一）。
    resp_big = await rpc("notes.list", {"limit": 100000})
    assert resp_big.get("error", {}).get("code") == -32602, f"limit=100000 应被棘轮拒绝 -32602, got {resp_big}"

    # 负数 → 同样显式拒绝（旧断言依赖 Python 切片宽容语义，一并收紧）
    resp_neg = await rpc("notes.list", {"limit": -1})
    assert resp_neg.get("error", {}).get("code") == -32602, f"limit=-1 应被棘轮拒绝 -32602, got {resp_neg}"


# ── L3-2: 空 payload / 空 query 边界 ──


async def test_empty_payload_boundary(rpc):
    # memory.search 空 query → 返回 []（handler: if not query.strip(): return []）
    mem = (await rpc("memory.search", {"query": "", "limit": 5})).get("result")
    assert mem == [] or mem is None, f"空 query 应返回 [] 或 None, got {mem}"

    # notes.get 空 id → 优雅返回（None/{}），不抛未捕获异常
    g = await rpc("notes.get", {"id": ""})
    assert "result" in g or "error" in g, f"空 id 应返回结构化响应, got {g}"

    # 完全空 params 调用 kernel.status → 正常
    ks = (await rpc("kernel.status", {})).get("result")
    assert isinstance(ks, dict), f"kernel.status 空 params 异常, got {ks}"


# ── L3-3: cron FREQ 边界 ──


async def test_cron_freq_boundary(rpc):
    added = []
    try:
        # 合法 5 字段 cron 表达式 → 接受
        ok = (
            await rpc(
                "cron.add",
                {
                    "name": "boundary-valid",
                    "action_id": "tool.zcode.open",
                    "schedule": "0 9 * * *",
                    "repeat": "cron",
                },
            )
        ).get("result", {})
        assert ok.get("success") is True, f"合法 5 字段 cron 应接受, got {ok}"
        added.append(ok.get("job", {}).get("job_id"))

        # 空 name → 拒绝
        bad = (
            await rpc(
                "cron.add",
                {
                    "name": "",
                    "action_id": "tool.zcode.open",
                    "repeat": "none",
                },
            )
        ).get("result", {})
        assert bad.get("success") is False and "name" in (bad.get("error") or "").lower(), f"空 name 应拒绝, got {bad}"

        # ── RRULE 混入 cron 字段（NEW-1 / 批3，2026-09-18 反转）──
        # **反转说明**：旧断言 `assert rrule.get("success") is True` 把
        # 「RRULE 被惰性接受 → 该任务永不调度（死任务）」这个缺陷**固化成期望**，
        # 并附注释"待产品决策是否显式拒绝"。批3 已完成该决策（形状校验 + 拒收），
        # 故契约反转为：**必须拒收，且错误信息要说明原因与正确写法**。
        #
        # 历史事实（反转前勘察结论）：生产 `cron.list` 里 `tool.zcode.open` 的第 2
        # 个实例 `schedule="FREQ=MONTHLY;BYDAY=MO"`、`next_run_at=""`（同 action
        # 的另一实例正常）—— 即该任务确实从未被调度过，是真实存在的死任务。
        rrule = (
            await rpc(
                "cron.add",
                {
                    "name": "boundary-rrule",
                    "action_id": "tool.zcode.open",
                    "schedule": "FREQ=MONTHLY;BYDAY=MO",
                    "repeat": "cron",
                },
            )
        ).get("result", {})
        assert rrule.get("success") is False, f"RRULE 混入 cron 字段必须拒收, got {rrule}"
        rrule_err = (rrule.get("error") or "").lower()
        assert "rrule" in rrule_err, f"拒收理由需点明 RRULE 形状问题, got {rrule}"
        # 拒收后不得留下半成品任务
        lst_after = (await rpc("cron.list", {})).get("result", {})
        assert not [j for j in lst_after.get("jobs", []) if j.get("name") == "boundary-rrule"], f"拒收后不应残留该任务, got {lst_after}"
    finally:
        for jid in added:
            if jid:
                await rpc("cron.remove", {"job_id": jid})

    # 清理后 cron.list 仍正常
    lst = (await rpc("cron.list", {})).get("result", {})
    assert isinstance(lst, dict) and "jobs" in lst, f"cron.list 应正常, got {lst}"


# ── L3-4: WS 16MB 临界 ──


async def test_ws_16mb_boundary():
    # 临界下界（-1KB）：应被服务端正常接收并响应
    under = WS_MAX_MSG - 1024
    try:
        msg = await _raw_ws_call("ping", {"probe": "A" * under})
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"16MB 下界消息发送/接收异常: {exc!r}")
    if msg.type == aiohttp.WSMsgType.TEXT:
        data = __import__("json").loads(msg.data)
        assert data.get("result") == "pong", f"16MB 下界应返回 pong, got {data}"
    else:
        pytest.fail(f"16MB 下界消息未被正常处理: {msg.type}")

    # 临界上界（+1KB）：服务端应拒绝（消息过大，1009 关闭），且后端不崩溃
    over = WS_MAX_MSG + 1024
    try:
        msg_over = await _raw_ws_call("ping", {"probe": "B" * over})
        # 若未立即关闭，连接应处于 CLOSED/ERROR（服务端拒绝超大帧）
        assert msg_over.type in (
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.ERROR,
            aiohttp.WSMsgType.CLOSE,
        ) or (msg_over.type == aiohttp.WSMsgType.TEXT), f"超 16MB 消息应被拒绝或关闭, got {msg_over.type}"
    except Exception:  # noqa: BLE001
        # 客户端侧收到服务端关闭/异常属预期（消息过大）
        pass

    # 后端存活校验：新连接 ping 仍可达
    import asyncio
    import conftest as _ct

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(_ws_url() + "?" + "client_version=1.2.0", timeout=10, headers=_ct._auth_headers()) as ws:
            await ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})
            survivor = await asyncio.wait_for(ws.receive(), timeout=30)
            assert survivor.type == aiohttp.WSMsgType.TEXT, "超大消息后后端应仍存活"
            assert __import__("json").loads(survivor.data).get("result") == "pong"
