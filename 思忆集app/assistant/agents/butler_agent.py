"""PydanticAI 管家执行引擎 — chat.send 的默认单 agent 循环（P3 收敛）。

对标 Claude Code 的形态：单一 agent 循环 + 原生 function calling + 丰富工具，
不做关键词预判——每条消息都携带工具定义，由模型自己决定聊天还是干活。

关键设计：
* ``SiyiChatModel`` 是 PydanticAI 自定义 Model 适配器，底层调用现有
  ``context.ai_client.chat_completion``，因此隐私分级（local_only 不出境）、
  fallback 降级、预算控制、小问人格注入全部保留。
* 工具从 ``services.react_tool_catalog.REACT_TOOL_CATALOG`` 动态构建，
  自由文本 payload_schema 解析为标准 JSON Schema（pydantic create_model）。
* 工具分级保留旧 stream_tools 语义：SAFE 直接放行；CONFIRM 返回 blocked；
  SANDBOX 走 sandbox_manager；``tool.*`` / ``mcp.*`` 动态注册。
* 工具结果经标准 ``role=tool`` 协议回喂（PydanticAI 内部消息流保证）。

依赖 ``pydantic-ai-slim``；未安装时 ``get_butler_agent`` 返回 None，
调用方回退 simple_stream。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from typing import Any

logger = logging.getLogger("siyi.assistant.butler_agent")

MAX_MODEL_REQUESTS = 12
TOOL_TIMEOUT_SECONDS = 60.0
# 视觉描述类工具（本地 VLM 首次加载模型 + CPU 推理）耗时更长，单独放宽
# 实测：首次推理 157s（CPU 加载 3b 模型），300s 留足余量
VISION_TOOL_TIMEOUT_SECONDS = 300.0
_TOOL_RESULT_MAX_CHARS = 8000

# ── 工具分级（P3 升级：CONFIRM 集改为“请求用户确认”而非直接 blocked）───────
SAFE_TOOLS: frozenset[str] = frozenset(
    {
        "file.read",
        "file.write",
        "file.find",
        "file.copy",
        "file.grep",
        "file.edit",
        "file.delete",
        "web.search",
        "web.fetch",
        "memory.recall",
        "memory.search",
        "knowledge.query",
        "knowledge.search",
        "notes.list",
        "notes.search",
        "notes.create",
        "notes.get",
        "notes.update",
        "notes.delete",
        "notes.tag",
        "notes.batch_delete",
        "tasks.list",
        "tasks.search",
        "tasks.create",
        "tasks.get",
        "tasks.update",
        "tasks.delete",
        "tasks.toggle",
        "inbox.add",
        "inbox.list",
        "translate.text",
        "system.info",
        "clipboard.read",
        "weather.current",
        "transfer.send",
        "browser.navigate",
        "browser.extract",
        "browser.screenshot",
        "desktop.screenshot",
        "info.brief",
        "vocab.quiz_in_chat",
        "vocab.review_status",
        "subagent.spawn",
        "subagent.list",
        "workflow.execute",
        "confirmation.status",
    }
)
CONFIRM_TOOLS: frozenset[str] = frozenset(
    {
        "shell.run",
        "process.list",
        "process.kill",
        "browser.click",
        "browser.type",
        "browser.automate",
        "app.launch",
        "web.download",
        "cron.add",
        "cron.remove",
    }
)
SANDBOX_TOOLS: frozenset[str] = frozenset({"code.execute"})

# shell 总开关：SIYI_BUTLER_SHELL=0 时 shell.run 不注册
_SHELL_ENABLED = os.environ.get("SIYI_BUTLER_SHELL", "1") != "0"

# 裁决 3 兜底名单：guard 完全不可用时（纯单测/降级路径）elevated 动作按同一份
# 名单保守升格为确认（宁可多问一次，不可静默拦死）。与 core.action_guard 同源，
# 不复制成员（漂移即回归测试可见）。
from core.action_guard import _LLM_ELEVATED_ACTIONS as _ELEVATED_FALLBACK


def is_tool_allowed(action_id: str) -> bool:
    if action_id == "shell.run" and not _SHELL_ENABLED:
        return False
    return (
        action_id in SAFE_TOOLS
        or action_id in CONFIRM_TOOLS
        or action_id in SANDBOX_TOOLS
        or action_id.startswith("tool.")
        or action_id.startswith("mcp.")
    )


BUTLER_SYSTEM_PROMPT = """你是思忆集个人管家，负责帮用户处理日常事务。你拥有真实的执行能力，不只是聊天。

能力域：笔记与待办管理、文件读写/搜索/编辑（file.*）、命令行执行（shell.run）、
桌面截屏（desktop.screenshot）、网络搜索与网页抓取、本地记忆与知识库检索、
翻译、天气、资讯简报、浏览器自动化、设备间传送。

