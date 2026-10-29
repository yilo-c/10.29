"""assistant_execution_plan 单元测试。"""
from __future__ import annotations

import sys
import json
from pathlib import Path
from unittest.mock import MagicMock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from assistant.execution_plan import ExecutionPlan, ExecutionPlanStep, ExecutionPlanStore, PlanStatus


class TestExecutionPlanStep:
    """ExecutionPlanStep 测试。"""

    def test_basic_step(self):
        step = ExecutionPlanStep("step_1", "file.read", "读取配置文件", risk_level="low")
        assert step.step_id == "step_1"
        assert step.risk_label == "低风险"
        assert step.impact_description != ""
        assert "秒" in step.estimated_display

    def test_high_risk_step(self):
        step = ExecutionPlanStep("step_2", "file.write", "写入配置", risk_level="high")
        assert step.risk_label == "高风险"
        assert step.estimated_seconds == 120

    def test_step_to_dict(self):
        step = ExecutionPlanStep("step_1", "file.read", "读取", risk_level="low", file_paths=["config.json"])
        d = step.to_dict()
        assert d["step_id"] == "step_1"
        assert d["file_paths"] == ["config.json"]
        assert d["enabled"] is True


class TestExecutionPlan:
    """ExecutionPlan 测试。"""

    def test_initial_state(self):
        plan = ExecutionPlan("plan_001", "更新配置")
        assert plan.status == PlanStatus.PENDING
        assert plan.goal == "更新配置"

    def test_approve(self):
        plan = ExecutionPlan("plan_002", "更新配置")
        plan.approve()
        assert plan.status == PlanStatus.APPROVED

    def test_reject(self):
        plan = ExecutionPlan("plan_003", "危险操作")
        plan.reject(reason="风险太大")
        assert plan.status == PlanStatus.REJECTED
        summary = plan.get_plan_summary()
        assert summary["reject_reason"] == "风险太大"

    def test_add_step(self):
        plan = ExecutionPlan("plan_004", "任务")
        plan.add_step("step_1", "file.read", "读取文件", risk_level="low")
        plan.add_step("step_2", "file.write", "写入文件", risk_level="high")
        steps = plan.get_steps()
        assert len(steps) == 2
        assert steps[0].action_id == "file.read"
        assert steps[1].action_id == "file.write"

    def test_edit_disable_steps(self):
        plan = ExecutionPlan("plan_005", "任务")
        plan.add_step("step_1", "file.read", "读取")
        plan.add_step("step_2", "file.write", "写入")
        plan.edit(disabled_steps=["step_2"])
        assert len(plan.get_steps()) == 1
        assert plan.get_steps()[0].step_id == "step_1"

    def test_edit_reorder_steps(self):
        plan = ExecutionPlan("plan_006", "任务")
        plan.add_step("step_1", "file.read", "读取")
        plan.add_step("step_2", "file.write", "写入")
        plan.add_step("step_3", "file.delete", "删除")
        plan.edit(disabled_steps=["step_3"], reorder=["step_2", "step_1"])
        enabled = plan.get_steps()
        assert len(enabled) == 2
        assert enabled[0].step_id == "step_2"
        assert enabled[1].step_id == "step_1"

    def test_plan_summary(self):
        plan = ExecutionPlan("plan_007", "更新配置", reasoning="需要修改配置")
        plan.add_step("step_1", "file.read", "读取配置", risk_level="low")
        plan.add_step("step_2", "file.write", "写入配置", risk_level="medium", file_paths=["config.json"])
        summary = plan.get_plan_summary()
        assert summary["plan_id"] == "plan_007"
        assert summary["total_steps"] == 2
        assert summary["highest_risk"] == "medium"
        assert summary["total_estimated_seconds"] > 0

    def test_expired_plan(self):
        """过期计划不能 approve。"""
        plan = ExecutionPlan("plan_008", "任务", expires_minutes=-1)  # 已过期
        plan.approve()
        assert plan.status == PlanStatus.EXPIRED

    def test_is_expired(self):
        plan = ExecutionPlan("plan_009", "任务", expires_minutes=1)
        assert plan.is_expired() is False


class TestExecutionPlanStore:
    """ExecutionPlanStore 测试。"""

    @pytest.fixture
    def data_store(self):
        """模拟 data_store（内存存储）。"""
        ds = MagicMock()
        store: list = []

        def load_json(key, default):
            return list(store)

        def update_json(key, default, updater):
            nonlocal store
            store = updater(list(store))
            return store

        ds.load_json.side_effect = load_json
        ds.update_json.side_effect = update_json
        return ds

    def test_save_and_load_plan(self, data_store):
        store = ExecutionPlanStore(data_store)
        plan = ExecutionPlan("plan_store_001", "测试")
        plan.add_step("step_1", "file.read", "读取")

        store.save_plan(plan)
        loaded = store.load_plan("plan_store_001")
        assert loaded is not None
        assert loaded["plan_id"] == "plan_store_001"
        assert loaded["goal"] == "测试"

    def test_list_pending_plans(self, data_store):
        store = ExecutionPlanStore(data_store)

        plan1 = ExecutionPlan("p1", "任务1")
        plan1.add_step("s1", "file.read", "读")
        store.save_plan(plan1)

        plan2 = ExecutionPlan("p2", "任务2")
        plan2.add_step("s1", "file.write", "写")
        plan2.approve()
        store.save_plan(plan2)

        pending = store.list_pending_plans()
        assert len(pending) == 1
        assert pending[0]["plan_id"] == "p1"
