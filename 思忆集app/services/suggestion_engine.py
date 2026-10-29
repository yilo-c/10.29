"""主动建议引擎 — 观察 → 分析 → 建议。

核心流程：
1. 读取用户画像（习惯、偏好、关注领域）
2. 交叉关联笔记/任务/新闻的共同主题
3. 发现改进机会（重复操作、可自动化任务）
4. 检查 TechRadar 技术发现
5. 去重排序，返回 Top N 建议
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.events import AppEvent, event_bus as _global_event_bus

logger = logging.getLogger("siyi.suggestion_engine")

MAX_SUGGESTIONS = 6
CATEGORY_HABIT = "habit"
CATEGORY_CROSS_REF = "cross_ref"
CATEGORY_OPPORTUNITY = "opportunity"
CATEGORY_INFO = "info"


@dataclass
class Suggestion:
    """一条带证据的行动建议。"""

    id: str = ""
    text: str = ""
    reason: str = ""
    evidence: str = ""
    action_key: str = ""
    action_label: str = ""
    category: str = ""
    priority: int = 0  # 0-100, higher = more important
    created_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "reason": self.reason,
            "evidence": self.evidence,
            "action_key": self.action_key,
            "action_label": self.action_label,
            "category": self.category,
            "priority": self.priority,
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Suggestion:
        return Suggestion(
            id=str(data.get("id", "")),
            text=str(data.get("text", "")),
            reason=str(data.get("reason", "")),
            evidence=str(data.get("evidence", "")),
            action_key=str(data.get("action_key", "")),
            action_label=str(data.get("action_label", "")),
            category=str(data.get("category", "")),
            priority=int(data.get("priority", 0)),
            created_at=str(data.get("created_at", "")),
        )


class SuggestionEngine:
    """主动建议引擎。

    职责：
    1. 分析用户画像中的习惯数据 → 习惯类建议
    2. 交叉关联笔记/任务/新闻 → 关联类建议
    3. 发现改进机会（重复操作、工具推荐）→ 机会类建议
    4. 检查 TechRadar 发现 → 信息类建议
    5. 去重排序，返回 Top N
    """

    def __init__(
        self,
        user_profile_service=None,
        note_repository=None,
        task_repository=None,
        news_repository=None,
        tech_radar=None,
        evolution_orchestrator=None,
        ai_client=None,
        event_bus=None,
        agent_gateway=None,
    ):
        self._profile_svc = user_profile_service
        self._note_repo = note_repository
        self._task_repo = task_repository
        self._news_repo = news_repository
        self._tech_radar = tech_radar
        self._orchestrator = evolution_orchestrator
        self._ai_client = ai_client
        self._agent_gateway = agent_gateway
        # P2.3: 注入 EventBus 用于发射 proactive.suggestion 事件
        self._event_bus = event_bus if event_bus is not None else _global_event_bus

    def analyze(self) -> list[Suggestion]:
        """执行全面分析，返回排序后的建议列表。"""
        suggestions: list[Suggestion] = []

        suggestions.extend(self._analyze_habits())
        suggestions.extend(self._cross_reference())
        suggestions.extend(self._find_opportunities())
        suggestions.extend(self._check_tech_radar())

        # LLM 增强：如果规则建议不足，用模型生成更具体的建议
        if len(suggestions) < 3:
            llm_suggestions = self._llm_enhance()
            suggestions.extend(llm_suggestions)

        ranked = self.rank_and_deduplicate(suggestions)
        # P2.3: 排序完成后发射 proactive.suggestion 事件，让前端能实时收到
        # 排序后的 Top N 建议（无需等 inbox_service.deliver_suggestion 落库）
        self._emit_suggestion_event(ranked)
        return ranked

    def _emit_suggestion_event(self, ranked: list[Suggestion]) -> None:
        """发射 proactive.suggestion 事件到 EventBus（best-effort）。"""
        if self._event_bus is None or not ranked:
            return
        try:
            # 取前 3 条作为高优先级建议事件 payload（避免事件过大）
            top_n = ranked[:3]
            payload = {
                "suggestions": [s.to_dict() for s in top_n],
                "total_ranked": len(ranked),
                "top_category": top_n[0].category if top_n else "",
                "top_priority": top_n[0].priority if top_n else 0,
            }
            self._event_bus.emit(
                AppEvent(
                    event_type="proactive.suggestion",
                    source="suggestion_engine",
                    payload=payload,
                )
            )
        except Exception:
            logger.debug("proactive.suggestion emit failed", exc_info=True)

    # ── LLM 增强 ──────────────────────────────────────────

    def _llm_enhance(self) -> list[Suggestion]:
        """用本地模型生成更具体的建议（规则建议不足时补充）。"""
        if self._ai_client is None:
            return []
        try:
            profile = self._profile_svc.load() if self._profile_svc else {}
        except Exception:
            profile = {}

        long_term = profile.get("long_term", {})
        context = profile.get("context", {})
        topics = long_term.get("focus_topics", [])
        tools = long_term.get("preferred_tools", [])
        hours = context.get("habit_hours", [])
        focus = context.get("current_focus", "")

        prompt_parts = ["根据以下用户画像生成 3 条具体的行动建议。"]
        if topics:
            prompt_parts.append(f"关注话题：{', '.join(topics)}")
        if tools:
            prompt_parts.append(f"常用功能：{', '.join(tools)}")
        if hours:
            prompt_parts.append(f"活跃时段：{', '.join(hours)}")
        if focus:
            prompt_parts.append(f"当前关注：{focus}")
        prompt_parts.append(
            "要求：每条建议包含 text（建议文案）、reason（原因）、evidence（证据）、"
            "action_key（操作键）、action_label（按钮文字），用 JSON 数组格式返回。"
            "建议必须具体、可执行，不要说空话。"
        )
        prompt = "\n".join(prompt_parts)

        messages = [
            {"role": "system", "content": "你是一个桌面 AI 助手的建议生成器。基于用户画像输出具体、可执行的建议。返回 JSON 数组。"},
            {"role": "user", "content": prompt},
        ]

        chat_method = getattr(self._ai_client, "chat_completion", None) or getattr(self._ai_client, "chat", None)

        def _local_fallback() -> str:
            if chat_method is None:
                return ""
            result = chat_method(messages, temperature=0.7, max_tokens=1024)
            if isinstance(result, tuple):
                return str(result[0]) if result else ""
            if isinstance(result, dict):
                # chat_completion（JudgeAwareClient）返回 OpenAI 形状：choices[0].message.content
                choices = result.get("choices") or []
                if choices:
                    return str((choices[0].get("message", {}) or {}).get("content", "") or "")
                return str(result.get("content", result.get("message", result.get("text", ""))) or "")
            return str(result or "")

        # M-cron 收敛：AI 生成优先走成熟内核（dsh 等），本地 ai_client 兜底
        from services.kernel_ai import kernel_generate

        system_prompt = messages[0]["content"] if messages else None
        content = kernel_generate(
            self._agent_gateway,
            prompt,
            system=system_prompt,
            timeout_s=240,
            fallback=_local_fallback,
        )

        if not content:
            return []

        return self._parse_llm_suggestions(content)

    @staticmethod
    def _parse_llm_suggestions(content: str) -> list[Suggestion]:
        """解析 LLM 返回的 JSON 建议。"""
        import json
        import re

        # 提取 JSON
        json_match = re.search(r"\[[\s\S]*\]", content)
        if not json_match:
            return []
        try:
            items = json.loads(json_match.group())
        except json.JSONDecodeError:
            # 尝试修补常见的 JSON 格式问题
            cleaned = re.sub(r",\s*}", "}", content)
            cleaned = re.sub(r",\s*]", "]", cleaned)
            try:
                items = json.loads(cleaned)
            except json.JSONDecodeError:
                return []

        if not isinstance(items, list):
            return []

        suggestions = []
        for item in items[:5]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "") or "").strip()
            if len(text) < 5:
                continue
            suggestions.append(
                Suggestion(
                    text=text,
                    reason=str(item.get("reason", "") or ""),
                    evidence=str(item.get("evidence", "") or ""),
                    action_key=str(item.get("action_key", "") or ""),
                    action_label=str(item.get("action_label", "") or ""),
                    category=CATEGORY_OPPORTUNITY,
                    priority=45,
                )
            )
        return suggestions

    # ── 习惯分析 ───────────────────────────────────────────

    def _analyze_habits(self) -> list[Suggestion]:
        """从用户画像读取习惯数据，生成习惯类建议。"""
        if self._profile_svc is None:
            return []
        try:
            profile = self._profile_svc.load()
        except Exception:
            logger.debug("_analyze_habits: load profile failed", exc_info=True)
            return []

        results: list[Suggestion] = []
        long_term = profile.get("long_term", {})
        context = profile.get("context", {})

        # 1. 习惯时段 → 建议固定工作流
        habit_hours = context.get("habit_hours", [])
        if habit_hours:
            hour_str = "、".join(habit_hours[:3])
            results.append(
                Suggestion(
                    text=f"你常在 {hour_str} 活跃，要不要设定固定工作流？",
                    reason="习惯时段已形成规律",
                    evidence="habit_hours: " + ", ".join(habit_hours),
                    action_key="cron.add",
                    action_label="设定定时工作流",
                    category=CATEGORY_HABIT,
                    priority=40,
                )
            )

        # 2. 高频工具 → 建议快捷键或批处理
        preferred_tools = long_term.get("preferred_tools", [])
        if preferred_tools:
            tool_str = "、".join(preferred_tools[:3])
            results.append(
                Suggestion(
                    text=f"你常使用 {tool_str}，需要我帮你整理常用操作为快捷键？",
                    reason="高频操作可加速",
                    evidence="preferred_tools: " + ", ".join(preferred_tools),
                    action_key="settings.shortcuts",
                    action_label="配置快捷键",
                    category=CATEGORY_HABIT,
                    priority=35,
                )
            )

        # 3. 关注话题 → 建议扩源
        focus_topics = long_term.get("focus_topics", [])
        common_sources = long_term.get("common_sources", [])
        if focus_topics and not common_sources:
            topic_str = "、".join(focus_topics[:3])
            results.append(
                Suggestion(
                    text=f"你关注 {topic_str}，需要我推荐相关信源？",
                    reason="关注话题但未配置信息源",
                    evidence="focus_topics without common_sources",
                    action_key="settings.sources",
                    action_label="配置信息源",
                    category=CATEGORY_HABIT,
                    priority=30,
                )
            )

        return results

    # ── 交叉关联 ───────────────────────────────────────────

    def _cross_reference(self) -> list[Suggestion]:
        """关联笔记/任务/新闻的共同主题。"""
        if self._profile_svc is None:
            return []
        try:
            profile = self._profile_svc.load()
        except Exception:
            return []

        topics = profile.get("long_term", {}).get("focus_topics", [])
        if not topics:
            return []

        results: list[Suggestion] = []
        topic = topics[0]

        # 统计各领域条数（简化：只检查是否有笔记/任务/新闻 repo）
        note_count = self._count_repo(self._note_repo, "notes")
        task_count = self._count_repo(self._task_repo, "tasks")

        # 共同主题提示
        if note_count > 0 and task_count > 0:
            results.append(
                Suggestion(
                    text=f"你的笔记和任务都涉及「{topic}」主题，需要我帮你统一整理？",
                    reason="跨模块主题关联",
                    evidence=f"notes={note_count}, tasks={task_count}, topic={topic}",
                    action_key="notes.organize",
                    action_label="整理关联内容",
                    category=CATEGORY_CROSS_REF,
                    priority=50,
                )
            )

        # 新闻关联
        news_count = self._count_repo(self._news_repo, "news_items")
        if news_count > 0 and note_count > 0:
            results.append(
                Suggestion(
                    text=f"有 {news_count} 条新闻和你的笔记主题「{topic}」相关，需要我整理简报？",
                    reason="新闻与笔记主题重合",
                    evidence=f"news={news_count}, topic={topic}",
                    action_key="digest.create",
                    action_label="生成主题简报",
                    category=CATEGORY_CROSS_REF,
                    priority=45,
                )
            )

        return results

    def _count_repo(self, repo, method: str) -> int:
        """尝试读取 repository 的条目数量。"""
        if repo is None:
            return 0
        try:
            items: list[Any] = getattr(repo, f"get_all_{method}", lambda: [])()

            if items is None:
                return 0
            return len(items) if isinstance(items, (list, tuple)) else 0
        except Exception:
            return 0

    # ── 改进机会 ───────────────────────────────────────────

    def _find_opportunities(self) -> list[Suggestion]:
        """发现可改进的工作流和自动化机会。"""
        if self._profile_svc is None:
            return []
        try:
            profile = self._profile_svc.load()
        except Exception:
            return []

        results: list[Suggestion] = []
        permission = profile.get("permission", {})

        proactive = permission.get("allow_proactive_push", True)
        if not proactive:
            results.append(
                Suggestion(
                    text="主动推送已关闭，开启后可收到习惯分析和信息建议",
                    reason="主动推送被禁用",
                    evidence="permission.allow_proactive_push = false",
                    action_key="settings.proactive",
                    action_label="开启主动推送",
                    category=CATEGORY_OPPORTUNITY,
                    priority=25,
                )
            )

        return results

    # ── 技术雷达 ───────────────────────────────────────────

    def _check_tech_radar(self) -> list[Suggestion]:
        """检查 TechRadar 发现，生成信息类建议。"""
        if self._orchestrator is None and self._tech_radar is None:
            return []

        results: list[Suggestion] = []

        # 优先从 orchestrator 取已汇报的发现
        if self._orchestrator is not None:
            try:
                briefing = self._orchestrator.get_last_briefing()
                if briefing is not None:
                    findings = getattr(briefing, "findings", []) or getattr(briefing, "items", [])
                    for f in findings[:3]:
                        name = getattr(f, "name", str(f))
                        results.append(
                            Suggestion(
                                text=f"「{name}」有可用更新，需要我了解详情？",
                                reason="技术雷达发现新版本",
                                evidence=f"tech_radar: {name}",
                                action_key="evolution.show",
                                action_label="查看详情",
                                category=CATEGORY_INFO,
                                priority=55,
                            )
                        )
            except Exception:
                logger.debug("_check_tech_radar: orchestrator briefing failed", exc_info=True)

        # 备用：直接从 tech_radar 扫描
        if not results and self._tech_radar is not None:
            try:
                findings = self._tech_radar.scan()
                for f in findings[:3]:
                    name = getattr(f, "name", str(f))
                    results.append(
                        Suggestion(
                            text=f"发现：{name} 有新版本可用",
                            reason="技术雷达扫描",
                            evidence=f"tech_radar: {name}",
                            action_key="evolution.show",
                            action_label="查看升级",
                            category=CATEGORY_INFO,
                            priority=55,
                        )
                    )
            except Exception:
                logger.debug("_check_tech_radar: scan failed", exc_info=True)

        return results

    # ── 排序去重 ───────────────────────────────────────────

    @staticmethod
    def rank_and_deduplicate(suggestions: list[Suggestion]) -> list[Suggestion]:
        """按优先级降序排列，去重（相同 text 保留最高优先级）。"""
        seen: dict[str, Suggestion] = {}
        for s in suggestions:
            key = s.text.strip()[:50]
            if key not in seen or s.priority > seen[key].priority:
                seen[key] = s
        ranked = sorted(seen.values(), key=lambda x: -x.priority)
        return ranked[:MAX_SUGGESTIONS]

    @staticmethod
    def build_suggestion_plain(
        text: str,
        reason: str = "",
        evidence: str = "",
        action_key: str = "",
        action_label: str = "",
        category: str = "",
        priority: int = 0,
    ) -> Suggestion:
        """快速创建一条纯文本建议（供外部使用）。"""
        return Suggestion(
            text=text,
            reason=reason,
            evidence=evidence,
            action_key=action_key,
            action_label=action_label,
            category=category,
            priority=priority,
        )
