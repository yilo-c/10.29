"""统一同步摘要视图。

将 ApiSyncOrchestrator.get_sync_summary() 的输出包装为
标准化的 SyncSummaryView 数据类，并提供 UI 格式化工具。

用法::

    from sync.summary_view import SyncSummaryView, format_sync_summary_text

    # 在同步编排器上调用
    raw = orchestrator.get_sync_summary()
    view = SyncSummaryView.from_raw(raw)

    # 格式化为 UI 文本
    text = format_sync_summary_text(view)
    status_label.setText(text)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# P3-10: 视为 stale 的同步陈旧阈值（默认 24 小时）
SYNC_STALE_THRESHOLD = timedelta(hours=24)


@dataclass
class SyncSummaryView:
    """统一同步摘要视图模型。

    Attributes
    ----------
    provider : str
        同步提供方标识（如 "automation_hub"）
    last_sync_time : str | None
        上次同步时间（ISO 8601 或 None）
    total_pending : int
        待同步条目总数
    total_conflicts : int
        未解决冲突总数
    resource_summaries : list[dict]
        每个资源的同步摘要
    """

    provider: str = ""
    last_sync_time: str | None = None
    total_pending: int = 0
    total_conflicts: int = 0
    resource_summaries: list[dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def from_raw(raw: dict[str, Any]) -> SyncSummaryView:
        """从 ApiSyncOrchestrator.get_sync_summary() 的输出构建视图。"""
        return SyncSummaryView(
            provider=str(raw.get("provider") or ""),
            last_sync_time=raw.get("last_sync_time"),
            total_pending=int(raw.get("total_pending") or 0),
            total_conflicts=int(raw.get("total_conflicts") or 0),
            resource_summaries=list(raw.get("resources") or []),
        )

    @property
    def is_stale(self) -> bool:
        """上次同步时间是否超过 ``SYNC_STALE_THRESHOLD``（默认 24 小时）。

        - ``last_sync_time`` 为空 → stale
        - 无法解析的时间字符串 → 保守视为 stale
        - 距今超过阈值 → stale
        """
        if not self.last_sync_time:
            return True
        try:
            normalized = self.last_sync_time.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
        except (ValueError, TypeError):
            return True
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt > SYNC_STALE_THRESHOLD

    @property
    def has_conflicts(self) -> bool:
        return self.total_conflicts > 0

    @property
    def has_pending(self) -> bool:
        return self.total_pending > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "last_sync_time": self.last_sync_time,
            "total_pending": self.total_pending,
            "total_conflicts": self.total_conflicts,
            "resources": self.resource_summaries,
        }


def format_sync_summary_text(view: SyncSummaryView) -> str:
    """将 SyncSummaryView 格式化为 UI 状态标签文本。"""
    parts = []

    if view.last_sync_time:
        parts.append(f"上次同步 {_format_sync_time(view.last_sync_time)}")
    else:
        parts.append("未同步")

    if view.total_pending > 0:
        parts.append(f"待同步 {view.total_pending}")
    else:
        parts.append("同步完成")

    if view.total_conflicts > 0:
        parts.append(f"冲突 {view.total_conflicts}")

    return " · ".join(parts)


def format_sync_summary_html(view: SyncSummaryView) -> str:
    """将 SyncSummaryView 格式化为 HTML（用于富文本标签）。"""
    parts = []
    if view.last_sync_time:
        parts.append(f'<span style="color:#666;">上次同步</span> {_format_sync_time(view.last_sync_time)}')
    else:
        parts.append('<span style="color:#999;">未同步</span>')

    if view.total_pending > 0:
        parts.append(f'<span style="color:#e68a00;">待同步 {view.total_pending}</span>')
    else:
        parts.append('<span style="color:#4caf50;">✓ 同步完成</span>')

    if view.total_conflicts > 0:
        parts.append(f'<span style="color:#d32f2f;">冲突 {view.total_conflicts}</span>')

    return " · ".join(parts)


def _format_sync_time(iso_time: str | None) -> str:
    """将 ISO 时间格式化为简短显示。"""
    if not iso_time:
        return "未知"
    try:
        normalized = iso_time.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return dt.strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(iso_time)[:16]
