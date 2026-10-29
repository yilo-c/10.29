"""叙事与 Agent 状态 RPC — 查询记忆叙事、小问状态。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.narrative")


def register_narrative(registry, context) -> None:
    """Register narrative and agent state JSON-RPC methods."""

    # ── memory.narrative.latest ──
    async def narrative_latest(narrative_type: str = "weekly", limit: int = 1, **kwargs: Any) -> list[dict[str, Any]]:
        """获取最近的叙事条目（周报/里程碑/情绪曲线）。"""
        try:
            from memory.narrative.narrative_store import NarrativeStore

            data_dir = getattr(getattr(context, "local_data_store", None), "base_dir", None)
            if not data_dir:
                return []
            # P1 修复：NarrativeStore 构造函数中执行同步文件 I/O，
            # list_by_type/list_recent 也可能涉及文件 I/O，整体用 to_thread 包裹
            store = NarrativeStore(data_dir)
            if narrative_type and narrative_type != "all":
                return await asyncio.to_thread(store.list_by_type, narrative_type, limit=limit)
            return await asyncio.to_thread(store.list_recent, limit=limit)
        except Exception as exc:
            logger.debug("Failed to get narratives: %s", exc)
            return []

    registry.register_with_schema(
        "memory.narrative.latest",
        narrative_latest,
        schema={"properties": {"limit": {"type": "integer"}, "narrative_type": {"type": "string"}}, "type": "object"},
    )

    # ── memory.narrative.all_types ──
    async def narrative_types(**kwargs: Any) -> list[str]:
        """列出所有可用的叙事类型。"""
        return ["weekly", "milestone", "emotion", "monthly"]

    registry.register_with_schema(
        "memory.narrative.types",
        narrative_types,
        schema={"properties": {}, "type": "object"},
    )

    # ── memory.narrative.generate_monthly（T7.1）──
    async def narrative_generate_monthly(**kwargs: Any) -> dict[str, Any]:
        """手动触发月度信生成（复用月度管线，不污染周度去重标记）。"""
        try:
            from backend import narrative_scheduler

            narrative = await narrative_scheduler.generate_monthly_now(context)
            if narrative is None:
                return {"success": False, "error": "no source data or generation failed"}
            return {
                "success": True,
                "narrative_id": narrative.get("narrative_id"),
                "title": narrative.get("title"),
                "month_label": narrative.get("month_label"),
            }
        except Exception as exc:
            logger.debug("narrative generate monthly failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register_with_schema(
        "memory.narrative.generate_monthly",
        narrative_generate_monthly,
        schema={"properties": {}, "type": "object"},
    )

    # ── agent.state.summary ──
    async def agent_state_summary(**kwargs: Any) -> dict[str, object]:
        """获取小问状态摘要（完整度/趋同度/记忆密度）。"""
        try:
            from services.agent_state import AgentStateManager

            data_dir = getattr(getattr(context, "local_data_store", None), "base_dir", None)
            # P1 修复：AgentStateManager 构造函数中执行同步文件 I/O，用 to_thread 包裹
            manager = await asyncio.to_thread(AgentStateManager, data_dir)
            return await asyncio.to_thread(manager.get_summary)
        except Exception as exc:
            logger.debug("Failed to get agent state: %s", exc)
            return {"completeness": 0.0, "alignment": 0.0, "memory_density": 0}

    registry.register("agent.state.summary", agent_state_summary)

    # ── agent.state.self_context ──
    async def agent_self_context(**kwargs: Any) -> dict[str, str]:
        """获取小问的自我认知文本（供对话注入用）。"""
        try:
            from services.agent_state import AgentStateManager

            data_dir = getattr(getattr(context, "local_data_store", None), "base_dir", None)
            # P1 修复：AgentStateManager 构造函数中执行同步文件 I/O，用 to_thread 包裹
            manager = await asyncio.to_thread(AgentStateManager, data_dir)
            return {"context": await asyncio.to_thread(manager.get_self_context)}
        except Exception as exc:
            logger.debug("Failed to get agent context: %s", exc)
            return {"context": ""}

    registry.register("agent.state.context", agent_self_context)

    # ── xiaowen.quote ──
    async def xiaowen_quote(action: str = "idle", **kwargs: Any) -> dict[str, str]:
        """生成小问台词（基于记忆的驱动台词）。返回 { quote, mood }。

        当后端可用时，台词由记忆/状态驱动；
        不可用时由前端固定数组兜底。
        """
        try:
            from services.agent_state import AgentStateManager

            data_dir = getattr(getattr(context, "local_data_store", None), "base_dir", None)
            if not data_dir:
                return {"quote": "", "mood": "neutral"}

            # P1 修复：AgentStateManager 构造函数中执行同步文件 I/O，用 to_thread 包裹
            manager = await asyncio.to_thread(AgentStateManager, data_dir)
            state = await asyncio.to_thread(lambda: manager.state)

            # 根据 action 和 agent_state 生成台词
            if action == "idle":
                quotes = [
                    f"已经记了 {state.memory_density} 件事了……",
                    "剑在人在……呼……",
                    "今天也是剑意满满的一天！",
                ]
            elif action == "happy":
                quotes = ["今天状态不错！"]
            elif action == "thinking":
                quotes = ["让我想想……", "嗯……有意思。"]
            elif action == "alert":
                if await asyncio.to_thread(manager.should_remind):
                    quotes = [f"你还有 {len(state.unresolved)} 件事没处理……"]
                else:
                    quotes = ["有情况。"]
            else:
                quotes = ["剑在人在……"]

            import random

            return {"quote": random.choice(quotes), "mood": action}
        except Exception as exc:
            logger.debug("Failed to get xiaowen quote: %s", exc)
            return {"quote": "", "mood": "neutral"}

    registry.register("xiaowen.quote", xiaowen_quote)
