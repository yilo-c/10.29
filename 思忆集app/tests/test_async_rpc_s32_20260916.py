"""S3-2 慢同步 RPC 测试 —— 回退版（2026-09-16 架构复审，audit §1）。

背景：S3-2 曾把六处 RPC 入口改为「秒回 job_id 信封 + 前端轮询」，但前端消费方
从未适配轮询（见 deliverables/architecture-conformance-audit-2026-09-16.md）：
  - translate.text/full  前端读 translated → 恒 undefined（快速/深度翻译坏死）
  - evolution.scan_and_report 前端按同步形状判成败 → 入队瞬间假报「扫描完成」
  - personal.review     前端把返回值直接渲染回顾内容 → 渲染的是信封
  - knowledge.brief / report.daily_summary  前端零消费者（死改动）
故 RPC 入口已回退为「同步直调 run_*，返回终局结果」；异步骨架（_async_rpc.py、
cron_actions worker、*.status 轮询器、*.completed 事件）**完整保留**，待 S3-2b
先建前端轮询消费层后再翻转入口。

本测试因此拆成两层断言，每处方法各覆盖：

1) **入口契约回归**（红→绿的反向钉子）：dispatch RPC 返回的是**终局内容 dict**
   —— 含前端读取的真实字段（translated / section / stats / markdown /
   report_length…），且**不含** job_id / status=queued 信封键。这条正是防止
   S3-2「信封不忠实」复发（共因 A）的回归测试。
2) **骨架端到端仍可用**：经 queued_rpc 直接入队 → cron worker 执行 →
   dispatch *.status 轮询拿到真结果 + *.completed 事件发出。证明保留的异步
   底座对 S3-2b 仍完好（无人从 RPC 入口入队时它零成本，入队即正常跑）。

隔离：SIYI_DATA_DIR 指临时目录、PersistentTaskQueue 用 tmp_path 建库，
所有 service/repo 均为内存假件，绝不触碰生产 local_storage.db / task_queue.db。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from backend.handlers._async_rpc import queued_rpc
from backend.method_registry import MethodRegistry
from services.cron_actions import execute_cron_action
from services.task_queue import PersistentTaskQueue


# ── 隔离 fixture ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SIYI_DATA_DIR", str(tmp_path / "siyi_data"))


@pytest.fixture
def queue(tmp_path):
    q = PersistentTaskQueue(db_path=str(tmp_path / "task_queue.db"))
    yield q
    q.close_all()


def _run_worker(queue: PersistentTaskQueue, ctx, job_id: str):
    """模拟 cron worker：dequeue(cron) → 经 cron action 执行 → complete。"""
    task = queue.dequeue(queue_name="cron")
    assert task is not None and task.task_id == job_id
    out = execute_cron_action(ctx, task.capability_id, dict(task.payload))
    assert out is not None
    assert queue.complete(task.task_id)
    return out


def _subscribe(event_name: str):
    from core.events import event_bus

    got: list = []
    event_bus.on(event_name, got.append)
    return got


def _assert_not_envelope(r: dict) -> None:
    """回退断言：RPC 入口必须返回终局内容、绝非入队信封。"""
    assert "job_id" not in r, f"入口仍返回异步信封（含 job_id）：{r}"
    assert "task_id" not in r, f"入口仍返回异步信封（含 task_id）：{r}"
    assert r.get("status") != "queued", f"入口仍返回 queued 信封：{r}"


# ── 假件 ──────────────────────────────────────────────────────────


class SlowAI:
    """翻译/总结用的慢假 AI：记录调用；被同步等待则明显变慢。"""

    def __init__(self, full_json: dict | None = None, text: str = "你好世界"):
        self.complete_calls = 0
        self.chat_calls = 0
        self._text = text
        self._full_json = full_json or {
            "translated": "译文",
            "words": [{"word": "w", "phonetic": "/w/", "meaning": "意", "context": "ctx", "examples": ["e"]}],
            "grammar": {"tenses": ["present"], "structure": "SVO", "grammarPoints": []},
        }

    def complete_text(self, messages, max_tokens=0):
        self.complete_calls += 1
        time.sleep(0.02)
        return self._text

    def complete_text_json(self, messages, max_tokens=0):
        self.complete_calls += 1
        time.sleep(0.02)
        return json.dumps(self._full_json, ensure_ascii=False)

    def chat_completion(self, messages, temperature=0.0, max_tokens=0):
        self.chat_calls += 1
        time.sleep(0.02)
        return {"content": "这是本阶段你关注的几个话题的总结。"}


class FakeRepo:
    def __init__(self, items: list):
        self.list_calls = 0
        self._items = items

    def list(self, limit=500, created_after=None, **kwargs):
        self.list_calls += 1
        return list(self._items)


# ══════════════════════════════════════════════════════════════════
# 1) personal.review
# ══════════════════════════════════════════════════════════════════


def _personal_ctx(queue, ai, repo):
    reg = MethodRegistry()
    from backend.handlers._personal import register_personal

    ctx = SimpleNamespace(task_queue=queue, ai_client=ai, raw_item_repo=repo)
    register_personal(reg, ctx)
    return reg, ctx


def test_personal_review_sync_terminal_not_envelope(queue):
    """入口回归：生产 task_queue 可用时也必须同步返回回顾内容，而非 job_id 信封。"""
    ai = SlowAI()
    repo = FakeRepo([{"source": "chat", "sensitivity": "personal", "content": "今天写了代码", "meta": {"sender": "张三"}}])
    reg, _ = _personal_ctx(queue, ai, repo)

    r = asyncio.run(reg.dispatch("personal.review", {"period": "week"}))

    _assert_not_envelope(r)
    assert r["success"] is True
    assert r["stats"]["total"] == 1  # 前端 PersonalModule 直接渲染的内容 dict
    assert r["summary"] == "这是本阶段你关注的几个话题的总结。"
    assert repo.list_calls == 1 and ai.chat_calls == 1


def test_personal_review_skeleton_roundtrip(queue):
    """骨架保留：经 queued_rpc 入队 → worker → personal.review.status 轮询拿真结果。"""
    ai = SlowAI()
    repo = FakeRepo([{"source": "chat", "sensitivity": "personal", "content": "今天写了代码", "meta": {"sender": "张三"}}])
    reg, ctx = _personal_ctx(queue, ai, repo)
    got = _subscribe("personal.review.completed")
    try:
        enq = asyncio.run(queued_rpc(ctx, capability_id="personal.review", payload={"period": "week"}))
        assert enq["success"] is True and enq["status"] == "queued" and enq["job_id"]
        job_id = enq["job_id"]
        assert repo.list_calls == 0  # 入队阶段绝不执行

        st0 = asyncio.run(reg.dispatch("personal.review.status", {"job_id": job_id}))
        assert st0["status"] == "queued" and st0["success"] is True

        _run_worker(queue, ctx, job_id)

        st1 = asyncio.run(reg.dispatch("personal.review.status", {"job_id": job_id}))
        assert st1["success"] is True
        assert st1["status"] == "completed"
        assert st1["stats"]["total"] == 1
        assert st1["summary"] == "这是本阶段你关注的几个话题的总结。"
        assert repo.list_calls == 1 and ai.chat_calls == 1
    finally:
        from core.events import event_bus

        event_bus.off("personal.review.completed", got.append)
    assert len(got) == 1
    assert got[0].payload["job_id"] == job_id


def test_personal_review_works_without_queue():
    """无 task_queue 的精简启动同样返回终局内容（回退后同步是唯一路径）。"""
    ai = SlowAI()
    repo = FakeRepo([{"source": "chat", "sensitivity": "personal", "content": "hello", "meta": {}}])
    reg, _ = _personal_ctx(None, ai, repo)
    r = asyncio.run(reg.dispatch("personal.review", {"period": "week"}))
    assert r["success"] is True
    _assert_not_envelope(r)
    assert "stats" in r and r["summary"]


# ══════════════════════════════════════════════════════════════════
# 2) evolution.scan_and_report
# ══════════════════════════════════════════════════════════════════


class FakeBriefing:
    def to_dict(self):
        return {"section": "技术雷达早报", "items": [{"name": "X", "score": 0.9}]}


class FakeOrch:
    def __init__(self):
        self.calls = 0

    def scan_and_report(self):
        self.calls += 1
        time.sleep(0.02)
        return FakeBriefing()


def _evolution_ctx(queue, orch):
    from backend.handlers._evolution import register_evolution

    reg = MethodRegistry()
    ctx = SimpleNamespace(task_queue=queue, evolution_orchestrator=orch)
    register_evolution(reg, ctx)
    return reg, ctx


def test_evolution_scan_sync_terminal_not_envelope(queue):
    """入口回归：ManagementModule 靠 res.error 判成败、随后立即 reload —— 必须同步返回终局。"""
    orch = FakeOrch()
    reg, _ = _evolution_ctx(queue, orch)
    r = asyncio.run(reg.dispatch("evolution.scan_and_report", {}))
    _assert_not_envelope(r)
    assert r["section"] == "技术雷达早报"  # 扫描真跑完、终局早报到手
    assert orch.calls == 1


def test_evolution_scan_skeleton_roundtrip(queue):
    orch = FakeOrch()
    reg, ctx = _evolution_ctx(queue, orch)
    got = _subscribe("evolution.scan_and_report.completed")
    try:
        enq = asyncio.run(queued_rpc(ctx, capability_id="evolution.scan_and_report", payload={}))
        job_id = enq["job_id"]
        assert orch.calls == 0  # 入队阶段不执行
        _run_worker(queue, ctx, job_id)
        st = asyncio.run(reg.dispatch("evolution.scan_and_report.status", {"job_id": job_id}))
        assert st["success"] is True and st["status"] == "completed"
        assert st["section"] == "技术雷达早报"
        assert orch.calls == 1
    finally:
        from core.events import event_bus

        event_bus.off("evolution.scan_and_report.completed", got.append)
    assert len(got) == 1


def test_evolution_scan_business_error_completed(queue):
    """orchestrator 缺失：runner 正常返回 {"error": ...} → 任务落 completed（不污染终态），
    错误原文交回客户端。"""
    from backend.handlers._evolution import register_evolution

    reg = MethodRegistry()
    ctx = SimpleNamespace(task_queue=queue, evolution_orchestrator=None)
    register_evolution(reg, ctx)
    enq = asyncio.run(queued_rpc(ctx, capability_id="evolution.scan_and_report", payload={}))
    job_id = enq["job_id"]
    _run_worker(queue, ctx, job_id)
    st = asyncio.run(reg.dispatch("evolution.scan_and_report.status", {"job_id": job_id}))
    assert st["status"] == "completed"
    assert st["success"] is False and "unavailable" in st["error"]


def test_evolution_scan_sync_business_error_when_orch_missing(queue):
    """回退后同步入口同样带 error（非信封、非 success）：与前端 res?.error 判定契约一致。"""
    from backend.handlers._evolution import register_evolution

    reg = MethodRegistry()
    ctx = SimpleNamespace(task_queue=queue, evolution_orchestrator=None)
    register_evolution(reg, ctx)
    r = asyncio.run(reg.dispatch("evolution.scan_and_report", {}))
    _assert_not_envelope(r)
    assert "unavailable" in r["error"]


# ══════════════════════════════════════════════════════════════════
# 3) report.daily_summary
# ══════════════════════════════════════════════════════════════════


def _daily_ctx(queue):
    from backend.handlers._info_proxy import register_info_proxy

    reg = MethodRegistry()
    ctx = SimpleNamespace(task_queue=queue)
    register_info_proxy(reg, ctx)
    return reg, ctx


def test_daily_summary_sync_terminal_not_envelope(queue, monkeypatch):
    """入口回归：report.daily_summary RPC 已于 2026-09-23 审计波次①-c 下线（前端零消费者，
    日报正文走 cron 自动投递 + daily_summary.ready 事件）；非信封终态契约改由 cron 路径
    run_report_daily_summary 承接，另锁 RPC 注册表不再含该入口防复活。"""
    import backend.weekly_report_scheduler as wrs
    from backend.handlers._info_proxy import run_report_daily_summary

    async def _fake_run(target_date=None, context=None):
        return {"success": True, "report_length": 123, "inbox_id": 9, "stats": {"n": 1}}

    monkeypatch.setattr(wrs, "run_daily_summary", _fake_run)
    ctx = SimpleNamespace(task_queue=queue)
    r = asyncio.run(run_report_daily_summary(ctx, target_date="2026-09-16"))
    _assert_not_envelope(r)
    assert r["report_length"] == 123 and r["inbox_id"] == 9

    reg, _ = _daily_ctx(queue)
    # 2026-10-15 反转：report.daily_summary 注册已恢复（批次 A c6fb2bc7，用户拍板
    # 「不减任何功能」）——原断言锁定的是 58e5fa8 的下线结论。
    assert "report.daily_summary" in reg.list_methods(), "report.daily_summary 应已恢复注册"


def test_daily_summary_skeleton_roundtrip(queue, monkeypatch):
    import backend.weekly_report_scheduler as wrs

    seen = {}

    async def _fake_run(target_date=None, context=None):
        seen["target_date"] = target_date
        seen["ctx"] = context
        return {"success": True, "report_length": 123, "inbox_id": 9, "stats": {"n": 1}}

    monkeypatch.setattr(wrs, "run_daily_summary", _fake_run)

    reg, ctx = _daily_ctx(queue)
    got = _subscribe("report.daily_summary.completed")
    try:
        enq = asyncio.run(queued_rpc(ctx, capability_id="report.daily_summary", payload={"target_date": "2026-09-16"}))
        job_id = enq["job_id"]
        _run_worker(queue, ctx, job_id)
        st = asyncio.run(reg.dispatch("report.daily_summary.status", {"job_id": job_id}))
        assert st["success"] is True and st["status"] == "completed"
        assert st["report_length"] == 123 and st["inbox_id"] == 9
        assert seen["target_date"] == "2026-09-16"
        assert seen["ctx"] is ctx
    finally:
        from core.events import event_bus

        event_bus.off("report.daily_summary.completed", got.append)
    assert len(got) == 1


# ══════════════════════════════════════════════════════════════════
# 4) translate.text
# ══════════════════════════════════════════════════════════════════


class TranslateAI:
    def __init__(self, text="你好世界"):
        self.calls = 0
        self._text = text

    def complete_text(self, messages, max_tokens=0):
        self.calls += 1
        time.sleep(0.02)
        return self._text


def _translate_ctx(queue, ai):
    from backend.handlers._ai_translate import register_ai_translate

    reg = MethodRegistry()
    ctx = SimpleNamespace(task_queue=queue, ai_client=ai)
    register_ai_translate(reg, ctx)
    return reg, ctx


def test_translate_text_sync_terminal_not_envelope(queue):
    """入口回归（P0 修复主证）：前端读 transResp?.translated —— 必须返回 translated 终局字段，
    绝非只有 job_id 的信封（信封会导致快速翻译恒空）。"""
    ai = TranslateAI("译文内容")
    reg, _ = _translate_ctx(queue, ai)
    r = asyncio.run(reg.dispatch("translate.text", {"text": "hello", "source": "en", "target": "zh"}))
    _assert_not_envelope(r)
    assert r["original"] == "hello" and r["translated"] == "译文内容" and r["target"] == "zh"
    assert ai.calls == 1  # 同步直调真实现


def test_translate_text_empty_shortcircuits(queue):
    """空文本短路直返，不碰 AI、不入队。"""
    ai = TranslateAI()
    reg, _ = _translate_ctx(queue, ai)
    r = asyncio.run(reg.dispatch("translate.text", {"text": "   ", "source": "auto", "target": "zh"}))
    assert r == {"original": "", "translated": "", "source": "auto", "target": "zh"}
    assert ai.calls == 0


def test_translate_text_skeleton_roundtrip(queue):
    ai = TranslateAI("译文内容")
    reg, ctx = _translate_ctx(queue, ai)
    got = _subscribe("translate.text.completed")
    try:
        enq = asyncio.run(queued_rpc(ctx, capability_id="translate.text", payload={"text": "hello", "source": "en", "target": "zh"}))
        job_id = enq["job_id"]
        assert ai.calls == 0  # 入队阶段不执行 AI
        _run_worker(queue, ctx, job_id)
        st = asyncio.run(reg.dispatch("translate.text.status", {"job_id": job_id}))
        assert st["success"] is True and st["status"] == "completed"
        assert st["original"] == "hello" and st["translated"] == "译文内容" and st["target"] == "zh"
        assert ai.calls == 1
    finally:
        from core.events import event_bus

        event_bus.off("translate.text.completed", got.append)
    assert len(got) == 1
    assert got[0].payload["result"]["translated"] == "译文内容"


# ══════════════════════════════════════════════════════════════════
# 5) translate.full
# ══════════════════════════════════════════════════════════════════


class FullAI:
    def __init__(self, payload):
        self.calls = 0
        self._payload = payload

    def complete_text(self, messages, max_tokens=0):
        self.calls += 1
        time.sleep(0.02)
        return self._payload


def test_translate_full_sync_terminal_not_envelope(queue):
    """入口回归：前端读 fullResp?.translated/words/grammar —— 必须返回这三字段终局，
    绝非 job_id 信封（信封会导致深度翻译整块坏死）。"""
    payload = json.dumps(
        {
            "translated": "你好",
            "words": [{"word": "hi", "phonetic": "/haɪ/", "meaning": "你好", "context": "c", "examples": ["e"]}],
            "grammar": {"tenses": ["pres"]},
        },
        ensure_ascii=False,
    )
    ai = FullAI(payload)
    reg, _ = _translate_ctx(queue, ai)
    r = asyncio.run(reg.dispatch("translate.full", {"text": "hi", "source": "en", "target": "zh", "mode": "learn"}))
    _assert_not_envelope(r)
    assert r["translated"] == "你好"
    assert r["words"][0]["word"] == "hi"
    assert r["grammar"]["tenses"] == ["pres"]
    assert ai.calls == 1


def test_translate_full_skeleton_roundtrip(queue):
    payload = json.dumps(
        {
            "translated": "你好",
            "words": [{"word": "hi", "phonetic": "/haɪ/", "meaning": "你好", "context": "c", "examples": ["e"]}],
            "grammar": {"tenses": ["pres"]},
        },
        ensure_ascii=False,
    )
    ai = FullAI(payload)
    reg, ctx = _translate_ctx(queue, ai)
    got = _subscribe("translate.full.completed")
    try:
        enq = asyncio.run(queued_rpc(ctx, capability_id="translate.full", payload={"text": "hi", "source": "en", "target": "zh", "mode": "learn"}))
        job_id = enq["job_id"]
        assert ai.calls == 0
        _run_worker(queue, ctx, job_id)
        st = asyncio.run(reg.dispatch("translate.full.status", {"job_id": job_id}))
        assert st["success"] is True and st["status"] == "completed"
        assert st["translated"] == "你好"
        assert st["words"][0]["word"] == "hi"
        assert st["grammar"]["tenses"] == ["pres"]
        assert ai.calls == 1
    finally:
        from core.events import event_bus

        event_bus.off("translate.full.completed", got.append)
    assert len(got) == 1


def test_translate_full_bad_json_business_error_completed(queue):
    """AI 返回非 JSON：runner 正常返回含 error 的 dict → 任务落 completed，错误交客户端。"""
    ai = FullAI("这不是 JSON")
    reg, ctx = _translate_ctx(queue, ai)
    enq = asyncio.run(queued_rpc(ctx, capability_id="translate.full", payload={"text": "hi"}))
    job_id = enq["job_id"]
    _run_worker(queue, ctx, job_id)
    st = asyncio.run(reg.dispatch("translate.full.status", {"job_id": job_id}))
    assert st["status"] == "completed"
    assert st["success"] is False and "json" in st["error"].lower()


# ══════════════════════════════════════════════════════════════════
# 6) knowledge.brief（保持 register_with_schema 形态）
# ══════════════════════════════════════════════════════════════════


def _knowledge_ctx(queue):
    from backend.handlers._inbox_knowledge import register_inbox_knowledge

    reg = MethodRegistry()
    ctx = SimpleNamespace(task_queue=queue)
    register_inbox_knowledge(reg, ctx)
    return reg, ctx


def test_knowledge_brief_sync_terminal_not_envelope(queue, monkeypatch):
    """入口回归：前端零消费者，回退后同步返回简报 markdown（不经队列）。"""
    import backend.handlers._inbox_knowledge as ik

    seen = {}

    async def _fake_brief(context, *, days=7):
        seen["days"] = days
        return {"success": True, "markdown": "# 简报", "cards": 3, "entry_id": "e1"}

    monkeypatch.setattr(ik, "run_knowledge_brief", _fake_brief)
    reg, _ = _knowledge_ctx(queue)
    r = asyncio.run(reg.dispatch("knowledge.brief", {"days": 14}))
    _assert_not_envelope(r)
    assert r["markdown"] == "# 简报" and r["cards"] == 3
    assert seen["days"] == 14


def test_knowledge_brief_skeleton_roundtrip(queue, monkeypatch):
    import backend.handlers._inbox_knowledge as ik

    seen = {}

    async def _fake_brief(context, *, days=7):
        seen["days"] = days
        return {"success": True, "markdown": "# 简报", "cards": 3, "entry_id": "e1"}

    monkeypatch.setattr(ik, "run_knowledge_brief", _fake_brief)

    reg, ctx = _knowledge_ctx(queue)
    got = _subscribe("knowledge.brief.completed")
    try:
        enq = asyncio.run(queued_rpc(ctx, capability_id="knowledge.brief", payload={"days": 14}))
        job_id = enq["job_id"]
        assert queue.get(job_id).capability_id == "knowledge.brief"
        _run_worker(queue, ctx, job_id)
        st = asyncio.run(reg.dispatch("knowledge.brief.status", {"job_id": job_id}))
        assert st["success"] is True and st["status"] == "completed"
        assert st["markdown"] == "# 简报" and st["cards"] == 3
        assert seen["days"] == 14
    finally:
        from core.events import event_bus

        event_bus.off("knowledge.brief.completed", got.append)
    assert len(got) == 1


# ══════════════════════════════════════════════════════════════════
# 通用：轮询参数边界（*.status 骨架保留，仍注册）
# ══════════════════════════════════════════════════════════════════


def test_poll_requires_job_id(queue):
    reg, _ = _translate_ctx(queue, TranslateAI())
    r = asyncio.run(reg.dispatch("translate.text.status", {}))
    assert r["success"] is False and r["error"] == "job_id required"


def test_poll_unknown_job(queue):
    reg, _ = _translate_ctx(queue, TranslateAI())
    r = asyncio.run(reg.dispatch("translate.text.status", {"job_id": "nope"}))
    assert r["success"] is False and r["error"] == "task not found"


def test_poll_int_job_id_does_not_crash(queue):
    """客户端把 job_id 传成 int 不得 AttributeError（回归自样板 scrape_status）。"""
    reg, _ = _translate_ctx(queue, TranslateAI())
    r = asyncio.run(reg.dispatch("translate.text.status", {"job_id": 999999}))
    assert r is not None and r["success"] is False
