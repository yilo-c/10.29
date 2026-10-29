"""记忆层：偏好、历史决策和常用来源。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from assistant.models import PlannedStep

logger = logging.getLogger("siyi.memory.layer")


class AssistantMemoryLayer:
    """记忆层：偏好、历史决策和常用来源。"""

    def __init__(self, data_store, observability_service=None):
        self.data_store = data_store
        self.observability_service = observability_service

    def get_state(self) -> dict[str, Any]:
        state = self.data_store.load_json(
            "assistant_memory",
            {
                "preferences": {},
                "common_sources": [],
                "updated_at": None,
            },
        )
        if not isinstance(state, dict):
            state = {"preferences": {}, "common_sources": [], "updated_at": None}
        state.setdefault("preferences", {})
        state.setdefault("common_sources", [])
        return state

    def get_user_profile_snapshot(self) -> dict[str, Any]:
        state = self.get_state()
        preferences = dict(state.get("preferences") or {})
        return {
            "focus_topics": self._normalize_list_preference(preferences.get("focus_topics")),
            "preferred_delivery": self._normalize_scalar_preference(preferences.get("preferred_delivery"), default="chat"),
            "quiet_hours": self._normalize_list_preference(preferences.get("quiet_hours")),
            "common_sources": [str(item).strip() for item in list(state.get("common_sources") or []) if str(item).strip()],
        }

    def remember_preference(self, key: str, value: Any, source: str = "manual") -> None:
        timestamp = datetime.now(timezone.utc).isoformat()

        def _updater(state: dict[str, Any]) -> dict[str, Any]:
            normalized = dict(state) if isinstance(state, dict) else {}
            preferences = dict(normalized.get("preferences") or {})
            preferences[str(key)] = {
                "value": value,
                "source": str(source or "manual"),
                "updated_at": timestamp,
            }
            normalized["preferences"] = preferences
            normalized["common_sources"] = list(normalized.get("common_sources") or [])
            normalized["updated_at"] = timestamp
            return normalized

        self.data_store.update_json("assistant_memory", {"preferences": {}, "common_sources": [], "updated_at": None}, _updater)

    def remember_sources(self, sources: list[str]) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()

        def _updater(state: dict[str, Any]) -> dict[str, Any]:
            normalized = dict(state) if isinstance(state, dict) else {}
            normalized["preferences"] = dict(normalized.get("preferences") or {})
            current = list(normalized.get("common_sources") or [])
            existing = {str(item).strip() for item in current if str(item).strip()}
            for source in sources:
                text = str(source or "").strip()
                if not text or text in existing:
                    continue
                current.append(text)
                existing.add(text)
            normalized["common_sources"] = current[:50]
            normalized["updated_at"] = timestamp
            return normalized

        self.data_store.update_json("assistant_memory", {"preferences": {}, "common_sources": [], "updated_at": None}, _updater)

    def append_decision(self, goal: str, steps: list[PlannedStep], summary: dict[str, Any]) -> None:
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "goal": str(goal or "").strip(),
            "steps": [
                {
                    "step_id": step.step_id,
                    "action_id": step.action_id,
                    "risk_level": step.risk_level,
                }
                for step in steps
            ],
            "summary": dict(summary or {}),
        }

        def _updater(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
            normalized = list(decisions) if isinstance(decisions, list) else []
            normalized.append(entry)
            return normalized[-300:]

        self.data_store.update_json("assistant_decisions", [], _updater)

    @staticmethod
    def _normalize_scalar_preference(raw: Any, *, default: str = "") -> str:
        if isinstance(raw, dict):
            raw = raw.get("value")
        text = str(raw or "").strip()
        return text or default

    @staticmethod
    def _normalize_list_preference(raw: Any) -> list[str]:
        if isinstance(raw, dict):
            raw = raw.get("value")
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        if isinstance(raw, str):
            return [part.strip() for part in raw.splitlines() if part.strip()]
        return []
