"""Weather JSON-RPC handler.

M2 修复：CLAUDE.md 文档列了 weather.current 但之前没有 handler 注册。
本 handler 提供轻量天气查询，绕过 WeatherService.get_snapshot 那套
"任务-情绪联动"的重业务逻辑，直接调用底层 _fetch_live_weather。
"""

from __future__ import annotations

import logging
from typing import Any

from core.errors import InvalidParamsError

logger = logging.getLogger("siyi.backend.handlers.weather")

# R2-D（2026-09-17）契约诚实性：仅接受 city。/ 未知参数必须拒绝而不是静默忽略
# ——否则调用方传错参名（如 location/citx）时会被吞掉、退回默认城市并返回
# success，用户以为查的是自己指定的城市（P-I 长尾探针实测）。只有**完全不传**
# city 时才允许落到默认城市（handler 内 city=None 表达未指定）。
_ALLOWED_PARAMS = frozenset({"city"})


def register_weather(registry, context) -> None:
    """Register weather JSON-RPC methods."""

    # ── weather.current ──
    async def weather_current(city: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Get current weather for a city.

        Args:
            city: City name (Chinese or English). Only omitted (None/未传) falls
                  back to the user's configured default city; an empty string is
                  treated the same as omitted.
            kwargs: 不允许出现——未知参数直接 -32602，不静默忽略。

        Returns:
            {"success": True, "title": "...", "value": "...", "meta": "...",
             "tip": "..."} on success, or {"success": False, "error": "..."}
            when no weather service is available.

        Raises:
            InvalidParamsError: 出现未声明的参数名（JSON-RPC -32602）。
        """
        unexpected = sorted(set(kwargs) - _ALLOWED_PARAMS)
        if unexpected:
            # 覆盖绕过 registry 的直调路径；RPC 面另有注册层 schema
            # （additionalProperties=False）在 dispatch 前拦截。
            raise InvalidParamsError(
                message=f"Unexpected parameter(s): {', '.join(unexpected)}",
                details={"method": "weather.current", "unexpected": unexpected},
            )

        svc = getattr(context, "weather_service", None)
        if svc is None:
            return {"success": False, "error": "weather service unavailable"}

        # _fetch_live_weather 内部走 services.url_safety.safe_urlopen（P0-5 修复后）
        try:
            import asyncio

            requested_city = (city or "").strip() or getattr(svc, "_default_city", "beijing")
            requested_label = requested_city  # 简单实现，城市名当 label

            payload = await asyncio.to_thread(
                svc._fetch_live_weather,
                requested_city,
                requested_label,
            )
            if payload is None:
                return {"success": False, "error": "weather fetch failed (network or invalid city)"}
            return {"success": True, **payload}
        except Exception as exc:
            logger.warning("weather.current failed: %s", exc)
            return {"success": False, "error": str(exc)}

    # R2-D：注册层 schema 与 N-E 脏参数契约一致 —— 只有 city 是声明参数，
    # additionalProperties=False 让未知参数在 dispatch 前得到干净的 -32602。
    registry.register_with_schema(
        "weather.current",
        weather_current,
        schema={
            "type": "object",
            "properties": {"city": {"type": ["string", "null"]}},
            "additionalProperties": False,
        },
    )
