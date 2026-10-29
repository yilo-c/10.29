"""单词复习心情联动 — 把 vocabulary.review.recorded 翻译为 mood.changed 与会话总结事件。

订阅 EventBus，根据连续表现派发：
  - 连续 ≥3 次 forget(quality≤2) → mood.changed=alert（"嗯…这个词你和它好像不太熟"）
  - 一次 review session 全部 quality≥4 且 ≥5 词    → mood.changed=happy（"全都答对啦"）
  - session 空闲 ≥90s 视为结束，发 vocabulary.session.summary

设计要点：
  - 读时不动 repository / handler，纯订阅+派生事件，符合"事件驱动 / 反应式"原则
  - 短期状态由本模块持有（in-memory），不影响持久化
  - 时间戳从 event.created_at（UTC ISO）解析，避免 Date.now()
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque

from core.events import AppEvent, event_bus

logger = logging.getLogger("siyi.backend.vocab_mood")

# ── 阈值 ──
FORGET_QUALITY_MAX = 2          # ≤2 视为忘记
GOOD_QUALITY_MIN = 4            # ≥4 视为答对
CONSECUTIVE_FORGET_TRIGGER = 3  # 连续多少次 forget 触发 alert
HAPPY_MIN_WORDS = 5             # 全部答对的最小词数
SESSION_IDLE_SECONDS = 90       # session 空闲多少秒后判定结束
RECENT_HISTORY_LIMIT = 50       # 维护的最近评分队列上限

# 用 alert/happy 复用现有 PetMood，不引入未注册值；reason 字段携带语义
MOOD_CONCERNED = "alert"
MOOD_PROUD = "happy"


@dataclass
class _SessionState:
    """每会话累计状态。会话以"连续 ≤90s 间隔的复习评分"为界。"""
    started_at: datetime | None = None
    last_review_at: datetime | None = None
    total: int = 0
    forgot: int = 0
    good: int = 0
    consecutive_forget: int = 0
    last_alert_at: datetime | None = None
    last_happy_at: datetime | None = None
    recent_words: Deque[str] = field(default_factory=lambda: deque(maxlen=RECENT_HISTORY_LIMIT))


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        # AppEvent.created_at 是 UTC ISO，含微秒
        return datetime.fromisoformat(value)
    except Exception:
        return None


class VocabMoodCoordinator:
    """监听 vocabulary.review.recorded，派生 mood/summary 事件。"""

    def __init__(self) -> None:
        self._state = _SessionState()
        self._lock = threading.Lock()
        self._on_review_handler = None
        self._enabled = False

    # ── 生命周期 ──
    def start(self) -> None:
        if self._enabled:
            return
        self._on_review_handler = self._on_review
        event_bus.on("vocabulary.review.recorded", self._on_review_handler)
        self._enabled = True
        logger.info("vocab mood coordinator started")

    def stop(self) -> None:
        if not self._enabled:
            return
        if self._on_review_handler:
            event_bus.off("vocabulary.review.recorded", self._on_review_handler)
        self._enabled = False
        logger.info("vocab mood coordinator stopped")

    # ── 公开（测试用） ──
    def snapshot(self) -> dict:
        with self._lock:
            s = self._state
            return {
                "total": s.total,
                "forgot": s.forgot,
                "good": s.good,
                "consecutive_forget": s.consecutive_forget,
                "started_at": s.started_at.isoformat() if s.started_at else None,
            }

    def reset(self) -> None:
        with self._lock:
            self._state = _SessionState()

    # ── 内部 ──
    def _on_review(self, event: AppEvent) -> None:
        try:
            payload = event.payload or {}
            quality = int(payload.get("quality") or 0)
            word = str(payload.get("word") or "")
            now = _parse_iso(event.created_at) or datetime.now(timezone.utc)

            with self._lock:
                s = self._state

                # 会话切换：闲置 >90s 算新一段
                pending_events: list[AppEvent] = []

                if s.last_review_at:
                    delta = (now - s.last_review_at).total_seconds()
                    if delta > SESSION_IDLE_SECONDS:
                        summary_event = self._emit_session_summary_locked(s, ended_at=s.last_review_at, reason="idle_timeout")
                        if summary_event:
                            pending_events.append(summary_event)
                        s = _SessionState()
                        self._state = s

                if s.started_at is None:
                    s.started_at = now
                s.last_review_at = now
                s.total += 1
                if word:
                    s.recent_words.append(word)

                if quality <= FORGET_QUALITY_MAX:
                    s.forgot += 1
                    s.consecutive_forget += 1
                else:
                    s.consecutive_forget = 0
                    if quality >= GOOD_QUALITY_MIN:
                        s.good += 1

                pending_events.extend(self._evaluate_triggers_locked(s, now))

            # 释放锁后再 emit，避免回调里的同步订阅者拿不到锁
            for ev in pending_events:
                try:
                    event_bus.emit(ev)
                except Exception:
                    logger.debug("emit event failed", exc_info=True)
        except Exception:
            logger.exception("vocab mood _on_review failed")

    def _evaluate_triggers_locked(self, s: _SessionState, now: datetime) -> list[AppEvent]:
        out: list[AppEvent] = []

        # 1) 连续 forget → concerned
        if s.consecutive_forget >= CONSECUTIVE_FORGET_TRIGGER:
            # 节流：同 session 同 alert 至少间隔 60s
            if not s.last_alert_at or (now - s.last_alert_at).total_seconds() > 60:
                s.last_alert_at = now
                out.append(AppEvent(
                    event_type="mood.changed",
                    payload={
                        "mood": MOOD_CONCERNED,
                        "reason": "vocab_consecutive_forget",
                        "consecutive_forget": s.consecutive_forget,
                        "message": f"嗯…这几个词你和它好像不太熟，连续 {s.consecutive_forget} 个没记住。",
                    },
                    source="vocab_mood",
                ))

        # 2) session 全对且 ≥HAPPY_MIN_WORDS → proud（happy）
        # 仅在评分次数刚好越过阈值时触发一次，避免重复
        if (
            s.total >= HAPPY_MIN_WORDS
            and s.forgot == 0
            and s.good == s.total
            and not s.last_happy_at
        ):
            s.last_happy_at = now
            out.append(AppEvent(
                event_type="mood.changed",
                payload={
                    "mood": MOOD_PROUD,
                    "reason": "vocab_session_perfect",
                    "count": s.total,
                    "message": f"前 {s.total} 个全答对！这状态保持住。",
                },
                source="vocab_mood",
            ))

        return out

    def _emit_session_summary_locked(
        self,
        s: _SessionState,
        ended_at: datetime,
        reason: str,
    ) -> AppEvent | None:
        """会话结束构造总结事件（持锁内构造，emit 在调用者外完成）。"""
        if s.total <= 0:
            return None
        try:
            duration = (ended_at - (s.started_at or ended_at)).total_seconds()
        except Exception:
            duration = 0
        return AppEvent(
            event_type="vocabulary.session.summary",
            payload={
                "total": s.total,
                "good": s.good,
                "forgot": s.forgot,
                "duration_seconds": int(duration),
                "ended_reason": reason,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "ended_at": ended_at.isoformat(),
            },
            source="vocab_mood",
        )


# 全局协调器实例（在 headless_main 启动时调用 start()）
vocab_mood_coordinator = VocabMoodCoordinator()
