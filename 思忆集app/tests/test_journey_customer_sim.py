"""模拟客户交互 · 跨模块 + 跨系统 集成旅程测试（代码为准，实测契约）

设计来源：全部方法名/参数/响应形状均由一次性探针实测确认，非臆造。
运行（自启隔离离线临时后端）：
    $env:SIYI_INTEGRATION_AUTOSTART=1
    pytest 思忆集app/tests/test_journey_customer_sim.py -m integration

覆盖三层：
  · 单模块可用性/稳定性：notes / tasks / wordbook+FSRS / knowledge / notification / task_queue / cron
  · 跨系统协调：写后读一致性、R4 审批闸门(method_registry↔sandbox↔重放)、事件目录一致性
  · 健壮性棘轮：未知方法、N-E 分页参数越界在 dispatch 层统一拒绝
"""
from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.integration


def _result(resp):
    """断言 RPC 成功并返回 result（失败则抛，带错误体）。"""
    assert isinstance(resp, dict), resp
    assert "error" not in resp, f"RPC 失败: {resp.get('error')}"
    return resp.get("result")


async def test_j1_backend_health_and_catalog(rpc):
    """连接与系统健康：ping / system.health / system.info / model.list 一致可用。"""
    assert _result(await rpc("ping")) == "pong"

    health = _result(await rpc("system.health"))
    assert "overall_severity" in health and isinstance(health.get("subsystems"), list)

    info = _result(await rpc("system.info"))
    assert info.get("version") and "ws_port" in info

    models = _result(await rpc("model.list"))
    assert isinstance(models, list) and len(models) >= 1


async def test_j2_notes_crud_roundtrip(rpc):
    """笔记写入→读取往返一致（Typora 式 markdown 落库真源）。"""
    marker = f"旅程-{uuid.uuid4().hex[:8]}"
    created = _result(await rpc("notes.create", {"title": marker, "content": f"正文 {marker}"}))
    nid = created["id"]
    assert nid is not None

    got = _result(await rpc("notes.get", {"note_id": nid}))
    assert marker in json.dumps(got, ensure_ascii=False)

    _result(await rpc("notes.update", {"note_id": nid, "content": f"{marker}-已更新"}))
    got2 = _result(await rpc("notes.get", {"note_id": nid}))
    assert f"{marker}-已更新" in json.dumps(got2, ensure_ascii=False)


async def test_j3_tasks_lifecycle(rpc):
    """待办创建 + 完成态切换（tasks.* 与 store 语义一致）。"""
    marker = f"任务-{uuid.uuid4().hex[:8]}"
    created = _result(await rpc("tasks.create", {"text": marker}))
    tid = created["id"]
    assert tid is not None

    # 切换完成态应成功返回（以 task_id 为参，实测契约）
    _result(await rpc("tasks.toggle", {"task_id": tid}))

    listing = _result(await rpc("tasks.list"))
    assert isinstance(listing, list)


async def test_j4_wordbook_srs_fsrs(rpc):
    """词库 → 加词 → 到期计数 → FSRS 复习：学习系统核心调度端到端。"""
    wb = _result(await rpc("wordbook.create", {"name": f"词本-{uuid.uuid4().hex[:6]}"}))
    wbid = wb["id"]

    word = _result(await rpc("word.add", {"wordbook_id": wbid, "word": "journey", "meaning": "旅程"}))
    wid = word["id"]

    cards = _result(await rpc("word.list", {"wordbook_id": wbid}))
    assert isinstance(cards, list) and cards

    due = _result(await rpc("word.count_due", {"wordbook_id": wbid}))
    assert "count" in due

    review = _result(await rpc("word.review", {"word_id": wid, "quality": 4}))
    # FSRS 调度器真实产出：复习次数 / 易度因子 / 间隔 / 下次复习
    assert review.get("success") is not False
    for key in ("repetitions", "efactor", "interval", "next_review"):
        assert key in review, f"FSRS 输出缺少 {key}: {review}"


async def test_j5_knowledge_network(rpc):
    """知识网络后端：图谱 / 知识卡 / 主题 MOC 三读端点结构一致。"""
    graph = _result(await rpc("knowledge.graph"))
    assert isinstance(graph.get("entities"), list) and isinstance(graph.get("relations"), list)

    cards = _result(await rpc("knowledge.cards", {"days": 7}))
    assert isinstance(cards, list)

    mocs = _result(await rpc("knowledge.mocs"))
    assert isinstance(mocs, list)


