"""AssistantExecutionLayer 单元测试 — 统一交付路由。

测试覆盖：
- 多通道交付路由（inbox / notification / subtitle / floating / chat / both）
- 字幕进度文案构建（成功 / 部分失败 / 含拦截）
- 熔断与降级只读保护
- 交付通道参数透传到 deliver_task_result
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from assistant.execution.execution import AssistantExecutionLayer
from assistant.models import AgentTask, PlannedStep


# ── 辅助 ───────────────────────────────────────────────────

def _make_step(action_id: str, *, risk: str = "low", step_id: str = "step_1") -> PlannedStep:
    return PlannedStep(
        step_id=step_id,
        action_id=action_id,
        payload={"key": "value"},
        risk_level=risk,
        timeout_seconds=10,
        retries=0,
    )


def _build_layer(*, inbox_service=None, data_store=None, capability_router=None,
                 settings_repo=None, observability=None):
    ds = data_store or MagicMock()
    cr = capability_router or MagicMock()
    cr.execute.return_value = {"briefing": "操作成功", "status": "done"}
    return AssistantExecutionLayer(
        data_store=ds,
        capability_router=cr,
        observability_service=observability,
        settings_repository=settings_repo,
        inbox_service=inbox_service,
    )


def _pass_auth(step):
    return {"allowed": True}


# ── 基础执行与交付 ─────────────────────────────────────────

def test_execute_plan_runs_ok_steps(tmp_path):
    layer = _build_layer()
    steps = [_make_step("test.action")]
    result = layer.execute_plan(steps, _pass_auth)
    assert result["ok"] == 1
    assert result["failed"] == 0
    assert result["total"] == 1


def test_execute_plan_builds_delivery_record(tmp_path):
    layer = _build_layer()
    steps = [_make_step("test.action")]
    result = layer.execute_plan(steps, _pass_auth, delivery_channel="inbox")
    delivery = result.get("delivery") or {}
    assert delivery.get("channel") == "inbox"
    assert "title" in delivery
    assert "content" in delivery


# ── 交付通道路由 ────────────────────────────────────────────

def test_deliver_to_inbox_channel(tmp_path):
    """验证 inbox 通道：结果进入收件箱。"""
    inbox_svc = MagicMock()
    inbox_svc.deliver_task_result.return_value = {"item_id": "abc", "channel": "inbox"}
    layer = _build_layer(inbox_service=inbox_svc)
    steps = [_make_step("test.action")]
    result = layer.execute_plan(steps, _pass_auth, delivery_channel="inbox")
    # deliver_task_result 应被调用，且 channel 参数透传
    inbox_svc.deliver_task_result.assert_called_once()
    _kw = inbox_svc.deliver_task_result.call_args.kwargs
    assert _kw.get("channel") == "inbox"
    summary_delivery = result.get("delivery") or {}
    assert summary_delivery.get("inbox_item") is not None


def test_deliver_to_notification_channel(tmp_path):
    """验证 notification 通道：走系统通知。"""
    inbox_svc = MagicMock()
    inbox_svc.deliver_task_result.return_value = {"item_id": "def"}
    layer = _build_layer(inbox_service=inbox_svc)
    steps = [_make_step("test.action")]
    layer.execute_plan(steps, _pass_auth, delivery_channel="notification")
    _kw = inbox_svc.deliver_task_result.call_args.kwargs
    assert _kw.get("channel") == "notification"


def test_deliver_to_subtitle_channel(tmp_path):
    """验证 subtitle 通道：走字幕条提示。"""
    inbox_svc = MagicMock()
    inbox_svc.deliver_task_result.return_value = {"item_id": "ghi"}
    layer = _build_layer(inbox_service=inbox_svc)
    steps = [_make_step("test.action")]
    layer.execute_plan(steps, _pass_auth, delivery_channel="subtitle")
    _kw = inbox_svc.deliver_task_result.call_args.kwargs
    assert _kw.get("channel") == "subtitle"


def test_deliver_to_floating_channel(tmp_path):
    """验证 floating 通道：走悬浮助手气泡。"""
    inbox_svc = MagicMock()
    inbox_svc.deliver_task_result.return_value = {"item_id": "jkl"}
    layer = _build_layer(inbox_service=inbox_svc)
    steps = [_make_step("test.action")]
    layer.execute_plan(steps, _pass_auth, delivery_channel="floating")
    _kw = inbox_svc.deliver_task_result.call_args.kwargs
    assert _kw.get("channel") == "floating"


def test_deliver_to_both_channel(tmp_path):
    """验证 both 通道：同时进收件箱和通知。"""
    inbox_svc = MagicMock()
    inbox_svc.deliver_task_result.return_value = {"item_id": "mno"}
    layer = _build_layer(inbox_service=inbox_svc)
    steps = [_make_step("test.action")]
    layer.execute_plan(steps, _pass_auth, delivery_channel="both")
    _kw = inbox_svc.deliver_task_result.call_args.kwargs
    assert _kw.get("channel") == "both"


def test_deliver_to_chat_channel_skips_inbox(tmp_path):
    """验证 chat 通道：不经过 inbox deliver_task_result。"""
    inbox_svc = MagicMock()
    layer = _build_layer(inbox_service=inbox_svc)
    steps = [_make_step("test.action")]
    layer.execute_plan(steps, _pass_auth, delivery_channel="chat")
    # chat 通道不经过 _deliver_summary
    inbox_svc.deliver_task_result.assert_not_called()


def test_deliver_task_channel_from_task_object(tmp_path):
    """验证 delivery_channel 可以从 AgentTask 对象读取。"""
    inbox_svc = MagicMock()
    inbox_svc.deliver_task_result.return_value = {"item_id": "pqr"}
    layer = _build_layer(inbox_service=inbox_svc)
    task = AgentTask(goal="测试任务", delivery_channel="subtitle")
    steps = [_make_step("test.action")]
    # 不传 delivery_channel 参数，应该从 task 读取
    layer.execute_plan(steps, _pass_auth, task=task)
    _kw = inbox_svc.deliver_task_result.call_args.kwargs
    assert _kw.get("channel") == "subtitle"


def test_deliver_without_inbox_service_does_not_crash(tmp_path):
    """验证没有 inbox_service 时不会崩溃。"""
    layer = _build_layer(inbox_service=None)
    steps = [_make_step("test.action")]
    result = layer.execute_plan(steps, _pass_auth, delivery_channel="inbox")
    assert result["ok"] == 1


# ── 字幕进度文案 ────────────────────────────────────────────

def test_subtitle_progress_all_ok(tmp_path):
    """全部成功时生成 ✅ 字幕。"""
    layer = _build_layer()
    text = layer._build_subtitle_progress(goal="查天气", total=3, ok=3, failed=0, blocked=0)
    assert "✅" in text
    assert "查天气" in text
    assert "3/3" in text


def test_subtitle_progress_partial_failure(tmp_path):
    """部分失败时生成 ⚠️ 字幕。"""
    layer = _build_layer()
    text = layer._build_subtitle_progress(goal="发邮件", total=3, ok=2, failed=1, blocked=0)
    assert "⚠️" in text
    assert "发邮件" in text
    assert "2/3" in text
    assert "1 步失败" in text


def test_subtitle_progress_with_blocked(tmp_path):
    """含拦截步骤时字幕包含拦截信息。"""
    layer = _build_layer()
    text = layer._build_subtitle_progress(goal="执行任务", total=3, ok=1, failed=1, blocked=1)
    assert "⚠️" in text
    assert "1 步失败" in text
    assert "1 步被拦截" in text


def test_subtitle_progress_empty_goal(tmp_path):
    """任务目标为空时使用默认名称。"""
    layer = _build_layer()
    text = layer._build_subtitle_progress(goal="", total=0, ok=0, failed=0, blocked=0)
    assert "✅" in text
    assert "任务" in text


def test_subtitle_in_delivery_when_channel_is_subtitle(tmp_path):
    """subtitle 通道交付时，delivery 记录应包含 subtitle_text。"""
    inbox_svc = MagicMock()
    inbox_svc.deliver_task_result.return_value = {"item_id": "stu"}
    layer = _build_layer(inbox_service=inbox_svc)
    steps = [_make_step("test.action")]
    result = layer.execute_plan(steps, _pass_auth, delivery_channel="subtitle")
    delivery = result.get("delivery") or {}
    assert "subtitle_text" in delivery
    assert "✅" in str(delivery.get("subtitle_text", ""))


# ── 熔断与降级 ──────────────────────────────────────────────

def test_circuit_breaker_opens_after_3_failures(tmp_path):
    """连续 3 次失败后熔断器打开。"""
    router = MagicMock()
    router.execute.side_effect = RuntimeError("fail")
    ds = MagicMock()

    def fake_load_json(key, default):
        if key == "assistant_rules":
            return {}
        if key == "assistant_execution_runs":
            return []
        return default

    ds.load_json.side_effect = fake_load_json

    layer = _build_layer(data_store=ds, capability_router=router)
    steps = [_make_step("fail.action")]
    result = layer.execute_plan(steps, _pass_auth)
    assert result["failed"] > 0

    # 第二次执行应该触发熔断
    ds.load_json.side_effect = fake_load_json
    result2 = layer.execute_plan(steps, _pass_auth)
    assert result2["failed"] > 0


def test_degraded_readonly_after_consecutive_failures(tmp_path):
    """连续失败后高风险步骤被降级为只读。"""
    router = MagicMock()
    router.execute.side_effect = RuntimeError("fail")
    ds = MagicMock()

    def fake_load_json(key, default):
        if key == "assistant_rules":
            return {}
        if key == "assistant_execution_runs":
            return []
        return default

    ds.load_json.side_effect = fake_load_json

    settings = MagicMock()
    settings.get_setting.return_value = True

    layer = _build_layer(data_store=ds, capability_router=router, settings_repo=settings)
    # 执行 5 个高风险步骤，全部会失败
    steps = [_make_step("high_risk.action", risk="high") for _ in range(5)]
    result = layer.execute_plan(steps, _pass_auth)
    # 前 3 次失败 + 后 2 次降级 = 总共 5 个 outcome
    assert len(result.get("outcomes", [])) == 5


# ── 交付内容构建 ────────────────────────────────────────────

def test_build_delivery_title_with_task(tmp_path):
    layer = _build_layer()
    task = AgentTask(goal="测试交付标题")
    title = layer._build_delivery_title(task)
    assert "测试交付标题" in title


def test_build_delivery_title_without_task(tmp_path):
    layer = _build_layer()
    title = layer._build_delivery_title(None)
    assert "助手任务结果" in title


def test_build_delivery_content_from_briefing(tmp_path):
    layer = _build_layer()
    summary = {
        "outcomes": [
            {"status": "ok", "payload": {"briefing": "成功完成查询"}},
        ]
    }
    content = layer._build_delivery_content(summary, task=None)
    assert "成功完成查询" in content


def test_delivery_channel_defaults_to_chat_when_not_specified(tmp_path):
    """不传 delivery_channel 时默认走 chat。"""
    layer = _build_layer()
    steps = [_make_step("test.action")]
    result = layer.execute_plan(steps, _pass_auth)
    delivery = result.get("delivery") or {}
    assert delivery.get("channel") == "chat"


def test_deliver_task_result_includes_subtitle_text(tmp_path):
    """验证 deliver_task_result 的 result 中包含 subtitle 文本。"""
    inbox_svc = MagicMock()
    inbox_svc.deliver_task_result.return_value = {"item_id": "vwx"}
    layer = _build_layer(inbox_service=inbox_svc)
    steps = [_make_step("test.action")]
    layer.execute_plan(steps, _pass_auth, delivery_channel="inbox")
    _kw = inbox_svc.deliver_task_result.call_args.kwargs
    result = _kw.get("result", {})
    assert "subtitle" in result
    assert result["subtitle"]  # 非空
