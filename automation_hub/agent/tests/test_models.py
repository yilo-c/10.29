"""
测试 Agent 数据模型
"""

import pytest
from pydantic import ValidationError
from ..models import (
    TaskType,
    StepStatus,
    PlanStep,
    ExecutionPlan,
    StepResult,
    ExecutionResult
)


def test_task_type_enum():
    """测试任务类型枚举"""
    assert TaskType.CODE_SEARCH == "code_search"
    assert TaskType.CUSTOM == "custom"


def test_step_status_enum():
    """测试步骤状态枚举"""
    assert StepStatus.PENDING == "pending"
    assert StepStatus.COMPLETED == "completed"
    assert StepStatus.FAILED == "failed"


def test_plan_step_creation():
    """测试创建步骤"""
    step = PlanStep(
        step_id="step_1",
        tool_id="code_search",
        tool_name="代码搜索",
        args={"pattern": "TODO"},
        reason="搜索 TODO 注释"
    )
    
    assert step.step_id == "step_1"
    assert step.tool_id == "code_search"
    assert step.args["pattern"] == "TODO"
    assert step.status == StepStatus.PENDING
    assert step.retry_on_fail is False
    assert step.timeout_seconds == 60


def test_plan_step_with_dependencies():
    """测试带依赖的步骤"""
    step = PlanStep(
        step_id="step_2",
        tool_id="format_code",
        tool_name="格式化代码",
        args={},
        reason="格式化",
        depends_on=["step_1"],
        retry_on_fail=True,
        timeout_seconds=120
    )
    
    assert step.depends_on == ["step_1"]
    assert step.retry_on_fail is True
    assert step.timeout_seconds == 120


def test_execution_plan_creation():
    """测试创建执行计划"""
    steps = [
        PlanStep(
            step_id="step_1",
            tool_id="code_search",
            tool_name="搜索",
            args={},
            reason="搜索代码"
        )
    ]
    
    plan = ExecutionPlan(
        plan_id="plan123",
        user_query="搜索 TODO",
        task_type=TaskType.CODE_SEARCH,
        steps=steps,
        estimated_duration=30,
        created_at="2026-01-22T10:00:00Z"
    )
    
    assert plan.plan_id == "plan123"
    assert plan.task_type == TaskType.CODE_SEARCH
    assert len(plan.steps) == 1
    assert plan.estimated_duration == 30


def test_step_result_creation():
    """测试创建步骤结果"""
    result = StepResult(
        step_id="step_1",
        status=StepStatus.COMPLETED,
        output="找到 10 个 TODO",
        execution_time=2.5
    )
    
    assert result.step_id == "step_1"
    assert result.status == StepStatus.COMPLETED
    assert result.output == "找到 10 个 TODO"
    assert result.execution_time == 2.5
    assert result.error is None


def test_step_result_with_error():
    """测试带错误的步骤结果"""
    result = StepResult(
        step_id="step_1",
        status=StepStatus.FAILED,
        error="工具未找到"
    )
    
    assert result.status == StepStatus.FAILED
    assert result.error == "工具未找到"
    assert result.output is None


def test_execution_result_creation():
    """测试创建执行结果"""
    step_results = [
        StepResult(
            step_id="step_1",
            status=StepStatus.COMPLETED,
            output="成功"
        )
    ]
    
    result = ExecutionResult(
        plan_id="plan123",
        status="success",
        step_results=step_results,
        summary="所有步骤成功",
        completed_at="2026-01-22T10:00:30Z",
        total_duration=5.0
    )
    
    assert result.plan_id == "plan123"
    assert result.status == "success"
    assert len(result.step_results) == 1
    assert result.total_duration == 5.0


def test_validation_error():
    """测试验证错误"""
    with pytest.raises(ValidationError):
        # 缺少必需字段
        PlanStep(
            step_id="step_1"
            # 缺少 tool_id, tool_name, args, reason
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
