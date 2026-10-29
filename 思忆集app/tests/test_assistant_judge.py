from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.routing.judge import (
    JudgeDecision,
    JudgeScheduler,
)
from assistant.routing.judge_decision import (
    ROUTE_EXECUTOR_LOCAL,
    ROUTE_EXECUTOR_DEEPSEEK,
)


def _make_judge(ai_client=None):
    return JudgeScheduler(ai_client=ai_client)


def test_judge_routes_code_task_by_rule():
    """代码相关任务规则 fallback 应路由到 deepseek。"""
    judge = _make_judge()
    result = judge.route("帮我重构这个模块")
    assert result.executor == ROUTE_EXECUTOR_DEEPSEEK
    assert result.confidence >= 0.8


def test_judge_routes_translation_by_rule():
    """翻译任务规则 fallback 应路由到 deepseek。"""
    judge = _make_judge()
    result = judge.route("翻译这段话成英文")
    assert result.confidence >= 0.8


def test_judge_routes_research_by_rule():
    """搜索任务规则 fallback 应路由到 deepseek。"""
    judge = _make_judge()
    result = judge.route("搜索最新的 AI 论文")
    assert result.executor == ROUTE_EXECUTOR_DEEPSEEK
    assert result.confidence >= 0.8


def test_judge_routes_privacy_to_local():
    """隐私关键词任务应路由到 local。"""
    judge = _make_judge()
    result = judge.route("帮我看看这张照片")
    assert result.executor == ROUTE_EXECUTOR_LOCAL
    assert result.privacy is True


def test_judge_routes_greeting_to_local():
    """简单问候应路由到 local（省 token）。"""
    judge = _make_judge()
    result = judge.route("你好")
    assert result.executor == ROUTE_EXECUTOR_LOCAL
    assert result.confidence >= 0.8


def test_judge_falls_back_to_deepseek():
    """无法判断时默认路由到 deepseek。"""
    judge = _make_judge()
    result = judge.route("")
    assert result.executor == ROUTE_EXECUTOR_DEEPSEEK


def test_judge_decision_to_dict():
    """JudgeDecision to_dict 应包含新 v2 字段。"""
    d = JudgeDecision(
        executor=ROUTE_EXECUTOR_LOCAL,
        reason="test_routing",
        privacy=True,
        confidence=0.9,
    )
    serialized = d.to_dict()
    assert serialized["executor"] == ROUTE_EXECUTOR_LOCAL
    assert serialized["reason"] == "test_routing"
    assert serialized["privacy"] is True
    assert serialized["confidence"] == 0.9


def test_judge_records_stats():
    """路由统计应记录 executor。"""
    judge = _make_judge()
    judge.route("重构这段代码")
    judge.route("你好")
    stats = judge.get_stats()
    assert stats["total_routed"] == 2
    assert stats["by_executor"].get(ROUTE_EXECUTOR_DEEPSEEK, 0) >= 1
    assert stats["by_executor"].get(ROUTE_EXECUTOR_LOCAL, 0) >= 1


def test_judge_status_snapshot():
    judge = _make_judge()
    judge.route("写一个 Python 脚本")
    snapshot = judge.get_status_snapshot()
    assert snapshot["total_routed"] >= 1
    assert snapshot["last_route"] is not None


def test_judge_default_route_empty_input():
    judge = _make_judge()
    result = judge.route("")
    assert result.executor == ROUTE_EXECUTOR_DEEPSEEK


def test_cost_estimate_non_negative():
    judge = _make_judge()
    result = judge.route("帮我设计数据库 schema")
    assert result.estimated_cost_usd >= 0


# ── P1-15: 路由去重回归 ────────────────────────────────────────────
# _route_local_if_simple (step 0) 已优先拦截 PRIVACY / SIMPLE_TASK；
# _route_by_rules (step 3) 不应再包含相同分支。

def test_route_by_rules_no_longer_handles_privacy():
    """P1-15：privacy 关键词的命中应来自 step 0 (simple precheck) 而非 rules。"""
    judge = _make_judge()
    result = judge.route("帮我看看这张照片")
    assert result.executor == ROUTE_EXECUTOR_LOCAL
    assert result.privacy is True
    # step 0 的 reason 是 "privacy_precheck"；step 3 的旧 reason 是 "privacy_keyword_match"
    assert result.reason == "privacy_precheck"


def test_route_by_rules_no_longer_handles_simple_greeting():
    """P1-15：简单问候的命中应来自 step 0 而非 rules。"""
    judge = _make_judge()
    result = judge.route("你好")
    assert result.executor == ROUTE_EXECUTOR_LOCAL
    assert result.reason == "simple_greeting_precheck"


def test_route_by_rules_returns_none_for_pure_privacy_or_greeting():
    """P1-15：直接调 _route_by_rules，纯隐私/纯问候输入应不再命中（返回 None）。"""
    judge = _make_judge()
    assert judge._route_by_rules("帮我看看这张照片", {}) is None
    assert judge._route_by_rules("你好", {}) is None
    # 但仍能命中 code / analysis / search / translation 分支
    code_decision = judge._route_by_rules("refactor 这段代码", {})
    assert code_decision is not None
    assert code_decision.executor == ROUTE_EXECUTOR_DEEPSEEK


