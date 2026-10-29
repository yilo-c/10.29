"""Sync UI support — display helpers for sync status, conflict summaries, manual triggers.

Used by high-traffic dialogs (notes, tasks, vocabulary, translation, settings)
to show a unified sync summary bar.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# ── Constants ────────────────────────────────────────────────────────

AUTO_SYNC_LABEL = "自动同步"
AUTO_SYNC_TOOLTIP = "默认关闭；开启后每 120 秒轮询一次，本地变更会在 8 秒去抖后自动发起同步。"
SYNC_STATUS_TOOLTIP = "状态统一显示为「资源总量 · 待同步数量 · 上次同步时间」。"
SYNC_CONFLICT_TOOLTIP = "默认保留本地版本；可以接受远端覆盖，或保留本地修改后重传。"

SYNC_NOW_LABEL = "立即同步"
SYNC_NOW_TOOLTIP = "手动触发一次全量同步，拉取远端变更并推送本地待同步项。"

LAST_SYNC_UNKNOWN = "从未同步"
LAST_SYNC_JUST_NOW = "刚刚"
LAST_SYNC_MINUTES_AGO = "{minutes} 分钟前"
LAST_SYNC_HOURS_AGO = "{hours} 小时前"
LAST_SYNC_DAYS_AGO = "{days} 天前"

CONFLICT_SUMMARY_LABEL = "存在 {count} 个同步冲突"
NO_CONFLICT_LABEL = "无冲突"
PENDING_COUNT_LABEL = "{count} 项待同步"
ALL_SYNCED_LABEL = "全部已同步"

# ── Formatting helpers ───────────────────────────────────────────────


def format_last_sync_time(last_sync_time: str | None) -> str:
    """Format the last sync time for human-readable display.

    Args:
        last_sync_time: ISO 8601 timestamp or None.

    Returns:
        Human-readable relative time string.
    """
    if not last_sync_time:
        return LAST_SYNC_UNKNOWN

    try:
        if isinstance(last_sync_time, str):
            if last_sync_time.endswith("Z"):
                last_sync_time = last_sync_time[:-1] + "+00:00"
            last_dt = datetime.fromisoformat(last_sync_time)
        elif isinstance(last_sync_time, datetime):
            last_dt = last_sync_time
        else:
            return str(last_sync_time)

        now = datetime.now(timezone.utc)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)

        delta = now - last_dt
        total_seconds = delta.total_seconds()

        if total_seconds < 60:
            return LAST_SYNC_JUST_NOW
        elif total_seconds < 3600:
            return LAST_SYNC_MINUTES_AGO.format(minutes=int(total_seconds // 60))
        elif total_seconds < 86400:
            return LAST_SYNC_HOURS_AGO.format(hours=int(total_seconds // 3600))
        else:
            return LAST_SYNC_DAYS_AGO.format(days=int(total_seconds // 86400))
    except (ValueError, TypeError):
        return str(last_sync_time) if last_sync_time else LAST_SYNC_UNKNOWN


def format_conflict_count(conflict_count: int) -> str:
    """Format conflict count for display."""
    if conflict_count <= 0:
        return NO_CONFLICT_LABEL
    return CONFLICT_SUMMARY_LABEL.format(count=conflict_count)


def format_pending_count(pending_count: int) -> str:
    """Format pending item count for display."""
    if pending_count <= 0:
        return ALL_SYNCED_LABEL
    return PENDING_COUNT_LABEL.format(count=pending_count)


def build_sync_status_text(
    *,
    last_sync_time: str | None = None,
    pending_count: int = 0,
    conflict_count: int = 0,
    resource_counts: dict[str, int] | None = None,
) -> str:
    """Build a unified sync status line for display.

    Format:
        "全部已同步 · 刚刚"
        "2 项待同步 · 5 分钟前"
        "存在 1 个同步冲突 · 2 小时前"

    Args:
        last_sync_time: ISO 8601 timestamp of last sync.
        pending_count: Number of items pending sync.
        conflict_count: Number of unresolved conflicts.
        resource_counts: Optional map of resource &rarr; pending count.

    Returns:
        Formatted status string.
    """
    parts = []
    if conflict_count > 0:
        parts.append(format_conflict_count(conflict_count))
    elif pending_count > 0:
        parts.append(format_pending_count(pending_count))
    else:
        parts.append(ALL_SYNCED_LABEL)

    parts.append("·")
    parts.append(format_last_sync_time(last_sync_time))

    return " ".join(parts)


def build_resource_summary(resource_states: list[dict[str, Any]]) -> str:
    """Build a multi-line resource sync summary.

    Args:
        resource_states: List of dicts with 'resource', 'pending', 'conflicts' keys.

    Returns:
        Multi-line summary string.
    """
    lines = []
    for state in resource_states:
        resource = state.get("resource", "unknown")
        pending = state.get("pending", 0)
        conflicts = state.get("conflicts", 0)
        indicators = []
        if pending > 0:
            indicators.append(f"{pending} pending")
        if conflicts > 0:
            indicators.append(f"{conflicts} conflicts")
        status = ", ".join(indicators) if indicators else "synced"
        lines.append(f"  {resource}: {status}")
    return "\n".join(lines)


# ── Qt Widget helpers ────────────────────────────────────────────────


def apply_sync_summary_bar(
    *,
    status_label: Any = None,
    conflict_label: Any = None,
    sync_now_button: Any = None,
    last_sync_time: str | None = None,
    pending_count: int = 0,
    conflict_count: int = 0,
) -> None:
    """Apply sync status to common UI widgets.

    Args:
        status_label: QLabel for main sync status.
        conflict_label: QLabel for conflict summary.
        sync_now_button: QPushButton for manual sync trigger.
        last_sync_time: ISO 8601 timestamp.
        pending_count: Number of pending items.
        conflict_count: Number of conflicts.
    """
    if status_label is not None:
        text = build_sync_status_text(
            last_sync_time=last_sync_time,
            pending_count=pending_count,
            conflict_count=conflict_count,
        )
        set_text = getattr(status_label, "setText", None)
        if callable(set_text):
            set_text(text)
        set_tooltip = getattr(status_label, "setToolTip", None)
        if callable(set_tooltip):
            set_tooltip(SYNC_STATUS_TOOLTIP)

    if conflict_label is not None:
        set_text = getattr(conflict_label, "setText", None)
        if callable(set_text):
            set_text(format_conflict_count(conflict_count))
        set_tooltip = getattr(conflict_label, "setToolTip", None)
        if callable(set_tooltip):
            set_tooltip(SYNC_CONFLICT_TOOLTIP)

    if sync_now_button is not None:
        set_text = getattr(sync_now_button, "setText", None)
        if callable(set_text):
            set_text(SYNC_NOW_LABEL)
        set_tooltip = getattr(sync_now_button, "setToolTip", None)
        if callable(set_tooltip):
            set_tooltip(SYNC_NOW_TOOLTIP)


def apply_standard_sync_ui(
    *,
    toggle_control: Any = None,
    status_label: Any = None,
    conflict_summary_label: Any = None,
) -> None:
    """Legacy helper. Prefer apply_sync_summary_bar for new code."""
    if toggle_control is not None:
        set_text = getattr(toggle_control, "setText", None)
        set_tooltip = getattr(toggle_control, "setToolTip", None)
        if callable(set_text):
            set_text(AUTO_SYNC_LABEL)
        if callable(set_tooltip):
            set_tooltip(AUTO_SYNC_TOOLTIP)

    if status_label is not None:
        set_tooltip = getattr(status_label, "setToolTip", None)
        if callable(set_tooltip):
            set_tooltip(SYNC_STATUS_TOOLTIP)

    if conflict_summary_label is not None:
        set_tooltip = getattr(conflict_summary_label, "setToolTip", None)
        if callable(set_tooltip):
            set_tooltip(SYNC_CONFLICT_TOOLTIP)


def read_auto_sync_enabled(settings_repository: Any, setting_key: str, default: bool = False) -> bool:
    if settings_repository is None:
        return bool(default)
    return bool(settings_repository.get_setting(setting_key, default))


def apply_auto_sync_enabled(toggle_control: Any, timer: Any, debounce_timer: Any, enabled: bool) -> bool:
    resolved_enabled = bool(enabled)
    if toggle_control is not None:
        block_signals = getattr(toggle_control, "blockSignals", None)
        set_checked = getattr(toggle_control, "setChecked", None)
        if callable(block_signals):
            block_signals(True)
        if callable(set_checked):
            set_checked(resolved_enabled)
        if callable(block_signals):
            block_signals(False)
    if resolved_enabled:
        if timer is not None and hasattr(timer, "start"):
            timer.start()
    else:
        if timer is not None and hasattr(timer, "stop"):
            timer.stop()
        if debounce_timer is not None and hasattr(debounce_timer, "stop"):
            debounce_timer.stop()
    return resolved_enabled
