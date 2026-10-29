"""意图路由层：统一入口，用户一句话自动分流为三类意图。

意图分类：
- chat:       聊天问答（知识检索、闲聊、摘要）
- execute:    执行任务（调能力/插件/MCP/子代理）
- automate:   创建自动化（定时任务、条件规则、规则中心）
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger("siyi.assistant.intent_router")

# ── 意图常量 ─────────────────────────────────────────────
INTENT_CHAT = "chat"
INTENT_EXECUTE = "execute"
INTENT_AUTOMATE = "automate"

# ── 关键词规则 ───────────────────────────────────────────
_AUTOMATE_KEYWORDS: list[str] = [
    "每天", "每天早上", "每天晚上", "每周", "每月", "定时", "定期", "自动执行", "cron",
    "提醒我", "到时候", "自动推送", "创建规则", "添加规则",
    "每日", "每晚", "每早", "周报自动", "自动化", "schedule",
]

_EXECUTE_KEYWORDS: list[str] = [
    "打开", "关闭", "启动", "运行", "执行", "下载", "同步", "搜索", "搜一下",
    "读取", "写入", "查看文件", "系统信息", "进程", "剪贴板",
    "新闻", "资讯", "简报", "笔记同步", "升级", "检查更新",
    "web.search", "web.fetch", "mcp", "子代理",
    "帮我做", "帮我找", "帮我查", "帮我搜",
    "抓取", "获取", "分析这个", "总结这个",
    # ── 检查/扫描类 ──
    "检查", "扫描", "检测", "诊断",
    "汇报",
    # ── 查询对象 ──
    "能力", "技能", "功能", "环境", "概况", "状态",
    # ── 画像/习惯 ──
    "习惯", "画像", "了解我", "学到了",
]

_CHAT_KEYWORDS: list[str] = [
    "你好", "你是谁", "介绍", "解释", "什么是", "为什么", "怎么理解",
    "聊聊", "说说", "讲讲", "谈谈", "想法", "建议", "看法",
    "帮我想", "思考", "分析一下", "评价",
]

_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


class IntentClassification:
    """意图分类结果。"""

    __slots__ = ("confidence", "intent", "metadata", "reason", "sub_intent")

    def __init__(
        self,
        intent: str,
        confidence: float = 1.0,
        reason: str = "",
        sub_intent: str = "",
        metadata: dict[str, Any] | None = None,
    ):
        self.intent = intent
        self.confidence = confidence
        self.reason = reason
        self.sub_intent = sub_intent
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "reason": self.reason,
            "sub_intent": self.sub_intent,
            "metadata": self.metadata,
        }


class AssistantIntentRouter:
    """意图路由器：rule-based 优先，LLM fallback 兜底。

    所有入口（主输入框、悬浮助手、浏览器桥）都通过这里统一分流。
    """

    def __init__(self, *, ai_client=None, user_profile_service=None):
        self._ai_client = ai_client
        self._user_profile_service = user_profile_service

    def set_ai_client(self, client) -> None:
        self._ai_client = client

    def classify(self, user_input: str) -> IntentClassification:
        """对用户输入进行意图分类。"""
        text = str(user_input or "").strip()
        if not text:
            return IntentClassification(INTENT_CHAT, 0.5, "empty_input")

        profile_context = self._get_profile_context()

        # 1) 规则优先
        rule_result = self._classify_by_rules(text)
        if rule_result.confidence >= 0.7:
            return rule_result

        # 1.5) 使用画像偏好修正模糊请求
        profile_bias_result = self._classify_by_profile_bias(text, profile_context, rule_result)
        if profile_bias_result is not None:
            return profile_bias_result

        # 2) LLM fallback
        if self._ai_client is not None:
            try:
                llm_result = self._classify_by_llm(text, profile_context=profile_context)
                if llm_result is not None:
                    return llm_result
            except Exception as exc:
                logger.info("LLM 意图分类失败，降级到规则: %s", exc, exc_info=True)

        # 3) 规则低置信度结果
        if rule_result.confidence > 0:
            return rule_result

        # 4) 默认 chat
        return IntentClassification(INTENT_CHAT, 0.3, "default_fallback")

    def classify_fast(self, user_input: str) -> IntentClassification:
        """仅使用规则做快速分类，避免在 UI 主线程触发 LLM fallback。"""
        text = str(user_input or "").strip()
        if not text:
            return IntentClassification(INTENT_CHAT, 0.5, "empty_input")
        rule_result = self._classify_by_rules(text)
        profile_bias_result = self._classify_by_profile_bias(text, self._get_profile_context(), rule_result)
        if profile_bias_result is not None:
            return profile_bias_result
        if rule_result.confidence > 0:
            return rule_result
        return IntentClassification(INTENT_CHAT, 0.3, "fast_default_fallback")

    def route(self, user_input: str) -> dict[str, Any]:
        """分类并返回路由信息，包括下一步应调用哪个组件。"""
        classification = self.classify(user_input)
        routing: dict[str, Any] = {
            "input": user_input,
            "classification": classification.to_dict(),
            "next_component": self._resolve_next_component(classification),
        }
        # 注入画像上下文
        ctx = self._get_profile_context()
        if ctx:
            routing["profile_context"] = ctx
            self._apply_profile_adjustments(routing, ctx, classification)
        return routing

    def _get_profile_context(self) -> dict[str, Any]:
        if self._user_profile_service is None:
            return {}
        try:
            ctx = self._user_profile_service.build_routing_context()
        except Exception as exc:
            logger.warning("读取画像上下文失败: %s", exc, exc_info=True)
            return {}
        return dict(ctx) if isinstance(ctx, dict) else {}

    @staticmethod
    def _build_profile_summary(ctx: dict[str, Any] | None) -> str:
        profile = dict(ctx or {})
        if not profile:
            return ""
        lines = []
        current_focus = str(profile.get("current_focus") or "").strip()
        if current_focus:
            lines.append(f"当前关注: {current_focus}")
        active_project = str(profile.get("active_project") or "").strip()
        if active_project:
            lines.append(f"活跃项目: {active_project}")
        focus_topics = [str(item).strip() for item in list(profile.get("focus_topics") or []) if str(item).strip()]
        if focus_topics:
            lines.append(f"关注主题: {', '.join(focus_topics[:5])}")
        preferred_delivery = str(profile.get("preferred_delivery") or "").strip()
        if preferred_delivery:
            lines.append(f"默认交付: {preferred_delivery}")
        auto_execute_level = str(profile.get("auto_execute_level") or "").strip()
        if auto_execute_level:
            lines.append(f"自动执行级别: {auto_execute_level}")
        return "\n".join(lines)

    def _classify_by_profile_bias(
        self,
        text: str,
        ctx: dict[str, Any] | None,
        rule_result: IntentClassification,
    ) -> IntentClassification | None:
        profile = dict(ctx or {})
        focus_topics = [str(item).strip().lower() for item in list(profile.get("focus_topics") or []) if str(item).strip()]
        if not focus_topics:
            return None
        normalized = text.lower()
        if any(topic and topic in normalized for topic in focus_topics):
            if rule_result.intent == INTENT_CHAT and rule_result.reason in {"short_input", "ambiguous_default"}:
                return IntentClassification(INTENT_EXECUTE, 0.65, "profile_focus_bias", "profile_topic_task")
        return None

    # ── 画像调整 ─────────────────────────────────────────

    def _apply_profile_adjustments(
        self,
        routing: dict[str, Any],
        ctx: dict[str, Any],
        classification: IntentClassification,
    ) -> None:
        """根据画像上下文调整路由结果。"""
        # quiet_hours 静默抑制
        quiet_hours = ctx.get("quiet_hours") or []
        if quiet_hours and self._in_quiet_hours(quiet_hours):
            routing["quiet_suppressed"] = True

        # auto_execute_level='none' 降级 execute → chat
        if ctx.get("auto_execute_level") == "none" and classification.intent == INTENT_EXECUTE:
            routing["classification"] = IntentClassification(
                INTENT_CHAT,
                classification.confidence,
                "downgraded_by_profile",
                classification.sub_intent,
                classification.metadata,
            ).to_dict()
            routing["next_component"] = "chat_handler"

    @staticmethod
    def _in_quiet_hours(quiet_hours: list[str]) -> bool:
        """判断当前时间是否在任意 quiet_hours 区间内。

        格式: ["HH:MM-HH:MM", ...]
        """
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute
        for qh in quiet_hours:
            parts = str(qh).split("-")
            if len(parts) != 2:
                continue
            try:
                start_parts = parts[0].strip().split(":")
                end_parts = parts[1].strip().split(":")
                start_m = int(start_parts[0]) * 60 + int(start_parts[1])
                end_m = int(end_parts[0]) * 60 + int(end_parts[1])
            except (ValueError, IndexError):
                continue
            if start_m <= end_m:
                if start_m <= current_minutes <= end_m:
                    return True
            else:
                # 跨午夜
                if current_minutes >= start_m or current_minutes <= end_m:
                    return True
        return False

    # ── 规则分类 ─────────────────────────────────────────

    def _classify_by_rules(self, text: str) -> IntentClassification:
        lower = text.lower()

        # automate 优先级最高（包含定时/规则关键词）
        automate_score = self._keyword_score(lower, _AUTOMATE_KEYWORDS)
        if automate_score >= 0.7:
            sub = self._detect_automate_sub_intent(lower)
            return IntentClassification(INTENT_AUTOMATE, automate_score, "keyword_match", sub)

        # execute
        execute_score = self._keyword_score(lower, _EXECUTE_KEYWORDS)
        if execute_score >= 0.7:
            sub = self._detect_execute_sub_intent(lower)
            return IntentClassification(INTENT_EXECUTE, execute_score, "keyword_match", sub)

        # URL 检测 → execute
        if _URL_PATTERN.search(text):
            return IntentClassification(INTENT_EXECUTE, 0.8, "url_detected", "web_action")

        # chat
        chat_score = self._keyword_score(lower, _CHAT_KEYWORDS)
        if chat_score >= 0.5:
            return IntentClassification(INTENT_CHAT, chat_score, "keyword_match", "conversation")

        # 短文本（≤6字）偏 chat
        if len(text) <= 6:
            return IntentClassification(INTENT_CHAT, 0.4, "short_input")

        # 不确定 → 默认为聊天（避免误触发任务执行）
        return IntentClassification(INTENT_CHAT, 0.4, "ambiguous_default")

    @staticmethod
    def _keyword_score(text: str, keywords: list[str]) -> float:
        hits = sum(1 for kw in keywords if kw in text)
        if hits == 0:
            return 0.0
        if hits == 1:
            return 0.6
        if hits == 2:
            return 0.8
        return min(1.0, 0.8 + hits * 0.05)

    @staticmethod
    def _detect_automate_sub_intent(text: str) -> str:
        if any(kw in text for kw in ("每天", "每日", "每早", "每晚", "daily")):
            return "daily_schedule"
        if any(kw in text for kw in ("每周", "weekly", "周报")):
            return "weekly_schedule"
        if any(kw in text for kw in ("提醒我", "到时候")):
            return "reminder"
        if any(kw in text for kw in ("规则", "条件", "触发")):
            return "condition_rule"
        return "cron_generic"

    @staticmethod
    def _detect_execute_sub_intent(text: str) -> str:
        if any(kw in text for kw in ("新闻", "资讯", "简报", "热点")):
            return "news"
        if any(kw in text for kw in ("搜索", "搜一下", "帮我查", "帮我搜", "web.search")):
            return "web_search"
        if any(kw in text for kw in ("文件", "目录", "读取", "写入")):
            return "file_op"
        if any(kw in text for kw in ("同步", "笔记")):
            return "sync"
        if any(kw in text for kw in ("打开", "启动", "运行")):
            return "app_launch"
        if any(kw in text for kw in ("系统", "进程", "cpu", "内存")):
            return "system"
        if any(kw in text for kw in ("检查", "扫描", "检测", "诊断")):
            return "system_check"
        if any(kw in text for kw in ("能力", "技能", "功能")):
            return "capability_query"
        return "general_task"

    # ── LLM 分类 ─────────────────────────────────────────

    _LLM_CLASSIFY_PROMPT = (
        "你是一个意图分类器。用户会输入一句话，你需要判断属于以下哪一类：\n"
        "1. chat - 聊天问答、闲聊、知识问题、解释说明\n"
        "2. execute - 执行一个具体任务（搜索、打开、下载、同步、查询等）\n"
        "3. automate - 创建自动化规则（定时任务、周期执行、提醒等）\n\n"
        '仅返回一个 JSON 对象：{"intent": "chat|execute|automate", "confidence": 0.0-1.0, "reason": "简要原因"}\n'
        "不要输出其他内容。"
    )

    def _classify_by_llm(self, text: str, profile_context: dict[str, Any] | None = None) -> IntentClassification | None:
        if self._ai_client is None:
            return None
        system_prompt = self._LLM_CLASSIFY_PROMPT
        profile_summary = self._build_profile_summary(profile_context)
        if profile_summary:
            system_prompt = f"{system_prompt}\n\n## 用户画像摘要\n{profile_summary}"
        try:
            response = self._ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                max_tokens=128,
            )
        except Exception as exc:
            # P0 修复：LLM 分类失败时记录完整堆栈，否则意图路由全部静默不可用。
            logger.warning("IntentRouter LLM classification failed: %s", exc, exc_info=True)
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
            import json
            parsed = json.loads(raw.strip().strip("`").strip())
            intent = str(parsed.get("intent") or "").strip()
            if intent not in (INTENT_CHAT, INTENT_EXECUTE, INTENT_AUTOMATE):
                logger.warning("IntentRouter LLM returned invalid intent: %r", intent)
                return None
            confidence = float(parsed.get("confidence", 0.7))
            reason = str(parsed.get("reason") or "llm_classification")
            return IntentClassification(intent, confidence, reason)
        except Exception as exc:
            # P0 修复：JSON 解析失败时记录原始响应，便于排查格式问题。
            logger.warning(
                "IntentRouter failed to parse LLM response %r: %s", raw, exc, exc_info=True,
            )
            return None

    # ── 路由解析 ─────────────────────────────────────────

    @staticmethod
    def _resolve_next_component(classification: IntentClassification) -> str:
        intent = classification.intent
        if intent == INTENT_CHAT:
            return "chat_handler"
        if intent == INTENT_EXECUTE:
            return "planner_and_executor"
        if intent == INTENT_AUTOMATE:
            return "automation_builder"
        return "chat_handler"
