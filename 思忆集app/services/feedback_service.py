"""P2.8 用户反馈学习闭环服务。

承载 ``chat.feedback.*`` RPC 的业务逻辑，由 ``headless_main.py`` 显式注入 context。

功能：
- 记录用户对 AI 回复的反馈信号（👍/👎/编辑后重发）
- 聚合为偏好摘要文本，在后续对话中通过 ``_chat_context_anchor._preference_summary()``
  自动注入到「对话锚点」system prompt（位于用户画像之后）。

存储约定：
- 反馈样本：``local_storage`` 的 ``chat_feedback`` 键（list[dict]，最多 500 条 LRU）
- 偏好摘要：``local_storage`` 的 ``chat_feedback_preference`` 键

隐私：
- 聚合时只取最近 20 条学习样本，prompt 中原文截断到 200 字
- 偏好摘要限长 600 字，仅存规则不存原文
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("siyi.feedback")

# ── 模块级常量 ──────────────────────────────────────────────

MAX_FEEDBACK_RECORDS = 500  #: 反馈样本 LRU 上限
MAX_LEARNING_SAMPLES = 20  #: 聚合时最多取最近 N 条样本
MAX_SAMPLE_TEXT_LEN = 200  #: 隐私：prompt 中原文截断长度
MAX_PREFERENCE_LEN = 600  #: 偏好摘要限长
DEFAULT_AGGREGATE_THRESHOLD = 5  #: 默认聚合触发阈值（👎 + edited 样本数）


class FeedbackService:
    """用户反馈收集 + 偏好聚合服务。

    由 ``headless_main.py`` 显式注入 ``context.feedback_service``。
    """

    def __init__(
        self,
        *,
        data_store,
        settings_repository,
        ai_client=None,
        event_bus=None,
        ai_text_service=None,
    ) -> None:
        self._store = data_store
        self._settings = settings_repository
        self._ai = ai_client
        self._event_bus = event_bus
        self._ai_service = ai_text_service
        self._threshold = int(
            self._settings.get_setting("chat_feedback_aggregate_threshold", DEFAULT_AGGREGATE_THRESHOLD)
            if self._settings is not None
            else DEFAULT_AGGREGATE_THRESHOLD
        )

    # ── 记录反馈 ──────────────────────────────────────────

    def record_feedback(
        self,
        *,
        session_id: str | None = None,
        message_index: int | None = None,
        rating: str,
        original_user_msg: str = "",
        original_ai_msg: str = "",
        edited_text: str = "",
        comment: str = "",
    ) -> dict[str, Any]:
        """记录一条反馈。

        Args:
            rating: 必填，``"up"`` 或 ``"down"``。
            其余参数可空。

        Returns:
            ``{"recorded": True, "feedback": <record>}``
        """
        if rating not in ("up", "down"):
            raise ValueError(f"rating must be 'up' or 'down', got: {rating!r}")

        feedback = {
            "id": f"fb_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
            "session_id": session_id,
            "message_index": message_index,
            "rating": rating,
            "original_user_msg": original_user_msg,
            "original_ai_msg": original_ai_msg,
            "edited_text": edited_text,
            "is_edited": bool(edited_text) and edited_text != original_ai_msg,
            "comment": comment,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        records = self._store.load_json("chat_feedback", [])
        if not isinstance(records, list):
            records = []
        records.append(feedback)
        # LRU：超过上限丢弃最早的
        if len(records) > MAX_FEEDBACK_RECORDS:
            records = records[-MAX_FEEDBACK_RECORDS:]
        self._store.save_json("chat_feedback", records)

        if self._event_bus is not None:
            try:
                # B5（2026-08-26）：统一 AppEvent 单参契约（同 feedback.aggregated 修复）
                from core.events import AppEvent

                self._event_bus.emit(AppEvent("feedback.recorded", payload={"feedback": feedback}))
            except Exception:
                logger.debug("emit feedback.recorded failed", exc_info=True)

        return {"recorded": True, "feedback": feedback}

    # ── 查询 ──────────────────────────────────────────────

    def list_feedback(
        self,
        *,
        session_id: str | None = None,
        rating: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """查询反馈列表（按写入顺序倒序）。"""
        limit = max(1, min(limit, 500))
        records = self._store.load_json("chat_feedback", [])
        if not isinstance(records, list):
            return []
        # 倒序（最新优先）
        records = list(reversed(records))
        if session_id is not None:
            records = [r for r in records if r.get("session_id") == session_id]
        if rating is not None:
            records = [r for r in records if r.get("rating") == rating]
        return records[:limit]

    def get_stats(self, days: int = 30) -> dict[str, Any]:
        """聚合统计：按 rating 计数 + 学习样本数 + 配置状态。

        参数按位置可传（backend/handlers/_feedback.py 以 get_stats(days)
        位置方式调用），同时保留关键字用法。
        """
        records = self._store.load_json("chat_feedback", [])
        if not isinstance(records, list):
            records = []
        up_count = sum(1 for r in records if r.get("rating") == "up")
        down_count = sum(1 for r in records if r.get("rating") == "down")
        edited_count = sum(1 for r in records if r.get("is_edited"))
        learning_samples = sum(1 for r in records if r.get("rating") == "down" or r.get("is_edited"))
        # 近 N 天每日分布（按本地日期的 created_at 聚合，缺失时计入 unknown）
        try:
            days = max(1, min(int(days), 365))
        except (TypeError, ValueError):
            days = 30
        now = datetime.now(timezone.utc)
        day_keys = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
        daily = {k: 0 for k in day_keys}
        for r in records:
            created = str(r.get("created_at") or "")[:10]
            if created in daily:
                daily[created] += 1
        return {
            "total": len(records),
            "up": up_count,
            "down": down_count,
            "edited": edited_count,
            "learning_samples": learning_samples,
            "aggregate_threshold": self._threshold,
            "days": days,
            "daily": daily,
        }

    def count_learning_samples(self) -> int:
        """学习样本数（👎 + edited）。"""
        return self.get_stats()["learning_samples"]

    def delete_feedback(self, feedback_id: str) -> bool:
        """按 ID 删除单条反馈。"""
        records = self._store.load_json("chat_feedback", [])
        if not isinstance(records, list):
            return False
        new_records = [r for r in records if r.get("id") != feedback_id]
        if len(new_records) == len(records):
            return False
        self._store.save_json("chat_feedback", new_records)
        return True

    # ── 偏好聚合 ──────────────────────────────────────────

    def get_preference(self) -> str:
        """读取已聚合的偏好摘要文本。"""
        pref = self._store.load_json("chat_feedback_preference", "")
        return pref if isinstance(pref, str) else ""

    def set_preference(self, summary: str) -> None:
        """手动覆盖偏好摘要（限长 600 字）。"""
        if len(summary) > MAX_PREFERENCE_LEN:
            summary = summary[:MAX_PREFERENCE_LEN]
        self._store.save_json("chat_feedback_preference", summary)

    async def aggregate(self, *, force: bool = False) -> dict[str, Any]:
        """触发一次偏好聚合。

        Args:
            force: ``False``（默认）仅当样本数 ≥ 阈值时执行；``True`` 强制重新聚合。

        Returns:
            ``{"aggregated": bool, "summary": str, "samples": int, ...}``
        """
        samples = self.count_learning_samples()
        if not force and samples < self._threshold:
            return {"aggregated": False, "reason": "below_threshold", "samples": samples}

        records = self._store.load_json("chat_feedback", [])
        if not isinstance(records, list):
            records = []
        # 取最近 N 条学习样本（down 或 edited）
        learning = [r for r in records if r.get("rating") == "down" or r.get("is_edited")]
        learning = learning[-MAX_LEARNING_SAMPLES:]

        if not learning:
            return {"aggregated": False, "reason": "no_samples", "samples": 0}

        # 优先用 AI 提炼，失败回退规则
        summary = await self._ai_summarize(learning)
        if not summary:
            summary = self._rule_summarize(learning)

        self.set_preference(summary)
        if self._event_bus is not None:
            try:
                # B5（2026-08-26）：统一走 AppEvent 单参契约，与 core.events.EventBus.emit 一致。
                # 旧双参 emit("feedback.aggregated", payload) 会被 try/except 吞掉，通知实际从不发出。
                from core.events import AppEvent

                self._event_bus.emit(AppEvent("feedback.aggregated", payload={"summary": summary, "samples": len(learning)}))
            except Exception:
                logger.debug("emit feedback.aggregated failed", exc_info=True)

        return {"aggregated": True, "summary": summary, "samples": len(learning)}

    async def _ai_summarize(self, samples: list[dict[str, Any]]) -> str:
        """AI 提炼偏好摘要（经 ai_text_service 端口；优先本地，避免上云）。失败返回空串。"""
        if self._ai is None:
            return ""
        try:
            lines = []
            for s in samples:
                user = (s.get("original_user_msg") or "")[:MAX_SAMPLE_TEXT_LEN]
                ai = (s.get("original_ai_msg") or "")[:MAX_SAMPLE_TEXT_LEN]
                edited = (s.get("edited_text") or "")[:MAX_SAMPLE_TEXT_LEN]
                rating = s.get("rating")
                lines.append(f"[{rating}] user={user!r} ai={ai!r} edited={edited!r}")
            prompt = (
                "以下是用户对 AI 回复的反馈样本（👍=up / 👎=down / edited=用户改写）。\n"
                "请提炼 3-5 条用户偏好规则（不引用原文），每行一条，限 600 字：\n\n" + "\n".join(lines)
            )
            # 调用 AI 客户端；优先本地，避免敏感反馈数据上云
            text: str = ""
            if self._ai_service is not None:
                try:
                    resp = self._ai_service.complete(user_prompt=prompt)
                    result = resp
                except Exception as exc:
                    logger.debug("feedback AI via port failed: %s", exc)
                    result = None
                if isinstance(result, dict):
                    choices = result.get("choices") or []
                    if choices:
                        text = choices[0].get("message", {}).get("content", "") or ""
                elif isinstance(result, str):
                    text = result
            elif hasattr(self._ai, "chat_completion"):
                # ← 无端口时的兼容路径（M5 删）
                result = self._ai.chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                )
                if isinstance(result, dict):
                    choices = result.get("choices") or []
                    if choices:
                        text = choices[0].get("message", {}).get("content", "") or ""
            elif hasattr(self._ai, "complete_text"):
                text = self._ai.complete_text(prompt) or ""
            return (text or "").strip()[:MAX_PREFERENCE_LEN]
        except Exception:
            logger.warning("AI summarize feedback failed", exc_info=True)
            return ""

    def _rule_summarize(self, samples: list[dict[str, Any]]) -> str:
        """AI 不可用时的规则兜底。"""
        rules = []
        down_count = sum(1 for s in samples if s.get("rating") == "down")
        edited_count = sum(1 for s in samples if s.get("is_edited"))
        if down_count > edited_count:
            rules.append("用户对部分回复不满意，回复需更准确、避免空泛。")
        if edited_count > 0:
            rules.append("用户会主动改写回复，需更贴近用户语气与措辞。")
        rules.append("回复保持简洁，避免冗长解释。")
        return "\n".join(rules)[:MAX_PREFERENCE_LEN]
