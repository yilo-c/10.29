# -*- coding: utf-8 -*-
"""观察期切换判定（G 项）。

提供 ``should_switch_to_butler(context=None) -> bool`` 与
``get_observation_status(context=None) -> dict`` 两个纯函数，供运维查看与
（未来的）路由倾斜读取。

判定语义（默认 **False = 不切换**，与现状逐字节一致）：
    should_switch = 观察期已开始 且（无 end 或 now>=end）且 s1_satisfied

- ``agent_kernel.observation_period``：``{start, end}`` ISO 日期（"YYYY-MM-DD"）；
  缺省无（未配置 → 不切换）。仅 start 或仅 end 任一存在即视为"已配置观察期"。
- ``agent_kernel.s1_satisfied``：S1 安全指标达标（默认 True）；显式 false 时不切换。
- 任何缺失 / 异常 → False（保守，不切换）。

**本批次只埋判定函数与度量，不真正切换路由**（q-1 决策：观察期切换由后续批次
在 ``should_switch_to_butler`` 命中时激活，本批次仅暴露判定与状态）。
"""
from __future__ import annotations

import datetime
from typing import Any

from services.agent_gateway.factory import parse_agent_kernel_value


def _read_agent_kernel_config(context: Any) -> dict:
    """读 settings 的 agent_kernel 并解析为完整配置；异常时返回空 dict。

    - ``context`` 为 dict：直接当作已解析的 agent_kernel 配置（供单测直传）。
    - ``context`` 含 ``settings_repository``：读 ``agent_kernel`` 原始值并解析。
    - 其他 / 异常：返回空 dict（保守，不切换）。
    """
    if isinstance(context, dict):
        return context
    repo = getattr(context, "settings_repository", None)
    if repo is None:
        return {}
    try:
        return parse_agent_kernel_value(repo.get_setting("agent_kernel", None))
    except Exception:
        return {}


def _parse_iso_date(value: Any) -> datetime.date | None:
    """把 ISO 日期（date / datetime / "YYYY-MM-DD[...]" 字符串）解析为 date。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:  # 兜底：截前 10 位按 ISO 日期解析
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        return None


def should_switch_to_butler(context: Any = None) -> bool:
    """观察期是否达标、应切换至 butler/DSH 内核（默认 False = 不切换）。"""
    try:
        cfg = _read_agent_kernel_config(context)
        s1 = cfg.get("s1_satisfied", True)
        if not s1:
            return False
        obs = cfg.get("observation_period")
        if not isinstance(obs, dict):
            return False
        start = _parse_iso_date(obs.get("start"))
        end = _parse_iso_date(obs.get("end"))
        has_start = start is not None
        has_end = end is not None
        if not (has_start or has_end):
            return False  # 未配置观察期
        today = datetime.date.today()
        if has_start and today < start:
            return False  # 观察期未开始
        if has_end and today < end:
            return False  # 观察期未结束
        return True
    except Exception:
        return False


def get_observation_status(context: Any = None) -> dict:
    """返回观察期切换判定的子条件明细，供运维查看（不切换，仅度量）。"""
    try:
        cfg = _read_agent_kernel_config(context)
        s1 = cfg.get("s1_satisfied", True)
        obs = cfg.get("observation_period")
        if not isinstance(obs, dict):
            return {
                "configured": False,
                "s1_satisfied": bool(s1),
                "period_started": False,
                "period_ended": False,
                "should_switch": False,
                "reason": "observation_period 未配置",
            }
        start = _parse_iso_date(obs.get("start"))
        end = _parse_iso_date(obs.get("end"))
        has_start = start is not None
        has_end = end is not None
        today = datetime.date.today()
        period_started = (not has_start) or (today >= start)
        period_ended = (not has_end) or (today >= end)
        should = bool(s1) and (has_start or has_end) and period_started and period_ended
        return {
            "configured": bool(has_start or has_end),
            "s1_satisfied": bool(s1),
            "period_started": period_started,
            "period_ended": period_ended,
            "should_switch": should,
            "reason": "ok" if should else "观察期未到或 S1 未达标",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "configured": False,
            "s1_satisfied": False,
            "period_started": False,
            "period_ended": False,
            "should_switch": False,
            "reason": f"error: {exc}",
        }


__all__ = ["should_switch_to_butler", "get_observation_status"]
