"""对话记忆锚点上下文构建器 (P3.5)。

为无头 WebSocket 链路下的 `chat.send` fallback 提供一段「锚点 system 消息」，
让回退到单轮 AI 的对话也能感知：
- 当前本机时间（自然语言：下午 15:30，星期一）
- 最近相关记忆 (memory_index_service.find_related)
- 当前天气（如果 weather_service 可用）
- 用户偏好画像 (user_profile_service.build_planner_summary)

PyQt6 路径下相同语义由 `assistant/prompt_context.py` 完成；本模块是
其无头化镜像，依赖 AppContext 而非 MainWindow，便于在 backend handler 调用。

设计原则：
- 纯函数 + 防御式：任何服务缺失/抛错都安静降级为空字符串。
- 单条 system 消息：组合好的字符串以 `[对话锚点]` 开头返回给调用方。
- 不阻塞：所有调用为同步快速操作，对 IO 类操作（weather）添加超时容忍。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from backend.handlers._wrap import CODE_CHAT_INT, degrade

logger = logging.getLogger("siyi.backend.handlers.chat_context_anchor")

_WEEKDAY_ZH = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def _format_local_time(now: datetime | None = None) -> str:
    now = now or datetime.now()
    hour = now.hour
    if hour < 6:
        period = "凌晨"
    elif hour < 12:
        period = "上午"
    elif hour < 14:
        period = "中午"
    elif hour < 18:
        period = "下午"
    else:
        period = "晚上"
    weekday = _WEEKDAY_ZH[now.weekday()]
    return f"{period} {now.strftime('%H:%M')}，{weekday}（{now.strftime('%Y-%m-%d')}）"


def _recall_related_memories(context: Any, message: str, limit: int = 4) -> list[str]:
    svc = getattr(context, "memory_index_service", None)
    if svc is None or not message.strip():
        return []
    try:
        items = svc.find_related(message, limit=limit)
    except Exception:
        return degrade(logger, CODE_CHAT_INT, "find_related failed", fallback=[])
    lines: list[str] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        snippet = str(it.get("summary") or it.get("snippet") or "").strip()
        src = str(it.get("source_type") or "memory").strip()
        if not title and not snippet:
            continue
        label = f"[{src}] {title}" if title else f"[{src}]"
        if snippet:
            label = f"{label} — {snippet}"
        if len(label) > 160:
            label = label[:157] + "..."
        lines.append(label)
    return lines[:limit]


def _profile_summary(context: Any) -> str:
    svc = getattr(context, "user_profile_service", None)
    if svc is None:
        return ""
    try:
        summary = svc.build_planner_summary()
    except Exception:
        return degrade(logger, CODE_CHAT_INT, "build_planner_summary failed", fallback="")
    text = str(summary or "").strip()
    if not text:
        return ""
    # 限长，避免淹没系统提示
    if len(text) > 600:
        text = text[:597] + "..."
    return text


def _preference_summary(context: Any) -> str:
    """P2.8: 读取用户反馈聚合出的偏好摘要，注入对话锚点。

    P3.2: 统一通过 ``context.feedback_service`` 访问（由 headless_main.py 注入）。
    偏好摘要为空时返回空串（调用方应跳过）。
    """
    svc = getattr(context, "feedback_service", None)
    if svc is None:
        # P3.2: 不再在 backend handler 中懒构建；服务未注入时直接跳过
        return ""
    try:
        text = svc.get_preference()
    except Exception:
        return degrade(logger, CODE_CHAT_INT, "get_preference failed", fallback="")
    text = str(text or "").strip()
    if not text:
        return ""
    if len(text) > 600:
        text = text[:597] + "..."
    return text


def _weather_brief(context: Any, now: datetime | None = None) -> str:
    svc = getattr(context, "weather_service", None)
    if svc is None:
        return ""
    try:
        snap = svc.get_snapshot(now or datetime.now(), {}, force_refresh=False)
    except Exception:
        return degrade(logger, CODE_CHAT_INT, "weather_service.get_snapshot failed", fallback="")
    if not isinstance(snap, dict):
        return ""
    parts: list[str] = []
    city = str(snap.get("city_label") or snap.get("city") or "").strip()
    if city:
        parts.append(city)
    condition = str(snap.get("condition") or snap.get("condition_text") or "").strip()
    if condition:
        parts.append(condition)
    temp = snap.get("temperature")
    if temp is not None:
        try:
            parts.append(f"{float(temp):.0f}℃")
        except (TypeError, ValueError):
            pass
    tip = str(snap.get("tip") or "").strip()
    if tip:
        parts.append(tip)
    return " · ".join(parts)


def build_chat_context_anchor(context: Any, message: str, *, now: datetime | None = None) -> str:
    """组合一段供 chat.send 注入的「对话锚点」system 消息。

    返回值为空串时调用方应跳过注入。
    """
    lines: list[str] = []

    # ── 时间 ──
    try:
        time_line = _format_local_time(now)
        if time_line:
            lines.append(f"现在：{time_line}")
    except Exception:
        degrade(logger, CODE_CHAT_INT, "time format failed")

    # ── 天气 ──
    try:
        weather = _weather_brief(context, now)
        if weather:
            lines.append(f"天气：{weather}")
    except Exception:
        degrade(logger, CODE_CHAT_INT, "weather build failed")

    # ── 相关记忆 ──
    try:
        mem_lines = _recall_related_memories(context, message, limit=4)
        if mem_lines:
            lines.append("相关记忆：")
            for ml in mem_lines:
                lines.append(f"  - {ml}")
    except Exception:
        degrade(logger, CODE_CHAT_INT, "recall memories failed")

    # ── 用户画像 ──
    try:
        profile = _profile_summary(context)
        if profile:
            lines.append("用户画像：")
            for pl in profile.splitlines():
                pl_stripped = pl.strip()
                if pl_stripped:
                    lines.append(f"  - {pl_stripped}")
    except Exception:
        degrade(logger, CODE_CHAT_INT, "profile summary failed")

    # ── P2.8: 用户偏好（基于反馈学习）──
    try:
        preference = _preference_summary(context)
        if preference:
            lines.append("用户偏好（基于反馈学习）：")
            for pl in preference.splitlines():
                pl_stripped = pl.strip()
                if pl_stripped:
                    lines.append(f"  - {pl_stripped}")
    except Exception:
        degrade(logger, CODE_CHAT_INT, "preference summary failed")

    if not lines:
        return ""

    return "[对话锚点]\n" + "\n".join(lines)


def is_anchor_enabled(context: Any, *, override: Any = None) -> bool:
    """读取 settings.chat_context_anchor（默认 True），允许 params 覆盖。"""
    if override is not None:
        return bool(override)
    repo = getattr(context, "settings_repository", None)
    if repo is None:
        return True
    try:
        return bool(repo.get_setting("chat_context_anchor", True))
    except Exception:
        logger.exception("读取对话锚点启用配置失败")
        return True


async def build_anchor_async(
    context: Any,
    message: str,
    *,
    override: Any = None,
    now: datetime | None = None,
) -> str:
    """异步构建对话锚点 system 消息文本。

    将 ``is_anchor_enabled`` + ``build_chat_context_anchor`` 一起包裹
    ``asyncio.to_thread``，避免在 async handler 中直接调用同步 SQLite 读
    （settings.chat_context_anchor）和同步 memory/profile/preference/weather 服务。

    返回空串表示锚点未启用或构建失败；调用方应跳过注入。

    保留同步版本 ``is_anchor_enabled`` / ``build_chat_context_anchor`` 供
    非 async 路径或测试使用。
    """

    def _build_sync() -> str:
        if not is_anchor_enabled(context, override=override):
            return ""
        return build_chat_context_anchor(context, message, now=now)

    try:
        return await asyncio.to_thread(_build_sync)
    except Exception:
        return degrade(logger, CODE_CHAT_INT, "build_anchor_async failed", fallback="")


__all__ = [
    "build_chat_context_anchor",
    "build_anchor_async",
    "is_anchor_enabled",
]