# ── P1-1: 监管组件装配 + 预算监管 ────────────────────────────────


def test_judge_accepts_cost_and_budget():
    """P1-1：JudgeScheduler 构造时应接收并保存 cost_tracker / budget_manager。"""
    from assistant.budget_manager import BudgetManager
    from assistant.cost_tracker import CostTracker

    budget = BudgetManager()
    cost = CostTracker()
    judge = JudgeScheduler(
        ai_client=None,
        cost_tracker=cost,
        budget_manager=budget,
    )
    assert judge._budget_manager is budget
    assert judge._cost_tracker is cost


def test_judge_route_records_cost_on_deepseek_routing():
    """P1-1：路由到 DEEPSEEK 时应记录成本到 CostTracker + BudgetManager。"""
    from assistant.budget_manager import BudgetManager
    from assistant.cost_tracker import CostTracker, TIER_JUDGE

    budget = BudgetManager()
    cost = CostTracker()
    judge = JudgeScheduler(
        ai_client=None,
        cost_tracker=cost,
        budget_manager=budget,
    )

    # 路由一个会命中 deepseek 的任务（规则 fallback）
    decision = judge.route("重构这段代码")
    assert decision.executor == ROUTE_EXECUTOR_DEEPSEEK

    # CostTracker 应记录了一次 judge_route 调用
    assert cost._today_total > 0, "cost_tracker 应记录非零成本"
    assert len(cost._records) == 1
    assert cost._records[0].tier == TIER_JUDGE
    assert cost._records[0].task_type == "judge_route"

    # BudgetManager 应记录了一次 usage
    status = budget.get_status()
    assert status.cost_usd > 0, "budget_manager 应记录非零成本"
    assert status.api_calls >= 1


def test_judge_route_records_zero_cost_on_local_routing():
    """P1-1：路由到 LOCAL 时成本记录为 0（不消耗云端 token）。"""
    from assistant.budget_manager import BudgetManager
    from assistant.cost_tracker import CostTracker, TIER_LOCAL

    budget = BudgetManager()
    cost = CostTracker()
    judge = JudgeScheduler(
        ai_client=None,
        cost_tracker=cost,
        budget_manager=budget,
    )

    # 简单问候 → local
    decision = judge.route("你好")
    assert decision.executor == ROUTE_EXECUTOR_LOCAL

    # CostTracker 记录了 LOCAL tier，成本为 0
    assert len(cost._records) == 1
    assert cost._records[0].tier == TIER_LOCAL
    assert cost._records[0].cost_usd == 0.0
    # BudgetManager 不应记录云端调用（cost=0 跳过）
    assert budget.get_status().cost_usd == 0.0


def test_judge_budget_exceeded_forces_local():
    """P1-1：预算超限时 route() 应强制 LOCAL 路由（拒绝调 DeepSeek）。

    验收标准：check_budget() 在超预算时被真实调用并产生拒绝决策。
    """
    from assistant.budget_manager import BudgetManager, BudgetLimit
    from assistant.cost_tracker import CostTracker

    # 设置极低预算（daily_cost_usd=0.0005），单次路由判断（~$0.001）就会超限
    budget = BudgetManager(limit=BudgetLimit(daily_cost_usd=0.0005))
    cost = CostTracker()
    judge = JudgeScheduler(
        ai_client=None,
        cost_tracker=cost,
        budget_manager=budget,
    )

    # 即使是复杂任务，预算超限也应强制 local
    decision = judge.route("重构这段代码并优化性能")
    assert decision.executor == ROUTE_EXECUTOR_LOCAL
    assert decision.reason == "budget_exceeded_precheck"
    assert "预算超限" in decision.reasoning

    # 统计中应记录 budget_exceeded_precheck
    stats = judge.get_stats()
    assert stats["by_reason"].get("budget_exceeded_precheck", 0) >= 1


def test_judge_budget_check_failure_does_not_block():
    """P1-1：budget_manager.check_budget 抛异常时不应阻塞路由。"""
    from assistant.cost_tracker import CostTracker

    class _BrokenBudgetManager:
        def check_budget(self, **kwargs):
            raise RuntimeError("simulated budget check failure")

        def record_usage(self, **kwargs):
            pass

    budget = _BrokenBudgetManager()
    cost = CostTracker()
    judge = JudgeScheduler(
        ai_client=None,
        cost_tracker=cost,
        budget_manager=budget,
    )

    # 预算检查失败，路由应正常进行（不阻塞）
    decision = judge.route("重构这段代码")
    # 不应路由到 budget_exceeded_precheck，而应正常走规则 fallback
    assert decision.reason != "budget_exceeded_precheck"
    assert decision.executor == ROUTE_EXECUTOR_DEEPSEEK


def test_judge_without_budget_manager_skips_precheck():
    """P1-1：未装配 budget_manager 时（测试环境），预算预检应跳过。"""
    judge = JudgeScheduler(ai_client=None)  # 不传 budget_manager
    assert judge._budget_manager is None
    # _is_budget_exceeded 应返回 False（不阻塞）
    assert judge._is_budget_exceeded() is False

    # 路由正常工作
    decision = judge.route("重构这段代码")
    assert decision.executor == ROUTE_EXECUTOR_DEEPSEEK
