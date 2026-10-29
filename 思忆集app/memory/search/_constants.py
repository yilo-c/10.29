"""Search constants — supertag patterns, source labels, time patterns.

Extracted from memory/search.py — module-level constants and registration functions.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Callable

# ── Supertag natural language query patterns ──

SUPERTAG_QUERY_PATTERNS: list[tuple[re.Pattern, str, dict[str, Any]]] = []


def register_supertag_patterns() -> None:
    """Register supertag NL query patterns (lazy registration)."""
    if SUPERTAG_QUERY_PATTERNS:
        return

    patterns = [
        (re.compile(r"(这周|本周).*(到期|截止|deadline).*(任务|todo)"),
         "@任务", {"status_not": "done", "deadline_range": "this_week"}),
        (re.compile(r"(未完成|未做|待办|pending|todo).*(任务|todo)"),
         "@任务", {"status_not": "done"}),
        (re.compile(r"(高优先级|重要|紧急|urgent|high).*(任务|todo)"),
         "@任务", {"priority": "high"}),
        (re.compile(r"(任务|todo).*(高优先级|重要|紧急|urgent|high)"),
         "@任务", {"priority": "high"}),
        (re.compile(r"(进行中|正在做|in_progress).*(任务|todo)"),
         "@任务", {"status": "in_progress"}),
        (re.compile(r"(已完成|做完|done|完成).*(任务|todo)"),
         "@任务", {"status": "done"}),
        (re.compile(r"(任务|todo|待办事项)"),
         "@任务", {}),
        (re.compile(r"(北京|上海|深圳|广州|杭州).*(联系人|朋友|同事)"),
         "@联系人", {}),
        (re.compile(r"(联系人|通讯录|朋友|同事)"),
         "@联系人", {}),
        (re.compile(r"(进行中|active).*(项目|project)"),
         "@项目", {"status": "active"}),
        (re.compile(r"(已归档|archived).*(项目|project)"),
         "@项目", {"status": "archived"}),
        (re.compile(r"(项目|project)"),
         "@项目", {}),
    ]
    SUPERTAG_QUERY_PATTERNS.extend(patterns)


# ── Source labels ──

SOURCE_LABELS: dict[str, str] = {
    "memory": "记忆",
    "knowledge": "知识库",
    "note": "笔记",
    "web": "网页",
    "task": "任务",
    "inbox": "收件箱",
    "decision": "决策",
    "execution_run": "执行记录",
}

SOURCE_LABEL_COLORS: dict[str, str] = {
    "memory": "#4A90D9",
    "knowledge": "#7B68EE",
    "note": "#2ECC71",
    "web": "#E67E22",
    "task": "#E74C3C",
    "inbox": "#F39C12",
    "decision": "#1ABC9C",
    "execution_run": "#95A5A6",
}

DEFAULT_SOURCE_LABEL = "记忆"
DEFAULT_SOURCE_COLOR = "#95A5A6"


# ── Natural language time patterns ──

TIME_PATTERNS: list[tuple[re.Pattern[Any], Callable[..., tuple[datetime, datetime]]]] = []


def _make_part_of_day_fn(start_hour: int, end_hour: int):
    """创建特定时段的时间范围函数。"""
    def _fn(match) -> tuple[datetime, datetime]:
        day_text = match.group(1)
        now = datetime.now()
        if "昨天" in day_text:
            day = (now - timedelta(days=1)).replace(hour=start_hour, minute=0, second=0, microsecond=0)
        elif "前天" in day_text:
            day = (now - timedelta(days=2)).replace(hour=start_hour, minute=0, second=0, microsecond=0)
        else:
            day = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        end = day.replace(hour=end_hour, minute=0, second=0, microsecond=0)
        return day, end
    return _fn


def register_time_patterns() -> None:
    """注册自然语言时间模式。延迟注册以避免模块加载时的性能影响。"""
    if TIME_PATTERNS:
        return

    now = datetime.now()

    def _today_range():
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now

    def _yesterday_range():
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start, end

    def _this_week_range():
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now

    def _last_week_range():
        this_monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = this_monday
        start = end - timedelta(days=7)
        return start, end

    def _this_month_range():
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, now

    def _last_month_range():
        first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = first_this
        start = (first_this - timedelta(days=1)).replace(day=1)
        return start, end

    def _this_year_range():
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, now

    def _specific_year_range(year: int):
        start = datetime(year, 1, 1)
        end = datetime(year, 12, 31, 23, 59, 59)
        return start, end

    def _specific_month_range(year: int, month: int):
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)
        return start, end

    TIME_PATTERNS.append((re.compile(r"今天|今日"), lambda _: _today_range()))
    TIME_PATTERNS.append((re.compile(r"昨天|昨日"), lambda _: _yesterday_range()))
    TIME_PATTERNS.append((re.compile(r"本周|这周"), lambda _: _this_week_range()))
    TIME_PATTERNS.append((re.compile(r"上周"), lambda _: _last_week_range()))
    TIME_PATTERNS.append((re.compile(r"这个月|本月|这月"), lambda _: _this_month_range()))
    TIME_PATTERNS.append((re.compile(r"上个月|上月"), lambda _: _last_month_range()))
    TIME_PATTERNS.append((re.compile(r"今年|本年"), lambda _: _this_year_range()))
    TIME_PATTERNS.append((re.compile(r"(\d{1,2})月"), lambda m: _specific_month_range(now.year, int(m.group(1)))))
    TIME_PATTERNS.append((re.compile(r"(\d{4})年(\d{1,2})月"), lambda m: _specific_month_range(int(m.group(1)), int(m.group(2)))))
    TIME_PATTERNS.append((re.compile(r"(\d{4})年"), lambda m: _specific_year_range(int(m.group(1)))))

    _day_part_map = {
        "凌晨": (0, 6), "早上": (6, 9), "上午": (6, 12),
        "中午": (11, 14), "下午": (12, 18), "晚上": (18, 24), "半夜": (0, 3),
    }
    for part_name, (start_h, end_h) in _day_part_map.items():
        pat_str = rf"(昨天|前天|今天)({part_name})"
        TIME_PATTERNS.append((
            re.compile(pat_str),
            _make_part_of_day_fn(start_h, end_h),
        ))

    def _day_before_yesterday():
        start = (now - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start, end
    TIME_PATTERNS.append((re.compile(r"前天"), lambda _: _day_before_yesterday()))
    TIME_PATTERNS.append((re.compile(r"(\d+)天前"), lambda m: (
        (now - timedelta(days=int(m.group(1)))).replace(hour=0, minute=0, second=0, microsecond=0), now
    )))
    TIME_PATTERNS.append((re.compile(r"最近(\d+)天"), lambda m: (
        (now - timedelta(days=int(m.group(1)))).replace(hour=0, minute=0, second=0, microsecond=0), now
    )))
