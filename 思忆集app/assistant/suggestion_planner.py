"""Assistant suggestion planner — builds smart suggestion lists from snapshots.

Standalone service (no main_window dependency).  All routing goes through the
optional assistant_inbox_service for low-interruption delivery.

Each suggestion dict contains:
    text (str):        显示文本
    action (str):      主动作键
    reason (str):      建议原因（为什么现在显示）
    convertible_actions (list[dict]): 可转换的动作列表
"""
from __future__ import annotations

import logging
import uuid
from typing import Any


def _tag_suggestion(item: dict[str, Any]) -> dict[str, Any]:
    """为建议条目添加唯一 ID（如无）。"""
    if "suggestion_id" not in item or not item["suggestion_id"]:
        item["suggestion_id"] = uuid.uuid4().hex[:12]
    return item


class AssistantSuggestionPlanner:
    """Builds the intelligent suggestion list from a dashboard snapshot.

    Parameters
    ----------
    workbench_service : PersonalWorkbenchService | None
        Used to build context-aware suggestions from the current workspace.
    assistant_inbox_service : AssistantInboxService | None
        Used to /route suggestions to low-interruption surfaces.
    """

    def __init__(
        self,
        workbench_service=None,
        assistant_inbox_service=None,
    ):
        self._workbench_service = workbench_service
        self._assistant_inbox_service = assistant_inbox_service
        self._log = logging.getLogger("siyi.suggestion_planner")

    # ------------------------------------------------------------------
    #  build suggestions (pure data — no side effects)
    # ------------------------------------------------------------------

    def build_suggestions(
        self, snapshot: dict[str, Any], doctor_suggestion: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Build a deduplicated, capped list of suggestion dicts.

        Parameters
        ----------
        snapshot : dict
            Dashboard snapshot as produced by ``_collect_dashboard_snapshot()``.
        doctor_suggestion : dict | None
            Pre-built doctor continue suggestion from DesktopDoctorService,
            or None to skip.

        Returns
        -------
        list[dict[str, Any]]
            At most 6 entries, each with at least ``text`` and ``action`` keys.
        """
        suggestions: list[dict[str, Any]] = []

        if doctor_suggestion is not None:
            suggestions.append(
                {
                    "text": doctor_suggestion.get("text") or "继续整理想 Doctor",
                    "action": doctor_suggestion.get("action") or "open_doctor",
                    "reason": doctor_suggestion.get("reason") or "桌面诊断有待跟进项",
                    "convertible_actions": doctor_suggestion.get("convertible_actions")
                    or [{"label": "打开诊断", "action": "open_doctor"}],
                }
            )

        wb = self._workbench_service
        if wb is not None:
            suggestions.extend(
                {
                    "text": item.get("text") or "继续整理当前上下文",
                    "action": item.get("action") or "capture",
                    "reason": item.get("reason") or "",
                    "convertible_actions": item.get("convertible_actions")
                    or [{"label": "执行", "action": item.get("action") or "capture"}],
                }
                for item in wb.build_context_suggestions(snapshot)
            )
        else:
            if snapshot.get("pending_tasks"):
                suggestions.append(
                    {
                        "text": f"先处理待办，当前有 {len(snapshot['pending_tasks'])} 个未完成任务",
                        "action": "tasks",
                        "reason": "待办未完成，注意力易分散",
                        "convertible_actions": [{"label": "打开待办", "action": "tasks"}],
                    }
                )
            if snapshot.get("notes"):
                suggestions.append(
                    {
                        "text": "打开笔记，整理最近一条记录的标题、分类和标签",
                        "action": "notes",
                        "reason": "有笔记沉淀可继续整理",
                        "convertible_actions": [{"label": "打开笔记", "action": "notes"}],
                    }
                )
            else:
                suggestions.append(
                    {
                        "text": "先写一条今日笔记，把任务和想法沉淀下来",
                        "action": "notes",
                        "reason": "笔记区尚空，建议从今日输入开始",
                        "convertible_actions": [{"label": "新建笔记", "action": "notes"}],
                    }
                )
            suggestions.append(
                {
                    "text": "先把当前想到的内容收进收件箱",
                    "action": "capture",
                    "reason": "收件箱为空，适合主动收一条",
                    "convertible_actions": [{"label": "快速收件", "action": "capture"}],
                }
            )

        # deduplicate and cap at 6
        deduped: list[dict[str, Any]] = []
        seen: set = set()
        for item in suggestions:
            key = (str(item.get("text")), str(item.get("action")))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(_tag_suggestion(item))
        return deduped[:6]

    # ------------------------------------------------------------------
    #  route suggestions — non-interruptive delivery
    # ------------------------------------------------------------------

    def route_suggestions(
        self,
        suggestions: list[dict[str, Any]],
        *,
        default_surface: str = "inbox_card",
    ) -> list[dict[str, Any]]:
        """Route built suggestions to low-interruption surfaces.

        Each suggestion is delivered via ``assistant_inbox_service`` if available,
        otherwise returned as-is for the caller to handle.

        Parameters
        ----------
        suggestions : list[dict]
            Output of ``build_suggestions()``.
        default_surface : str
            Target surface: workbench_card / floating_entry / inbox_card.

        Returns
        -------
        list[dict[str, Any]]
            Delivery results — each entry contains ``delivered_to`` and ``item``
            if routed, or the original suggestion dict if routing is unavailable.
        """
        inbox = self._assistant_inbox_service
        if inbox is None:
            return list(suggestions)

        results = []
        for item in suggestions:
            text = str(item.get("text") or "").strip()
            action = str(item.get("action") or "").strip()
            reason = str(item.get("reason") or "").strip()
            convertible = item.get("convertible_actions") or []
            result = inbox.deliver_suggestion(
                text=text,
                action=action,
                reason=reason,
                convertible_actions=convertible,
                surface=default_surface,
                metadata={"source": "suggestion_planner", "suggestion_id": item.get("suggestion_id", "")},
            )
            results.append(result)
        return results
