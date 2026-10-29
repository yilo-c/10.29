"""L6 安全 —— 注入/XSS 防护（集成级，独立临时后端实例）。

隔离策略：不使用共享 conftest 的 rpc fixture（避免污染用户真实后端数据），
而是本文件自启一个**独立临时后端**（随机端口 + 临时数据目录），全部写入操作
都落在临时实例上，测试结束自动清理。

覆盖：
1. XSS payload 内容存取往返：含 `<script>` / `onerror` 的内容被**原样存储**，
   读回一致（服务端不做执行/二次解析；转义责任在前端渲染层）。
2. 畸形参数 / NoSQL 风格注入参数：被 schema 校验或 handler 优雅拒绝，
   不抛异常、后端存活（ping 复查）。
3. 不存在资源删除：优雅处理，不崩溃。
4. 超大 params（>16MB）被服务端拒绝，后端存活。
"""

from __future__ import annotations

import pytest

from _isolated_backend import rpc, rpc_approved, spawn_backend, teardown_backend


@pytest.fixture(scope="module")
def isolated_backend():
    proc = ws_url = data_dir = None
    try:
        proc, ws_url, data_dir = spawn_backend()
        yield {"ws_url": ws_url, "data_dir": data_dir}
    finally:
        teardown_backend(proc, data_dir)


# ── XSS 内容存取往返 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_xss_content_roundtrip(isolated_backend) -> None:
    """XSS payload 被原样存储、原样读回（服务端不执行/不二次解析）。"""
    ws = isolated_backend["ws_url"]
    title = "<script>alert(document.cookie)</script>"
    content = '<img src=x onerror="alert(1)">\n<b>hello & world</b>'

    created = await rpc(ws, "notes.create", {"title": title, "content": content})
    assert "result" in created, f"create 失败: {created}"
    note_id = created["result"]["id"]
    assert note_id

    got = await rpc(ws, "notes.get", {"note_id": note_id})
    assert "result" in got, f"get 失败: {got}"
    note = got["result"]
    # 原样回读：服务端不做 sanitize / 转义（XSS 防护责任在前端渲染层）
    assert note["title"] == title
    assert note["content"] == content

    # notes.delete 属 R4 破坏性方法（P0-6 之后需审批）：必须走
    # pending → sandbox.approve → 重放 链路，否则只拿到 pending、删除并未发生。
    deleted = await rpc_approved(ws, "notes.delete", {"note_id": note_id})
    assert "result" in deleted
    assert not (deleted.get("result") or {}).get("pending"), f"删除仍卡在审批：{deleted}"

    gone = await rpc(ws, "notes.get", {"note_id": note_id})
    assert "result" in gone and gone["result"] is None


# ── 畸形 / 注入参数优雅拒绝 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_malformed_note_id_no_crash(isolated_backend) -> None:
    """NoSQL 风格/类型错误的 note_id 被优雅拒绝，不崩溃。"""
    ws = isolated_backend["ws_url"]
    for bad in ({"$ne": 1}, [], ["1", "2"], {"$gt": ""}):
        resp = await rpc(ws, "notes.get", {"note_id": bad})
        # 合法信封：要么 result（含 None），要么 error；绝不能是异常泄漏
        assert "result" in resp or "error" in resp, f"非法信封: {resp}"

    # 后端仍存活
    ping = await rpc(ws, "ping", {})
    assert ping.get("result") == "pong"


@pytest.mark.asyncio
async def test_sql_injection_string_no_crash(isolated_backend) -> None:
    """SQL 注入风格字符串参数被当普通数据/拒绝，不崩溃。"""
    ws = isolated_backend["ws_url"]
    resp = await rpc(ws, "notes.get", {"note_id": "1'; DROP TABLE notes;--"})
    assert "result" in resp or "error" in resp
    resp2 = await rpc(ws, "notes.delete", {"note_id": "abc OR 1=1"})
    assert "result" in resp2 or "error" in resp2


@pytest.mark.asyncio
async def test_delete_nonexistent_graceful(isolated_backend) -> None:
    """删除不存在的资源：优雅返回，不抛异常。"""
    ws = isolated_backend["ws_url"]
    resp = await rpc(ws, "notes.delete", {"note_id": 999999999})
    assert "result" in resp or "error" in resp


@pytest.mark.asyncio
async def test_oversized_payload_rejected(isolated_backend) -> None:
    """超大 params（>服务端 WS 上限 16MB）被服务端拒绝，后端存活。"""
    ws = isolated_backend["ws_url"]
    big = {"title": "x" * (16 * 1024 * 1024 + 1024)}  # >16MB
    resp = await rpc(ws, "notes.create", big, timeout=60)
    assert "error" in resp  # 被服务端拒绝（连接/消息过大）
    ping = await rpc(ws, "ping", {})
    assert ping.get("result") == "pong"
