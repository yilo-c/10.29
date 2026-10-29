"""
Agent API 路由

提供自然语言交互接口
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import uuid
import logging
import asyncio
import json
import subprocess
from pathlib import Path

from automation_hub.api.config import settings
from automation_hub.api.auth.deps import require_scope
from automation_hub.api.repos.router import _search_repo_or_raise
from agent import (
    AgentPlanner,
    AgentExecutor,
    ConversationContext,
    ExecutionPlan,
    OpenAICompatibleClient,
    StepStatus,
)
from agent import create_fallback_client
from automation_hub.api.db import (
    create_tool_run,
    get_tool_run_by_id,
    update_tool_run_status,
)
from automation_hub.api.tools.registry import get_tool
from automation_hub.api.approvals.service import create_approval

from automation_hub.api._worker_bridge import enqueue_job

# TODO: 导入认证依赖（需要实际实现）
# from ..auth.deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


def _is_enabled() -> bool:
    return bool(getattr(settings, "AGENT_ENABLED", False))


def _has_scope(token: dict, scope: str) -> bool:
    try:
        scopes = json.loads(token.get("scopes") or "[]")
        return isinstance(scopes, list) and scope in scopes
    except Exception:
        return False


def _ensure_agent_available(token: dict) -> None:
    if not _is_enabled():
        # 404 更安全：不暴露服务能力
        raise HTTPException(status_code=404, detail="agent is disabled")
    # agent 只是交互入口，实际执行仍必须具备 tool:execute
    if not _has_scope(token, "tool:execute"):
        raise HTTPException(status_code=403, detail="missing scope: tool:execute")


# ==================== 内部状态 ====================

_SESSION_STORE: Dict[str, ConversationContext] = {}
_RUN_STORE: Dict[str, Dict[str, Any]] = {}
_PLAN_STORE: Dict[str, Dict[str, Any]] = {}
_LLM_CLIENT = None


def _get_llm_client() -> Any:
    global _LLM_CLIENT
    if _LLM_CLIENT is None:
        # 尝试从环境或配置创建带回退的客户端（主服务 -> 本地 -> 缓存）
        local_base = getattr(settings, "LOCAL_LLM_BASE_URL", None)
        _LLM_CLIENT = create_fallback_client(local_base_url=local_base)
    return _LLM_CLIENT


def _get_context(session_id: str, user_id: str) -> ConversationContext:
    ctx = _SESSION_STORE.get(session_id)
    if ctx is None:
        ctx = ConversationContext(user_id=user_id, session_id=session_id)
        _SESSION_STORE[session_id] = ctx
    return ctx


def _apply_extra_context(ctx: ConversationContext, extra: Optional[dict]) -> None:
    if not extra:
        return
    cwd = extra.get("cwd") or extra.get("working_directory")
    files = extra.get("recent_files")
    project_type = extra.get("project_type")
    ctx.update_working_context(cwd=cwd, files=files, project_type=project_type)
    preferences = extra.get("preferences")
    if isinstance(preferences, dict):
        for k, v in preferences.items():
            ctx.set_preference(k, v)
    variables = extra.get("variables")
    if isinstance(variables, dict):
        for k, v in variables.items():
            ctx.set_variable(k, v)


class _LocalRunClient:
    """工具执行客户端：落库到 tool_runs，并通过 ARQ 入队工具任务。"""

    def __init__(self, actor_user_id: str, actor_device_id: str | None):
        self.actor_user_id = actor_user_id
        self.actor_device_id = actor_device_id

    def _requires_approval(self, risk_level: str) -> bool:
        return risk_level in {"write", "exec_high"}

    async def create_run(self, tool_id: str, args: dict, user_id: str) -> Dict[str, Any]:
        tool = get_tool(tool_id)
        if not tool or not int(tool.get("is_enabled", 1)):
            raise ValueError(f"tool not found: {tool_id}")

        run_id = str(uuid.uuid4())

        # 输出文件路径
        from pathlib import Path

        run_dir = Path(settings.RUNS_DIR) / "tool_runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = str(run_dir / "stdout.txt")
        stderr_path = str(run_dir / "stderr.txt")

        risk_level = tool.get("risk_level", "exec_low")
        approval_id: str | None = None
        status = "queued"

        if self._requires_approval(risk_level):
            status = "pending_approval"
            approval_id = create_approval(
                user_id=self.actor_user_id,
                device_id=self.actor_device_id,
                resource_type="run",
                resource_id=run_id,
                action="execute",
                risk_level=risk_level,
                reason=f"execute tool {tool_id}",
                payload={"tool_id": tool_id, "args": args, "run_id": run_id},
            )

        ok = create_tool_run(
            run_id=run_id,
            tool_id=tool_id,
            args=args,
            status=status,
            created_by_user_id=self.actor_user_id,
            approval_request_id=approval_id,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        if not ok:
            raise RuntimeError("failed to create tool run")

        if status == "queued":
            timeout = int(tool.get("timeout_sec") or 120) + 30
            await enqueue_job(
                "arq_run_tool_job",
                run_id,
                tool_id,
                args,
                self.actor_user_id,
                _job_timeout=timeout,
            )
            return {"run_id": run_id, "status": "queued"}

        return {"run_id": run_id, "status": "pending_approval", "approval_id": approval_id}

    async def get_run_status(self, run_id: str) -> Dict[str, Any]:
        r = get_tool_run_by_id(run_id)
        if not r:
            return {"status": "failed", "error": "run not found"}
        status = r.get("status")
        if status == "succeeded":
            return {"status": "succeeded", "output": "ok"}
        if status in {"failed", "denied"}:
            return {"status": status, "error": r.get("error_msg") or "failed"}
        if status == "pending_approval":
            return {"status": "pending_approval", "approval_id": r.get("approval_request_id")}
        return {"status": status}


# ==================== 请求/响应模型 ====================

class AskRequest(BaseModel):
    """询问请求"""
    query: str = Field(description="用户自然语言输入", min_length=1, max_length=2000)
    session_id: Optional[str] = Field(default=None, description="会话ID（可选，用于保持上下文）")
    context: Optional[dict] = Field(default=None, description="额外上下文信息")
    repo_context: Optional["RepoAutoContextRequest"] = Field(default=None, description="可选：在规划前自动注入的 repo 搜索上下文")


class AskResponse(BaseModel):
    """询问响应"""
    plan_id: str = Field(description="执行计划ID")
    summary: str = Field(description="执行结果摘要")
    status: str = Field(description="执行状态: success|partial|failed|blocked")
    steps_completed: int = Field(description="完成的步骤数")
    total_steps: int = Field(description="总步骤数")
    session_id: str = Field(description="会话ID")
    execution_time: float = Field(description="总执行时间（秒）")
    pending_approvals: List[str] = Field(default_factory=list, description="待审批 ID 列表（当 status=blocked 时）")
    repo_context: Optional["AppliedRepoContext"] = Field(default=None, description="本次请求自动注入的 repo 搜索上下文")


class PlanRequest(BaseModel):
    """仅规划请求（不执行）"""
    query: str = Field(description="用户自然语言输入")
    session_id: Optional[str] = None
    context: Optional[dict] = Field(default=None, description="额外上下文信息")
    repo_context: Optional["RepoAutoContextRequest"] = Field(default=None, description="可选：在规划前自动注入的 repo 搜索上下文")


class PlanResponse(BaseModel):
    """规划响应"""
    plan_id: str
    task_type: str
    steps: List[dict]
    estimated_duration: int
    repo_context: Optional["AppliedRepoContext"] = None


class RepoAutoContextRequest(BaseModel):
    repo_id: str = Field(description="仓库 ID")
    query: str = Field(description="搜索关键字", min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class AppliedRepoContext(BaseModel):
    repo_id: str
    query: str
    engine: str
    total: int
    injected_files: List[str] = Field(default_factory=list)
    items: List[dict] = Field(default_factory=list)


class RepoContextRequest(BaseModel):
    repo_id: str = Field(description="仓库 ID")
    query: str = Field(description="搜索关键字", min_length=1, max_length=500)
    session_id: Optional[str] = Field(default=None, description="会话ID（可选，用于保持上下文）")
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class RepoContextResponse(BaseModel):
    session_id: str
    repo_id: str
    query: str
    engine: str
    total: int
    injected_files: List[str] = Field(default_factory=list)
    items: List[dict] = Field(default_factory=list)


AskRequest.model_rebuild()
AskResponse.model_rebuild()
PlanRequest.model_rebuild()
PlanResponse.model_rebuild()


class ExecutePlanRequest(BaseModel):
    """执行已有计划"""
    plan_id: str = Field(description="计划ID")


class SessionResponse(BaseModel):
    """会话详情响应。"""

    session_id: str
    user_id: str
    working_directory: Optional[str] = None
    recent_files: List[str] = Field(default_factory=list)
    project_type: Optional[str] = None
    preferences: Dict[str, Any] = Field(default_factory=dict)
    variables: Dict[str, Any] = Field(default_factory=dict)
    history: List[Dict[str, Any]] = Field(default_factory=list)
    history_count: int = 0
    plans: List[Dict[str, Any]] = Field(default_factory=list)


def _store_plan(plan: ExecutionPlan, session_id: str, user_id: str) -> None:
    _PLAN_STORE[plan.plan_id] = {
        "plan": plan.model_dump(),
        "session_id": session_id,
        "user_id": user_id,
    }


def _load_plan_for_user(plan_id: str, user_id: str) -> tuple[ExecutionPlan, str]:
    payload = _PLAN_STORE.get(plan_id)
    if not payload:
        raise HTTPException(status_code=404, detail="plan not found")

    plan_data = payload.get("plan")
    session_id = str(payload.get("session_id") or "").strip()
    owner_user_id = str(payload.get("user_id") or "").strip()
    if owner_user_id and owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="plan does not belong to current user")
    if not isinstance(plan_data, dict):
        raise HTTPException(status_code=500, detail="plan payload is invalid")
    if not session_id:
        raise HTTPException(status_code=500, detail="plan session is missing")

    try:
        plan = ExecutionPlan.model_validate(plan_data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"plan payload parse failed: {str(exc)}") from exc
    return plan, session_id


def _inject_repo_search_context(ctx: ConversationContext, repo: dict, query: str, search_payload) -> list[str]:
    items = [item.model_dump() if hasattr(item, "model_dump") else item for item in (search_payload.items or [])]
    injected_files = [item["path"] for item in items]

    ctx.update_working_context(
        cwd=repo.get("root_path"),
        files=injected_files,
        project_type=ctx.project_type or "repo-search",
    )
    ctx.set_variable(
        "repo_search_last",
        {
            "repo_id": repo.get("id"),
            "repo_name": repo.get("name"),
            "query": query,
            "engine": search_payload.engine,
            "total": search_payload.total,
            "items": items,
        },
    )
    ctx.add_message(
        "system",
        f"Injected repo search context for '{query}' in repo {repo.get('name')}",
        metadata={
            "repo_id": repo.get("id"),
            "engine": search_payload.engine,
            "total": search_payload.total,
            "files": injected_files,
        },
    )
    return injected_files


def _build_applied_repo_context(repo: dict, query: str, search_payload, injected_files: list[str]) -> AppliedRepoContext:
    return AppliedRepoContext(
        repo_id=repo.get("id"),
        query=query,
        engine=search_payload.engine,
        total=search_payload.total,
        injected_files=injected_files,
        items=[item.model_dump() if hasattr(item, "model_dump") else item for item in search_payload.items],
    )


def _apply_repo_context_if_requested(
    ctx: ConversationContext,
    token: dict,
    repo_context: Optional[RepoAutoContextRequest],
) -> Optional[AppliedRepoContext]:
    if not repo_context:
        return None
    if not _has_scope(token, "repo:read"):
        raise HTTPException(status_code=403, detail="missing scope: repo:read")

    repo, search_payload = _search_repo_or_raise(
        repo_context.repo_id,
        repo_context.query,
        repo_context.limit,
        repo_context.offset,
    )
    injected_files = _inject_repo_search_context(ctx, repo, repo_context.query, search_payload)
    return _build_applied_repo_context(repo, repo_context.query, search_payload, injected_files)


# ==================== API 端点 ====================

@router.post("/ask", response_model=AskResponse)
async def agent_ask(
    request: AskRequest,
    token=Depends(require_scope("agent:use")),
):
    """
    Agent 主入口 - 自然语言询问
    
    流程:
    1. 解析意图并生成计划
    2. 执行计划
    3. 返回结果
    
    示例:
    ```json
    {
      "query": "搜索所有 TODO 注释",
      "session_id": "optional-session-id"
    }
    ```
    """
    try:
        _ensure_agent_available(token)
        session_id = request.session_id or str(uuid.uuid4())
        user_id = token.get("user_id")
        device_id = token.get("device_id")

        llm_client = _get_llm_client()
        planner = AgentPlanner(llm_client, db_path=str(settings.DATABASE_PATH))
        executor = AgentExecutor(api_client=_LocalRunClient(actor_user_id=user_id, actor_device_id=device_id))

        context = _get_context(session_id=session_id, user_id=user_id)
        _apply_extra_context(context, request.context)
        applied_repo_context = _apply_repo_context_if_requested(context, token, request.repo_context)

        context.add_message("user", request.query)
        plan = await planner.plan(request.query, context)
        result = await executor.execute_plan(plan, user_id=user_id)
        context.add_message("assistant", result.summary)

        steps_completed = len([r for r in result.step_results if r.status == StepStatus.COMPLETED])
        total_steps = len(result.step_results)
        pending_approvals = [r.approval_id for r in result.step_results if getattr(r, "approval_id", None)]

        return AskResponse(
            plan_id=result.plan_id,
            summary=result.summary,
            status=result.status,
            steps_completed=steps_completed,
            total_steps=total_steps,
            session_id=session_id,
            execution_time=result.total_duration,
            pending_approvals=pending_approvals,
            repo_context=applied_repo_context,
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("处理询问时出错")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"处理请求失败: {str(e)}"
        )


@router.post("/plan", response_model=PlanResponse)
async def create_plan(
    request: PlanRequest,
    token=Depends(require_scope("agent:use")),
):
    """
    仅生成计划（不执行）
    
    用于预览 Agent 将执行的操作
    """
    try:
        _ensure_agent_available(token)
        session_id = request.session_id or str(uuid.uuid4())
        user_id = token.get("user_id")

        llm_client = _get_llm_client()
        planner = AgentPlanner(llm_client, db_path=str(settings.DATABASE_PATH))

        context = _get_context(session_id=session_id, user_id=user_id)
        _apply_extra_context(context, request.context)
        applied_repo_context = _apply_repo_context_if_requested(context, token, request.repo_context)

        plan = await planner.plan(request.query, context)
        _store_plan(plan, session_id=session_id, user_id=user_id)

        return PlanResponse(
            plan_id=plan.plan_id,
            task_type=plan.task_type.value,
            steps=[step.model_dump() for step in plan.steps],
            estimated_duration=plan.estimated_duration,
            repo_context=applied_repo_context,
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("生成计划时出错")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"生成计划失败: {str(e)}"
        )


@router.post("/context/repo-search", response_model=RepoContextResponse)
async def inject_repo_search_context(
    request: RepoContextRequest,
    token=Depends(require_scope("agent:use")),
):
    try:
        _ensure_agent_available(token)
        if not _has_scope(token, "repo:read"):
            raise HTTPException(status_code=403, detail="missing scope: repo:read")

        repo, search_payload = _search_repo_or_raise(
            request.repo_id,
            request.query,
            request.limit,
            request.offset,
        )

        session_id = request.session_id or str(uuid.uuid4())
        context = _get_context(session_id=session_id, user_id=token.get("user_id"))
        injected_files = _inject_repo_search_context(context, repo, request.query, search_payload)

        return RepoContextResponse(
            session_id=session_id,
            repo_id=request.repo_id,
            query=request.query,
            engine=search_payload.engine,
            total=search_payload.total,
            injected_files=injected_files,
            items=[item.model_dump() if hasattr(item, "model_dump") else item for item in search_payload.items],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("注入 repo 搜索上下文时出错")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"注入上下文失败: {str(e)}"
        )


@router.post("/execute/{plan_id}", response_model=AskResponse)
async def execute_plan(
    plan_id: str,
    token=Depends(require_scope("agent:use")),
):
    """
    执行已生成的计划
    
    配合 /plan 端点使用，实现"预览-确认-执行"流程
    """
    try:
        _ensure_agent_available(token)
        user_id = token.get("user_id")
        device_id = token.get("device_id")

        plan, session_id = _load_plan_for_user(plan_id=plan_id, user_id=user_id)
        context = _get_context(session_id=session_id, user_id=user_id)

        executor = AgentExecutor(api_client=_LocalRunClient(actor_user_id=user_id, actor_device_id=device_id))
        result = await executor.execute_plan(plan, user_id=user_id)
        context.add_message(
            "assistant",
            result.summary,
            metadata={"plan_id": plan.plan_id, "status": result.status, "source": "execute"},
        )

        steps_completed = len([r for r in result.step_results if r.status == StepStatus.COMPLETED])
        total_steps = len(result.step_results)
        pending_approvals = [r.approval_id for r in result.step_results if getattr(r, "approval_id", None)]

        return AskResponse(
            plan_id=result.plan_id,
            summary=result.summary,
            status=result.status,
            steps_completed=steps_completed,
            total_steps=total_steps,
            session_id=session_id,
            execution_time=result.total_duration,
            pending_approvals=pending_approvals,
            repo_context=None,
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("执行计划时出错")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"执行计划失败: {str(e)}"
        )


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    token=Depends(require_scope("agent:use")),
):
    """
    获取会话上下文
    
    用于查看对话历史和上下文信息
    """
    try:
        _ensure_agent_available(token)
        user_id = token.get("user_id")
        context = _SESSION_STORE.get(session_id)
        if context is None:
            raise HTTPException(status_code=404, detail="session not found")
        if context.user_id != user_id:
            raise HTTPException(status_code=403, detail="session does not belong to current user")

        plans = []
        for pid, payload in _PLAN_STORE.items():
            if payload.get("session_id") != session_id:
                continue
            if payload.get("user_id") != user_id:
                continue
            plan_data = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
            plans.append(
                {
                    "plan_id": pid,
                    "task_type": plan_data.get("task_type"),
                    "estimated_duration": plan_data.get("estimated_duration"),
                    "created_at": plan_data.get("created_at"),
                    "step_count": len(plan_data.get("steps") or []),
                }
            )

        history = [dict(item) for item in context.history]
        return SessionResponse(
            session_id=context.session_id,
            user_id=context.user_id,
            working_directory=context.working_directory,
            recent_files=list(context.recent_files),
            project_type=context.project_type,
            preferences=dict(context.preferences),
            variables=dict(context.variables),
            history=history,
            history_count=len(history),
            plans=plans,
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("获取会话时出错")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取会话失败: {str(e)}"
        )
