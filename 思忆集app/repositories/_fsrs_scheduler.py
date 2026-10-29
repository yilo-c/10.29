"""FSRS scheduler shim — 思忆集双写策略。

设计：
  - 旧 SM-2 字段（repetitions/efactor/interval/next_review/last_review）保持原样可读
  - 新增 FSRS 字段（fsrs_state/fsrs_stability/fsrs_difficulty/fsrs_step/fsrs_due/fsrs_last_review）
  - 评分时双写两套字段；下次到期时间 (next_review) 取较早者，遵循"宁紧勿松"原则
  - 初次接入 FSRS 时从 SM-2 状态推算初值（无复习历史 → Learning；有 → Review，stability=interval）
  - 删除所有 fsrs_* 字段即可回滚到纯 SM-2

评分映射（用户 5 档 → FSRS 4 档）：
  0,1,2 → Again
  3     → Hard
  4     → Good
  5     → Easy

依赖 fsrs>=4.0
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fsrs import Card, Rating, Scheduler, State

logger = logging.getLogger("siyi.fsrs")

# 默认调度器（FSRS v6 自带合理 default 参数；后续可让用户覆盖）
_DEFAULT_SCHEDULER = Scheduler()


def quality_to_rating(quality: int) -> Rating:
    """SM-2 0-5 评分 → FSRS 4 档 Rating。"""
    if quality <= 2:
        return Rating.Again
    if quality == 3:
        return Rating.Hard
    if quality == 4:
        return Rating.Good
    return Rating.Easy


def _fsrs_fields(word: dict[str, Any]) -> dict[str, Any] | None:
    """从词条提取 FSRS 字段；缺失任何核心字段返回 None。"""
    state_raw = word.get("fsrs_state")
    if state_raw is None:
        return None
    return {
        "state": state_raw,
        "stability": word.get("fsrs_stability"),
        "difficulty": word.get("fsrs_difficulty"),
        "step": word.get("fsrs_step"),
        "due": word.get("fsrs_due"),
        "last_review": word.get("fsrs_last_review"),
    }


def _build_card_from_word(word: dict[str, Any]) -> Card:
    """根据词条现有 FSRS 字段（或从 SM-2 推算）构造 fsrs.Card。

    优先级：
      1. 已有 fsrs_state → 直接还原
      2. 已有 SM-2 复习历史（repetitions>0）→ Review 状态，stability≈interval
      3. 全新词 → Learning
    """
    fsrs_data = _fsrs_fields(word)
    if fsrs_data and fsrs_data.get("state") is not None:
        # 已迁移过；直接 from_dict
        try:
            return Card.from_dict({
                "card_id": int(word.get("id") or 0) or None,
                "state": int(fsrs_data["state"]),
                "step": fsrs_data.get("step"),
                "stability": fsrs_data.get("stability"),
                "difficulty": fsrs_data.get("difficulty"),
                "due": fsrs_data.get("due"),
                "last_review": fsrs_data.get("last_review"),
            })
        except Exception:
            logger.debug("fsrs Card.from_dict failed, will rebuild from SM-2", exc_info=True)

    # 从 SM-2 状态推算
    reps = int(word.get("repetitions") or 0)
    interval = float(word.get("interval") or 0)
    efactor = float(word.get("efactor") or 2.5)
    last_review_iso = str(word.get("last_review") or "").strip()

    if reps > 0 and interval > 0:
        # 有复习历史 → Review 状态
        # stability 直接用 interval（FSRS 单位也是天）
        # difficulty: SM-2 的 efactor 范围 [1.3, 2.5+]，FSRS difficulty 范围 [1, 10]
        # efactor 越高（越简单）→ difficulty 越低；efactor 2.5 → diff≈5；1.3 → diff≈9
        # 线性映射：difficulty = clip(10 - 4*(efactor-1.3), 1, 10)
        difficulty = max(1.0, min(10.0, 10.0 - 4.0 * (efactor - 1.3)))
        last_review_dt = _parse_iso(last_review_iso) or datetime.now(timezone.utc)
        next_review_dt = _parse_iso(str(word.get("next_review") or "")) or last_review_dt
        return Card(
            card_id=int(word.get("id") or 0) or None,
            state=State.Review,
            step=None,
            stability=max(0.1, interval),
            difficulty=difficulty,
            due=next_review_dt,
            last_review=last_review_dt,
        )

    # 全新词
    return Card(card_id=int(word.get("id") or 0) or None)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _earlier_iso(a: str, b: str) -> str:
    """返回较早的 ISO 时间字符串（取严格者）。空值视为最远未来。"""
    if not a:
        return b
    if not b:
        return a
    da = _parse_iso(a)
    db = _parse_iso(b)
    if da is None:
        return b
    if db is None:
        return a
    return a if da <= db else b


def review_with_dual_write(
    word: dict[str, Any],
    quality: int,
    now: datetime | None = None,
    scheduler: Scheduler | None = None,
) -> dict[str, Any]:
    """对单个词条应用一次评分，返回需写回的字段增量。

    输出 dict 是要 merge 到原 word 上的字段（**不修改原 word**）。
    包含两套字段：
      - SM-2: repetitions, efactor, interval, next_review, last_review
      - FSRS: fsrs_state, fsrs_stability, fsrs_difficulty, fsrs_step, fsrs_due, fsrs_last_review

    `next_review`（用于"何时到期"列表）取两套调度的较早者。
    """
    if not (0 <= quality <= 5):
        raise ValueError("quality must be 0-5")
    sch = scheduler or _DEFAULT_SCHEDULER
    now_dt = now or datetime.now(timezone.utc)

    # ── SM-2 计算 ──
    sm2 = _sm2_step(word, quality, now_dt)

    # ── FSRS 计算 ──
    rating = quality_to_rating(quality)
    card = _build_card_from_word(word)
    new_card, _log = sch.review_card(card, rating, now_dt)
    fsrs_state_dict = new_card.to_dict()
    due_val = fsrs_state_dict.get("due")
    fsrs_due_iso = due_val.isoformat() if isinstance(due_val, datetime) else (str(due_val or ""))
    fsrs_last_review_iso = fsrs_state_dict.get("last_review") or now_dt.isoformat()

    # next_review 取较早者
    next_review = _earlier_iso(sm2["next_review"], fsrs_due_iso)

    return {
        # SM-2 兼容字段
        "repetitions": sm2["repetitions"],
        "efactor": sm2["efactor"],
        "interval": sm2["interval"],
        "next_review": next_review,
        "last_review": now_dt.isoformat(),
        # FSRS 字段
        "fsrs_state": fsrs_state_dict.get("state"),
        "fsrs_stability": fsrs_state_dict.get("stability"),
        "fsrs_difficulty": fsrs_state_dict.get("difficulty"),
        "fsrs_step": fsrs_state_dict.get("step"),
        "fsrs_due": fsrs_due_iso,
        "fsrs_last_review": fsrs_last_review_iso,
        # 元数据
        "fsrs_enabled": True,
        "fsrs_last_rating": int(rating),
    }


def _sm2_step(word: dict[str, Any], quality: int, now: datetime) -> dict[str, Any]:
    """复刻原 record_review 中的 SM-2 计算（不写盘）。"""
    reps = int(word.get("repetitions") or 0)
    efactor = float(word.get("efactor") or 2.5)
    interval = int(word.get("interval") or 0)

    if quality >= 3:
        if reps == 0:
            interval = 1
        elif reps == 1:
            interval = 6
        else:
            interval = round(interval * efactor)
        reps += 1
    else:
        reps = 0
        interval = 1
    efactor = efactor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    efactor = max(1.3, efactor)
    efactor = round(efactor, 2)
    from datetime import timedelta
    next_review = (now + timedelta(days=interval)).isoformat()
    return {
        "repetitions": reps,
        "efactor": efactor,
        "interval": interval,
        "next_review": next_review,
    }


def rollback_fsrs(word: dict[str, Any]) -> dict[str, Any]:
    """返回去除所有 FSRS 字段后的词条 dict（深拷贝）。"""
    cleaned = dict(word)
    for key in (
        "fsrs_state",
        "fsrs_stability",
        "fsrs_difficulty",
        "fsrs_step",
        "fsrs_due",
        "fsrs_last_review",
        "fsrs_enabled",
        "fsrs_last_rating",
    ):
        cleaned.pop(key, None)
    return cleaned