任务策略（对标专业 Agent）：
1. 需要动手的任务，直接调用工具分步完成，不要只给建议。
2. 多步任务先侦察再动手：用 file.find/file.grep 了解现状，再执行修改。
3. 修改已有文件优先用 file.edit（局部替换）而非 file.write（整文件覆盖）。
4. 工具返回错误时，先分析原因并换一种方式重试一次；仍失败就如实告知用户。
5. 危险/系统级操作（shell.run 等）会自动请求用户确认：收到 pending_confirmation
   后不要反复催问，继续完成其他步骤或直接总结“已发起确认请求，等待你批准”。
   后续可用 confirmation.status 查询结果。
6. 涉及删除、批量操作前，先列出目标让用户确认（除非用户已明确要求）。
7. 每次只执行当前最需要的一步，根据工具返回结果决定下一步。
8. 完成后用简洁中文总结做了什么、结果如何。
9. 纯聊天或知识问答不需要工具时，直接回答即可。"""


# ═══════════════════════════════════════════════════════════════
# payload_schema 自由文本 → JSON Schema
# ═══════════════════════════════════════════════════════════════

_SCHEMA_RE = re.compile(r"^\s*(?P<types>[A-Za-z/]+)\s*(?:\((?P<meta>[^)]*)\))?\s*(?P<desc>.*)$")
_TYPE_MAP: dict[str, type] = {
    "string": str,
    "str": str,
    "text": str,
    # 目录里 number 均为计数/上限类参数，映射 int 避免 float 传入 DB 层
    "number": int,
    "float": float,
    "int": int,
    "integer": int,
    "bool": bool,
    "boolean": bool,
    "array": list,
    "list": list,
    "object": dict,
    "dict": dict,
}


def parse_payload_field(spec: str) -> tuple[type, bool, str]:
    """解析单条 payload_schema 描述，如 ``"string (必填) 笔记标题"``。

    Returns:
        (python_type, required, description)。复合类型（如 "number/string"）
        统一按 str 处理，保证模型传参不会因类型卡死。
    """
    m = _SCHEMA_RE.match(spec or "")
    if not m:
        return str, False, str(spec or "")
    type_tokens = [t.strip().lower() for t in m.group("types").split("/") if t.strip()]
    mapped = {_TYPE_MAP.get(t) for t in type_tokens} - {None}
    py_type = mapped.pop() if len(mapped) == 1 else str
    meta = m.group("meta") or ""
    required = "必填" in meta
    return py_type, required, (m.group("desc") or "").strip()


def build_args_model(action_id: str, payload_schema: dict[str, str]):
    """为单个工具动态生成参数 Pydantic 模型；无参数时返回 None。"""
    from pydantic import Field, create_model

    fields: dict[str, Any] = {}
    for name, spec in (payload_schema or {}).items():
        py_type, required, desc = parse_payload_field(str(spec))
        if required:
            fields[name] = (py_type, Field(..., description=desc))
        else:
            fields[name] = (py_type | None, Field(None, description=desc))
    if not fields:
        return None
    model_name = "Args_" + action_id.replace(".", "_").replace("-", "_")
    return create_model(model_name, **fields)


def sanitize_tool_name(action_id: str) -> str:
    """action_id → 合法工具名（OpenAI 规范：字母/数字/_/-）。"""
    name = re.sub(r"[^A-Za-z0-9_-]", "_", action_id)
    if not name or not name[0].isalpha():
        name = "t_" + name
    return name


# ═══════════════════════════════════════════════════════════════
# 工具执行（分级语义）
# ═══════════════════════════════════════════════════════════════


# ── 危险操作确认（CONFIRM 集）：待确认队列 + 事件通知 ────────────────

from services.confirmation_store import get_confirmation_store
from services.confirmation_flow import (
    create_and_emit_confirmation,
    register_context_store,
    register_execution_handler,
    resolve_confirmation as _flow_resolve_confirmation,
)

_CONFIRM_TTL_SECONDS = 300.0
# butler 内部执行上下文（含不可序列化对象）留进程内；确认状态交给 ConfirmationStore（M2a）。
_pending_contexts: dict[str, Any] = {}
_pending_contexts_lock = threading.Lock()


def _emit_event(event_type: str, payload: dict[str, Any]) -> None:
    """经 EventBus 发布事件（EventBridge 自动转发给前端），失败不阻断主流程。"""
    try:
        from core.events import AppEvent, event_bus

        event_bus.emit(AppEvent(event_type=event_type, payload=payload, source="butler_agent"))
    except Exception:
        logger.debug("butler event emit failed: %s", event_type, exc_info=True)


def _resolve_capability_router(context: Any) -> Any | None:
    """解析能力路由：真实挂载点是 autonomous_assistant_manager.router，
    context.capability_router 仅作兼容性兜底。"""
    cap = getattr(context, "capability_router", None)
    if cap is not None and hasattr(cap, "execute_direct"):
        return cap
    manager = getattr(context, "autonomous_assistant_manager", None)
    router = getattr(manager, "router", None)
    if router is not None and hasattr(router, "execute_direct"):
        return router
    return None


def _purge_expired_confirmations() -> None:
    get_confirmation_store().purge_expired()


def _request_confirmation(context: Any, action_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """把 CONFIRM 工具调用存入待确认队列并通知前端（状态落到 ConfirmationStore，M2a）。

    创建 + 执行上下文暂存 + 事件发射统一收敛到稳定层
    ``services.confirmation_flow.create_and_emit_confirmation``（依赖倒置；上下文
    经 ``register_context_store`` 注入本模块的 _pending_contexts）。
    """
    return create_and_emit_confirmation(context, action_id, args)


async def resolve_confirmation(confirmation_id: str, approved: bool, reason: str = "") -> dict[str, Any]:
    """用户批准/拒绝确认请求（confirmation.resolve RPC 调用）。

    状态翻转、set_result、事件发射与执行委托统一收敛到稳定层
    ``services.confirmation_flow.resolve_confirmation``（依赖倒置）；其中“有上下文
    真实执行（execute_direct/artifacts/checkpoint）”逻辑经模块尾部 ``_execute_confirmation``
    注册，由稳定层按需调用。
    """
    return await _flow_resolve_confirmation(confirmation_id, approved, reason=reason)


def get_confirmation_status(confirmation_id: str) -> dict[str, Any]:
    """查询确认请求状态（confirmation.status 工具 / RPC）。"""
    entry = get_confirmation_store().get(confirmation_id)
    if entry is None:
        return {"confirmation_id": confirmation_id, "status": "expired_or_not_found"}
    out: dict[str, Any] = {
        "confirmation_id": confirmation_id,
        "action_id": entry["action_id"],
        "status": entry["status"],
    }
    if entry["status"] == "approved" and entry.get("result") is not None:
        out["result"] = entry["result"]
    return out


def _requires_confirmation(context: Any, action_id: str) -> bool:
    """B1（2026-09-11）：审批判定接入 action_guard 的 approval_policy 维度。

    - policy=on-risk（默认）：与既有 CONFIRM_TOOLS 行为一致
    - policy=never：无人值守场景显式声明不询问（仍受白名单/sandbox 约束）
    - policy=always：写类能力也询问

    **裁决 3（2026-09-17 F-P1-2）追加 elevated 升格维度**：LLM 源命中
    ``_LLM_ELEVATED_ACTIONS`` 的动作（notes.delete / file.write / process.kill
    …，含 tool.* 危险名启发式）过去不在 CONFIRM 判据里 → 直落 broker 被
    ``llm_source_high_risk`` **终态拦死**（error dict，无审批出路，两通道两副
    面孔）。现判定统一为「elevated = LLM 源需要人类批准，且批准有出路」：
    命中 elevated 且未被显式降档（warn_only / policy=never / always_allow）→
    本函数返回 True，动作经 ``_request_confirmation`` 走 **shell.run 同一条既有
    CONFIRM 闭环**（ConfirmationStore + action.confirm_required 帧 +
    confirmation.resolve + checkpoint/chat.resume 续跑），不发明第二套挂起机制。

    guard 取自 capability_router.action_guard；不可用时退回 CONFIRM_TOOLS
    + _ELEVATED_FALLBACK 硬编码（保守兜底：宁可多问一次，不可静默拦死）。
    """
    if action_id == "confirmation.status":
        return False
    guard = None
    cap = _resolve_capability_router(context)
    if cap is not None:
        guard = getattr(cap, "action_guard", None)
    # 裁决 3：elevated 优先判定（与 guard.validate 的 elevated→block 判据同源，
    # 见 ActionGuard.is_llm_elevated / requires_llm_elevation_approval）。
    # 用 `is True` 严格判定：既有测试常用 MagicMock 冒充 guard（auto-attr 恒真），
    # 若宽松判定会把所有动作误升格。真实 ActionGuard 返回真 bool。
    if guard is not None and hasattr(guard, "requires_llm_elevation_approval"):
        try:
            if guard.requires_llm_elevation_approval(action_id, "assistant") is True:
                return True
        except Exception:
            logger.debug("elevated 升格判定失败，回落既有审批判定", exc_info=True)
    elif guard is not None:  # 旧版/替身 guard 无升格 API → 保守按名单升格
        if action_id in _ELEVATED_FALLBACK:
            return True
    if guard is not None and hasattr(guard, "should_require_confirmation"):
        try:
            return guard.should_require_confirmation(action_id)
        except Exception:
            logger.debug("approval_policy 判定失败，回退 CONFIRM_TOOLS", exc_info=True)
    return action_id in CONFIRM_TOOLS


async def execute_tool_action(context: Any, action_id: str, args: dict[str, Any]) -> str:
    """执行工具并返回 JSON 字符串（截断保护），供 LLM 读回。

    CONFIRM 集工具 → 发起用户确认请求（不再直接 blocked）；
    结果中的 artifacts（如截图 base64）剥离后经 chat.artifact 事件推给
    前端，不进 LLM 上下文。
    """
    result: dict[str, Any]
    if action_id == "confirmation.status":
        result = get_confirmation_status(str(args.get("confirmation_id") or ""))
    elif _requires_confirmation(context, action_id):
        result = _request_confirmation(context, action_id, args)
    elif action_id in SANDBOX_TOOLS:
        sb = getattr(context, "sandbox_manager", None)
        if sb is None:
            result = {"error": "sandbox 不可用"}
        else:
            try:
                result = await asyncio.wait_for(
                    sb.execute(args.get("code", ""), args.get("language", "python")),
                    timeout=TOOL_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                result = {"error": f"sandbox 执行失败: {exc}"}
    else:
        cap = _resolve_capability_router(context)
        if cap is None:
            result = {"error": "capability_router 不可用，无法执行工具"}
        else:
            # 视觉描述类工具单独放宽超时（本地 VLM 首次加载模型较慢）
            tool_timeout = VISION_TOOL_TIMEOUT_SECONDS if action_id in ("desktop.screenshot", "screen.read") else TOOL_TIMEOUT_SECONDS
            try:
                result = await asyncio.wait_for(
                    cap.execute_direct(action_id, args),
                    timeout=tool_timeout,
                )
            except asyncio.TimeoutError:
                result = {"error": f"工具 {action_id} 执行超时（{tool_timeout:.0f}s）"}
            except Exception as exc:
                result = {"error": f"工具 {action_id} 执行失败: {exc}"}

    # artifacts 剥离：base64 图片不进 LLM 上下文，经事件推给前端展示
    if isinstance(result, dict) and result.get("artifacts"):
        _emit_event(
            "chat.artifact",
            {
                "artifacts": result["artifacts"],
                "source": action_id,
            },
        )
        result = {k: v for k, v in result.items() if k != "artifacts"}
        result["note"] = "产物（图片等）已以卡片形式展示给用户"

    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > _TOOL_RESULT_MAX_CHARS:
        text = text[:_TOOL_RESULT_MAX_CHARS] + f"\n...(截断，共 {len(text)} 字符)"
    return text


# ═══════════════════════════════════════════════════════════════
# PydanticAI Model 适配器：包接现有 ai_client
# ═══════════════════════════════════════════════════════════════


def _convert_messages_to_openai(messages: list[Any]) -> list[dict[str, Any]]:
    """PydanticAI ModelMessage 列表 → OpenAI 格式消息列表。

    严格遵循 OpenAI tool-calling 协议：assistant 消息携带 tool_calls，
    工具结果用 role=tool + tool_call_id 回喂。
    """
    out: list[dict[str, Any]] = []
    system_parts: list[str] = []

    for msg in messages:
        parts = getattr(msg, "parts", None) or []
        assistant_text: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for part in parts:
            kind = getattr(part, "part_kind", "")
            if kind == "system-prompt":
                system_parts.append(str(getattr(part, "content", "") or ""))
            elif kind == "user-prompt":
                content = getattr(part, "content", "")
                if not isinstance(content, str):
                    # 多模态内容：只取文本部分
                    content = "".join(getattr(item, "text", "") for item in content if getattr(item, "type", "") == "text")
                out.append({"role": "user", "content": str(content)})
            elif kind == "text":
                assistant_text.append(str(getattr(part, "content", "") or ""))
            elif kind == "tool-call":
                tool_calls.append(
                    {
                        "id": getattr(part, "tool_call_id", "") or f"call_{len(out)}_{len(tool_calls)}",
                        "type": "function",
                        "function": {
                            "name": getattr(part, "tool_name", ""),
                            "arguments": json.dumps(
                                getattr(part, "args", {}) or {},
                                ensure_ascii=False,
                                default=str,
                            ),
                        },
                    }
                )
            elif kind == "tool-return":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": getattr(part, "tool_call_id", "") or "",
                        "content": str(getattr(part, "content", "") or ""),
                    }
                )
            elif kind == "retry-prompt":
                # 参数校验失败提示 → 作为 user 提示让模型纠正
                out.append(
                    {
                        "role": "user",
                        "content": f"[系统提示] 上一次工具调用参数无效，请修正后重试：{getattr(part, 'content', '')}",
                    }
                )
            # thinking / file 等其余 part 类型忽略
        if assistant_text or tool_calls:
            m: dict[str, Any] = {"role": "assistant", "content": "".join(assistant_text)}
            if tool_calls:
                m["tool_calls"] = tool_calls
            out.append(m)

    if system_parts:
        out.insert(0, {"role": "system", "content": "\n\n".join(p for p in system_parts if p)})
    return out


def _convert_response_to_model_response(resp: dict[str, Any]):
    """ai_client.chat_completion 响应 → PydanticAI ModelResponse。"""
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.usage import RequestUsage

    parts: list[Any] = []
    text = resp.get("text") or resp.get("content") or ""
    choices = resp.get("choices") or []
    if choices:
        msg = choices[0].get("message") or {}
        text = text or msg.get("content") or ""
        for tc in msg.get("tool_calls") or []:
            func = tc.get("function") or {}
            raw_args = func.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {}
            parts.append(
                ToolCallPart(
                    tool_name=func.get("name") or "",
                    args=args,
                    tool_call_id=tc.get("id") or "",
                )
            )
    if text:
        parts.append(TextPart(content=str(text)))
    if not parts:
        parts.append(TextPart(content="（模型未返回内容）"))

    usage = resp.get("usage") or {}
    return ModelResponse(
        parts=parts,
        usage=RequestUsage(
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        ),
        model_name=str(resp.get("model") or "siyi-chat"),
    )


def _make_siyi_model_class():
    """延迟构造 Model 子类（pydantic_ai 未安装时抛 ImportError）。"""
    from contextlib import asynccontextmanager

    from pydantic_ai.models import Model

    class SiyiChatModel(Model):
        """把 PydanticAI 的请求转发给思忆集现有 ai_client（保留隐私/fallback/预算链）。"""

        def __init__(self, ai_client: Any) -> None:
            self._client = ai_client

        @property
        def model_name(self) -> str:
            return "siyi-chat"

        @property
        def system(self) -> str:
            return "siyi"

        async def request(self, messages, model_settings, model_request_parameters):
            openai_msgs = _convert_messages_to_openai(messages)
            tool_defs = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": t.parameters_json_schema or {"type": "object", "properties": {}},
                    },
                }
                for t in (model_request_parameters.function_tools or [])
            ]
            kwargs: dict[str, Any] = {}
            if tool_defs:
                kwargs["tools"] = tool_defs
                kwargs["tool_choice"] = "auto"

            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: self._client.chat_completion(
                    messages=openai_msgs,
                    temperature=0.3,
                    max_tokens=4096,
                    **kwargs,
                ),
            )
            return _convert_response_to_model_response(resp)

        async def request_stream(
            self,
            messages,
            model_settings,
            model_request_parameters,
            run_context=None,
        ):
            """流式入口（async context manager 协议，yield StreamedResponse）。

            底层 ai_client 同步非流式，一次性产出完整响应；复用官方
            TestStreamedResponse 把完整 ModelResponse 拆成 delta 事件流，
            前端仍能看到输出效果（粒度为单次模型响应）。
            """
            from pydantic_ai.models.test import TestStreamedResponse

            model_settings, model_request_parameters = self.prepare_request(
                model_settings,
                model_request_parameters,
            )
            response = await self.request(messages, model_settings, model_request_parameters)
            yield TestStreamedResponse(
                model_request_parameters=model_request_parameters,
                _model_name=self.model_name,
                _structured_response=response,
                _messages=messages,
                _provider_name=self.system,
            )

        request_stream = asynccontextmanager(request_stream)

    return SiyiChatModel


# ═══════════════════════════════════════════════════════════════
# Agent 构建与缓存
# ═══════════════════════════════════════════════════════════════


def _make_no_arg_fn(action_id: str):
    """无参工具的执行函数工厂（闭包绑定 action_id）。"""
    from pydantic_ai import RunContext

    async def _fn(ctx: Any) -> str:
        return await execute_tool_action(ctx.deps, action_id, {})

    # PydanticAI 要求 takes_ctx 工具首参标注 RunContext[...]
    _fn.__annotations__["ctx"] = RunContext[Any]
    return _fn


def _make_arg_fn(action_id: str, args_model: Any):
    """带参工具的执行函数工厂。

    PydanticAI 约定：单个 Pydantic 模型参数即整个参数对象，
    参数 schema 由模型自动生成（已在 build_args_model 中带 description）。
    """
    from pydantic_ai import RunContext

    async def _fn(ctx: Any, args: Any) -> str:
        payload = args.model_dump(exclude_none=True) if hasattr(args, "model_dump") else {}
        return await execute_tool_action(ctx.deps, action_id, payload)

    # 动态类型标注：PydanticAI 通过 get_type_hints 解析签名
    _fn.__annotations__["ctx"] = RunContext[Any]
    _fn.__annotations__["args"] = args_model
    return _fn


def _make_mcp_fn(server: str, tname: str):
    """MCP 工具的执行函数工厂。"""
    from pydantic_ai import RunContext

    async def _fn(ctx: Any, args: Any = None) -> str:
        manager = getattr(ctx.deps, "mcp_client_manager", None)
        if manager is None:
            return json.dumps({"error": "mcp 不可用"}, ensure_ascii=False)
        try:
            result = await manager.call_tool(server, tname, args or {})
        except Exception as exc:
            result = {"error": str(exc)}
        return json.dumps(result, ensure_ascii=False, default=str)[:_TOOL_RESULT_MAX_CHARS]

    _fn.__annotations__["ctx"] = RunContext[Any]
    return _fn


# B-P1a.2：无状态“计划清单”工具（TodoWrite 语义）。
_PLAN_MAX_STEPS = 8
_PLAN_STATUSES = {"pending", "running", "done", "failed", "blocked", "skipped"}
_PLAN_USAGE_HINT = (
    "## 计划清单\n"
    "处理需要 ≥3 步的复杂任务时，先调用 update_plan 列出步骤计划（整份 goal + steps[{title,status}]）；"
    "随进展再次调用 update_plan 刷新整份清单的 status。步骤数超上限会被截断，届时请收敛步骤并直接执行或给出结论。"
)


def _build_update_plan_tool():
    """构造 update_plan 工具：模型每次传整份步骤清单，系统归一/截断步数并回显进度。

    无状态（不跨请求保存 Plan），避免缓存串台；返回的 tool 结果经现有 tool_progress 链路展示到前端时间线。
    """
    from pydantic import BaseModel
    from pydantic_ai import RunContext, Tool

    from assistant.plan_state import Plan

    class _StepIn(BaseModel):
        title: str
        status: str = "pending"  # pending/running/done/failed/blocked/skipped

    class UpdatePlanArgs(BaseModel):
        goal: str
        steps: list[_StepIn] = []

    async def _fn(ctx: Any, args: Any) -> str:
        plan = Plan(goal=args.goal or "任务", max_steps=_PLAN_MAX_STEPS)
        capped = False
        for st in args.steps:
            ps = plan.add_step(st.title)
            if ps is None:
                capped = True
                break
            ps.status = st.status if st.status in _PLAN_STATUSES else "pending"
        nxt = plan.next_pending()
        return json.dumps(
            {
                "ok": True,
                "goal": plan.goal,
                "total": plan.total,
                "done": plan.done_count,
                "next": (nxt.title if nxt else None),
                "remaining_steps": plan.remaining_steps(),
                "capped": capped,
                "hint": "步骤已达上限，请收敛后直接执行或给结论" if capped else "计划已记录，按计划推进",
            },
            ensure_ascii=False,
        )

    _fn.__annotations__["ctx"] = RunContext[Any]
    # args 用类对象而非名字标注（pydantic 按 globals 解析签名，局部类名会 NameError）
    _fn.__annotations__["args"] = UpdatePlanArgs
    return Tool(
        _fn,
        takes_ctx=True,
        name="update_plan",
        description="维护多步任务的计划清单（TodoWrite 式）：复杂/≥3 步任务先列步骤并随进度刷新整份清单。",
        timeout=TOOL_TIMEOUT_SECONDS,
    )


def _build_tools(context: Any) -> list[Any]:
    """从 REACT_TOOL_CATALOG + MCP 动态构建 PydanticAI Tool 列表。"""
    from pydantic_ai import Tool

    from services.react_tool_catalog import BUTLER_DECLARED_TOOL_IDS, REACT_TOOL_CATALOG

    tools: list[Any] = []
    entries = REACT_TOOL_CATALOG if isinstance(REACT_TOOL_CATALOG, list) else []
    for entry in entries:
        action_id = entry.get("action_id", "")
        # 声明源唯一化（审计波次② C-1 步骤 4）后 REACT_TOOL_CATALOG 是全平台
        # 能力目录（含原 planner 独有的 code.execute / mcp.tool.call /
        # process.kill / subagent.spawn …），butler 的 LLM 暴露面必须按
        # BUTLER_DECLARED_TOOL_IDS 取子集，否则合并会静默扩张管家可自主调用的
        # 工具集。真正的可见性判定仍由 is_tool_allowed（白/黑名单）把关。
        if not action_id or action_id not in BUTLER_DECLARED_TOOL_IDS:
            continue
        if not is_tool_allowed(action_id):
            continue
        tool_name = sanitize_tool_name(action_id)
        description = str(entry.get("description") or action_id)
        args_model = build_args_model(action_id, entry.get("payload_schema") or {})

        fn = _make_no_arg_fn(action_id) if args_model is None else _make_arg_fn(action_id, args_model)
        tools.append(
            Tool(
                fn,
                takes_ctx=True,
                name=tool_name,
                description=description,
                timeout=TOOL_TIMEOUT_SECONDS,
            )
        )

    # MCP 动态工具
    mcp = getattr(context, "mcp_client_manager", None)
    if mcp is not None:
        try:
            for mt in mcp.list_tools():
                server = str(mt.get("server", ""))
                tname = str(mt.get("name", ""))
                if not server or not tname:
                    continue
                tool_name = sanitize_tool_name(f"mcp.{server}.{tname}")
                tools.append(
                    Tool(
                        _make_mcp_fn(server, tname),
                        takes_ctx=True,
                        name=tool_name,
                        description=str(mt.get("description") or f"MCP {server}.{tname}"),
                        timeout=TOOL_TIMEOUT_SECONDS,
                    )
                )
        except Exception:
            logger.warning("butler agent: MCP tool listing failed", exc_info=True)

    tools.append(_build_update_plan_tool())
    return tools


def _environment_hints(context: Any) -> str:
    """本机真实路径与环境边界（2026-09-11）。

    生产 trace.db 实证：模型把桌面猜成 ``C:\\Users\\PC-20230405JYXU\\Desktop``
    （该用户名并不存在），file_read 因此反复以 path_outside_allowed_root 失败。
    根因不是权限——``_file_ops._read_roots()`` 早已把 ``Path.home()/"Desktop"``
    列入允许读根（其注释为「添加桌面目录，让 AI 可以读取用户桌面文件」）——而是
    模型不知道本机真实路径。这里把真实路径与读写边界一并注入，消除幻觉与无效重试。
    """
    from pathlib import Path

    home = Path.home()
    lines = [
        "## 本机环境（系统注入的真实路径，请直接使用，禁止猜测用户名）",
        f"- 用户主目录：{home}",
    ]
    desktop = home / "Desktop"
    if desktop.exists():
        lines.append(f"- 桌面（可读，不可写）：{desktop}")
    base_dir = getattr(getattr(context, "local_data_store", None), "base_dir", None)
    if base_dir:
        lines.append(f"- 应用数据目录（可读写）：{base_dir}")
    lines.append("- 需要读写文件时请直接使用上面列出的真实路径，不要凭猜测拼造路径（例如自行构造 C:/Users/某用户名/Desktop）。")
    return "\n".join(lines)


def _build_agent(context: Any):
    from pydantic_ai import Agent

    ai_client = getattr(context, "ai_client", None)
    if ai_client is None:
        raise RuntimeError("ai_client unavailable")
    model_cls = _make_siyi_model_class()
    return Agent(
        model_cls(ai_client),
        system_prompt=f"{BUTLER_SYSTEM_PROMPT}\n\n{_environment_hints(context)}\n\n{_PLAN_USAGE_HINT}",
        tools=_build_tools(context),
        retries=1,
        tool_timeout=TOOL_TIMEOUT_SECONDS,
        name="siyi-butler",
    )


_agent_cache: dict[int, tuple[Any, float, Any]] = {}
#                    键=id(context)  ↑agent ↑写入时刻 ↑绑定的 ai_client 身份
_cache_lock = threading.Lock()
_CACHE_TTL = 1800.0
_CACHE_MAX = 4


def invalidate_butler_agent_cache() -> int:
    """清空管家 Agent 缓存，返回被清掉的条目数（批5：provider 写后热生效）。

    为什么需要显式失效接口：``get_butler_agent`` 的条目在构建时把 ``context.ai_client``
    闭包进了 ``SiyiChatModel``（见 ``_build_agent``）。设置页 ``provider.add/update/remove``
    会 ``context.recreate_ai_client()`` 换掉 ``context.ai_client``，但缓存条目仍持旧
    client —— 在 TTL（1800s）内继续用旧 provider 路由（实测：改 provider 后需重启
    后端才接上）。本函数是该缓存的**唯一失效入口**，由 ``AppContext.recreate_ai_client``
    经 ``core.events`` 的 ``ai_client.recreated`` 事件主动调用（依赖倒置：assistant
    层订阅，稳定区不 import assistant）。
    """
    with _cache_lock:
        removed = len(_agent_cache)
        _agent_cache.clear()
    if removed:
        logger.info("butler agent cache invalidated: %d entry(ies) dropped", removed)
    return removed


def _on_ai_client_recreated(_event: Any = None) -> None:
    """``ai_client.recreated`` 事件处理器：AI 客户端换新即失效缓存。"""
    invalidate_butler_agent_cache()


def get_butler_agent(context: Any) -> Any | None:
    """获取（或构建）当前 context 的管家 Agent；不可用时返回 None。

    缓存命中需同时满足：TTL 内 **且** 条目绑定的 ``ai_client`` 与当前
    ``context.ai_client`` 是同一对象（批5 双保险）。

    为什么要比 client 身份：显式失效依赖"订阅了事件 + 事件被发出"两个前提，
    任何一环（模块尚未导入、写入路径绕过 ``recreate_ai_client``）失效就会静默
    回退到 TTL 行为。身份比对让"换了 client 却仍命中旧 agent"在结构上不可能
    发生 —— 写入后立刻读必定拿到新值，无需等待 TTL。
    """
    if context is None or getattr(context, "ai_client", None) is None:
        return None
    try:
        import pydantic_ai  # noqa: F401
    except ImportError:
        logger.warning("pydantic_ai not installed — butler agent unavailable")
        return None

    key = id(context)
    current_client = context.ai_client
    now = time.monotonic()
    with _cache_lock:
        hit = _agent_cache.get(key)
        if hit is not None and now - hit[1] < _CACHE_TTL and hit[2] is current_client:
            return hit[0]

    try:
        agent = _build_agent(context)
    except Exception as exc:
        logger.warning("butler agent build failed: %s", exc, exc_info=True)
        return None

    with _cache_lock:
        if len(_agent_cache) >= _CACHE_MAX:
            oldest = min(_agent_cache, key=lambda k: _agent_cache[k][1])
            _agent_cache.pop(oldest, None)
        _agent_cache[key] = (agent, now, current_client)
    return agent


# ═══════════════════════════════════════════════════════════════
# 审批闭环依赖倒置注册（模块尾部）
# ═══════════════════════════════════════════════════════════════
# 把 butler 掌握的“执行上下文 + 真实执行动作”逻辑经 services.confirmation_flow 的
# handler 注册表注入稳定层（稳定区反向依赖 assistant 已解除）。
# 注册时机安全性：条目带上下文 ⇒ butler 本进程存活 ⇒ 本模块已加载 ⇒ handler 已注册；
# 进程重启则上下文本就丢失，语义不变。


def _store_confirmation_context(confirmation_id: str, context: Any) -> None:
    """把确认对应的执行上下文暂存到进程内 dict（供批准后 handler 取回执行）。"""
    with _pending_contexts_lock:
        _pending_contexts[confirmation_id] = context


async def _execute_confirmation(
    confirmation_id: str,
    approved: bool,
    reason: Any,
) -> dict[str, Any]:
    """批准后的“真实执行”handler：取上下文 → capability_router.execute_direct →
    artifacts 下发 → checkpoint 记录。无上下文时不执行动作（返回等待重发 note）。
    """
    entry = get_confirmation_store().get(confirmation_id)
    action_id = entry["action_id"] if entry else None
    args = entry["args"] if entry else {}
    with _pending_contexts_lock:
        context = _pending_contexts.pop(confirmation_id, None)
    # 无上下文说明这不是 butler 暂存的确认（如 DSH/mcp:kernel 等其它来源走同一
    # 执行 handler 链）。返回 None 让出处理权，交给更匹配的 handler；若无 handler
    # 认领，由 confirmation_flow.resolve_confirmation 的默认兜底处理（等价语义）。
    # 该分支对但ler 自身的"进程重启丢上下文"同样适用：兜底也会返回 waiting-note。
    if context is None or action_id is None:
        return None
    if not approved:
        # 拒绝：不执行任何工具，仅把拒绝结果记录到挂起任务的 checkpoint，
        # 供模型 resume 时调整方案继续（不中断整个任务）。
        try:
            from assistant.butler_checkpoint import record_confirmation_resolution

            record_confirmation_resolution(
                context,
                confirmation_id,
                approved=False,
                tool=action_id,
                result={"rejected": True},
            )
        except Exception:
            logger.debug("record rejection to checkpoint failed", exc_info=True)
        return {"rejected": True}
    result: dict[str, Any] = {}
    cap = _resolve_capability_router(context)
    if cap is None:
        result = {"error": "capability_router 不可用"}
    else:
        try:
            # approved=True：用户已人工授权，透传给 ActionGuard 跳过
            # LLM 高危拦截（否则批准后仍被 llm_source_high_risk 拦截）。
            result = await asyncio.wait_for(
                cap.execute_direct(action_id, args, approved=True),
                timeout=TOOL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            result = {"error": f"{action_id} 执行超时"}
        except Exception as exc:
            result = {"error": str(exc)}
    # artifacts 不随事件全量回传，单独推给前端展示
    if isinstance(result, dict) and result.get("artifacts"):
        _emit_event("chat.artifact", {"artifacts": result["artifacts"], "source": action_id})
        result = {k: v for k, v in result.items() if k != "artifacts"}
    # M2 断点续跑：把人工确认结果记录到挂起任务的 checkpoint。
    try:
        from assistant.butler_checkpoint import record_confirmation_resolution

        record_confirmation_resolution(
            context,
            confirmation_id,
            approved=True,
            tool=action_id,
            result=result,
        )
    except Exception:
        logger.debug("record confirmation resolution to checkpoint failed", exc_info=True)
    return result


register_context_store(_store_confirmation_context)
register_execution_handler(_execute_confirmation)


# ═══════════════════════════════════════════════════════════════
# provider 写后热生效（批5，2026-09-18）
# ═══════════════════════════════════════════════════════════════
# 依赖倒置：稳定区 app_runtime.AppContext.recreate_ai_client 只发
# ``ai_client.recreated``（core.events 是双方共用的稳定基元），由本层订阅并
# 失效自己的 agent 缓存 —— 稳定区不 import assistant，内核契约 I1 边界不破。
try:
    from core.events import event_bus as _event_bus

    _event_bus.on("ai_client.recreated", _on_ai_client_recreated)
except Exception:  # noqa: BLE001 — 订阅失败不致命：get_butler_agent 的身份比对仍兜底
    logger.warning("订阅 ai_client.recreated 失败（provider 热生效退回 client 身份比对）", exc_info=True)
