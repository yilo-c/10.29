"""JudgeAwareClient — Judge 路由 + FallbackOpenAIClient 包装。

从 fallback.py 提取，职责：
- Judge 懒初始化 + 路由决策
- local / deepseek / MCP 执行路由
- FallbackOpenAIClient 回退
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

from ai.routing_constants import ROUTE_EXECUTOR_DEEPSEEK, ROUTE_EXECUTOR_KIRO
from ai.types import AIServiceError
from ai._assistant_bridge import inject_xiaowen_persona

logger = logging.getLogger("siyi.ai.fallback")


def _strip_think_tags(text: str) -> str:
    """Remove Qwen <think> blocks and leading/trailing whitespace from model output."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


# 隐私内容本地路由失败时的中文提示（绝不 fallback 到云端）。
_LOCAL_UNAVAILABLE_MSG = (
    "本地模型 (Ollama) 未运行，这部分内容按规定不能发送到云端。请启动 Ollama（运行 `ollama serve`）后重试，或在设置中确认本地模型已正确配置。"
)


class JudgeAwareClient:
    """包装 FallbackOpenAIClient，DeepSeek 决策后路由到 local / deepseek / mcp 执行。

    v2 架构：
    1. 所有请求先走 Judge（DeepSeek 快速判断路由）
    2. Judge 返回 executor: "local" | "deepseek" | "mcp"
    3. 根据 executor 路由到对应模型执行
    4. 复杂/代码任务 → 委托给 AutonomousAssistantManager（含 CCBridge）
    5. MCP 任务 → 调用真实 MCP 工具
    6. Judge/DeepSeek 不可用时 -> 安静回退到 FallbackOpenAIClient
    """

    def __init__(
        self,
        inner_client,
        settings_repository=None,
        judge=None,
        assistant_manager=None,
        mcp_client=None,
        local_ai_router=None,
        aggregator=None,
        routing_table=None,
    ):
        self._inner = inner_client
        self._settings = settings_repository
        self._judge = judge
        self._assistant_manager = assistant_manager
        self._mcp_client = mcp_client
        self._local_ai_router = local_ai_router
        self._aggregator = aggregator
        # P2.6: 动态路由表（按查询类型选择 provider）
        self._routing_table = routing_table
        self._lazy_judge = None
        self._audit_log: list[dict[str, Any]] = []
        self._audit_lock = threading.Lock()
        self._max_audit = 500

    def _is_cloud_only(self) -> bool:
        """云化模式：全链路走云端，禁用本地腿路由（用户已同意数据上云）。"""
        try:
            from ai.client.config import is_ai_cloud_only

            return is_ai_cloud_only(self._settings)
        except Exception:
            return False

    # ── Judge 懒初始化 ──

    def _get_judge(self):
        if self._judge is not None:
            return self._judge
        if self._lazy_judge is not None:
            return self._lazy_judge
        try:
            import importlib

            _mod = importlib.import_module("assistant.judge")
            self._lazy_judge = _mod.JudgeScheduler(
                ai_client=self._inner.primary,
                cloud_only=self._is_cloud_only(),
            )
            logger.info("JudgeAwareClient: JudgeScheduler 已懒初始化")
            return self._lazy_judge
        except Exception:
            logger.warning("JudgeAwareClient: Judge 懒初始化失败", exc_info=True)
            return None

    # ── P2.6: 动态路由表懒初始化 ──

    def _get_routing_table(self):
        """懒加载 ``ProviderRoutingTable``。

        若 ``__init__`` 未注入则按需从 settings 创建。
        每次调用前重新加载配置（``ProviderRoutingTable._load_routing`` 是幂等的），
        以确保用户通过 RPC 修改 ``llm_provider_routing`` 后立即生效。
        """
        if self._routing_table is not None:
            return self._routing_table
        try:
            from ai.provider_routing import ProviderRoutingTable

            self._routing_table = ProviderRoutingTable(settings_repository=self._settings)
            return self._routing_table
        except Exception:
            logger.debug("JudgeAwareClient: ProviderRoutingTable 懒初始化失败", exc_info=True)
            return None

    def _get_health_monitor(self):
        """从 inner client 获取已注入的 ProviderHealthMonitor（可能为 None）。"""
        return getattr(self._inner, "_health_monitor", None)

    @staticmethod
    def _extract_user_text(messages: list[dict[str, str]]) -> str:
        if not messages:
            return ""
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content", "")
                if content:
                    return str(content)
        return str(messages[-1].get("content", "")) if messages else ""

    # ── 审计日志 ──

    def _record_decision(self, text: str, decision) -> None:
        # 隐私保护：审计日志只记输入长度，不记用户内容片段。
        # （此前 v2 写 input_preview=text[:120]，会把银行卡号/密码等敏感
        # 片段写入日志文件，违反隐私边界。）
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "input_len": len(text),
            "executor": decision.executor,
            "reason": decision.reason,
            "privacy": decision.privacy,
            "confidence": decision.confidence,
        }
        with self._audit_lock:
            self._audit_log.append(entry)
            if len(self._audit_log) > self._max_audit:
                self._audit_log = self._audit_log[-self._max_audit :]

        logger.info(
            "Judge 路由决策: input_len=%d executor=%s confidence=%.2f reason=%s privacy=%s",
            len(text),
            decision.executor,
            decision.confidence,
            decision.reason,
            decision.privacy,
        )

    def get_judge_audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._audit_lock:
            return list(self._audit_log[-limit:])

    @staticmethod
    def _sanitize_mcp_name(value: str, default: str = "unknown") -> str:
        """Restrict MCP server/tool names to safe characters only.

        防止 MCP 元数据中的恶意字符串（路径穿越、XSS、命令注入）进入
        下游 JSON 响应或日志。仅保留 ``[a-zA-Z0-9_.-]``，其余替换为 ``_``，
        去除首尾 ``.``/``-``，截断至 128 字符。
        """
        if not value or not isinstance(value, str):
            return default
        sanitized = re.sub(r"[^a-zA-Z0-9_.\-]", "_", value).strip(".-")
        return sanitized[:128] if sanitized else default

    @staticmethod
    def _build_mcp_response(decision) -> dict[str, Any]:
        mcp_tool = decision.mcp_tool or {}
        server = JudgeAwareClient._sanitize_mcp_name(mcp_tool.get("server", ""))
        tool = JudgeAwareClient._sanitize_mcp_name(mcp_tool.get("tool", ""))
        action_id = mcp_tool.get("action_id") or f"mcp.{server}.{tool}"
        action_id = JudgeAwareClient._sanitize_mcp_name(str(action_id), "mcp.unknown")
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "routed_to": "mcp",
                                "action_id": action_id,
                                "server": server,
                                "tool": tool,
                            },
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        }

    # ── Explicit user action detection ──

    _EXPLICIT_ACTION_KEYWORDS: tuple[str, ...] = (
        "翻译助手",
        "翻译以下文本",
        "专业翻译助手",
        "摘要助手",
        "总结以下",
        "润色助手",
        "润色以下",
        "写作助手",
        "续写",
        "解释助手",
        "解释以下",
    )

    @classmethod
    def _is_explicit_user_action(cls, messages: list[dict[str, str]]) -> bool:
        """Check if the system or user prompt indicates an explicit user action.

        用户显式请求的工具型操作（翻译/摘要/润色/写作/解释）可绕过 Judge 路由
        开销，直接走云端 AI —— 因为用户已明确选择云端能力。但仍需通过隐私
        检查（见 chat_completion 中的 local_only 守卫）。
        """
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if any(kw in content for kw in cls._EXPLICIT_ACTION_KEYWORDS):
                    return True
        return False

    # ── Privacy pre-check helpers ──

    @staticmethod
    def _classify_privacy(text: str) -> str:
        """快速隐私预检，防止敏感数据送到 Judge（Judge 会调 DeepSeek）。"""
        if not text:
            return "shareable"
        try:
            from ai._assistant_bridge import PrivacyClassifier

            return PrivacyClassifier().classify(text)
        except Exception as exc:
            # P0 修复：隐私分类器失败时，保守起见标记为 local_only，
            # 防止敏感数据因分类器异常被送到云端（Judge → DeepSeek）。
            logger.warning("PrivacyClassifier failed: %s", exc, exc_info=True)
            return "local_only"

    def _route_local(
        self,
        text: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        logger.info("Privacy pre-check: routing to local: input_len=%d", len(text))
        if self._inner.local is not None:
            try:
                resp = self._inner.local.chat_completion(messages, temperature=temperature, max_tokens=max_tokens)
                # Clean <think> tags from model output
                choices = resp.get("choices") or []
                for c in choices:
                    msg = c.get("message") or {}
                    if msg.get("content"):
                        msg["content"] = _strip_think_tags(msg["content"])
                return resp
            except Exception as exc:
                logger.error("Local chat failed for privacy-sensitive content: %s", exc, exc_info=True)
        # CRITICAL: 隐私内容绝不 fallback 到云端。本地不可用时返回中文提示。
        return {
            "choices": [
                {
                    "message": {"content": _LOCAL_UNAVAILABLE_MSG},
                }
            ],
        }

    def _route_local_stream(
        self,
        text: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
    ):
        logger.info("Privacy pre-check: routing to local stream: input_len=%d", len(text))
        if self._inner.local is not None:
            stream_fn = getattr(self._inner.local, "chat_completion_stream", None)
            if stream_fn is not None:
                try:
                    for chunk in stream_fn(messages, temperature=temperature, max_tokens=max_tokens):
                        cleaned = _strip_think_tags(chunk)
                        if cleaned:
                            yield cleaned
                    return
                except Exception as exc:
                    logger.error("Local stream failed for privacy-sensitive content: %s", exc, exc_info=True)
        # CRITICAL: 隐私内容绝不 fallback 到云端。本地不可用时返回中文提示。
        yield _LOCAL_UNAVAILABLE_MSG

    # ── 核心接口 ──

    @staticmethod
    def _is_complex_execution_task(decision) -> bool:
        """判断 Judge 决策是否需要实际执行（而非仅聊天回答）。"""
        if decision.executor != ROUTE_EXECUTOR_DEEPSEEK:
            return False
        if decision.confidence < 0.7:
            return False
        exec_reasons = ("complex_code_task", "analysis_task", "research_or_web_search")
        if decision.reason in exec_reasons:
            return True
        if decision.reason.startswith("llm_routed_to_deepseek") and decision.confidence >= 0.8:
            return True
        return False

    def _execute_by_decision(
        self,
        decision,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any] | None:
        """根据 Judge 决策执行路由。返回 None 表示需要 fallback。"""
        executor = decision.executor

        if executor == "mcp":
            mcp_tool = (decision.mcp_tool or {}) if hasattr(decision, "mcp_tool") else {}
            if self._mcp_client is not None and mcp_tool:
                try:
                    tool_name = mcp_tool.get("tool") or mcp_tool.get("name", "")
                    if tool_name:
                        result = self._mcp_client.call_tool(
                            server_name=mcp_tool.get("server", ""),
                            tool_name=tool_name,
                            arguments=mcp_tool.get("arguments", {}),
                        )
                        return {
                            "choices": [
                                {
                                    "message": {
                                        "content": json.dumps(
                                            {
                                                "routed_to": "mcp",
                                                "action_id": mcp_tool.get("action_id", ""),
                                                "result": result,
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ],
                        }
                except Exception as exc:
                    logger.warning("MCP 工具调用失败，回退: %s", exc)
                    return None
            return self._build_mcp_response(decision)

        if executor == "local":
            if self._inner.local is None:
                logger.warning(
                    "Judge routed to local but self._inner.local is None! inner=%s primary=%s",
                    type(self._inner).__name__,
                    getattr(self._inner.primary, "model", "NONE") if self._inner.primary else "NONE",
                )
            else:
                logger.info("Judge 路由到本地: %s (local=%s base=%s)", decision.reason, self._inner.local.model, self._inner.local.api_base)
                try:
                    resp = self._inner.route_chat("local", messages, temperature=temperature, max_tokens=max_tokens)
                    if resp:
                        choices = resp.get("choices") or []
                        for c in choices:
                            msg = c.get("message") or {}
                            if msg.get("content"):
                                msg["content"] = _strip_think_tags(msg["content"])
                    return resp
                except AIServiceError as exc:
                    logger.warning("本地执行失败，回退到 DeepSeek: %s", exc)
                    return None
                except Exception as exc:
                    logger.warning("本地执行异常，回退到 DeepSeek: %s", exc)
                    return None

        if executor == ROUTE_EXECUTOR_KIRO or executor == "kiro":
            try:
                from ai.client.config import create_kiro_client

                kiro_client = create_kiro_client()
                if kiro_client is None:
                    logger.warning("Kiro 不可用，回退到 DeepSeek")
                    return None
                logger.info("Judge 路由到 Kiro Claude: %s", decision.reason)
                return kiro_client.chat_completion(messages, temperature=temperature, max_tokens=max_tokens)
            except Exception as exc:
                logger.warning("Kiro 执行失败，回退到 DeepSeek: %s", exc)
                return None

        if self._is_complex_execution_task(decision) and self._assistant_manager is not None:
            try:
                text = self._extract_user_text(messages)
                logger.info("Judge 路由到执行引擎: %s (confidence=%.2f)", decision.reason, decision.confidence)
                result = self._assistant_manager.run_goal(text)
                answer = result.get("answer", "任务已完成。") or "任务已完成。"
                return {
                    "choices": [
                        {
                            "message": {"content": str(answer)},
                        }
                    ],
                }
            except Exception as exc:
                logger.warning("执行引擎处理失败，回退到 DeepSeek 聊天: %s", exc)
                return None

        return None

    def _resolve_skip_cloud(self, raw_messages: list[dict[str, Any]]) -> bool:
        """七维-安C1（2026-08-29）：隐私分类唯一入口，非流式/流式共用。

        用原始 messages（persona 注入前）分类；分类器异常时保守默认
        local_only（fail-close，数据安全优先）。

        注：内层 FallbackOpenAIClient 流式路径另有同型隐私闸
        （fallback.py _check_privacy_egress / stream 候选过滤），本方法
        保证外层语义一致——内层闸若将来重构，外层守卫仍在（防御纵深
        对齐，非新增闸门）。
        """
        # 云化模式：用户已同意数据上云，隐私守卫放行云端。
        if self._is_cloud_only():
            return False
        try:
            raw_text = self._extract_user_text(raw_messages)
            return self._classify_privacy(raw_text) == "local_only"
        except Exception as exc:
            # 隐私分类本身失败时，保守地视为 local_only（数据安全优先）
            logger.warning("privacy classification failed, defaulting to local_only: %s", exc, exc_info=True)
            return True

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
        skip_judge: bool = False,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        aggregation: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """非流式对话。

        response_format（E3 JSON 稳健模式）为 None 时行为与改动前完全一致；
        显式传入时只在云端路径透传，本地腿（privacy local / Judge local / Kiro）
        不转发——本地端点普遍不支持该字段。tools 分支不转发（工具调用与 JSON
        强约束语义冲突，且 ai_text_service 类 JSON 调用从不带 tools）。
        """
        # E3: 仅在显式请求时向下透传，保证未传参调用方零行为变化
        _rf: dict[str, Any] = {"response_format": response_format} if response_format is not None else {}
        # P0 修复：隐私分类必须最先执行（在 persona 注入之前），用原始 messages。
        # 任何后续异常（persona 注入 / Judge 路由）都不得绕过隐私守卫。
        # 七维-安C1：分类逻辑收敛到 _resolve_skip_cloud（流式/非流式共用）。
        skip_cloud = self._resolve_skip_cloud(messages)

        try:
            # P1-16: 先检测显式工具型操作（翻译/摘要/润色等），
            # 这些场景不应注入小问人格，否则专业输出会被剑客口吻污染。
            explicit_action = self._is_explicit_user_action(messages)
            messages = inject_xiaowen_persona(messages, inject_persona=not explicit_action)
            text = self._extract_user_text(messages)

            # PRIORITY 0: tools 参数存在时直接绕过 Judge（ReAct 引擎管理循环）
            # P0 修复：即使 tools 存在，local_only 数据也必须走本地
            if tools is not None:
                if skip_cloud:
                    logger.info("tools present but content is local_only, routing to local")
                    return self._route_local(text, messages, temperature, max_tokens)
                return self._inner.chat_completion(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    tool_choice=tool_choice,
                )

            # PRIORITY 0.5: Explicit user actions (translate, summarize, etc.) bypass
            # Judge/local router — user has opted into cloud AI. But still run
            # privacy check: sensitive content must stay local even for explicit actions.
            if explicit_action:
                if skip_cloud:
                    return self._route_local(text, messages, temperature, max_tokens)
                logger.info("Explicit user action on non-sensitive content, routing to cloud")
                return self._inner.chat_completion(messages, temperature=temperature, max_tokens=max_tokens, **_rf)

            # PRIORITY 1: Privacy pre-check — prevent sensitive data reaching
            # any cloud provider. Runs before preferred_provider dispatch so
            # even explicit provider selection respects privacy boundaries.
            # P0 修复：复用前面已计算的 skip_cloud，避免重复分类且确保一致性
            if skip_cloud:
                return self._route_local(text, messages, temperature, max_tokens)

            # PRIORITY 2: User explicitly specified a provider/model
            if preferred_provider:
                logger.info(
                    "User-specified provider: %s, model: %s",
                    preferred_provider,
                    preferred_model or "default",
                )
                client = self._inner.get_client_by_provider(
                    preferred_provider,
                    preferred_model,
                    settings_repository=self._settings,
                )
                if client is not None:
                    return client.chat_completion(messages, temperature=temperature, max_tokens=max_tokens, **_rf)
                logger.warning("Preferred provider %s not found, falling back to default", preferred_provider)

            # PRIORITY 1.8: P2.6 动态路由表（按查询类型选择 provider）
            # 在 user-specified provider 之后、聚合层之前执行。
            # 若配置为空或匹配 provider 不可用，则静默回退到默认链。
            if not preferred_provider:
                routing_table = self._get_routing_table()
                if routing_table is not None and routing_table.is_enabled():
                    # 重新加载配置以应用最新设置
                    try:
                        routing_table._load_routing()
                    except Exception:
                        pass
                    health_monitor = self._get_health_monitor()
                    available_names: list[str] | None = None
                    try:
                        from ai.provider_routing import list_enabled_provider_names

                        available_names = list_enabled_provider_names(self._settings)
                    except Exception:
                        available_names = None
                    decision = routing_table.decide(text, health_monitor, available_names)
                    if decision is not None:
                        dyn_provider, dyn_model = decision
                        logger.info(
                            "Dynamic routing: query_type matched provider=%s model=%s",
                            dyn_provider,
                            dyn_model or "default",
                        )
                        client = self._inner.get_client_by_provider(
                            dyn_provider,
                            dyn_model or None,
                            settings_repository=self._settings,
                        )
                        if client is not None:
                            return client.chat_completion(
                                messages,
                                temperature=temperature,
                                max_tokens=max_tokens,
                                **_rf,
                            )
                        logger.warning(
                            "Dynamic routing: provider '%s' client creation failed, falling back",
                            dyn_provider,
                        )

            # PRIORITY 1.5: API 聚合层（可选开启）
            if aggregation and self._aggregator is not None:
                return self._run_aggregation(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    aggregation=aggregation,
                )

            # 0. Local AI pre-check: try local before cloud Judge
            # 云化模式跳过：全链路走云端，不做本地预检
            if not self._is_cloud_only() and self._local_ai_router is not None:
                local_result = self._local_ai_router.process(text)
                if local_result.handled:
                    logger.info(
                        "LocalAI handled: input_len=%d confidence=%.2f reason=%s",
                        len(text),
                        local_result.confidence,
                        local_result.reason,
                    )
                    return {
                        "choices": [
                            {
                                "message": {"content": local_result.response or ""},
                            }
                        ],
                    }

            # Skip LLM Judge for known simple operations (translation, explain, etc.)
            # where routing decisions are unnecessary overhead.
            if not skip_judge:
                judge = self._get_judge()

                if judge is not None:
                    decision = judge.route(text)
                    self._record_decision(text, decision)

                    result = self._execute_by_decision(decision, messages, temperature, max_tokens)
                    if result is not None:
                        return result

            return self._inner.chat_completion(messages, temperature=temperature, max_tokens=max_tokens, **_rf)

        except Exception as exc:
            # P2 修复：顶层异常回退时记录完整堆栈，便于排查根因
            logger.warning("JudgeAwareClient.chat_completion 回退: %s", exc, exc_info=True)
            # P0 修复：异常回退时必须尊重隐私守卫 —— local_only 数据不得回退到云端
            if skip_cloud:
                logger.info("exception fallback respects local_only privacy, routing to local")
                try:
                    return self._route_local(
                        self._extract_user_text(messages),
                        messages,
                        temperature,
                        max_tokens,
                    )
                except Exception as inner_exc:
                    logger.error("local route also failed in fallback: %s", inner_exc, exc_info=True)
                    # 本地也失败时返回空回复，绝不上云
                    return {
                        "choices": [{"message": {"content": ""}}],
                        "error": "local_only fallback failed",
                    }
            # 修复 B2: 异常回退时传递所有原始参数，避免 ReAct 循环中 tools 丢失
            return self._inner.chat_completion(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=tool_choice,
                **_rf,
            )

    def _run_aggregation(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int | None,
        aggregation: dict[str, Any],
    ) -> dict[str, Any]:
        """同步包装异步聚合调用，返回 OpenAI 兼容格式。

        在事件循环线程上调用时，使用独立线程运行 asyncio.run 避免死锁
        （run_coroutine_threadsafe + future.result 会阻塞同一 loop）。
        """
        import asyncio
        import concurrent.futures

        strategy = aggregation.get("strategy")
        timeout_ms = aggregation.get("timeout_ms", 30_000)
        max_providers = aggregation.get("max_providers")

        async def _async_run() -> dict[str, Any]:
            # asyncio.run 每次创建新事件循环，而 _aggregator._semaphore 会绑定到
            # 首个循环并在该循环关闭后失效，导致第二次调用抛 RuntimeError
            # ("... bound to a different event loop")。在每次运行前重建 semaphore
            # 以绑定当前循环。
            agg = self._aggregator
            # P1-fix: 通过公共方法 rebind_semaphore 重建（如不存在则回退 setattr）
            max_cc = getattr(agg, "_max_concurrent", 5)
            rebind = getattr(agg, "rebind_semaphore", None)
            if rebind is not None:
                rebind(max_cc)
            else:
                setattr(agg, "_semaphore", asyncio.Semaphore(max_cc))
            result = await agg.aggregate(
                messages,
                strategy=strategy,
                temperature=temperature,
                max_tokens=max_tokens or 4096,
                timeout_ms=timeout_ms,
                max_providers=max_providers,
            )
            return {
                "choices": [
                    {
                        "message": {"content": result.content},
                    }
                ],
                "aggregation": {
                    "strategy": result.strategy,
                    "winner": result.winner,
                    "latency_ms": result.latency_ms,
                    "cost_estimate": result.cost_estimate,
                    "attempted": result.attempted,
                    "failed": result.failed,
                },
            }

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_async_run())

        # 在事件循环线程上：用独立线程运行，避免死锁
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _async_run()).result(timeout=timeout_ms / 1000.0)

    def _run_aggregation_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int | None,
        aggregation: dict[str, Any],
    ):
        """同步包装异步流式聚合调用，逐 chunk yield。

        使用线程安全队列 + 独立线程运行 async producer，实现真正的增量流式输出，
        而非缓冲全部内容后一次性返回。
        """
        import asyncio
        import concurrent.futures
        import queue as thread_queue

        strategy = aggregation.get("strategy")
        timeout_ms = aggregation.get("timeout_ms", 30_000)
        max_providers = aggregation.get("max_providers")

        async def _async_gen():
            async for item in self._aggregator.aggregate_stream(
                messages,
                strategy=strategy,
                temperature=temperature,
                max_tokens=max_tokens or 4096,
                timeout_ms=timeout_ms,
                max_providers=max_providers,
            ):
                if item.get("type") == "chunk":
                    data = item.get("data") or ""
                    if data:
                        yield str(data)

        _DONE = object()
        _ERROR = object()
        sync_queue: thread_queue.Queue = thread_queue.Queue()

        def _run_producer():
            async def _produce():
                try:
                    async for chunk in _async_gen():
                        sync_queue.put(chunk)
                except Exception as exc:
                    sync_queue.put((_ERROR, exc))
                finally:
                    sync_queue.put(_DONE)

            asyncio.run(_produce())

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        pool.submit(_run_producer)
        try:
            while True:
                try:
                    item = sync_queue.get(timeout=timeout_ms / 1000.0)
                except thread_queue.Empty:
                    break
                if item is _DONE:
                    break
                if isinstance(item, tuple) and len(item) == 2 and item[0] is _ERROR:
                    raise item[1]  # noqa: TRY002
                yield item
        finally:
            pool.shutdown(wait=False)

    def chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        aggregation: dict[str, Any] | None = None,
    ):
        # 七维-安C1：隐私分类最先执行（persona 注入前、任何分支前），与
        # 非流式语义对齐（此前流式 tools 分支无任何分类，local_only 依赖
        # 内层 fallback 隐私闸兜底——层间守卫不一致）。
        skip_cloud = self._resolve_skip_cloud(messages)
        try:
            # P1-16: 显式工具型操作不注入小问人格（同 chat_completion）
            explicit_action = self._is_explicit_user_action(messages)
            messages = inject_xiaowen_persona(messages, inject_persona=not explicit_action)
            text = self._extract_user_text(messages)

            # PRIORITY 0: tools 参数存在时直接绕过 Judge（ReAct 引擎管理循环）
            # 七维-安C1：local_only 与非流式一致路由本地。
            if tools is not None:
                if skip_cloud:
                    logger.info("tools present but content is local_only, routing to local (stream)")
                    yield from self._route_local_stream(text, messages, temperature, max_tokens)
                    return
                yield from self._inner.chat_completion_stream(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    tool_choice=tool_choice,
                )
                return

            # PRIORITY 0.5: Explicit user actions bypass Judge/local router but
            # still run privacy check — sensitive content stays local.
            if explicit_action:
                if self._classify_privacy(text) == "local_only":
                    yield from self._route_local_stream(text, messages, temperature, max_tokens)
                    return
                logger.info("Explicit user action on non-sensitive content (stream), routing to cloud")
                yield from self._inner.chat_completion_stream(messages, temperature=temperature, max_tokens=max_tokens)
                return

            # PRIORITY 1: Privacy pre-check — prevent sensitive data reaching
            # any cloud provider. Runs before preferred_provider dispatch so
            # even explicit provider selection respects privacy boundaries.
            # 七维-安C1：复用开头已算好的 skip_cloud（text 取自 user 消息，
            # persona 仅注入 system，分类输入不变），消除二次分类。
            if skip_cloud:
                yield from self._route_local_stream(text, messages, temperature, max_tokens)
                return

            # PRIORITY 2: User explicitly specified a provider/model
            if preferred_provider:
                logger.info(
                    "User-specified provider (stream): %s, model: %s",
                    preferred_provider,
                    preferred_model or "default",
                )
                client = self._inner.get_client_by_provider(
                    preferred_provider,
                    preferred_model,
                    settings_repository=self._settings,
                )
                if client is not None:
                    stream_fn = getattr(client, "chat_completion_stream", None)
                    if stream_fn is not None:
                        yield from stream_fn(messages, temperature=temperature, max_tokens=max_tokens)
                        return
                logger.warning("Preferred provider %s not found/streamable, falling back", preferred_provider)

            # PRIORITY 1.5: API 聚合层（可选开启，流式模式）
            if aggregation and self._aggregator is not None:
                yield from self._run_aggregation_stream(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    aggregation=aggregation,
                )
                return

            # 0. Local AI pre-check: try local before cloud Judge
            # 云化模式跳过：全链路走云端，不做本地预检
            if not self._is_cloud_only() and self._local_ai_router is not None:
                local_result = self._local_ai_router.process(text)
                if local_result.handled:
                    logger.info(
                        "LocalAI handled (stream): input_len=%d confidence=%.2f reason=%s",
                        len(text),
                        local_result.confidence,
                        local_result.reason,
                    )
                    yield local_result.response or ""
                    return

            judge = self._get_judge()

            if judge is not None:
                decision = judge.route(text)
                self._record_decision(text, decision)

                if decision.executor == "mcp":
                    # P0-2 修复：stream 模式下实际执行 MCP 工具（此前仅 yield 元数据 JSON，
                    # 工具从未被调用）。复用 _execute_by_decision 以保持与非 stream 一致。
                    mcp_resp = self._execute_by_decision(decision, messages, temperature, max_tokens)
                    if mcp_resp is not None:
                        yield mcp_resp["choices"][0]["message"]["content"]
                        return
                    # MCP 执行失败（_execute_by_decision 返回 None），回退到默认 stream
                    logger.warning("Judge stream MCP 执行失败，回退到默认 stream")

                if decision.executor == "local":
                    if self._inner.local is None:
                        logger.warning("Judge stream routed to local but self._inner.local is None!")
                    else:
                        stream_fn = getattr(self._inner.local, "chat_completion_stream", None)
                        if stream_fn is None:
                            logger.warning("Judge stream routed to local but local has no chat_completion_stream!")
                        else:
                            logger.info("Judge stream 路由到本地: model=%s base=%s", self._inner.local.model, self._inner.local.api_base)
                            try:
                                for chunk in stream_fn(messages, temperature=temperature, max_tokens=max_tokens):
                                    cleaned = _strip_think_tags(chunk)
                                    if cleaned:
                                        yield cleaned
                                return
                            except Exception as exc:
                                logger.warning("Local stream failed (non-critical): %s", exc)

            yield from self._inner.chat_completion_stream(messages, temperature=temperature, max_tokens=max_tokens)

        except Exception as exc:
            # P2 修复：顶层异常回退时记录完整堆栈，便于排查根因
            logger.warning("JudgeAwareClient.chat_completion_stream 回退: %s", exc, exc_info=True)
            # 修复 B2: 异常回退时传递所有原始参数，避免 ReAct 循环中 tools 丢失
            yield from self._inner.chat_completion_stream(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=tool_choice,
            )

    def stream_chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
    ):
        """流式 + 工具调用（透传到 FallbackOpenAIClient）。

        JudgeAwareClient 不在此层做 Judge 路由（tools 存在时绕过 Judge），
        直接透传到 inner 的 stream_chat_with_tools。

        ★ P1 修复：当用户显式选择了 provider 时，优先使用指定 provider，
        避免工具调用路径无视用户模型选择、走固定回退链的问题。
        yield 结构化事件 dict（同 FallbackOpenAIClient.stream_chat_with_tools）。
        """
        try:
            # ★ P1 修复：用户显式选择的 provider 优先
            if preferred_provider:
                logger.info(
                    "User-specified provider (stream+tools): %s, model: %s",
                    preferred_provider,
                    preferred_model or "default",
                )
                client = self._inner.get_client_by_provider(
                    preferred_provider,
                    preferred_model,
                    settings_repository=self._settings,
                )
                if client is not None:
                    stream_fn = getattr(client, "stream_chat_with_tools", None)
                    if stream_fn is not None:
                        yield from stream_fn(
                            messages=messages,
                            tools=tools,
                            tool_choice=tool_choice,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        )
                        return
                logger.warning(
                    "Preferred provider %s not found/streamable for tools, falling back",
                    preferred_provider,
                )

            # tools 存在时绕过 Judge（与 chat_completion_stream 一致）
            yield from self._inner.stream_chat_with_tools(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            # P2 修复：顶层异常回退时记录完整堆栈，便于排查根因
            logger.warning("JudgeAwareClient.stream_chat_with_tools 回退: %s", exc, exc_info=True)
            # 异常时尝试不带 tools 的普通流式（保底）
            try:
                for chunk in self._inner.chat_completion_stream(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    yield {"type": "text", "data": chunk}
                yield {"type": "done"}
            except Exception as fallback_exc:
                yield {"type": "error", "data": str(fallback_exc)}

    def complete_text(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
        skip_judge: bool = False,
    ) -> str:
        resp = self.chat_completion(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            skip_judge=skip_judge,
        )
        # Avoid circular import — use the method directly
        from .fallback import FallbackOpenAIClient

        return FallbackOpenAIClient._extract_text_from_response(resp)

    # ── 透明代理 ──

    def get_runtime_snapshot(self) -> dict[str, Any]:
        return self._inner.get_runtime_snapshot()

    def test_connection(self) -> dict[str, str]:
        return self._inner.test_connection()

    @property
    def primary(self):
        return self._inner.primary

    @primary.setter
    def primary(self, value):
        self._inner.primary = value

    @property
    def local(self):
        return self._inner.local

    @property
    def fallback(self):
        return self._inner.fallback

    def chat_completion_vision(
        self,
        text: str,
        image_base64: str,
        image_format: str = "png",
        system_prompt: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = 900,
    ) -> dict[str, Any]:
        return self._inner.chat_completion_vision(
            text=text,
            image_base64=image_base64,
            image_format=image_format,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def analyze_image(
        self,
        image_base64: str,
        image_format: str = "png",
        prompt: str = "请详细描述这张图片的内容。",
        max_tokens: int = 500,
    ) -> str:
        return self._inner.analyze_image(
            image_base64=image_base64,
            image_format=image_format,
            prompt=prompt,
            max_tokens=max_tokens,
        )
