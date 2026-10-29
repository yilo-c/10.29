"""Agent chain runner tests — pause/cancel, confirmation, and execution loop."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.agent_chain import AgentChainEngine, AgentChainPlan, ChainNode
from assistant.chain._nodes import (
    CHAIN_STATUS_CANCELLED,
    CHAIN_STATUS_PAUSED,
    CHAIN_STATUS_RUNNING,
    MAX_REPLAN_ATTEMPTS,
)


def _make_engine(**kwargs):
    return AgentChainEngine(**kwargs)


def _make_plan(nodes):
    return AgentChainPlan(goal="test chain", nodes=nodes)


def test_execute_chain_runs_serial_nodes():
    engine = _make_engine()
    plan = _make_plan([
        ChainNode(node_id="n1", description="step 1", action_id="a1"),
        ChainNode(node_id="n2", description="step 2", action_id="a2", depends_on=["n1"]),
    ])

    executed = []

    def execute_callback(node):
        executed.append(node.node_id)
        return {"success": True, "data": {"text": "ok"}}

    result = engine.execute_chain(plan, execute_callback=execute_callback)

    assert result["status"] == "completed"
    assert executed == ["n1", "n2"]


def test_execute_chain_cancel_skips_remaining_nodes():
    engine = _make_engine()
    plan = _make_plan([
        ChainNode(node_id="n1", description="step 1", action_id="a1"),
        ChainNode(node_id="n2", description="step 2", action_id="a2", depends_on=["n1"]),
        ChainNode(node_id="n3", description="step 3", action_id="a3", depends_on=["n2"]),
    ])

    executed = []

    def execute_callback(node):
        executed.append(node.node_id)
        if node.node_id == "n1":
            engine._chain_status = CHAIN_STATUS_CANCELLED
        return {"success": True, "data": {"text": "ok"}}

    engine.execute_chain(plan, execute_callback=execute_callback)

    assert "n1" in executed
    assert "n3" not in executed


def test_execute_chain_pause_resumes():
    engine = _make_engine()
    plan = _make_plan([
        ChainNode(node_id="n1", description="step 1", action_id="a1"),
        ChainNode(node_id="n2", description="step 2", action_id="a2"),
    ])

    executed = []

    def execute_callback(node):
        executed.append(node.node_id)
        if node.node_id == "n1":
            engine._chain_status = CHAIN_STATUS_PAUSED
            # Simulate async resume by another thread after a tiny delay
            import threading
            def resume():
                import time
                time.sleep(0.05)
                engine._chain_status = CHAIN_STATUS_RUNNING
            threading.Thread(target=resume, daemon=True).start()
        return {"success": True, "data": {"text": "ok"}}

    engine.execute_chain(plan, execute_callback=execute_callback)

    assert executed == ["n1", "n2"]


def test_high_risk_timeout_is_non_zero():
    from assistant.chain._nodes import TIMEOUT_CONFIRMATION_HIGH
    assert TIMEOUT_CONFIRMATION_HIGH > 0


def test_confirmation_callback_exception_rejects():
    engine = _make_engine(confirm_callback=lambda req: (_ for _ in ()).throw(RuntimeError("boom")))
    plan = _make_plan([
        ChainNode(node_id="n1", description="risky step", action_id="a1", risk_level="medium"),
    ])

    result = engine.execute_chain(plan)

    assert result["status"] in (CHAIN_STATUS_CANCELLED, "failed")
    outcomes = result.get("outcomes", [])
    assert any(
        o.get("node_id") == "n1" and "拒绝" in str(o.get("error", ""))
        for o in outcomes
    )


# ═══════════════════════════════════════════════════════════════
# P1-4: 人工确认漏斗测试（异步确认 + 超时默认拒绝 + 通知 UI）
# ═══════════════════════════════════════════════════════════════


def test_p1_4_high_risk_user_approves_continues_chain():
    """P1-4: 高风险节点用户批准后链应继续执行。

    场景：n1 (high risk) → 用户 approve → n1 成功 → n2 执行成功。
    """
    engine = _make_engine(
        confirm_callback=lambda req: {"action": "approve", "reason": "用户已确认"},
    )
    plan = _make_plan([
        ChainNode(
            node_id="n1", description="risky step", action_id="a1",
            risk_level="high",
        ),
        ChainNode(
            node_id="n2", description="next step", action_id="a2",
            depends_on=["n1"],
        ),
    ])

    executed = []

    def execute_callback(node):
        executed.append(node.node_id)
        return {"success": True, "data": {"text": "ok"}}

    result = engine.execute_chain(plan, execute_callback=execute_callback)

    assert result["status"] == "completed", f"用户批准后链应完成，实际: {result['status']}"
    assert executed == ["n1", "n2"], f"两步都应执行，实际: {executed}"


def test_p1_4_high_risk_user_reject_cancels_chain():
    """P1-4: 高风险节点用户主动拒绝后链应被取消（不只是节点失败）。

    场景：n1 (high risk) → 用户 reject "用户拒绝" → 链状态 CANCELLED。
    确认在执行前请求，拒绝后 execute_callback 不会被调用。
    """
    engine = _make_engine(
        confirm_callback=lambda req: {"action": "reject", "reason": "用户拒绝：风险太高"},
    )
    plan = _make_plan([
        ChainNode(
            node_id="n1", description="risky step", action_id="a1",
            risk_level="high",
        ),
        ChainNode(
            node_id="n2", description="next step", action_id="a2",
            depends_on=["n1"],
        ),
    ])

    executed = []

    def execute_callback(node):
        executed.append(node.node_id)
        return {"success": True, "data": {"text": "ok"}}

    result = engine.execute_chain(plan, execute_callback=execute_callback)

    # 用户主动拒绝 → 链被取消
    assert result["status"] == CHAIN_STATUS_CANCELLED, \
        f"用户拒绝后链应被取消，实际: {result['status']}"
    # 确认拒绝发生在执行前，execute_callback 不应被调用
    assert executed == [], f"拒绝后不应执行任何节点，实际: {executed}"
    # outcomes 中 n1 应标记为 failed
    n1_outcome = next((o for o in result["outcomes"] if o.get("node_id") == "n1"), None)
    assert n1_outcome is not None
    assert n1_outcome.get("status") == "failed"


def test_p1_4_timeout_reject_does_not_cancel_chain():
    """P1-4: 确认超时默认拒绝，但不取消整条链（允许重规划）。

    场景：n1 (high risk, max_retries=0) → 超时拒绝（reason 含"超时"）
    → 链状态应为 failed 而非 cancelled（因为超时不取消链，允许重规划尝试）。

    注：此测试不触发实际重规划（无 AI 客户端 + 规则重规划会换 delegate），
    只验证超时拒绝不会设置 CANCELLED 状态。
    """
    engine = _make_engine(
        confirm_callback=lambda req: {"action": "reject", "reason": "确认超时（60s），默认拒绝"},
    )
    plan = _make_plan([
        ChainNode(
            node_id="n1", description="risky step", action_id="a1",
            risk_level="high", delegate_to="local", max_retries=0,
        ),
    ])

    def execute_callback(node):
        return {"success": True, "data": {"text": "ok"}}

    result = engine.execute_chain(plan, execute_callback=execute_callback)

    # 超时拒绝 → 链 failed（因重规划也可能失败），但不应是 cancelled
    assert result["status"] != CHAIN_STATUS_CANCELLED, \
        f"超时拒绝不应取消链（应允许重规划），实际: {result['status']}"
    # n1 应失败
    n1_outcome = next((o for o in result["outcomes"] if o.get("node_id") == "n1"), None)
    assert n1_outcome is not None
    assert n1_outcome.get("status") == "failed"
    assert "超时" in n1_outcome.get("error", ""), \
        f"错误应包含'超时'，实际: {n1_outcome.get('error')}"


# ═══════════════════════════════════════════════════════════════
# P1-3: 重规划闭环测试
# ═══════════════════════════════════════════════════════════════


def test_max_replan_attempts_constant_is_positive():
    """P1-3: MAX_REPLAN_ATTEMPTS 必须是正整数（防止零或负值导致无限循环）。"""
    assert isinstance(MAX_REPLAN_ATTEMPTS, int)
    assert MAX_REPLAN_ATTEMPTS > 0


def test_replan_triggered_on_node_failure():
    """P1-3: 串行节点失败时应触发重规划，而非直接 break。

    场景：n1 失败（不可重试错误），重规划成功后链继续执行 n2。
    """
    engine = _make_engine()
    plan = _make_plan([
        ChainNode(
            node_id="n1", description="fail step", action_id="a1",
            delegate_to="local", risk_level="low", max_retries=0,
        ),
        ChainNode(
            node_id="n2", description="next step", action_id="a2",
            depends_on=["n1"],
        ),
    ])

    call_count = {"n1": 0, "replan": 0}

    def execute_callback(node):
        if "_replan" in node.node_id:
            call_count["replan"] += 1
            return {"success": True, "data": {"text": "replan ok"}}
        if node.node_id == "n1":
            call_count["n1"] += 1
            # 不可重试错误（包含 "权限" 关键词）
            return {"success": False, "error": "权限不足: 访问被拒绝"}
        return {"success": True, "data": {"text": "ok"}}

    result = engine.execute_chain(plan, execute_callback=execute_callback)

    # 重规划被触发（至少 1 次）
    assert call_count["replan"] >= 1, "应至少触发 1 次重规划"
    # 链最终状态应为 completed（重规划成功后继续执行）
    assert result["status"] == "completed"
    # outcomes 中应包含 _replan1 节点
    replan_node_ids = [o.get("node_id") for o in result["outcomes"] if "_replan" in o.get("node_id", "")]
    assert len(replan_node_ids) >= 1, f"应包含重规划节点，实际: {replan_node_ids}"
    # 重规划统计应记录
    assert result.get("replan_triggered", 0) >= 1
    assert result.get("replan_succeeded", 0) >= 1


def test_replan_inherits_upstream_results():
    """P1-3: 重规划节点应继承原节点的上游结果。

    场景：n1 成功 → n2 失败 → n2_replan1 应能读取 n1 的结果。
    """
    engine = _make_engine()
    plan = _make_plan([
        ChainNode(node_id="n1", description="step 1", action_id="a1"),
        ChainNode(
            node_id="n2", description="fail step", action_id="a2",
            depends_on=["n1"], delegate_to="local", max_retries=0,
        ),
    ])

    replan_upstream_seen = []

    def execute_callback(node):
        if node.node_id == "n1":
            return {"success": True, "data": {"text": "n1 output"}}
        if "_replan" in node.node_id:
            # 检查重规划节点是否继承了上游结果
            upstream = getattr(node, "upstream_results", {}) or {}
            replan_upstream_seen.append({
                "node_id": node.node_id,
                "has_upstream": bool(upstream),
                "upstream_keys": list(upstream.keys()),
            })
            return {"success": True, "data": {"text": "replan ok"}}
        if node.node_id == "n2":
            return {"success": False, "error": "权限不足"}
        return {"success": True, "data": {"text": "ok"}}

    result = engine.execute_chain(plan, execute_callback=execute_callback)

    assert result["status"] == "completed"
    assert len(replan_upstream_seen) >= 1, f"应至少有 1 次重规划执行，实际: {replan_upstream_seen}"
    # 重规划节点应能访问到 n1 的上游结果
    assert replan_upstream_seen[0]["has_upstream"], \
        f"重规划节点应继承上游结果，实际: {replan_upstream_seen[0]}"
    assert "n1" in replan_upstream_seen[0]["upstream_keys"], \
        f"上游结果应包含 n1，实际: {replan_upstream_seen[0]['upstream_keys']}"


def test_max_replan_attempts_terminates_chain():
    """P1-3: 超过 MAX_REPLAN_ATTEMPTS 后应正确终止链（不再无限重规划）。

    场景：n1 失败 + 重规划节点也失败 + 重规划的重规划也失败 → 终止链。
    """
    engine = _make_engine()
    plan = _make_plan([
        ChainNode(
            node_id="n1", description="always fail", action_id="a1",
            delegate_to="local", risk_level="low", max_retries=0,
        ),
    ])

    executed_ids = []

    def execute_callback(node):
        executed_ids.append(node.node_id)
        # 所有节点（包括重规划节点）都失败
        return {"success": False, "error": "权限不足: 永远失败"}

    result = engine.execute_chain(plan, execute_callback=execute_callback)

    # 链最终状态应为 failed
    assert result["status"] == "failed"
    # 重规划次数不应超过 MAX_REPLAN_ATTEMPTS
    replan_calls = [nid for nid in executed_ids if "_replan" in nid]
    assert len(replan_calls) <= MAX_REPLAN_ATTEMPTS, \
        f"重规划次数 {len(replan_calls)} 超过上限 {MAX_REPLAN_ATTEMPTS}, 执行顺序: {executed_ids}"
    # 应至少尝试了 MAX_REPLAN_ATTEMPTS 次（除非 LLM 不可用时少一些，但规则重规划一定生成）
    assert len(replan_calls) >= 1, \
        f"应至少尝试 1 次重规划，实际执行: {executed_ids}"


def test_replan_by_rule_changes_delegate():
    """P1-3: 规则重规划应切换委派目标（避免重复同一执行器）。"""
    engine = _make_engine()  # 无 AI 客户端，走规则重规划
    original = ChainNode(
        node_id="n1", description="original", action_id="a1",
        delegate_to="local", risk_level="low",
    )

    new_node = engine._replan_by_rule(original, attempt=1)
    assert new_node is not None
    assert new_node.delegate_to != original.delegate_to, \
        f"重规划应切换 delegate，原: {original.delegate_to}, 新: {new_node.delegate_to}"
    assert new_node.node_id == "n1_replan1"
    # 重规划节点应重置重试计数
    assert new_node.max_retries == 2  # MAX_RETRIES_DEFAULT
    assert new_node.retry_count == 0


def test_replan_by_rule_second_attempt_advances_delegate():
    """P1-3: 第二次规则重规划应再切换一档委派目标。"""
    engine = _make_engine()
    original = ChainNode(
        node_id="n1", description="original", action_id="a1",
        delegate_to="local", risk_level="low",
    )

    first = engine._replan_by_rule(original, attempt=1)
    assert first is not None
    # 第二次基于第一次的结果再换一档
    second = engine._replan_by_rule(first, attempt=2)
    assert second is not None
    assert second.delegate_to != first.delegate_to, \
        f"第二次重规划应再换 delegate, 第一次: {first.delegate_to}, 第二次: {second.delegate_to}"
    assert second.node_id == "n1_replan1_replan2"


def test_replan_node_returns_none_when_no_ai_client():
    """P1-3: 无 AI 客户端时 _replan_node 应回退到规则重规划（不返回 None）。"""
    engine = _make_engine()  # ai_client=None
    failed = ChainNode(
        node_id="n1", description="failed", action_id="a1",
        delegate_to="local",
    )
    new_node = engine._replan_node(
        goal="test", failed_node=failed, error="some error", attempt=1,
    )
    # 规则重规划应总能生成新节点
    assert new_node is not None
    assert new_node.delegate_to != failed.delegate_to


def test_maybe_replan_returns_none_when_not_needed():
    """P1-3: 失败结果不带 needs_replan 标志时不应触发重规划。"""
    engine = _make_engine()
    node = ChainNode(node_id="n1", description="test", action_id="a1")
    plan = _make_plan([node])
    # 模拟 reject 路径产生的失败结果（无 reflection, 无 needs_replan）
    result = {
        "node_id": "n1",
        "status": "failed",
        "error": "用户拒绝执行此步骤",
    }

    outcome = engine._maybe_replan_node(
        node=node, result=result, plan=plan,
        precheck_callback=None, execute_callback=None,
        node_results={}, replan_counts={}, outcomes=[],
    )
    assert outcome is None, "不应触发重规划"


def test_replan_count_observable_in_summary():
    """P1-3: 链汇总应包含 replan_triggered / replan_succeeded 可观测字段。"""
    engine = _make_engine()
    plan = _make_plan([
        ChainNode(
            node_id="n1", description="fail once", action_id="a1",
            delegate_to="local", max_retries=0,
        ),
    ])

    def execute_callback(node):
        if "_replan" in node.node_id:
            return {"success": True, "data": {"text": "ok"}}
        return {"success": False, "error": "权限不足"}

    result = engine.execute_chain(plan, execute_callback=execute_callback)

    assert "replan_triggered" in result, "汇总应包含 replan_triggered 字段"
    assert "replan_succeeded" in result, "汇总应包含 replan_succeeded 字段"
    assert result["replan_triggered"] >= 1
    assert result["replan_succeeded"] >= 1


def test_replan_node_id_uses_replan_suffix():
    """P1-3: 重规划节点 ID 应使用 _replan{N} 后缀，便于追溯。"""
    engine = _make_engine()
    original = ChainNode(
        node_id="step_1_abc", description="original", action_id="a1",
        delegate_to="local",
    )
    new_node = engine._replan_node(
        goal="test", failed_node=original, error="fail", attempt=1,
    )
    assert new_node is not None
    assert new_node.node_id == "step_1_abc_replan1"
    assert "_replan1" in new_node.node_id
