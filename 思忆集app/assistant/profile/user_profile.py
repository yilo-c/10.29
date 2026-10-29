"""用户画像层：分层偏好模型，驱动推理、自适应与主动性策略。

结构分四层：
- long_term:  长期偏好（关注主题、常用信息源、任务模板）
- context:    场景偏好（当前专注主题、活跃项目、临时兴趣）
- permission: 权限偏好（允许主动推送、安静时段、自动执行级别）
- output:     输出偏好（摘要风格、语言、详略程度）
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.assistant.user_profile")

# ── 默认画像 ────────────────────────────────────────────
DEFAULT_PROFILE: dict[str, Any] = {
    "schema_version": 1,
    "updated_at": "",
    # ── 长期偏好 ──
    "long_term": {
        "focus_topics": [],           # ["AI", "软件工程", "生产力"]
        "common_sources": [],         # ["36氪", "TechCrunch", "HackerNews"]
        "task_templates": [],         # [{"name": "每日新闻", "goal": "..."}]
        "preferred_tools": [],        # ["web.search", "notes.sync"]
        "key_people": [],             # 个人知识体系：重要人物 ["张三（朋友）", ...]
        "recent_events": [],          # 个人知识体系：重要事件 ["2024-01-01 周末爬山", ...]
    },
    # ── 场景偏好 ──
    "context": {
        "current_focus": "",          # "正在写论文"
        "active_project": "",         # "思忆集 v4"
        "temp_interests": [],         # 临时兴趣，自动过期
        "last_context_at": "",
        "habit_hours": [],            # 自动学习的活跃时段，如 ["09:00-12:00", "14:00-18:00"]
    },
    # ── 权限偏好 ──
    "permission": {
        "allow_proactive_push": True,        # 允许主动推送
        "proactive_categories": [            # 允许的主动类型
            "scheduled",                     # 定时触发
            "anomaly",                       # 异常触发
            "high_value_match",              # 强偏好匹配
        ],
        "quiet_hours": [],                   # ["22:00-08:00"]
        "auto_execute_level": "low",         # low=仅低风险自动 / medium / high
        "require_confirm_high_risk": True,
    },
    # ── 输出偏好 ──
    "output": {
        "summary_style": "concise",          # concise / detailed / bullet
        "language": "zh-CN",
        "max_items_per_digest": 10,
        "preferred_delivery": "inbox",       # inbox / notification / both
    },
}

_STORAGE_KEY = "assistant_user_profile"


class UserProfileService:
    """用户画像服务：CRUD + 推理辅助查询。"""

    def __init__(self, *, data_store, settings_repository=None):
        self.data_store = data_store
        self.settings_repository = settings_repository

    # ── 读写 ─────────────────────────────────────────────

    def load(self) -> dict[str, Any]:
        """加载画像，不存在则返回默认并持久化。"""
        raw = self.data_store.load_json(_STORAGE_KEY, None)
        if not isinstance(raw, dict) or "schema_version" not in raw:
            profile = self._build_initial_profile()
            self.save(profile)
            return profile
        return self._ensure_schema(raw)

    def save(self, profile: dict[str, Any]) -> None:
        """持久化画像。"""
        profile["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.data_store.save_json(_STORAGE_KEY, profile)

    def update_layer(self, layer: str, patch: dict[str, Any]) -> dict[str, Any]:
        """更新指定层并返回完整画像。"""
        profile = self.load()
        if layer not in ("long_term", "context", "permission", "output"):
            raise ValueError(f"unknown profile layer: {layer}")
        current_layer = dict(profile.get(layer) or {})
        current_layer.update(patch)
        profile[layer] = current_layer
        self.save(profile)
        return profile

    def merge_focus_topics(self, topics: list[str]) -> dict[str, Any]:
        """合并关注主题（去重、最多 12 个）。

        显式写操作：profile 一旦有值即成为源。首次合并时从默认空画像起步，
        不触发 settings 迁移（settings→profile 的 one-way 迁移只发生在纯读
        ``load()`` 上），避免把旧 settings 主题连带合并进用户显式设置的结果。
        """
        raw = self.data_store.load_json(_STORAGE_KEY, None)
        if isinstance(raw, dict) and "schema_version" in raw:
            profile = self._ensure_schema(copy.deepcopy(raw))
        else:
            profile = copy.deepcopy(DEFAULT_PROFILE)
        existing = list(profile.get("long_term", {}).get("focus_topics") or [])
        merged = list(dict.fromkeys(existing + [t.strip() for t in topics if t.strip()]))[:12]
        profile.setdefault("long_term", {})["focus_topics"] = merged
        self.save(profile)
        return profile

    def merge_key_people(self, people: list[str]) -> dict[str, Any]:
        """合并重要人物（去重、最多 30 个）。"""
        profile = self.load()
        existing = list(profile.get("long_term", {}).get("key_people") or [])
        merged = list(dict.fromkeys(existing + [str(p).strip() for p in people if str(p).strip()]))[:30]
        profile.setdefault("long_term", {})["key_people"] = merged
        self.save(profile)
        return profile

    def merge_recent_events(self, events: list[str]) -> dict[str, Any]:
        """合并重要事件（去重、最多 50 个）。"""
        profile = self.load()
        existing = list(profile.get("long_term", {}).get("recent_events") or [])
        merged = list(dict.fromkeys(existing + [str(e).strip() for e in events if str(e).strip()]))[:50]
        profile.setdefault("long_term", {})["recent_events"] = merged
        self.save(profile)
        return profile

    def set_current_focus(self, focus: str, project: str = "") -> dict[str, Any]:
        """设置当前专注场景。"""
        return self.update_layer("context", {
            "current_focus": focus.strip(),
            "active_project": project.strip(),
            "last_context_at": datetime.now(timezone.utc).isoformat(),
        })

    def set_quiet_hours(self, ranges: list[str]) -> dict[str, Any]:
        """设置安静时段。"""
        return self.update_layer("permission", {"quiet_hours": ranges})

    # ── 设置页 / 对话侧统一写入 ────────────────────────────

    import re as _re
    _VALID_QH_PATTERN = _re.compile(r"^\d{2}:\d{2}-\d{2}:\d{2}$")

    def save_settings_preferences(
        self,
        *,
        focus_topics: list[str] | None = None,
        common_sources: list[str] | None = None,
        current_focus: str | None = None,
        active_project: str | None = None,
        quiet_hours: list[str] | None = None,
        allow_proactive_push: bool | None = None,
        preferred_delivery: str | None = None,
        summary_style: str | None = None,
    ) -> dict[str, Any]:
        """设置页和对话侧偏好的统一写入入口。

        只更新调用方传入的字段，其余保持不变。
        """
        profile = self.load()

        # ── long_term ──
        lt = dict(profile.get("long_term") or {})
        if focus_topics is not None:
            deduped = list(dict.fromkeys(t.strip() for t in focus_topics if t.strip()))[:12]
            lt["focus_topics"] = deduped
        if common_sources is not None:
            deduped = list(dict.fromkeys(s.strip() for s in common_sources if s.strip()))[:20]
            lt["common_sources"] = deduped
        profile["long_term"] = lt

        # ── context ──
        ctx = dict(profile.get("context") or {})
        if current_focus is not None:
            ctx["current_focus"] = current_focus.strip()
            ctx["last_context_at"] = datetime.now(timezone.utc).isoformat()
        if active_project is not None:
            ctx["active_project"] = active_project.strip()
        profile["context"] = ctx

        # ── permission ──
        perm = dict(profile.get("permission") or {})
        if quiet_hours is not None:
            cleaned: list[str] = []
            for qh in quiet_hours:
                stripped = qh.strip()
                if not self._VALID_QH_PATTERN.match(stripped):
                    raise ValueError(f"invalid quiet hours format: {stripped!r}")
                cleaned.append(stripped)
            perm["quiet_hours"] = cleaned
        if allow_proactive_push is not None:
            perm["allow_proactive_push"] = bool(allow_proactive_push)
        profile["permission"] = perm

        # ── output ──
        out = dict(profile.get("output") or {})
        if preferred_delivery is not None:
            out["preferred_delivery"] = preferred_delivery.strip()
        if summary_style is not None:
            out["summary_style"] = summary_style.strip()
        profile["output"] = out

        self.save(profile)
        return profile

    def reset_profile(self) -> dict[str, Any]:
        """重置画像为默认值并持久化。"""
        profile = copy.deepcopy(DEFAULT_PROFILE)
        self.save(profile)
        return profile

    # ── 推理辅助查询 ─────────────────────────────────────

    def get_focus_topics(self) -> list[str]:
        """返回合并后的关注主题列表（画像 + settings 兜底）。"""
        profile = self.load()
        topics = list(profile.get("long_term", {}).get("focus_topics") or [])
        if not topics and self.settings_repository is not None:
            from core.settings_schema import KEY_ASSISTANT_FOCUS_TOPICS
            fallback = self.settings_repository.get_setting(KEY_ASSISTANT_FOCUS_TOPICS, [])
            topics = [str(t).strip() for t in (fallback if isinstance(fallback, list) else []) if str(t).strip()]
        return topics

    def get_common_sources(self) -> list[str]:
        """返回常用信息源列表。"""
        return list(self.load().get("long_term", {}).get("common_sources") or [])

    def get_summary_style(self) -> str:
        return str(self.load().get("output", {}).get("summary_style") or "concise")

    def get_preferred_delivery(self) -> str:
        return str(self.load().get("output", {}).get("preferred_delivery") or "inbox")

    def get_language(self) -> str:
        return str(self.load().get("output", {}).get("language") or "zh-CN")

    def get_auto_execute_level(self) -> str:
        return str(self.load().get("permission", {}).get("auto_execute_level") or "low")

    def is_proactive_allowed(self) -> bool:
        return bool(self.load().get("permission", {}).get("allow_proactive_push", True))

    def get_proactive_categories(self) -> list[str]:
        return list(self.load().get("permission", {}).get("proactive_categories") or [])

    def is_in_quiet_hours(self, now: datetime | None = None) -> bool:
        """判断当前是否处于安静时段。"""
        now = now or datetime.now()
        current_hhmm = now.strftime("%H:%M")
        ranges = list(self.load().get("permission", {}).get("quiet_hours") or [])
        for rng in ranges:
            parts = str(rng).split("-")
            if len(parts) != 2:
                continue
            start, end = parts[0].strip(), parts[1].strip()
            if start <= end:
                if start <= current_hhmm <= end:
                    return True
            else:
                if current_hhmm >= start or current_hhmm <= end:
                    return True
        return False

    def build_planner_summary(self) -> str:
        """为 Planner 构建简洁的画像摘要文本。"""
        profile = self.load()
        lines: list[str] = []
        lt = profile.get("long_term", {})
        topics = [str(t).strip() for t in (lt.get("focus_topics") or []) if str(t).strip()]
        if topics:
            lines.append(f"关注主题: {', '.join(topics[:6])}")
        sources = [str(s).strip() for s in (lt.get("common_sources") or []) if str(s).strip()]
        if sources:
            lines.append(f"常用来源: {', '.join(sources[:5])}")
        ctx = profile.get("context", {})
        focus = str(ctx.get("current_focus") or "").strip()
        if focus:
            lines.append(f"当前专注: {focus}")
        project = str(ctx.get("active_project") or "").strip()
        if project:
            lines.append(f"活跃项目: {project}")
        out = profile.get("output", {})
        style = str(out.get("summary_style") or "").strip()
        if style:
            lines.append(f"摘要风格: {style}")
        delivery = str(out.get("preferred_delivery") or "").strip()
        if delivery:
            lines.append(f"默认交付: {delivery}")
        # ── 长期偏好：首选项工具 ──
        tools = [str(t).strip() for t in (lt.get("preferred_tools") or []) if str(t).strip()]
        if tools:
            lines.append(f"常用工具: {', '.join(tools[:6])}")
        # ── 权限偏好 ──
        perm = profile.get("permission", {})
        auto_level = str(perm.get("auto_execute_level") or "").strip()
        if auto_level:
            lines.append(f"自动执行级别: {auto_level}")
        require_confirm = perm.get("require_confirm_high_risk")
        if isinstance(require_confirm, bool):
            lines.append(f"高风险需确认: {'是' if require_confirm else '否'}")
        cats = [str(c).strip() for c in (perm.get("proactive_categories") or []) if str(c).strip()]
        if cats:
            lines.append(f"主动推送类型: {', '.join(cats[:5])}")
        quiet = list(perm.get("quiet_hours") or [])
        if quiet:
            lines.append(f"安静时段: {', '.join(str(q) for q in quiet[:3])}")
        # ── 输出偏好：语言 ──
        lang = str(out.get("language") or "").strip()
        if lang:
            lines.append(f"语言: {lang}")
        # ── 场景偏好：活跃时段 ──
        hours = list(ctx.get("habit_hours") or [])
        if hours:
            lines.append(f"活跃时段: {', '.join(str(h) for h in hours[:5])}")
        # ── 个人知识体系（M2 提取）：重要人物 / 近期大事 ──
        people = [str(p).strip() for p in (lt.get("key_people") or []) if str(p).strip()]
        if people:
            lines.append(f"重要人物: {', '.join(people[:8])}")
        events = [str(e).strip() for e in (lt.get("recent_events") or []) if str(e).strip()]
        if events:
            lines.append(f"近期大事: {'；'.join(events[:5])}")
        # ── 画像新鲜度：提示画像是否由自动提取最近更新 ──
        updated = str(profile.get("updated_at") or "").strip()
        if updated:
            try:
                dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                fresh = dt.strftime("%Y-%m-%d")
                lines.append(f"画像更新: {fresh}")
            except (ValueError, TypeError):
                pass
        return "\n".join(lines)

    def build_routing_context(self) -> dict[str, Any]:
        """为 IntentRouter / CapabilityRouter 构建路由上下文。"""
        profile = self.load()
        return {
            "focus_topics": list(profile.get("long_term", {}).get("focus_topics") or []),
            "common_sources": list(profile.get("long_term", {}).get("common_sources") or []),
            "current_focus": str(profile.get("context", {}).get("current_focus") or ""),
            "active_project": str(profile.get("context", {}).get("active_project") or ""),
            "summary_style": str(profile.get("output", {}).get("summary_style") or "concise"),
            "preferred_delivery": str(profile.get("output", {}).get("preferred_delivery") or "inbox"),
            "auto_execute_level": str(profile.get("permission", {}).get("auto_execute_level") or "low"),
            "allow_proactive": bool(profile.get("permission", {}).get("allow_proactive_push", True)),
            "quiet_hours": list(profile.get("permission", {}).get("quiet_hours") or []),
        }

    # ── 偏好学习（被动采集） ─────────────────────────────

    def observe_action(self, action_id: str, goal: str = "") -> None:
        """记录用户执行的动作，用于偏好学习（轻量实现）。"""
        profile = self.load()
        tools = list(profile.get("long_term", {}).get("preferred_tools") or [])
        if action_id and action_id not in tools:
            tools.append(action_id)
            tools = tools[-20:]
            profile.setdefault("long_term", {})["preferred_tools"] = tools
            self.save(profile)

    # ── 内部 ─────────────────────────────────────────────

    def _build_initial_profile(self) -> dict[str, Any]:
        """从 settings 迁移已有偏好到画像。"""
        profile = copy.deepcopy(DEFAULT_PROFILE)
        if self.settings_repository is not None:
            from core.settings_schema import KEY_ASSISTANT_FOCUS_TOPICS, KEY_ASSISTANT_HIGH_RISK_REQUIRE_CONFIRM
            topics = self.settings_repository.get_setting(KEY_ASSISTANT_FOCUS_TOPICS, [])
            if isinstance(topics, list) and topics:
                profile["long_term"]["focus_topics"] = [str(t).strip() for t in topics if str(t).strip()][:12]
            confirm = self.settings_repository.get_setting(KEY_ASSISTANT_HIGH_RISK_REQUIRE_CONFIRM, True)
            profile["permission"]["require_confirm_high_risk"] = bool(confirm)
        return profile

    @staticmethod
    def _ensure_schema(raw: dict[str, Any]) -> dict[str, Any]:
        """确保画像包含所有必需字段。"""
        for layer_key, layer_defaults in DEFAULT_PROFILE.items():
            if isinstance(layer_defaults, dict):
                if layer_key not in raw or not isinstance(raw[layer_key], dict):
                    raw[layer_key] = copy.deepcopy(layer_defaults)
                else:
                    for field_key, field_default in layer_defaults.items():
                        if field_key not in raw[layer_key]:
                            raw[layer_key][field_key] = copy.deepcopy(field_default)
            elif layer_key not in raw:
                raw[layer_key] = copy.deepcopy(layer_defaults)
        return raw
