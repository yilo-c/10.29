"""Judge 调度员：DeepSeek 做决策大脑，本地 qwen 做手脚。

v2 架构变更：
- DeepSeek（云端）接收所有请求，快速判断：该本地执行还是 DeepSeek 执行
- 本地 qwen：简单任务（省 token）+ 隐私任务（数据不出本机）
- DeepSeek：复杂推理、编码、联网、多步任务
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from .judge_decision import (
    JUDGE_SYSTEM_PROMPT,
    PRIVACY_KEYWORDS,
    SIMPLE_TASK_KEYWORDS,
    ROUTE_EXECUTOR_LOCAL,
    ROUTE_EXECUTOR_DEEPSEEK,
    ROUTE_EXECUTOR_MCP,
    extract_json_block,
    JudgeDecision,
)
from .judge_supertag import suggest_supertag
from assistant.models import IntentDepth
from .privacy_classifier import PrivacyClassifier, PrivacyLevel


logger = logging.getLogger("siyi.assistant.judge")


# P2-17: 翻译任务优先走本地 qwen 节省 token —— 仅当文本短到本地模型胜任时。
_TRANSLATION_PATTERNS = ("翻译", "translate", "译成", "翻成", "翻译成", "译为")


def _resolve_local_translate_max_chars() -> int:
    """短翻译走本地的字符数阈值；超过则回落到 DeepSeek。

    可通过 ``SIYI_TRANSLATE_LOCAL_MAX_CHARS`` 覆盖。设为 0 关闭本地优先。
    """
    raw = os.environ.get("SIYI_TRANSLATE_LOCAL_MAX_CHARS")
    if raw is None or raw == "":
        return 200
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 200
    return max(0, value)


def _is_short_translation(text: str) -> bool:
    """文本看起来像短翻译任务（含翻译关键词且总长在阈值内）。"""
    limit = _resolve_local_translate_max_chars()
    if limit <= 0:
        return False
    lower = text.lower()
    if not any(p in lower for p in _TRANSLATION_PATTERNS):
        return False
    return len(text) <= limit


class JudgeScheduler:
    """Judge 调度员：DeepSeek 决策，路由到 local 或 deepseek 执行。

    设计原则：
    - 所有请求先由 DeepSeek（便宜模型 deepseek-chat）做快速路由判断
    - 路由结果：local（本地 qwen 执行）或 deepseek（DeepSeek 自身执行）
    - 关键词规则仅作为 LLM 不可用时的降级 fallback
    """

    def __init__(self, ai_client, cost_tracker=None, budget_manager=None, mcp_client=None, local_router=None, cloud_only: bool = False):
        self._ai_client = ai_client
        self._cost_tracker = cost_tracker
        self._budget_manager = budget_manager
        self._mcp_client = mcp_client
        self._local_router = local_router
        self._mcp_tool_index: list[dict[str, Any]] = []
        # P3-18: MCP 工具索引刷新 TTL（秒）。索引超过此时间后自动刷新，
        # 避免新增 MCP 服务器后需重启进程才生效。
        self._mcp_index_time: float = 0.0
        self._mcp_index_ttl: float = 300.0  # 5 分钟
        self._stats: dict[str, Any] = {
            "total_routed": 0,
            "by_executor": {},
            "by_reason": {},
            "last_routes": [],
        }
        self._privacy_classifier = PrivacyClassifier()
        # 云化模式：全链路走云端，禁用本地腿路由（用户已同意数据上云）
        if cloud_only or str(os.environ.get("SIYI_AI_CLOUD_ONLY", "")).strip().lower() not in ("", "0", "false"):
            self._cloud_only = True
        else:
            self._cloud_only = False

    def _refresh_mcp_tool_index(self) -> None:
        """从 MCP 客户端刷新可用工具索引。"""
        if self._mcp_client is None:
            self._mcp_tool_index = []
            return
        try:
            tools = self._mcp_client.list_tools()
            self._mcp_tool_index = tools if isinstance(tools, list) else []
            # P3-18: 记录刷新时间，供 _mcp_index_stale() 判定 TTL
            self._mcp_index_time = time.time()
        except Exception as exc:
            logger.warning("MCP tool index refresh failed: %s", exc, exc_info=True)
            self._mcp_tool_index = []

    def _mcp_index_stale(self) -> bool:
        """P3-18: 判断 MCP 工具索引是否过期需刷新。"""
        if not self._mcp_tool_index:
            return True
        return (time.time() - self._mcp_index_time) > self._mcp_index_ttl

    def _find_mcp_tool_for_task(self, text: str) -> dict[str, Any] | None:
        """根据任务描述匹配 MCP 工具。返回匹配的工具信息或 None。"""
        if not self._mcp_tool_index:
            return None
        lower = text.lower()

        if any(
            p in lower
            for p in ("读取", "读文件", "写文件", "读写", "文件", "read file", "write file", "list directory", "创建文件", "删除文件", "列出目录")
        ):
            for tool in self._mcp_tool_index:
                if "filesystem" in tool.get("server", "").lower():
                    return tool
            for tool in self._mcp_tool_index:
                name = tool.get("name", "").lower()
                if "read" in name or "write" in name:
                    return tool

        if any(p in lower for p in ("sql", "数据库", "database", "query", "查询", "select ")):
            for tool in self._mcp_tool_index:
                if "sqlite" in tool.get("server", "").lower():
                    return tool

        if any(p in lower for p in ("抓取", "fetch", "web scraping", "读取网页", "网页内容", "网页", "url")):
            for tool in self._mcp_tool_index:
                server = tool.get("server", "").lower()
                if "fetch" in server or "web" in server:
                    return tool

        if any(p in lower for p in ("git", "提交代码")):
            for tool in self._mcp_tool_index:
                if "git" in tool.get("server", "").lower():
                    return tool

        return None

    # ── 核心入口 ─────────────────────────────────────────────

    def route(self, task: str, context: dict[str, Any] | None = None) -> JudgeDecision:
        """分析任务并返回路由决策。

        决策顺序：
        0. 预算预检：超预算时强制 local（不调 DeepSeek）—— P1-1
        0.1 快速本地预检：简单问候/闲聊/隐私 → 直接 local（不调 DeepSeek）
        0.5 PrivacyClassifier 预检：敏感内容在调 LLM 前拦截（P0 修复）
        1. LLM 优先：DeepSeek 做路由判断（支持 local/deepseek）
        2. MCP 关键词匹配：明确匹配到 MCP 工具时路由到 mcp
        3. 规则 fallback：关键词匹配（LLM 不可用时）
        4. 默认：路由到 DeepSeek
        """
        text = str(task or "").strip()
        ctx = dict(context or {})

        # 0. P1-1: 预算预检 —— 超预算时强制本地，不调 DeepSeek 路由
        if self._is_budget_exceeded():
            decision = JudgeDecision(
                executor=ROUTE_EXECUTOR_LOCAL,
                reason="budget_exceeded_precheck",
                confidence=0.95,
                reasoning="预算超限，强制本地执行（不调 LLM 路由）",
            )
            self._record_route(decision)
            logger.info("预算超限预检: 路由到 local (budget_exceeded)")
            return decision

        # 0.1 快速本地预检：不调 DeepSeek 的简单任务
        # 云化模式跳过：全链路走云端
        local_check = None if self._cloud_only else self._route_local_if_simple(text)
        if local_check is not None:
            self._record_route(local_check)
            return local_check

        # 0.5 P0 修复：PrivacyClassifier 预检 —— 在调 DeepSeek 做路由判断之前
        # 先用 PrivacyClassifier 检测敏感内容。此前 PrivacyClassifier 在 LLM 路由
        # 之后才执行覆盖，但此时用户原文已经发给 DeepSeek 做路由判断，隐私已泄露。
        # 现在提前到 LLM 路由之前，确保敏感内容永不离开本机。
        # 云化模式跳过：用户已同意数据上云
        if not self._cloud_only and self._privacy_classifier.classify(text) == PrivacyLevel.LOCAL_ONLY:
            decision = JudgeDecision(
                executor=ROUTE_EXECUTOR_LOCAL,
                reason="privacy_precheck_classifier",
                privacy=True,
                confidence=0.95,
                reasoning="PrivacyClassifier 预检检测到敏感内容，强制本地执行（不调 LLM 路由）",
            )
            self._record_route(decision)
            logger.info("PrivacyClassifier 预检拦截: 路由到 local (敏感内容, input_len=%d)", len(text))
            return decision

        # 0.7 LocalAIRouter 预检：在调 DeepSeek 前先让本地模型自我评估
        # 云化模式跳过：全链路走云端
        # [local-leg-policy 2026-09-23] 当前策略：本地模型能力不足，此腿（本地自我评估参与 judge 决策）仅作兑底/当前不允许主导路由（审计波次②裁决③：代码保留）。
        # 待本地模型能力达标后，规划让本地模型加入 judge 并接管本地管理。
        if not self._cloud_only and self._local_router is not None:
            try:
                local_result = self._local_router.process(text, ctx)
                if local_result.handled:
                    decision = JudgeDecision(
                        executor=ROUTE_EXECUTOR_LOCAL,
                        reason="local_ai_router",
                        confidence=local_result.confidence,
                        reasoning=local_result.reason or "Local AI self-judged capable",
                    )
                    self._record_route(decision)
                    return decision
                if hasattr(local_result, "judge_context") and local_result.judge_context:
                    ctx.update(local_result.judge_context)
            except Exception as exc:
                logger.warning("Local router pre-check failed, falling through to LLM: %s", exc)

        # 1. LLM 路由（DeepSeek 决策）
        llm_decision = self._route_by_llm(text, ctx)
        # 云化模式：LLM 若裁决 local（如"简单任务"），强制改走云端（本地腿已禁用）
        if self._cloud_only and llm_decision is not None and llm_decision.executor == ROUTE_EXECUTOR_LOCAL:
            llm_decision = JudgeDecision(
                executor=ROUTE_EXECUTOR_DEEPSEEK,
                reason="cloud_only_override",
                privacy=False,
                confidence=llm_decision.confidence,
                reasoning=f"云化模式强制云端路由: {llm_decision.reason or llm_decision.reasoning}",
            )
        if llm_decision is not None:
            # LLM 返回 mcp 时，用关键词匹配补全 mcp_tool 信息
            if llm_decision.executor == ROUTE_EXECUTOR_MCP:
                mcp = self._match_mcp_tool(text)
                if mcp is not None:
                    llm_decision.mcp_tool = mcp

            # PrivacyClassifier 安全网（保留作为 LLM 返回后的二次确认）
            # 云化模式跳过：用户已同意数据上云
            if not self._cloud_only and self._privacy_classifier.classify(text) == PrivacyLevel.LOCAL_ONLY:
                if llm_decision.executor in (ROUTE_EXECUTOR_DEEPSEEK, ROUTE_EXECUTOR_MCP):
                    llm_decision = JudgeDecision(
                        executor=ROUTE_EXECUTOR_LOCAL,
                        reason="privacy_override",
                        privacy=True,
                        confidence=llm_decision.confidence,
                        reasoning="PrivacyClassifier 检测到敏感内容，覆盖为本地执行",
                    )
                    logger.info("PrivacyClassifier 覆盖路由: %s → local (敏感内容)", text[:40])

            self._record_route(llm_decision)
            return llm_decision

        # 2. MCP 关键词匹配（LLM 不可用时）
        mcp_decision = self._route_by_mcp_fallback(text, ctx)
        if mcp_decision is not None:
            self._record_route(mcp_decision)
            return mcp_decision

        # 3. 规则 fallback
        rule_decision = self._route_by_rules(text, ctx)
        # 云化模式：规则 fallback 判 local（如短翻译）也强制改走云端
        if self._cloud_only and rule_decision is not None and rule_decision.executor == ROUTE_EXECUTOR_LOCAL:
            rule_decision = JudgeDecision(
                executor=ROUTE_EXECUTOR_DEEPSEEK,
                reason="cloud_only_override_rules",
                privacy=False,
                confidence=rule_decision.confidence,
                reasoning=f"云化模式强制云端路由: {rule_decision.reason or rule_decision.reasoning}",
            )
        if rule_decision is not None:
            self._record_route(rule_decision)
            return rule_decision

        # 4. 默认：DeepSeek 执行
        fallback = self._default_route(text)
        self._record_route(fallback)
        return fallback

    # ── LLM 路由（主要路径）─────────────────────────────────

    def _route_local_if_simple(self, text: str) -> JudgeDecision | None:
        """快速本地预检：在调 DeepSeek 前判断是否可本地处理。

        匹配模式直接返回 local 路由，节省 DeepSeek 路由调用成本（~$0.001/次）。
        """
        lower = text.lower()

        # 隐私内容 → 强制本地（数据不出本机）
        if any(p in lower for p in PRIVACY_KEYWORDS):
            return JudgeDecision(
                executor=ROUTE_EXECUTOR_LOCAL,
                reason="privacy_precheck",
                privacy=True,
                confidence=0.9,
                reasoning="预检检测到隐私关键词，强制本地执行",
            )

        # 简单问候/闲聊 → 本地
        if any(p in lower for p in SIMPLE_TASK_KEYWORDS):
            has_task_word = any(
                p in lower
                for p in (
                    "查",
                    "找",
                    "搜索",
                    "整理",
                    "做",
                    "写",
                    "创建",
                    "实现",
                    "代码",
                    "分析",
                    "修复",
                    "debug",
                    "翻译",
                )
            )
            if not has_task_word:
                return JudgeDecision(
                    executor=ROUTE_EXECUTOR_LOCAL,
                    reason="simple_greeting_precheck",
                    confidence=0.95,
                    reasoning="简单问候/闲聊，本地处理节省 token",
                )

        # P2-17: 短翻译走本地 qwen（节省 token + 数据不出本机）。
        # 长文本/带格式/含代码的翻译仍走 DeepSeek 由 LLM 路由或规则 fallback 决定。
        if _is_short_translation(text):
            return JudgeDecision(
                executor=ROUTE_EXECUTOR_LOCAL,
                reason="translation_short_local",
                confidence=0.85,
                reasoning="短翻译任务优先本地 qwen 处理（数据不出本机）",
            )

        return None

    def _route_by_llm(self, text: str, context: dict[str, Any]) -> JudgeDecision | None:
        """DeepSeek 做路由决策——这是主要路径。"""
        if self._ai_client is None:
            return None

        ctx_hint = ""
        if context:
            profile = context.get("user_profile", {}) or {}
            if profile:
                ctx_hint = f"\n用户画像摘要: {json.dumps(profile, ensure_ascii=False, indent=2)[:200]}"

        try:
            response = self._ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT + ctx_hint},
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                max_tokens=150,  # 路由决策只需少量 token
                timeout=15,  # P1-3: 路由决策短超时，避免 DeepSeek 卡住阻塞整个对话链路
            )
        except Exception as exc:
            logger.warning("Judge LLM route failed: %s", exc, exc_info=True)
            return None

        raw = ""
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices:
                raw = (choices[0].get("message") or {}).get("content", "")
        elif isinstance(response, str):
            raw = response

        if not raw.strip():
            return None

        try:
            json_str = extract_json_block(raw)
            if json_str is None:
                return None
            parsed = json.loads(json_str)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("Judge LLM response parse failed: %s", exc, exc_info=True)
            return None

        executor = str(parsed.get("route", parsed.get("executor", ROUTE_EXECUTOR_DEEPSEEK)))
        reason = str(parsed.get("reason", ""))
        privacy = bool(parsed.get("privacy", False))
        confidence = min(1.0, max(0.0, float(parsed.get("confidence", 0.0))))

        # 校验 executor 合法性
        if executor not in (ROUTE_EXECUTOR_LOCAL, ROUTE_EXECUTOR_DEEPSEEK, ROUTE_EXECUTOR_MCP):
            executor = ROUTE_EXECUTOR_DEEPSEEK

        decision = JudgeDecision(
            executor=executor,
            reason=reason or f"llm_routed_to_{executor}",
            privacy=privacy,
            confidence=confidence,
            reasoning=f"LLM routed: {reason}" if reason else f"LLM routed to {executor}",
        )
        return decision

    # ── MCP 路由（关键词匹配）────────────────────────────────

    def _match_mcp_tool(self, text: str) -> dict[str, Any] | None:
        """根据文本匹配 MCP 工具，返回工具信息或 None。"""
        mcp_match = self._route_by_mcp_fallback(text, None)
        if mcp_match is not None:
            return mcp_match.mcp_tool
        return None

    def _route_by_mcp_fallback(self, text: str, context: dict[str, Any] | None) -> JudgeDecision | None:
        """关键词匹配 MCP 工具（LLM 不可用时 fallback）。"""
        if self._mcp_client is None or not self._mcp_client.is_enabled():
            return None

        # P3-18: 索引为空或过期(TTL)时刷新
        if self._mcp_index_stale():
            self._refresh_mcp_tool_index()
        if not self._mcp_tool_index:
            return None

        matched_tool = self._find_mcp_tool_for_task(text)
        if matched_tool is None:
            return None

        server_name = str(matched_tool.get("server", ""))
        tool_name = str(matched_tool.get("name", ""))
        action_id = f"mcp.{server_name}.{tool_name}"

        return JudgeDecision(
            executor=ROUTE_EXECUTOR_MCP,
            reason=f"mcp_tool:{action_id}",
            confidence=0.8,
            reasoning=f"MCP tool matched: {action_id}",
            mcp_tool={"server": server_name, "tool": tool_name, "action_id": action_id, "arguments": {}},
        )

    # ── 规则 fallback ────────────────────────────────────────

    def _route_by_rules(self, text: str, context: dict[str, Any]) -> JudgeDecision | None:
        """关键词规则 fallback——仅在 LLM 不可用时使用。

        P1-15：隐私关键词 + 简单问候已在 step 0（``_route_local_if_simple``）中
        优先拦截，本方法只负责 step 0 未覆盖的"复杂任务/分析/搜索/翻译"分发，
        避免与预检逻辑出现三重重复。
        """
        lower = text.lower()

        # 复杂任务 → DeepSeek
        complex_patterns = ("写代码", "debug", "修复 bug", "代码审查", "重构", "implement", "refactor")
        if any(p in lower for p in complex_patterns):
            return JudgeDecision(
                executor=ROUTE_EXECUTOR_DEEPSEEK,
                reason="complex_code_task",
                confidence=0.9,
                reasoning="检测到代码相关任务，需要 DeepSeek",
            )

        # 分析类任务 → DeepSeek
        analysis_patterns = ("分析", "analyze", "审查", "review")
        if any(p in lower for p in analysis_patterns):
            return JudgeDecision(
                executor=ROUTE_EXECUTOR_DEEPSEEK,
                reason="analysis_task",
                confidence=0.85,
                reasoning="检测到分析类任务，需要 DeepSeek",
            )

        search_patterns = ("搜索", "查一下", "research", "最新", "新闻", "trending", "联网")
        if any(p in lower for p in search_patterns):
            return JudgeDecision(
                executor=ROUTE_EXECUTOR_DEEPSEEK,
                reason="research_or_web_search",
                confidence=0.85,
                reasoning="需要联网搜索或研究，需要 DeepSeek",
            )

        # 翻译任务：短翻译 → 本地 qwen；长翻译/复杂格式 → DeepSeek
        translation_patterns = _TRANSLATION_PATTERNS
        if any(p in lower for p in translation_patterns):
            if _is_short_translation(text):
                return JudgeDecision(
                    executor=ROUTE_EXECUTOR_LOCAL,
                    reason="translation_task_local",
                    confidence=0.85,
                    reasoning="短翻译任务由本地 qwen 处理",
                )
            return JudgeDecision(
                executor=ROUTE_EXECUTOR_DEEPSEEK,
                reason="translation_task",
                confidence=0.85,
                reasoning="长翻译任务由 DeepSeek 处理（本地模型容量不足）",
            )

        return None

    def _default_route(self, text: str) -> JudgeDecision:
        """无法判断时默认路由到 DeepSeek（保守策略）。"""
        return JudgeDecision(
            executor=ROUTE_EXECUTOR_DEEPSEEK,
            reason="default_fallback",
            confidence=0.5,
            reasoning="无法明确判断，默认由 DeepSeek 处理",
        )

    # ── 统计 ─────────────────────────────────────────────────

    def _record_route(self, decision: JudgeDecision) -> None:
        self._stats["total_routed"] += 1
        executor = decision.executor
        self._stats["by_executor"][executor] = self._stats["by_executor"].get(executor, 0) + 1
        reason = decision.reason[:40]
        self._stats["by_reason"][reason] = self._stats["by_reason"].get(reason, 0) + 1
        self._stats["last_routes"].append(
            {
                "time": datetime.now(timezone.utc).isoformat(),
                "executor": executor,
                "reason": reason,
                "privacy": decision.privacy,
                "confidence": decision.confidence,
            }
        )
        if len(self._stats["last_routes"]) > 100:
            self._stats["last_routes"] = self._stats["last_routes"][-100:]
        # P1-1: 记录路由成本到 CostTracker + BudgetManager
        self._record_cost(decision)

    # ── P1-1: 成本监管 ──────────────────────────────────────

    def _is_budget_exceeded(self) -> bool:
        """P1-1: 检查预算是否超限。

        在路由决策前调用，超预算时强制 LOCAL 路由，避免调 DeepSeek 产生额外成本。
        预估单次路由判断的成本约 $0.001（DeepSeek deepseek-chat ~1k token）。
        """
        if self._budget_manager is None:
            return False
        try:
            return not self._budget_manager.check_budget(
                estimated_cost=0.001,
                estimated_api_calls=1,
            )
        except Exception:
            logger.debug("budget check failed, allowing route", exc_info=True)
            return False

    def _record_cost(self, decision: JudgeDecision) -> None:
        """P1-1: 记录路由决策的成本到 CostTracker + BudgetManager。

        - LOCAL 路由：$0（不消耗云端 token）
        - DEEPSEEK/MCP 路由：~$0.001（一次 Judge 路由调用）
        """
        if self._cost_tracker is None and self._budget_manager is None:
            return
        try:
            from assistant.cost_tracker import TIER_LOCAL, TIER_JUDGE

            if decision.executor == ROUTE_EXECUTOR_LOCAL:
                tier = TIER_LOCAL
                cost = 0.0
            else:
                tier = TIER_JUDGE
                cost = 0.001

            if self._cost_tracker is not None:
                self._cost_tracker.record(
                    tier=tier,
                    cost_usd=cost,
                    task_type="judge_route",
                    model="deepseek-chat" if tier != TIER_LOCAL else "local",
                    metadata={
                        "reason": decision.reason,
                        "executor": decision.executor,
                        "confidence": decision.confidence,
                    },
                )

            if self._budget_manager is not None and cost > 0:
                self._budget_manager.record_usage(
                    cost_usd=cost,
                    api_calls=1,
                    model="deepseek-chat",
                    category="judge_route",
                )
        except Exception as exc:
            logger.warning("记录路由成本失败: %s", exc, exc_info=True)

    def get_stats(self) -> dict[str, Any]:
        return dict(self._stats)

    def get_status_snapshot(self) -> dict[str, Any]:
        return {
            "total_routed": self._stats["total_routed"],
            "by_executor": dict(self._stats["by_executor"]),
            "last_route": self._stats["last_routes"][-1] if self._stats["last_routes"] else None,
        }

    # ── 意图深度分类（保留，供外部调用）─────────────────────

    CLASSIFICATION_RULES_L1 = [
        "你好",
        "hi",
        "hello",
        "嗨",
        "哈",
        "早上好",
        "下午好",
        "晚上好",
        "再见",
        "拜拜",
        "bye",
        "goodbye",
        "谢谢",
        "thank",
        "thanks",
        "你是谁",
        "你叫什么",
        "你能做什么",
        "今天天气",
        "现在几点",
        "几点了",
        "嗯",
        "哦",
        "好的",
        "ok",
        "是的",
        "对",
    ]

    CLASSIFICATION_RULES_L3 = [
        "整理",
        "收件箱",
        "归档",
        "组织",
        "重构",
        "迁移",
        "分析",
        "对比",
        "比较",
        "调研",
        "研究",
        "多步",
        "逐步",
        "先",
        "然后",
        "再",
        "之后",
        "如果",
        "否则",
        "根据不同情况",
        "批量",
        "所有文件",
        "每个",
        "逐",
        "写一个",
        "开发",
        "实现",
        "创建一个",
    ]

    CLASSIFICATION_RULES_L2_TOOL = [
        "查",
        "搜索",
        "找",
        "找一下",
        "搜一下",
        "查一下",
        "读取",
        "读",
        "打开",
        "获取",
        "翻译",
        "转换",
        "下载",
        "保存",
        "创建文件",
        "写文件",
        "计算",
        "统计",
        "画",
        "绘图",
        "生成图片",
    ]

    def classify_intent_depth(
        self,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[IntentDepth, str, float]:
        """三级意图深度判断。"""
        lower = text.strip().lower()
        ctx = dict(context or {})
        confidence = 0.0

        if any(p in lower for p in self.CLASSIFICATION_RULES_L1):
            has_task_word = any(
                p in lower
                for p in (
                    "查",
                    "找",
                    "搜索",
                    "整理",
                    "做",
                    "写",
                    "创建",
                    "实现",
                )
            )
            if not has_task_word:
                return (
                    IntentDepth.L1_DIRECT_ANSWER,
                    "检测到简单问候或致谢，无需工具调用",
                    0.9,
                )

        l3_keyword_count = sum(1 for p in self.CLASSIFICATION_RULES_L3 if p in lower)
        action_verbs = [v for v in ("整理", "分析", "创建", "写", "重构", "对比", "迁移", "实现", "开发") if v in lower]
        has_multi_operation = any(p in lower for p in ("然后", "再", "之后", "先", "逐步", "依次"))
        is_code_task = any(p in lower for p in ("写代码", "debug", "修复 bug", "代码审查", "重构", "脚本", "python", "实现一个", "开发一个"))

        if l3_keyword_count >= 2 or len(action_verbs) >= 2 or has_multi_operation or is_code_task:
            l3_reason_parts = []
            if l3_keyword_count >= 2:
                l3_reason_parts.append(f"多步关键词×{l3_keyword_count}")
            if len(action_verbs) >= 2:
                l3_reason_parts.append(f"多动作动词×{len(action_verbs)}")
            if has_multi_operation:
                l3_reason_parts.append("顺序操作词")
            return (
                IntentDepth.L3_AGENT_CHAIN,
                f"检测到多步操作（{' + '.join(l3_reason_parts)}），需要多步 Agent 链",
                min(0.75 + l3_keyword_count * 0.05 + len(action_verbs) * 0.05, 0.95),
            )

        l2_score = sum(1 for p in self.CLASSIFICATION_RULES_L2_TOOL if p in lower)
        if l2_score >= 1:
            return (
                IntentDepth.L2_SINGLE_TOOL,
                f"检测到工具调用关键词（L2得分={l2_score}），单步工具即可完成",
                0.85,
            )

        if self._ai_client is not None and confidence == 0.0:
            try:
                llm_depth, llm_reason, llm_confidence = self._llm_classify_depth(text, ctx)
                if llm_depth is not None:
                    return (llm_depth, llm_reason, llm_confidence)
            except Exception:
                logger.debug("LLM depth classification failed, using rule fallback", exc_info=True)

        text_len = len(text.strip())
        if text_len > 150:
            return (
                IntentDepth.L3_AGENT_CHAIN,
                "长文本/复杂指令，默认使用多步 Agent 链",
                0.6,
            )
        if self._mcp_client is not None and self._mcp_client.is_enabled():
            return (
                IntentDepth.L2_SINGLE_TOOL,
                "没有明确意图但 MCP 工具可用，默认单步工具调用",
                0.5,
            )
        return (
            IntentDepth.L1_DIRECT_ANSWER,
            "未匹配到明确任务特征，默认直接回答",
            0.5,
        )

    def _llm_classify_depth(
        self,
        text: str,
        context: dict[str, Any],
    ) -> tuple[IntentDepth | None, str, float]:
        """用 LLM 辅助判断意图深度。"""
        prompt = (
            "分析用户输入，判断意图深度级别，只输出 JSON：\n"
            '{"depth": "L1"|"L2"|"L3", "reasoning": "简短原因", "confidence": 0.0-1.0}\n\n'
            "L1=直接回答（简单问答，不需工具）\n"
            "L2=单步工具调用（需查记忆/文件/网络等，一次调用）\n"
            "L3=多步 Agent 链（复杂任务，需多步执行、条件分支）\n\n"
            f"用户输入：{text}"
        )

        try:
            response = self._ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": "你是意图深度分类器，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=150,
            )

            raw = ""
            if isinstance(response, dict):
                choices = response.get("choices") or []
                if choices:
                    raw = (choices[0].get("message") or {}).get("content", "")
            elif isinstance(response, str):
                raw = response

            if not raw.strip():
                return None, "", 0.0

            json_str = extract_json_block(raw)
            if json_str is None:
                return None, "", 0.0
            parsed = json.loads(json_str)
            depth_str = str(parsed.get("depth", ""))
            depth_map = {"L1": IntentDepth.L1_DIRECT_ANSWER, "L2": IntentDepth.L2_SINGLE_TOOL, "L3": IntentDepth.L3_AGENT_CHAIN}
            depth = depth_map.get(depth_str)
            if depth is None:
                return None, "", 0.0
            reason = str(parsed.get("reasoning", ""))
            confidence = min(1.0, max(0.0, float(parsed.get("confidence", 0.0))))
            return depth, reason, confidence
        except Exception as exc:
            # P0 修复：_parse_depth 是 Judge 路由的关键输入，失败时记录完整堆栈，
            # 否则路由质量下降且无法排查（此前为静默失败）。
            logger.warning("Judge _parse_depth failed: %s", exc, exc_info=True)
            return None, "", 0.0

    # ── 执行计划生成（保留）─────────────────────────────────

    def generate_execution_plan(self, goal: str, steps: list[Any]) -> dict[str, Any] | None:
        """根据目标和步骤列表生成执行计划摘要。"""
        from assistant.execution_plan import ExecutionPlan
        import uuid

        plan = ExecutionPlan(
            plan_id=uuid.uuid4().hex[:12],
            goal=goal,
            source="judge",
            reasoning="Judge 自动生成的执行计划",
        )

        file_paths_affected: list[str] = []
        for step in steps:
            action_id = str(getattr(step, "action_id", step.get("action_id", "")) if isinstance(step, dict) else getattr(step, "action_id", ""))
            risk_level = str(
                getattr(step, "risk_level", step.get("risk_level", "low")) if isinstance(step, dict) else getattr(step, "risk_level", "low")
            )
            description = self._describe_action(action_id, risk_level)
            step_id = str(getattr(step, "step_id", step.get("step_id", "")) if isinstance(step, dict) else getattr(step, "step_id", ""))

            payload = dict(getattr(step, "payload", step.get("payload", {})) if isinstance(step, dict) else getattr(step, "payload", {}))
            fp = payload.get("path") or payload.get("file_path") or ""
            step_file_paths = [fp] if fp else []

            plan.add_step(
                step_id=step_id or f"step_{len(plan._steps) + 1}",
                action_id=action_id,
                description=description,
                risk_level=risk_level,
                file_paths=step_file_paths,
            )
            if fp:
                file_paths_affected.append(fp)

        summary = plan.get_plan_summary()
        summary["file_paths_affected"] = file_paths_affected
        return summary

    @staticmethod
    def _describe_action(action_id: str, risk_level: str) -> str:
        descriptions = {
            "file.read": "读取文件",
            "file.write": "写入文件",
            "shell.run": "执行系统命令",
            "web.search": "联网搜索",
            "web.fetch": "抓取网页内容",
            "news.lookup": "检索新闻",
            "news.briefing": "生成新闻摘要",
            "notes.sync": "同步笔记",
            "notes.organize": "整理笔记",
            "memory.recall": "检索记忆",
            "browser.open": "打开浏览器",
            "app.launch": "启动应用",
            "clipboard.read": "读取剪贴板",
            "clipboard.write": "写入剪贴板",
            "process.list": "列出进程",
            "process.kill": "结束进程",
            "system.info": "获取系统信息",
            "journal.daily_draft": "生成日志草稿",
            "upgrade.check": "检查升级",
            "usage.snapshot": "使用情况快照",
            "web.download": "下载文件",
            "cron.list": "列出定时任务",
            "cron.add": "添加定时任务",
            "cron.remove": "删除定时任务",
            "mcp.tools.list": "列出 MCP 工具",
            "mcp.tool.call": "调用 MCP 工具",
            "subagent.spawn": "启动子代理",
            "subagent.list": "列出子代理",
            "subagent.get": "查看子代理详情",
        }
        desc = descriptions.get(action_id, f"执行 {action_id}")
        risk_mark = "⚠️ " if risk_level == "high" else "ℹ️ "
        return f"{risk_mark}{desc}"

    def suggest_supertag(
        self,
        text: str,
        tag_service=None,
    ) -> dict[str, Any] | None:
        return suggest_supertag(text, tag_service=tag_service)


__all__ = [
    "JudgeDecision",
    "JudgeScheduler",
]