async def test_j6_multi_system_directory_coherence(rpc):
    """各子系统协调统一：收件/通知/队列/调度/审批/同步读端点均返回契约结构。"""
    assert "items" in _result(await rpc("notification.list"))

    stats = _result(await rpc("notification.stats", {"days": 7}))
    for key in ("total", "unread", "by_event", "by_priority", "subscribed_events", "enabled"):
        assert key in stats, f"notification.stats 缺 {key}"

    tq = _result(await rpc("task_queue.list"))
    assert tq.get("success") is True and isinstance(tq.get("tasks"), list)

    jobs = _result(await rpc("cron.list", {"include_disabled": True}))
    assert jobs.get("success") is True and "jobs" in jobs
    assert (_result(await rpc("cron.status"))).get("success") is True

    assert isinstance(_result(await rpc("sandbox.list_pending")), list)
    assert "confirmations" in _result(await rpc("confirmation.list"))
    assert isinstance(_result(await rpc("transfer.history")), list)

    ss = _result(await rpc("sync.status"))
    assert {"enabled", "configured", "running"} <= set(ss)
    conflicts = _result(await rpc("sync.conflicts.list"))
    assert "conflicts" in conflicts and "count" in conflicts


async def test_j7_write_read_consistency_across_subsystems(rpc):
    """跨子系统写后读一致性：笔记子系统与词库子系统各自持久化立即可查。"""
    marker = f"一致性-{uuid.uuid4().hex[:8]}"
    nid = _result(await rpc("notes.create", {"title": marker, "content": marker}))["id"]
    assert marker in json.dumps(_result(await rpc("notes.get", {"note_id": nid})), ensure_ascii=False)

    wb = _result(await rpc("wordbook.create", {"name": f"一致性本-{uuid.uuid4().hex[:6]}"}))
    _result(await rpc("word.add", {"wordbook_id": wb["id"], "word": marker, "meaning": marker}))
    listed = json.dumps(_result(await rpc("word.list", {"wordbook_id": wb["id"]})), ensure_ascii=False)
    assert marker in listed


async def test_j8_destructive_approval_coordination(rpc):
    """受控执行铁律：R4 破坏性方法未审批→入 sandbox 队列；审批后重放执行。

    验证 method_registry(R4 闸门) ↔ sandbox_manager(队列) ↔ 审批重放 的跨系统协调。
    """
    nid = _result(await rpc("notes.create", {"title": "待销毁", "content": "x"}))["id"]

    pending = _result(await rpc("notes.permanent_delete", {"note_id": nid}))
    assert pending.get("pending") is True, f"R4 应返回 pending，实得：{pending}"
    pid = pending.get("pending_id")
    assert pid

    ack = _result(await rpc("sandbox.approve", {"pending_id": pid}))
    assert isinstance(ack, dict) and "error" not in (ack or {})


async def test_j9_dispatch_layer_robustness(rpc):
    """健壮性棘轮：未知方法 → JSON-RPC 错误；分页越界在 dispatch 层统一拒绝(-32602)。"""
    unknown = await rpc("definitely.not.a.method", {})
    assert "error" in unknown, "未知方法应返回 JSON-RPC error"

    # N-E 范围棘轮：limit 越界 → -32602（与 handler 无关，dispatch 层拦截）
    over = await rpc("notes.list", {"limit": 10**9})
    assert over.get("error", {}).get("code") == -32602, f"越界 limit 未被棘轮拒绝：{over}"

    nonint = await rpc("notes.list", {"limit": "not-a-number"})
    assert nonint.get("error", {}).get("code") == -32602, f"非法 limit 未被棘轮拒绝：{nonint}"


async def test_j10_ai_chat_fail_fast_when_unconfigured(rpc):
    """AI 对话旅程：离线/未配置时 chat.send 友好 fail-fast，不挂起、不进回退链。

    实测契约：result 为 chunk 文本（list 或 str），携带「尚未配置 / API Key」友好提示。
    """
    res = _result(await rpc("chat.send", {"message": "你好"}))
    text = "".join(res) if isinstance(res, list) else str(res)
    assert ("尚未配置" in text) or ("API Key" in text), f"未得到友好未配置提示：{res}"


async def test_j11_url_learning_guards(rpc):
    """URL 学习旅程边界：空输入被校验拒绝；内网地址被 SSRF 安全闸门拦截。"""
    empty = _result(await rpc("url_learning.learn", {"url": ""}))
    assert isinstance(empty, dict) and empty.get("error"), f"空 url 未校验：{empty}"

    internal = _result(await rpc("url_learning.learn", {"url": "http://127.0.0.1:9/x"}))
    assert isinstance(internal, dict) and internal.get("error"), f"内网地址未被拦截：{internal}"
    assert "内网" in internal["error"] or "安全" in internal["error"], f"SSRF 拦截文案异常：{internal}"


async def test_j12_transfer_send_history_roundtrip(rpc):
    """跨设备传送往返：transfer.send 落库后 transfer.history 可查回该条。"""
    marker = f"传送-{uuid.uuid4().hex[:8]}"
    sent = _result(await rpc("transfer.send", {"text": marker}))
    assert sent.get("success") is True, f"transfer.send 未成功：{sent}"

    history = _result(await rpc("transfer.history"))
    assert isinstance(history, list)
    assert any(marker in json.dumps(item, ensure_ascii=False) for item in history), f"history 未含本次传送：{history[:3]}"
