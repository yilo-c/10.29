"""统一时间源与时钟倒退检测（P0-1 治本，2026-09-18）。

## 为什么需要它（过往经验）

env-drill 第三波演练（T2 时钟跳变）实证：全栈时间判据长期**直读 naive 墙钟**
（``time.time()`` / ``datetime.now()``），同一病灶在四处**独立复发**：

======================  ==========================================================
位置                     回拨/恢复方向的实际后果
======================  ==========================================================
``confirmation_store``  回拨后 ``now > expires_at`` 恒假 → 审批窗口无限拉长
                        （回拨 48h 即 48h 内不超时），2 天前的 R4 破坏性审批
                        仍可被点击批准生效
``task_queue`` lease    回拨后 ``now - leased_at`` 为负 → 崩溃前 ``running``
                        任务永不回 ``pending``
``cron_service``        恢复方向：``_catch_up_missed`` 把回拨后的墙钟写进
``_catch_up_missed``    ``last_run`` → 同一触发点二次判"错过" → 重复补跑
``weekly_report``       同款墙钟日期字符串做等值防御（非单调比较）
======================  ==========================================================

**逐处打补丁 = 治标**：同一类缺陷会在第五处、第六处继续复发。本模块把
"读时间"与"判过期"收敛为**单一入口 + 倒退检测基元**；后续所有时间敏感代码经此
实现，新增站点不再需要零散补丁。

## 个人使用场景（为何对本项目尤其重要）

本应用是**单人单机自用**：笔记本休眠/唤醒、双系统时间不同步、虚拟机快照恢复、
用户手动改系统时间，都会产生**真实的墙钟跳变**——比服务器场景更容易踩到。
而受影响的恰是个人资产：**审批历史、定时备份、任务队列**。

## 用法

* :func:`wall_ts` —— 唯一允许直读墙钟的入口（持久化 / 展示 / 跨进程比较）
* :func:`mono_ts` —— 进程内"经过了多久"（TTL / lease）用单调钟
* :func:`detect_rollback` —— 持久化场景的倒退检测（跨进程可用的墙钟护栏）
* :func:`deadline_expired` —— 统一"是否已过期"判据（正常比较 + 倒退护栏）

纪律由 ``scripts/check_clock_discipline.py`` 棘轮锁定：时间敏感模块不得再直接
调用 ``time.time()`` / ``datetime.now()``（存量只允许下降）。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

# 倒退容忍窗：吸收 NTP 微调 / 闰秒，避免正常校时误杀合法待处理项。
DEFAULT_ROLLBACK_TOLERANCE_S = 60.0


def wall_ts() -> float:
    """墙钟秒级时间戳（epoch）。

    **持久化 / 展示 / 跨进程比较的唯一入口**。判"是否过期"请改用
    :func:`deadline_expired`（带倒退护栏），不要自己比较。
    """
    return time.time()


def mono_ts() -> float:
    """单调钟秒级读数。

    **仅限进程内**时长测量（TTL / lease / 限流窗口）。单调钟不受系统校时影响，
    但**进程重启后归零**，故不可持久化、不可跨进程比较——持久化条目（如
    ``confirmations.json``）的 TTL 不能只靠它，必须配 :func:`detect_rollback`。
    """
    return time.monotonic()


def wall_now() -> datetime:
    """本地 naive ``datetime``（与既有 ``datetime.now()`` 语义一致）。"""
    return datetime.now()


def detect_rollback(
    now: float,
    reference: Optional[float],
    *,
    tolerance_s: float = DEFAULT_ROLLBACK_TOLERANCE_S,
) -> bool:
    """``now`` 是否早于 ``reference`` 超过容忍窗（= 墙钟被回拨）。

    ``reference`` 取"该条目创建/上次观测时的墙钟"（如 ``created_at``）。
    ``None`` 视为无基准 → 返回 False。
    """
    if reference is None:
        return False
    return now < float(reference) - float(tolerance_s)


def deadline_expired(
    now: float,
    deadline: Optional[float],
    *,
    reference: Optional[float] = None,
    tolerance_s: float = DEFAULT_ROLLBACK_TOLERANCE_S,
) -> bool:
    """统一"是否已过期"判据。

    * ``deadline`` 为 ``None`` → False（缺字段语义交由调用方各自决定）；
    * 检测到墙钟倒退（``now < reference - 容忍窗``）→ **True**（保守判过期）；
    * 否则 ``now > deadline``。

    倒退方向判 True 是本模块的核心安全语义：宁可让合法审批提前失效（用户重新
    发起即可），也不允许**2 天前的破坏性操作**被误批准生效。
    """
    if deadline is None:
        return False
    if detect_rollback(now, reference, tolerance_s=tolerance_s):
        return True
    return now > float(deadline)


def safe_timestamp_write(
    now: float,
    previous: Optional[float],
    *,
    tolerance_s: float = DEFAULT_ROLLBACK_TOLERANCE_S,
) -> float:
    """落盘时间戳的**防毒写**：墙钟倒退时拒绝覆写，保留 ``previous``。

    用于 ``cron`` 的 ``last_run`` 等"写坏就会导致重复触发"的字段
    （P1-5：``_catch_up_missed`` 用回拨后的墙钟覆写 ``last_run``，时间恢复后
    同一触发点被二次判"错过" → 双触发）。``previous`` 为 None 时直接采用 ``now``。
    """
    if previous is not None and detect_rollback(now, previous, tolerance_s=tolerance_s):
        return float(previous)
    return float(now)
