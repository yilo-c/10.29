"""inbox.* JSON-RPC handlers：收件箱 CRUD + AI 分类 + 人工确认链路。

从原 _inbox_data.py 拆分而来。模块内使用共享的 ``spawn_bg_task``（见 _bg_tasks.py）
管理跨域后台分类任务，不自行维护任务集合。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.settings_keys import (
    KEY_AI_AUTO_TAG_ENABLED,
    KEY_AI_AUTO_TAG_THRESHOLD,
    KEY_INBOX_AUTO_CLASSIFY,
)
from backend.handlers._bg_tasks import spawn_bg_task
from backend.handlers._list_guard import clamp_limit

logger = logging.getLogger("siyi.backend.handlers.inbox_crud")

# ── inbox.list 分页安全边界（与仓储层常量保持一致）──
# 默认 50 条：与 notes.list 既有约定对齐；上限 200：单帧实测远低于客户端帧上限。
INBOX_LIST_DEFAULT_LIMIT = 50
INBOX_LIST_MAX_LIMIT = 200


def _coerce_page(limit: Any, offset: Any, order: Any) -> tuple[int, int, str]:
    """把 RPC 传入的分页参数夹取到安全区间（防御 **kwargs 时代的超大 limit）。"""
    try:
        norm_limit = int(limit)
    except (TypeError, ValueError):
        norm_limit = INBOX_LIST_DEFAULT_LIMIT
    if norm_limit <= 0:
        norm_limit = INBOX_LIST_DEFAULT_LIMIT
    elif norm_limit > INBOX_LIST_MAX_LIMIT:
        norm_limit = INBOX_LIST_MAX_LIMIT
    try:
        norm_offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        norm_offset = 0
    norm_order = "desc" if str(order or "").strip().lower() == "desc" else "asc"
    return norm_limit, norm_offset, norm_order


def register_inbox_crud(registry, context) -> None:
    """注册 inbox.* 命名空间下的 RPC 方法。"""

    async def _get_auto_tag_config() -> tuple[bool, float]:
        """读取 AI 自动分类开关与置信度阈值（兼容旧 inbox_auto_classify）。"""
        settings = getattr(context, "settings_repository", None)
        if settings is None:
            return False, 0.7
        enabled = await asyncio.to_thread(
            settings.get_setting,
            KEY_AI_AUTO_TAG_ENABLED,
            False,
        )
        # 旧设置兼容：若未显式设置新开关，回退到旧 inbox_auto_classify
        if not enabled:
            legacy = await asyncio.to_thread(
                settings.get_setting,
                KEY_INBOX_AUTO_CLASSIFY,
                False,
            )
            enabled = bool(legacy)
        threshold = await asyncio.to_thread(
            settings.get_setting,
            KEY_AI_AUTO_TAG_THRESHOLD,
            0.7,
        )
        return bool(enabled), float(threshold or 0.7)

    # ── inbox helper: async classification ──
    async def _classify_inbox_entry(entry_id: int | str, content: str, title: str) -> None:
        """后台异步执行 AI 分类，非阻塞事件循环。

        （2026-10-15 id 口径审计：标注从 str 放宽为 int | str——调用方
        inbox_add 传的是 add_entry 返回的 int 主键；仓储 update_entry 亦收 int。）
        """
        enabled, threshold = await _get_auto_tag_config()
        if not enabled:
            return

        ai_client = getattr(context, "ai_client", None)
        if not ai_client:
            return

        from services.inbox_classifier_service import InboxClassifierService

        classifier = InboxClassifierService(
            ai_client=ai_client,
            ai_text_service=getattr(context, "ai_text_service", None),
        )
        try:
            # classifier.classify 只是 async wrapper 包裹同步 AI 调用
            # 直接 await 即可，无需 asyncio.run 嵌套（每次嵌套创建新事件循环）
            result = await classifier.classify(content, title)
        except Exception as exc:
            logger.debug("inbox classification failed: %s", exc)
            return

        repo = context.personal_inbox_repository
        confidence = float(result.get("confidence", 0))
        if repo and result.get("kind") != "uncertain" and confidence >= threshold:
            try:
                await asyncio.to_thread(
                    repo.update_entry,
                    entry_id,
                    {
                        "metadata": {"classification": result},
                        "tags": [result["kind"]],
                    },
                )
                logger.info(
                    "inbox entry %s classified as %s (%.2f)",
                    entry_id,
                    result["kind"],
                    confidence,
                )
            except Exception as exc:
                logger.debug("inbox classification metadata update failed: %s", exc)

    # ── inbox.list ──
    # 分页原语（P0-2026-09-16）：此前签名只有 include_archived，**kwargs 静默吞掉
    # limit/offset —— 真实数据 689 条下单帧 2,321,996 字节（≈2.3MB）打爆客户端帧上限，
    # 同连接后续调用全灭（表现为"断联"）。现显式声明 limit/offset/order。
    async def inbox_list(
        include_archived: bool = False,
        limit: int = INBOX_LIST_DEFAULT_LIMIT,
        offset: int = 0,
        order: str = "asc",
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        repo = context.personal_inbox_repository
        if not repo:
            return []
        norm_limit, norm_offset, norm_order = _coerce_page(limit, offset, order)
        if norm_limit != limit:
            logger.warning(
                "inbox.list limit=%r 越界/非法，已夹取为 %d（安全上限 %d）",
                limit,
                norm_limit,
                INBOX_LIST_MAX_LIMIT,
            )
        entries = await asyncio.to_thread(repo.list_entries, include_archived, norm_limit, norm_offset, norm_order)
        logger.info(
            "inbox.list include_archived=%s limit=%s offset=%d order=%s → returned=%d",
            include_archived,
            norm_limit,
            norm_offset,
            norm_order,
            len(entries),
        )
        return entries

    registry.register("inbox.list", inbox_list)

    # ── inbox.add ──
    async def inbox_add(content: str = "", **kwargs: Any) -> dict[str, Any]:
        repo = context.personal_inbox_repository
        if not repo:
            return {"error": "inbox repository unavailable"}
        entry_info: dict[str, Any] = {"content": content, **kwargs}
        entry_id = await asyncio.to_thread(repo.add_entry, entry_info)

        # Async auto-classification (fire-and-forget)
        spawn_bg_task(_classify_inbox_entry(entry_id, content, kwargs.get("title", "")))

        return {"id": entry_id, **entry_info}

    registry.register("inbox.add", inbox_add)

    # ── inbox.update ──
    async def inbox_update(entry_id: int | str = "", **kwargs: Any) -> dict[str, Any]:
        repo = context.personal_inbox_repository
        if not repo or not entry_id:
            return {"error": "entry_id required"}
        # 2026-10-15（T1 写旅程实测）：原签名标注 `entry_id: str`，而仓储
        # `get_entry(entry_id: int)`（repositories/_inbox.py:400）主键为 int
        # → 按标注传字符串的调用方（探针/MCP/agent）一律静默 "entry not found"；
        # 前端恰好传 int（IdeaPanel.test.tsx:161 `entry_id: 7`）才可用。
        # 修：类型归一（数字串 → int，其余原样）并修正标注。
        raw_id = str(entry_id).strip()
        key: Any = int(raw_id) if raw_id.lstrip("-").isdigit() else raw_id
        # S0-1：先探活。update_entry 对不存在的 id 静默空转（无命中信号），
        # 旧实现无条件 return {"success": True} → ghost update 假成功。
        entry = await asyncio.to_thread(repo.get_entry, key)
        if not entry:
            return {"success": False, "error": "entry not found"}
        await asyncio.to_thread(repo.update_entry, key, kwargs)
        return {"success": True}

    registry.register("inbox.update", inbox_update)

    # ── inbox.classify (AI 分类单条收件箱条目并写回) ──
    async def inbox_classify(entry_id: int = 0, **kwargs: Any) -> dict[str, Any]:
        """对单条 inbox entry 调用 AI 分类，结果写回 entry 的 ai_kind 字段。"""
        if not entry_id:
            return {"success": False, "error": "entry_id required"}
        repo = getattr(context, "personal_inbox_repository", None)
        if not repo:
            return {"success": False, "error": "inbox repository unavailable"}
        entry = await asyncio.to_thread(repo.get_entry, entry_id)
        if not entry:
            return {"success": False, "error": "entry not found"}

        ai_client = getattr(context, "ai_client", None)
        if not ai_client:
            return {"success": False, "error": "ai_client unavailable"}

        content = str(entry.get("content") or "")
        title = str(entry.get("title") or "")
        try:
            prompt = (
                "请对以下收件箱内容进行分类，返回 JSON（不要任何解释、不要 markdown 代码块）：\n"
                '{"kind":"note|task|reminder|idea|uncertain",'
                '"confidence":0.0-1.0,"reason":"简短理由",'
                '"suggested_action":"convert_to_note|convert_to_task|convert_to_reminder|keep|archive"}\n\n'
                f"标题：{title or '（无）'}\n内容：{content[:1000]}"
            )
            resp = await asyncio.to_thread(
                ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=300,
            )
            text = (resp or "").strip()
            if text.startswith("```"):
                text = text.removeprefix("```json").removeprefix("```")
                if text.endswith("```"):
                    text = text.removesuffix("```")
                text = text.strip()
            import json as _json

            brace = text.find("{")
            if brace < 0:
                return {"success": False, "error": "ai returned non-json"}
            parsed = _json.loads(text[brace:])
            if not isinstance(parsed, dict):
                return {"success": False, "error": "ai returned invalid json"}

            kind = str(parsed.get("kind") or "uncertain")
            await asyncio.to_thread(
                repo.update_entry,
                entry_id,
                {
                    "ai_kind": kind,
                    "ai_confidence": parsed.get("confidence"),
                    "ai_reason": parsed.get("reason"),
                    "ai_suggested_action": parsed.get("suggested_action"),
                },
            )
            return {
                "success": True,
                "kind": kind,
                "confidence": parsed.get("confidence"),
                "reason": parsed.get("reason"),
                "suggested_action": parsed.get("suggested_action"),
            }
        except Exception as exc:
            logger.warning("inbox.classify failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("inbox.classify", inbox_classify)

    # ── inbox.relay (跨设备投递到收件箱) ──
    # 手机端 readingClient.ts relayQuickCapture 调用，期望返回 {id, status}
    async def inbox_relay(
        content: str = "",
        title: str = "",
        source: str = "mobile_quick_capture",
        device_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """将跨设备内容投递到桌面端收件箱。

        与 inbox.add 的区别：relay 专用于跨设备场景，会记录来源设备 ID，
        并触发异步分类。返回 {id, status} 供手机端确认。

        Args:
            content: 内容主体
            title: 标题（可选，默认取 content 前 50 字）
            source: 来源标识，默认 'mobile_quick_capture'
            device_id: 来源设备 ID（可选）
        """
        repo = context.personal_inbox_repository
        if not repo:
            return {"id": "", "status": "error", "error": "inbox repository unavailable"}
        if not content or not content.strip():
            return {"id": "", "status": "error", "error": "content is required"}
        entry_info: dict[str, Any] = {
            "content": content,
            "title": title or content[:50],
            "source": source or "mobile_quick_capture",
        }
        if device_id:
            entry_info["metadata"] = {"device_id": device_id, "relayed": True}
        try:
            entry_id = await asyncio.to_thread(repo.add_entry, entry_info)
            # 异步分类（非阻塞）— 保存强引用防止 GC 回收
            spawn_bg_task(_classify_inbox_entry(entry_id, content, entry_info["title"]))
            return {"id": str(entry_id), "status": "ok"}
        except Exception as exc:
            logger.warning("inbox.relay failed: %s", exc)
            return {"id": "", "status": "error", "error": str(exc)}

    registry.register("inbox.relay", inbox_relay)

    # ── inbox.respond_confirmation ──
    # 前端订阅 inbox.delivered 事件后，识别 metadata.awaiting_response=True
    # 的条目弹出确认弹窗；用户点击 approve/reject 后调用此方法写回响应。
    # _agent_chain_confirm_callback 在另一侧轮询 check_confirmation_response 拿到结果。
    async def inbox_respond_confirmation(
        confirm_id: str = "",
        action: str = "",
        reason: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """P1-4: 用户对 Agent 链确认请求的响应。

        Args:
            confirm_id: 确认请求 ID（来自 inbox.delivered 事件 payload.metadata.confirm_id）
            action: ``approve`` 或 ``reject``
            reason: 用户给出的理由（可选）

        Returns:
            ``{"recorded": bool, "confirm_id": str, "action": str, "error": str}``
        """
        if not confirm_id:
            return {"recorded": False, "error": "confirm_id required"}
        if action not in ("approve", "reject"):
            return {"recorded": False, "error": f"invalid action: {action}（必须 approve/reject）"}

        inbox_svc = getattr(context, "assistant_inbox_service", None)
        if inbox_svc is None:
            return {"recorded": False, "error": "assistant_inbox_service unavailable"}

        try:
            result = await asyncio.to_thread(
                inbox_svc.respond_to_confirmation,
                confirm_id=confirm_id,
                action=action,
                reason=reason,
            )
            logger.info(
                "P1-4 用户确认响应: confirm_id=%s action=%s recorded=%s",
                confirm_id,
                action,
                result.get("recorded"),
            )
            return result
        except Exception as exc:
            logger.warning("inbox.respond_confirmation failed: %s", exc)
            return {"recorded": False, "error": str(exc)}

    # @deprecated (S11, 2026-08-07): 僵尸接口，前端未调用。见 UNUSED_METHODS_BASELINE。
    registry.register("inbox.respond_confirmation", inbox_respond_confirmation)

    # ── inbox.list_confirmations ──
    # 前端启动时调用此方法获取所有未响应的确认请求，重建 ApprovalModal 状态
    # （避免前端刷新后丢失正在等待的确认弹窗）。
    async def inbox_list_confirmations(
        include_responded: bool = False,
        limit: int = 50,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """P1-4: 列出待响应的人工确认请求。

        Args:
            include_responded: 是否包含已响应的（默认 False，只返回 awaiting_response=True）
            limit: 最多返回条数，默认 50

        Returns:
            ``[{confirm_id, title, content, risk_level, chain_id, node_id,
                detail, timeout_seconds, created_at, awaiting_response}, ...]``
        """
        inbox_svc = getattr(context, "assistant_inbox_service", None)
        if inbox_svc is None:
            return []

        def _list() -> list[dict[str, Any]]:
            items = inbox_svc.list_items(
                source="confirmation_request",
                limit=clamp_limit(limit, default=50),
            )
            result: list[dict[str, Any]] = []
            for item in items:
                metadata = item.get("metadata") or {}
                responded = inbox_svc.check_confirmation_response(metadata.get("confirm_id", "")) is not None
                if not include_responded and responded:
                    continue
                result.append(
                    {
                        "confirm_id": metadata.get("confirm_id", ""),
                        "title": item.get("title", ""),
                        "content": item.get("content", ""),
                        "risk_level": metadata.get("risk_level", "medium"),
                        "chain_id": metadata.get("chain_id", ""),
                        "node_id": metadata.get("node_id", ""),
                        "detail": metadata.get("detail", ""),
                        "timeout_seconds": metadata.get("timeout_seconds", 60),
                        "created_at": item.get("created_at", ""),
                        "awaiting_response": not responded,
                    }
                )
            return result

        try:
            return await asyncio.to_thread(_list)
        except Exception as exc:
            logger.warning("inbox.list_confirmations failed: %s", exc)
            return []

    # @deprecated (S11, 2026-08-07): 僵尸接口，前端未调用。见 UNUSED_METHODS_BASELINE。
    registry.register("inbox.list_confirmations", inbox_list_confirmations)
