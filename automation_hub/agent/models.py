"""
Agent 数据模型定义
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from enum import Enum


class TaskType(str, Enum):
    """任务类型枚举"""
    CODE_SEARCH = "code_search"
    CODE_MODIFY = "code_modify"
    FILE_OPERATION = "file_operation"
    GIT_OPERATION = "git_operation"
    TEST_EXECUTION = "test_execution"
    DEPLOYMENT = "deployment"
    CUSTOM = "custom"


class StepStatus(str, Enum):
    """步骤状态枚举"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"  # 等待审批
    SKIPPED = "skipped"


class PlanStep(BaseModel):
    """执行计划的单个步骤"""
    step_id: str = Field(description="步骤唯一ID")
    tool_id: str = Field(description="工具ID，对应 tools 表")
    tool_name: str = Field(description="工具名称（易读）")
    args: Dict[str, Any] = Field(default_factory=dict, description="工具参数")
    reason: str = Field(description="为什么需要这一步")
    status: StepStatus = StepStatus.PENDING
    depends_on: List[str] = Field(default_factory=list, description="依赖的步骤ID列表")
    retry_on_fail: bool = Field(default=False, description="失败时是否重试")
    timeout_seconds: int = Field(default=60, description="超时时间（秒）")
    on_fail: str = Field(default="stop", description="失败时的行为: stop|continue|rollback")


class ExecutionPlan(BaseModel):
    """完整的执行计划"""
    plan_id: str = Field(description="计划ID")
    user_query: str = Field(description="原始用户输入")
    task_type: TaskType = Field(description="任务类型")
    steps: List[PlanStep] = Field(description="执行步骤列表")
    estimated_duration: int = Field(default=0, description="预计执行时间（秒）")
    created_at: str = Field(description="创建时间")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "plan_id": "plan_abc123",
                "user_query": "搜索所有 TODO 注释",
                "task_type": "code_search",
                "steps": [
                    {
                        "step_id": "step_1",
                        "tool_id": "code_search",
                        "tool_name": "代码搜索",
                        "args": {"pattern": "TODO"},
                        "reason": "搜索代码中的 TODO 注释",
                        "status": "pending"
                    }
                ],
                "estimated_duration": 10,
                "created_at": "2026-01-22T10:00:00Z"
            }
        }
    )


class StepResult(BaseModel):
    """步骤执行结果"""
    step_id: str = Field(description="步骤ID")
    status: StepStatus = Field(description="执行状态")
    output: Optional[str] = Field(default=None, description="执行输出")
    error: Optional[str] = Field(default=None, description="错误信息")
    run_id: Optional[str] = Field(default=None, description="关联的 run ID")
    approval_id: Optional[str] = Field(default=None, description="关联的审批 ID（当 status=blocked 时）")
    execution_time: float = Field(default=0.0, description="执行时长（秒）")
    started_at: Optional[str] = Field(default=None, description="开始时间")
    completed_at: Optional[str] = Field(default=None, description="完成时间")


class ExecutionResult(BaseModel):
    """完整执行结果"""
    plan_id: str = Field(description="计划ID")
    status: str = Field(description="整体状态: success|partial|failed")
    step_results: List[StepResult] = Field(description="步骤结果列表")
    summary: str = Field(description="执行总结，返回给用户")
    completed_at: str = Field(description="完成时间")
    total_duration: float = Field(default=0.0, description="总执行时长（秒）")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "plan_id": "plan_abc123",
                "status": "success",
                "step_results": [
                    {
                        "step_id": "step_1",
                        "status": "completed",
                        "output": "找到 15 个 TODO 注释",
                        "execution_time": 2.5
                    }
                ],
                "summary": "成功找到 15 个 TODO 注释",
                "completed_at": "2026-01-22T10:00:15Z",
                "total_duration": 2.5
            }
        }
    )