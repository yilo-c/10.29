"""cron 表达式语义基元——星期字段"数字歧义"的单一收敛点（批3 · P2-1 治本，2026-09-18）。

## 为什么需要它（过往经验）

env-drill 之后的 cron 审计（``deliverables/backlog-review-2026-09-18.md`` §10.1）实测：
5 字段 cron 的星期字段在本仓长期存在**两套互不相容的数字语义**：

======================================  ==========================================
读法                                      ``0`` 的含义
======================================  ==========================================
Python ``datetime.weekday()``             周一
APScheduler ``CronTrigger(day_of_week=)`` 周一（内部 ``WEEKDAYS=['mon',...,'sun']``）
**标准 cron（用户 / 前端 / 绝大多数调度器）**  **周日**
======================================  ==========================================

后果（实测，非推断）：

* ``system.auto_backup`` 写 ``0 3 * * 0``、注释"周日 03:00" → **实跑周一**；
* ``system.error_digest`` 写 ``0 8 * * 1``、命名"每周一" → **实跑周二**；
* ``desk-notes`` 前端只有 ``(5 fields: min hour day month week)`` 提示、无结构化
  星期选择器，用户按标准 cron 手写 → 同一个坑**直接作用于用户**；
* 内部判据 ``_cron_matches`` 只认单值与 ``*``，遇到 ``*/30 7-23 * * *`` /
  ``0 9 * * 1-5`` 直接返回 False，而 APScheduler 照常触发 → **两引擎分裂**
  （poll 引擎下 ``proactive.tick`` 永不触发、永不补跑）。

**逐处打补丁 = 治标**：数字歧义会在第五处、第六处继续复发。本模块把
"cron 表达式 → 可判定的字段值集合"收敛为**单一入口**：

* 数字星期按**标准 cron**（``0=Sun,1=Mon..6=Sat,7=Sun``）解读后**立即规范化成
  星期名字**——名字是自描述的，APScheduler 与内部判据都不会再二义；
* 内部判据与 APScheduler 触发器**消费同一份规范化结果**，分裂在结构上不可能
  （一致性由 ``tests/test_batch3_time_semantics_20260918.py`` 的契约测试机器断言）；
* ``schedule`` 字段加**形状校验**：合法 5 字段 cron 才收，RRULE 等一律拒收 +
  告警，杜绝"死任务"（P2-NEW-1）。

## 用法

```python
from core.cron_expr import normalize_cron_schedule, cron_matches, validate_schedule_shape

normalize_cron_schedule("0 3 * * 0")   # -> "0 3 * * sun"
cron_matches("0 3 * * sun", dt)        # 内部补跑判据（与 APScheduler 同源）
validate_schedule_shape("FREQ=MONTHLY;BYDAY=MO")  # -> ok=False, reason="rrule_misplaced"
```

## 设计取舍

* **规范化输出用逗号列表而非区间**：APScheduler 的 ``WeekdayRangeExpression``
  把名字映射回 ``mon=0..sun=6`` 且**不支持跨周回绕**（``sun-tue`` 直接抛
  ValueError）。列表形式在所有引擎下都无歧义，故宁可牺牲一点可读性。
* **``*`` 保持 ``*``**：它是"每天"的既有惯用写法，展开成 7 个名字只会变吵。
* **本模块只做语法/语义，不读时钟、不落盘**：纯函数，可被任何引擎安全复用。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# 标准 cron 星期序：索引 = 规范数值（0=Sun, 1=Mon, ..., 6=Sat）。
# 与 Python / APScheduler 的 ``datetime.weekday()``（0=Mon）**不同**——这正是 P2-1 根因。
WEEKDAY_NAMES: tuple[str, ...] = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")

# 星期别名 → 规范数值。数字键含标准 cron 允许的 7=Sun。
WEEKDAY_ALIASES: dict[str, int] = {
    "sun": 0,
    "sunday": 0,
    "mon": 1,
    "monday": 1,
    "tue": 2,
    "tues": 2,
    "tuesday": 2,
    "wed": 3,
    "wednesday": 3,
    "thu": 4,
    "thur": 4,
    "thurs": 4,
    "thursday": 4,
    "fri": 5,
    "friday": 5,
    "sat": 6,
    "saturday": 6,
    "7": 0,
}

MONTH_NAMES: tuple[str, ...] = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
MONTH_ALIASES: dict[str, int] = {name: index + 1 for index, name in enumerate(MONTH_NAMES)}

# 旧 ``_WEEKDAY_MAP`` 的反函数：Python/APScheduler 语义（0=Mon..6=Sun）→ 名字。
# 仅供**存量迁移**使用：历史上 ``tech_watch.judge`` 的 dow 是由星期**名字**经
# 旧 ``_WEEKDAY_MAP`` 转成数字写进 schedule 的，数字本身丢了意图，只有种子
# 代码知道它原本是哪个名字。新代码一律不得再用（见 _LEGACY_PY_WEEKDAY_JOBS）。
PY_WEEKDAY_TO_NAME: dict[int, str] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# RRULE 显式通道前缀：``FREQ=...`` 是 RRULE，不是 cron。带前缀者视为
# "作者明确声明这是 RRULE"，与"把 RRULE 误填进 cron 字段"区分开，便于给出
# 不同的可执行提示。
RRULE_PREFIX = "RRULE:"


@dataclass(frozen=True)
class _FieldSpec:
    """单个 cron 字段的取值域与名字别名。"""

    name: str
    low: int
    high: int
    aliases: dict[str, int]


# 字段顺序：分 时 日 月 周（标准 cron）。
# 星期字段 high=7：标准 cron 允许 7 作 0（周日）的同义词，展开时再归一到 0..6。
_FIELD_SPECS: tuple[_FieldSpec, ...] = (
    _FieldSpec("minute", 0, 59, {}),
    _FieldSpec("hour", 0, 23, {}),
    _FieldSpec("day", 1, 31, {}),
    _FieldSpec("month", 1, 12, MONTH_ALIASES),
    _FieldSpec("weekday", 0, 7, WEEKDAY_ALIASES),
)

_FIELD_ORDER = ("minute", "hour", "day", "month", "weekday")


@dataclass(frozen=True)
class ScheduleCheck:
    """``validate_schedule_shape`` 的结论。

    ``reason`` 取值：``""``（合法） / ``"empty"`` / ``"rrule_channel"``（显式
    RRULE 通道，当前无引擎） / ``"rrule_misplaced"``（RRULE 误填进 cron 字段，
    即生产实测的死任务形态） / ``"not_five_fields"`` / ``"bad_field"``。
    """

    ok: bool
    normalized: str = ""
    reason: str = ""
    hint: str = ""


def split_fields(expr: str) -> Optional[list[str]]:
    """拆 5 字段；字段数不为 5 时返回 None。"""
    parts = str(expr or "").strip().split()
    if len(parts) != 5:
        return None
    return parts


def _resolve_value(token: str, spec: _FieldSpec) -> Optional[int]:
    """把单个字面量解析为数值（含名字别名）；越界/不可识别返回 None。"""
    text = token.strip().lower()
    if not text:
        return None
    if text in spec.aliases:
        return spec.aliases[text]
    if text.isdigit():
        value = int(text)
        if spec.low <= value <= spec.high:
            return value
        return None
    return None


def _canonical_value(value: int, spec: _FieldSpec) -> int:
    """把字段内数值归一到规范域（目前只有星期需要 7 → 0）。"""
    if spec.name == "weekday" and value == 7:
        return 0
    return value


def _parse_field_values(field: str, spec: _FieldSpec) -> Optional[list[int]]:
    """把单个字段解析为**升序去重的数值集合**；任何无法解析的 token 返回 None。

    支持的语法（与 APScheduler ``CronTrigger`` 对齐，避免两引擎分裂）：
    ``*`` / ``*/n`` / ``a`` / ``a-b`` / ``a-b/n`` / 逗号列表；``month`` 与
    ``weekday`` 额外接受名字（``jan``/``mon``）。**不支持** APScheduler 的
    ``2nd fri`` / ``last`` 位置表达式——形状校验会拒收，故不会造成分裂。
    """
    text = str(field or "").strip()
    if not text:
        return None
    values: set[int] = set()
    for raw in text.split(","):
        token = raw.strip()
        if not token:
            return None
        step = 1
        base = token
        if "/" in token:
            base, _, step_text = token.partition("/")
            if not step_text.strip().isdigit() or int(step_text) < 1:
                return None
            step = int(step_text)
            base = base.strip()
        if base == "*":
            first, last, wrap = spec.low, spec.high, False
        elif "-" in base:
            first_text, _, last_text = base.partition("-")
            first = _resolve_value(first_text, spec)
            last = _resolve_value(last_text, spec)
            if first is None or last is None:
                return None
            wrap = first > last
        else:
            single = _resolve_value(base, spec)
            if single is None:
                return None
            first = last = single
            wrap = False
        if wrap:
            # 跨周回绕（如 6-1）：显式展开成两段，绝不把区间原样交给 APScheduler
            # （其 WeekdayRangeExpression 对 first>last 直接抛 ValueError）。
            seq = list(range(first, spec.high + 1)) + list(range(spec.low, last + 1))
        else:
            seq = list(range(first, last + 1))
        if step > 1:
            seq = seq[::step]
        for value in seq:
            values.add(_canonical_value(value, spec))
    return sorted(values)


def normalize_weekday_field(dow: str) -> str:
    """把星期字段规范化为名字（标准 cron 语义）。

    ``*`` 原样返回；数字（``0``/``7`` = 周日、``1`` = 周一 …）与区间/步长/列表
    一律展开成**逗号分隔的名字列表**。无法解析时**原样返回**（不做有损猜测），
    交由 :func:`validate_schedule_shape` 报错。
    """
    text = str(dow or "").strip()
    if text == "*":
        return "*"
    values = _parse_field_values(text, _FIELD_SPECS[4])
    if values is None:
        return text
    return ",".join(WEEKDAY_NAMES[value] for value in values)


def normalize_cron_schedule(expr: str) -> str:
    """规范化整条 5 字段表达式（当前仅星期字段需要）。

    非 5 字段表达式原样返回——**规范化不做校验**，非法形状由
    :func:`validate_schedule_shape` 负责拒收。
    """
    parts = split_fields(expr)
    if parts is None:
        return str(expr or "").strip()
    parts[4] = normalize_weekday_field(parts[4])
    return " ".join(parts)


def validate_schedule_shape(schedule: str) -> ScheduleCheck:
    """``schedule`` 字段的形状校验（P2-NEW-1 治本）。

    合法形状 = 5 字段 cron（含名字/区间/步长/列表）。其余一律拒收：RRULE 语法
    被**显式识别**（``RRULE:`` 前缀 = 显式通道；裸 ``FREQ=`` = 误填进 cron
    字段），以便给出"改用什么"的可执行提示，而不是让任务静默变死任务。
    """
    text = str(schedule or "").strip()
    if not text:
        return ScheduleCheck(False, "", "empty", "schedule 不能为空（repeat=cron 必须给 5 字段 cron）")
    upper = text.upper()
    if upper.startswith(RRULE_PREFIX):
        return ScheduleCheck(
            False,
            "",
            "rrule_channel",
            "已识别为显式 RRULE 通道，但本仓无 RRULE 引擎；请改用 5 字段 cron（星期用 sun/mon/... 名字）",
        )
    if "FREQ=" in upper:
        return ScheduleCheck(
            False,
            "",
            "rrule_misplaced",
            "检测到 RRULE 语法（FREQ=...）混入 cron 字段——该任务此前永不调度（死任务）；请改用 5 字段 cron，如 '0 9 * * mon'",
        )
    parts = split_fields(text)
    if parts is None:
        return ScheduleCheck(
            False,
            "",
            "not_five_fields",
            f"cron 必须是 5 个字段（分 时 日 月 周），实际 {len(text.split())} 个",
        )
    for field, spec in zip(parts, _FIELD_SPECS):
        if _parse_field_values(field, spec) is None:
            return ScheduleCheck(
                False,
                "",
                "bad_field",
                f"{spec.name} 字段非法：{field!r}（允许 * / a / a-b / a-b/n / 逗号列表）",
            )
    return ScheduleCheck(True, normalize_cron_schedule(text), "", "")


def weekday_matches(dow_field: str, dt: datetime) -> bool:
    """``dt`` 的星期是否落在 ``dow_field`` 内（标准 cron 语义）。"""
    values = _parse_field_values(dow_field, _FIELD_SPECS[4])
    if values is None:
        return False
    return _to_standard_weekday(dt) in values


def _to_standard_weekday(dt: datetime) -> int:
    """``datetime.weekday()``（0=Mon）→ 标准 cron 值（0=Sun）。"""
    return (dt.weekday() + 1) % 7


def weekday_name(dt: datetime) -> str:
    """``dt`` 的星期名（标准 cron 三字母名，如 ``mon``）。

    供"把某一天交给调度器"的调用点使用——**传名字，不传数字**：数字一旦
    跨出本模块就会重新带上"标准 cron / Python"两套读法。
    """
    return WEEKDAY_NAMES[_to_standard_weekday(dt)]


def cron_matches(expr: str, dt: datetime) -> bool:
    """内部补跑判据：``dt`` 是否命中 5 字段 cron 表达式。

    与 APScheduler ``CronTrigger`` **同源语义**（字段间 AND、星期标准 cron、
    支持区间/步长/列表）——这是两引擎一致性契约的判据本体。
    表达式形状非法时返回 False（不抛异常：调用点在冷启动补跑路径上）。
    """
    parts = split_fields(expr)
    if parts is None:
        return False
    parsed: list[set[int]] = []
    for field, spec in zip(parts, _FIELD_SPECS):
        values = _parse_field_values(field, spec)
        if values is None:
            return False
        parsed.append(set(values))
    if dt.minute not in parsed[0] or dt.hour not in parsed[1]:
        return False
    if dt.day not in parsed[2] or dt.month not in parsed[3]:
        return False
    return _to_standard_weekday(dt) in parsed[4]


def coerce_weekday_name(value: object, default: str = "sun") -> str:
    """把设置项里的星期值归一为 3 字母名字（标准 cron 语义）。

    接受名字（``sun``/``sunday``，大小写不敏感）与数字（``0``/``7``=Sun、
    ``1``=Mon…）。无法识别时回落 ``default``——与旧实现"非法值回落默认日"
    的行为一致，但输出的是**名字**而非会漂移的数字。
    """
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text.isdigit():
        # 数字按标准 cron 解读（0/7=Sun、1=Mon…6=Sat）；越界即回落默认。
        index = int(text)
        if 0 <= index <= 7:
            return WEEKDAY_NAMES[_canonical_value(index, _FIELD_SPECS[4])]
        return default
    index = WEEKDAY_ALIASES.get(text)
    if index is None:
        return default
    return WEEKDAY_NAMES[index]


def is_numeric_weekday_literal(expr: str) -> bool:
    """表达式（或其星期字段）是否为**纯数字** dow —— 供棘轮门禁判定。

    数字 dow 是歧义的载体（标准 cron / Python 两套读法）；规范化后不应再出现。
    """
    parts = split_fields(expr)
    field = parts[4] if parts is not None else str(expr or "").strip()
    if not field or field == "*":
        return False
    for raw in field.split(","):
        token = raw.strip().split("/")[0].strip()
        if "-" in token:
            head = token.partition("-")[0].strip()
            if not head.isdigit():
                return False
            continue
        if not token.isdigit():
            return False
    return True


__all__ = [
    "WEEKDAY_NAMES",
    "WEEKDAY_ALIASES",
    "MONTH_NAMES",
    "PY_WEEKDAY_TO_NAME",
    "RRULE_PREFIX",
    "ScheduleCheck",
    "split_fields",
    "normalize_weekday_field",
    "normalize_cron_schedule",
    "validate_schedule_shape",
    "weekday_matches",
    "weekday_name",
    "cron_matches",
    "coerce_weekday_name",
    "is_numeric_weekday_literal",
]
