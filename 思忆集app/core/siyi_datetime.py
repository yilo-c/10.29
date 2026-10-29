"""思忆集统一 datetime 工具 — 防止跨模块时区 Bug。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def parse_iso_timestamp(ts: str | None) -> Optional[datetime]:
    """安全解析 ISO 8601 时间戳，始终返回带时区的 aware datetime。

    处理 Z 后缀、+HH:MM 偏移、无时区 naive 等常见格式。
    返回 None 表示解析失败（调用方应自行决定降级策略）。
    """
    if not ts or not isinstance(ts, str):
        return None
    try:
        normalized = ts.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def utc_now() -> datetime:
    """返回当前 UTC aware datetime。"""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """返回当前 UTC ISO 字符串。"""
    return utc_now().isoformat()
