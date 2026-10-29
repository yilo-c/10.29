"""收件箱统一入口服务。

所有收件通道（桌面 UI、浏览器桥、助手指令、悬浮助手、自治层）
在此单点收口，统一处理校验、事件追踪、guard 鉴权以及 AI 自动分类路由。

所有来源的条目通过 InboxEntry 模型标准化，确保同一条输入
从任何入口进入后具有一致的字段结构。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.settings_keys import KEY_AI_AUTO_TAG_ENABLED, KEY_INBOX_AUTO_CLASSIFY
from domain.inbox_models import InboxEntry

logger = logging.getLogger("siyi.inbox_capture")

# ── AI 分类提示词 ──

_CLASSIFY_SYSTEM_PROMPT = (
    "你是一个收件箱分类助手。请根据内容判断类型，只返回 JSON。\n"
    '类型定义：\n'
    '- "todo": 待办事项、任务、需执行的动作\n'
    '- "note": 笔记、灵感、需要保存的信息\n'
    '- "article": 文章链接、阅读材料、收藏\n'
    '- "memo": 随手记、碎片信息、备忘录\n'
    '- "trash": 垃圾内容、广告、无意义内容\n\n'
    '输出 JSON 格式：{"type": "todo|note|article|memo|trash", '
    '"confidence": 0.0-1.0, "reason": "简短判断理由"}'
)

_INBOX_CLASSIFY_USER_PROMPT = "请分类以下内容：\n{content}"


class InboxCaptureService:
    """统一收件入口。替代各调用方各自拼接 workbench_service / inbox_repository。"""

    def __init__(
        self,
        *,
        workbench_service=None,
        inbox_repository=None,
        action_guard=None,
        observability_service=None,
        assistant_inbox_service=None,
        ai_client=None,
        settings_repository=None,
        ai_text_service=None,
    ):
        self.workbench_service = workbench_service
        self.inbox_repository = inbox_repository
        self.action_guard = action_guard
        self.observability_service = observability_service
        self.assistant_inbox_service = assistant_inbox_service
        self.ai_client = ai_client
        self.settings_repository = settings_repository
        self.ai_text_service = ai_text_service

    # ── 主收件方法 ─────────────────────────────────────────────
    def capture(
        self,
        text: str,
        *,
        source: str = "manual",
        title: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        skip_guard: bool = False,
    ) -> dict[str, Any]:
        """统一收件入口。

        所有来源（桌面 UI、浏览器桥、助手、悬浮）统一通过此方法收件。
        InboxEntry.normalize() 确保同一条输入从任何来源进入后
        具有一致的字段结构。

        Returns:
            {"ok": True, "entry": {...}} 或 {"ok": False, "reason": "..."}
        """
        cleaned = str(text or "").strip()
        if not cleaned:
            return {"ok": False, "reason": "没有可收件的内容。"}

        # ── action_guard 鉴权 ──
        if not skip_guard and self.action_guard is not None:
            check = self.action_guard.validate(
                action_id="capture",
                payload={"source": source},
                source=f"inbox.capture.{source}",
            )
            if not check.allowed:
                self._track("inbox.capture.blocked", {"source": source, "reason": check.reason})
                return {"ok": False, "reason": f"动作已拒绝：capture（原因：{check.reason}）", "warnings": list(check.warnings)}
            if check.warnings:
                logger.info("capture warnings from %s: %s", source, check.warnings)

        # ── 标准化字段 ──
        resolved_title = str(title or "").strip() or cleaned[:40]
        normalized = InboxEntry.normalize({
            "title": resolved_title,
            "content": cleaned,
            "source": source,
            "tags": tags or [],
            "metadata": metadata or {},
        })

        # ── 写入 ──
        if self.workbench_service is not None:
            try:
                entry = self.workbench_service.capture_quick_entry(
                    normalized["content"],
                    source=normalized["source"],
                    title=normalized["title"],
                    tags=normalized["tags"],
                    metadata=normalized["metadata"],
                )
            except ValueError as exc:
                return {"ok": False, "reason": str(exc)}
        elif self.inbox_repository is not None:
            entry_id = self.inbox_repository.add_entry({
                "title": normalized["title"],
                "content": normalized["content"],
                "source": normalized["source"],
                "status": normalized["status"],
                "tags": normalized["tags"],
                "metadata": normalized["metadata"],
            })
            entry = self.inbox_repository.get_entry(entry_id) or {"id": entry_id, "title": normalized["title"]}
        else:
            return {"ok": False, "reason": "当前无法访问收件箱服务。"}

        self._track("inbox.capture.success", {"source": normalized["source"], "title": entry.get("title", "")})

        # AI 自动分类（不阻塞主流程）
        self._classify_and_route(normalized, entry)

        # 通过统一交付通道通知
        if self.assistant_inbox_service is not None:
            try:
                self.assistant_inbox_service.deliver(
                    title=normalized["title"],
                    content=normalized["content"][:200],
                    source="manual",
                    tags=normalized["tags"],
                    metadata=normalized["metadata"],
                )
            except Exception as exc:
                logger.warning("assistant_inbox delivery failed: %s", exc, exc_info=True)

        return {"ok": True, "entry": entry}

    # ── 批量收件 ──
    def capture_batch(
        self,
        items: list[dict[str, Any]],
        *,
        source: str = "batch",
        skip_guard: bool = False,
    ) -> dict[str, Any]:
        """批量收件，返回 {"ok": True, "results": [...]}。"""
        results = []
        for item in items:
            result = self.capture(
                item.get("text", ""),
                source=source,
                title=item.get("title"),
                tags=item.get("tags"),
                metadata=item.get("metadata"),
                skip_guard=skip_guard,
            )
            results.append(result)
        succeeded = sum(1 for r in results if r.get("ok"))
        return {"ok": succeeded > 0, "total": len(items), "succeeded": succeeded, "results": results}

    # ── AI 自动分类与路由 ─────────────────────────────────
    def _classify_and_route(self, normalized: dict[str, Any], entry: dict[str, Any]) -> None:
        """AI 分类收件条目，高置信度时自动路由到对应模块。"""
        if self.ai_client is None or self.settings_repository is None:
            return
        enabled = self.settings_repository.get_setting(KEY_AI_AUTO_TAG_ENABLED, False)
        if not enabled:
            enabled = self.settings_repository.get_setting(KEY_INBOX_AUTO_CLASSIFY, False)
        if not enabled:
            return

        content = normalized.get("content", "") or ""
        if not content.strip():
            return

        try:
            result = self._ai_classify(content)
        except Exception as exc:
            logger.debug("inbox AI classify skipped: %s", exc)
            return

        entry_type = result.get("type", "memo")
        confidence = float(result.get("confidence", 0))
        entry_id = entry.get("id") or entry.get("entry_id", "")

        # 记录分类结果到 entry metadata
        if entry_id and self.inbox_repository is not None:
            try:
                current = self.inbox_repository.get_entry(entry_id) or {}
                meta = dict(current.get("metadata", {}) or {})
                meta["ai_classify"] = {"type": entry_type, "confidence": round(confidence, 2), "reason": result.get("reason", "")}
                self.inbox_repository.update_entry(entry_id, {"metadata": meta})
            except Exception as exc:
                logger.debug("inbox AI classify metadata update failed: %s", exc)

        # 高置信度 → 自动路由
        if confidence >= 0.7:
            self._auto_route(entry_type, normalized, entry_id)

    def _ai_classify(self, content: str) -> dict[str, Any]:
        """调用本地 AI 分类收件内容（经 ai_text_service 端口）。"""
        prompt = _INBOX_CLASSIFY_USER_PROMPT.format(content=content[:500])
        if self.ai_text_service is not None:
            try:
                resp = self.ai_text_service.complete(
                    user_prompt=prompt,
                    system_prompt=_CLASSIFY_SYSTEM_PROMPT,
                    temperature=0.1,
                    max_tokens=100,
                )
            except Exception as exc:
                logger.debug("inbox AI classify via port failed: %s", exc)
                return {"type": "memo", "confidence": 0.3, "reason": "AI 调用失败"}
        elif self.ai_client is None:
            return {"type": "memo", "confidence": 0.3, "reason": "AI 不可用"}
        else:
            # ← 无端口时的兼容路径（M5 删）
            resp = self.ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=100,
            )
        raw = ""
        if isinstance(resp, dict):
            choices = resp.get("choices", [])
            if choices:
                raw = (choices[0].get("message", {}) or {}).get("content", "")
        elif isinstance(resp, str):
            raw = resp

        if not raw:
            return {"type": "memo", "confidence": 0.3, "reason": "AI 无响应"}

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            cleaned = cleaned.rsplit("\n", 1)[0] if "\n" in cleaned else cleaned
            cleaned = cleaned.removeprefix("json").strip()
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            logger.debug("inbox AI classify: failed to parse response: %s", raw[:100])
            return {"type": "memo", "confidence": 0.3, "reason": "解析失败"}

    def _auto_route(self, entry_type: str, normalized: dict[str, Any], entry_id: str) -> None:
        """高置信度分类结果自动路由到对应模块。"""
        content = normalized.get("content", "")
        title = normalized.get("title", "") or content[:40]
        tags = normalized.get("tags", [])

        if entry_type == "todo":
            if self.workbench_service is not None and hasattr(self.workbench_service, "add_todo"):
                try:
                    self.workbench_service.add_todo(title=title, content=content, tags=tags, source="inbox_auto")
                    logger.info("inbox auto-routed to todo: %s", title)
                except Exception as exc:
                    logger.warning("inbox auto-route to todo failed: %s", exc)

        elif entry_type == "note":
            if self.workbench_service is not None and hasattr(self.workbench_service, "add_note"):
                try:
                    self.workbench_service.add_note(title=title, content=content, tags=tags, source="inbox_auto")
                    logger.info("inbox auto-routed to note: %s", title)
                except Exception as exc:
                    logger.warning("inbox auto-route to note failed: %s", exc)

        elif entry_type == "article":
            if self.workbench_service is not None:
                try:
                    logger.info("inbox auto-routed to article (inbox kept): %s", title)
                except Exception as exc:
                    logger.warning("inbox auto-route to article failed: %s", exc)

    # ── 内部工具 ──
    def _track(self, event_name: str, payload: dict[str, Any]) -> None:
        if self.observability_service is not None:
            try:
                self.observability_service.track_event(event_name, payload=payload)
            except Exception:
                logger.warning("failed to track inbox event: %s", event_name)
        logger.debug("inbox event: %s %s", event_name, payload)
