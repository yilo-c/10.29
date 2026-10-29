"""Notes-related JSON-RPC handlers: list, create, get, update, delete, auto_tag, generate_summary."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from backend.handlers._cascade_cleanup import cascade_delete_note, report_cascade_failures
from backend.handlers._list_guard import cap_list, clamp_limit
from backend.handlers._wrap import CODE_AI_CONN, CODE_EVT_INT, CODE_STO_DATA, degrade
from backend.method_registry import RISK_R4
from core.events import AppEvent, event_bus
from core.settings_keys import KEY_AI_AUTO_TAG_ENABLED, KEY_AI_AUTO_TAG_THRESHOLD

logger = logging.getLogger("siyi.backend.handlers.notes")

# F-1: 后台 AI 自动打标任务强引用集合，防止 create_task 被 GC 回收
_pending_bg_tasks: set[asyncio.Task] = set()


def _find_note_row(repo, note_id: int | str, include_deleted: bool = False) -> dict[str, Any] | None:
    """按候选 id（原值/int/str 归一）查找笔记行，返回真实 dict（含 id）。

    - 默认走 ``get_note``（对软删笔记返回 None —— 防幽灵读语义，不得改）；
    - ``include_deleted=True`` 时改走 ``list_notes(include_deleted=True)``
      直查取值路径（参考 notes.list_trashed / audit_rollback QUA-4 同款绕法），
      可命中软删行 —— permanent_delete 必须用它，否则回收站条目恒探活失败。
    """
    candidates: list[Any] = [note_id]
    try:
        candidates.append(int(note_id))
    except (TypeError, ValueError):
        pass
    # str() 对任何对象都不抛异常，无需再包 try —— 原 except Exception: pass
    # 是本文件唯一真静默广义异常点（S2 棘轮治理，2026-09-16）。
    candidates.append(str(note_id))
    if include_deleted:
        try:
            all_notes = repo.list_notes(include_deleted=True)
        except TypeError:
            # 旧式仓储不支持 include_deleted 形参
            all_notes = repo.list_notes()
        for cid in candidates:
            for note in all_notes:
                if isinstance(note, dict) and note.get("id") == cid:
                    return note
        return None
    for cid in candidates:
        note = repo.get_note(cid)
        if note is not None:
            return note
    return None


def _resolve_note_id(repo, note_id: int | str, include_deleted: bool = False) -> int | str | None:
    """QUA-3：int/str 类型归一命中真实笔记 id；未命中返回 None。

    RPC schema 允许 string id 而存储 id 为 int（repo 严格 == 不命中），
    必须先归一再操作，杜绝「静默空转仍返回成功」的假成功。
    ``include_deleted`` 语义见 :func:`_find_note_row`（S0-2）。
    """
    note = _find_note_row(repo, note_id, include_deleted=include_deleted)
    return note.get("id") if note is not None else None


def register_notes(registry, context) -> None:
    """Register notes-related JSON-RPC methods."""

    def _notify_changed(action: str, note_ids: list[str] | None = None) -> None:
        """Push notes.changed event to notify frontend to refresh."""
        try:
            event_bus.emit(
                AppEvent(
                    "notes.changed",
                    {
                        "action": action,
                        "note_ids": note_ids or [],
                    },
                )
            )
        except Exception:
            degrade(logger, CODE_EVT_INT, "notes.changed event emit failed")

    async def _emit_related_memories(note_content: str) -> None:
        """T7.2 记忆回响：保存笔记后检索 top3 相关旧记忆并广播事件。

        复用聊天侧 RelatedMemories 的记忆检索能力（memory_index_service.recall），
        best-effort：检索或广播失败不阻塞笔记保存主流程。
        事件名 notes.related_memories，payload 供前端直接渲染。
        """
        query = str(note_content or "").strip()
        if not query:
            return
        try:
            mem_svc = getattr(context, "memory_index_service", None)
            if mem_svc is None or not hasattr(mem_svc, "recall"):
                return
            result = await asyncio.to_thread(lambda: mem_svc.recall(query, top_k=3))
            items = []
            if isinstance(result, dict):
                items = result.get("items") or []
            elif isinstance(result, list):
                items = result
            related = []
            for m in items[:3]:
                if not isinstance(m, dict):
                    continue
                related.append(
                    {
                        "id": m.get("id") or m.get("entry_id"),
                        "title": m.get("title") or m.get("content", "")[:40],
                        "content": (m.get("content") or "")[:200],
                        "source": m.get("source_type") or m.get("source", ""),
                    }
                )
            if not related:
                return
            event_bus.emit(
                AppEvent(
                    "notes.related_memories",
                    {"related_memories": related},
                )
            )
        except Exception:
            degrade(logger, CODE_EVT_INT, "related memories emit failed")

    async def _get_auto_tag_config() -> tuple[bool, float]:
        """读取 AI 自动打标开关与置信度阈值。"""
        settings = getattr(context, "settings_repository", None)
        if settings is None:
            return False, 0.7
        enabled = await asyncio.to_thread(
            settings.get_setting,
            KEY_AI_AUTO_TAG_ENABLED,
            False,
        )
        threshold = await asyncio.to_thread(
            settings.get_setting,
            KEY_AI_AUTO_TAG_THRESHOLD,
            0.7,
        )
        return bool(enabled), float(threshold or 0.7)

    async def _auto_tag_note_bg(note_id: int | str, content: str, threshold: float) -> None:
        """后台为笔记生成 AI 标签并应用（满足阈值时）。"""
        ai_client = getattr(context, "ai_client", None)
        repo = getattr(context, "personal_note_repository", None)
        if not ai_client or not repo:
            return
        if not content or not content.strip():
            return
        try:
            prompt = (
                "分析以下内容，返回 JSON（不要任何解释、不要 markdown 代码块）：\n"
                '{"tags": ["标签1", "标签2"], "confidence": 0.0-1.0}\n\n'
                f"{content[:2000]}"
            )
            result_text = await asyncio.to_thread(
                ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=120,
            )
            text = (result_text or "").strip()
            if text.startswith("```"):
                text = text.removeprefix("```json").removeprefix("```")
                if text.endswith("```"):
                    text = text.removesuffix("```")
                text = text.strip()

            tags: list[str] = []
            confidence = 1.0
            brace = text.find("{")
            if brace >= 0:
                try:
                    parsed = json.loads(text[brace:])
                    tags = [str(t).strip() for t in parsed.get("tags", []) if str(t).strip()]
                    confidence = float(parsed.get("confidence", 1.0))
                except Exception:
                    logger.exception("notes.auto_tag 解析 AI 返回的 JSON 标签失败，已回退为逗号分割")
                    tags = [t.strip() for t in text.split("，") if t.strip()]
            else:
                tags = [t.strip() for t in text.split("，") if t.strip()]

            tags = tags[:8]
            if not tags:
                return

            if confidence < threshold:
                event_bus.emit(
                    AppEvent(
                        "note.auto_tag_suggested",
                        {"note_id": str(note_id), "tags": tags, "applied": False},
                    )
                )
                return

            await asyncio.to_thread(repo.update_note, note_id, {"tags": tags})
            _notify_changed("auto_tag", [str(note_id)])
            event_bus.emit(
                AppEvent(
                    "note.auto_tag_suggested",
                    {"note_id": str(note_id), "tags": tags, "applied": True},
                )
            )
        except Exception:
            degrade(
                logger,
                CODE_AI_CONN,
                f"background auto-tag for note {note_id} failed",
            )

    # ── notes.auto_tag ──
    async def notes_auto_tag(content: str = "", **kwargs: Any) -> list[str]:
        """AI auto-tagging: analyze content and return relevant tags."""
        if not content or not content.strip():
            return []
        try:
            # P1 修复：complete_text 是同步 AI 调用（含网络请求），用 to_thread 避免阻塞事件循环
            result = await asyncio.to_thread(
                context.ai_client.complete_text,
                [
                    {
                        "role": "user",
                        "content": f"分析以下内容，返回 3-5 个中文标签（用逗号分隔）：\n\n{content[:2000]}",
                    }
                ],
                max_tokens=100,
            )
            if result:
                tags = [t.strip() for t in result.split("，") if t.strip()]
                return tags[:8]
        except Exception as exc:
            logger.warning("notes.auto_tag failed: %s", exc)
        return []

    registry.register("notes.auto_tag", notes_auto_tag)

    # ── notes.generate_summary ──
    async def notes_generate_summary(content: str = "", **kwargs: Any) -> str:
        """AI auto-summary: generate content summary."""
        if not content or not content.strip():
            return ""
        try:
            # P1 修复：complete_text 是同步 AI 调用（含网络请求），用 to_thread 避免阻塞事件循环
            result = await asyncio.to_thread(
                context.ai_client.complete_text,
                [
                    {
                        "role": "user",
                        "content": f"请用简洁的语言总结以下内容（50字以内）：\n\n{content[:3000]}",
                    }
                ],
                max_tokens=200,
            )
            return (result or "").strip()
        except Exception as exc:
            logger.warning("notes.generate_summary failed: %s", exc)
        return ""

    registry.register("notes.generate_summary", notes_generate_summary)

    # ── notes.list ──
    async def notes_list(
        limit: int = 50,
        include_empty: bool = True,
        folder: str = "",
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        repo = context.personal_note_repository
        if not repo:
            return []
        notes = await asyncio.to_thread(repo.list_notes)
        notes = [n if isinstance(n, dict) else {"id": str(getattr(n, "id", ""))} for n in notes]
        # B-11: 实现 AGENTS.md 方法表声明的 include_empty / folder 参数
        # （原实现只收 limit，两参数落入 **kwargs 被静默忽略 → 前端按文档传参无效）。
        # 空笔记定义与 notes.batch_delete 一致：content 为空或仅含空白。
        if not include_empty:
            notes = [n for n in notes if (n.get("content") or "").strip()]
        if folder:
            notes = [n for n in notes if (n.get("folder_path") or n.get("folder") or "") == folder]
        # 帧守卫（2026-10-15）：limit<=0 从"全量"收紧为默认 50，上限 clamp 2000
        limit = clamp_limit(limit, default=50)
        notes = notes[:limit]
        return notes

    registry.register("notes.list", notes_list)

    # ── notes.create ──
    async def notes_create(
        title: str = "",
        content: str = "",
        folder: str = "",
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        repo = context.personal_note_repository
        if not repo:
            return {"error": "note repository unavailable"}
        note_info: dict[str, Any] = {"title": title, "content": content}
        if "id" in kwargs:
            note_info["id"] = kwargs.pop("id")
        if folder:
            note_info["folder_path"] = folder
        if tags:
            note_info["tags"] = tags
        note_id = await asyncio.to_thread(repo.add_note, note_info)
        _notify_changed("create", [str(note_id)])

        # T7.2 记忆回响：保存后检索相关旧记忆推给前端（best-effort，不阻塞）
        try:
            await _emit_related_memories(content)
        except Exception:
            degrade(logger, CODE_EVT_INT, "related memories (create) failed")

        # F-1: 若未显式提供标签且开启自动打标，后台异步生成标签
        if not tags:
            try:
                enabled, threshold = await _get_auto_tag_config()
                if enabled:
                    task = asyncio.create_task(_auto_tag_note_bg(note_id, content, threshold))
                    _pending_bg_tasks.add(task)
                    task.add_done_callback(_pending_bg_tasks.discard)
            except Exception:
                degrade(logger, CODE_STO_DATA, "auto-tag scheduling failed")

        return {"id": note_id, **note_info}

    registry.register_with_schema(
        "notes.create",
        notes_create,
        schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
                "folder": {"type": "string"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "id": {"type": "string"},
            },
        },
    )

    # ── notes.get ──
    async def notes_get(note_id: int | str, **kwargs: Any) -> dict[str, Any] | None:
        repo = context.personal_note_repository
        if not repo:
            return None
        # P0 修复：使用 get_note 按 ID 直接查找，避免全量 list_notes 的 O(n) 扫描
        note = await asyncio.to_thread(repo.get_note, note_id)
        if note is not None:
            return note
        # N-B（QUA-3 收口）：RPC schema 允许 string id 而存储 id 为 int，
        # repo.get_note 严格 == 不命中 → str 传参恒 None（假「不存在」，
        # 与 update/delete 修复前同款病）。fast path 未命中时按 int/str
        # 归一取真实 id 再读；软删笔记经 get_note 幽灵读防护仍返回 None，
        # 语义不变。
        real_id = await asyncio.to_thread(_resolve_note_id, repo, note_id)
        if real_id is None:
            return None
        return await asyncio.to_thread(repo.get_note, real_id)

    registry.register_with_schema(
        "notes.get",
        notes_get,
        schema={
            "type": "object",
            "required": ["note_id"],
            "properties": {
                "note_id": {"type": ["string", "integer"]},
            },
        },
    )

    # ── notes.update ──
    async def notes_update(note_id: int | str, **new_info: Any) -> bool:
        repo = context.personal_note_repository
        if not repo:
            return False

        # S0-1（QUA-3）：先归一探活——更新不存在的 id 时仓库层过去静默空转，
        # handler 仍 return True，还触发 WriteGuard 生成 before=None 的假审计
        # 条目并广播不存在的变更。杜绝「静默空转仍返回成功」的假成功：
        # 未命中如实 False（不落审计、不广播）。
        real_id = await asyncio.to_thread(_resolve_note_id, repo, note_id)
        if real_id is None:
            return False
        note_id = real_id

        if "folder" in new_info and "folder_path" not in new_info:
            new_info["folder_path"] = new_info.pop("folder")

        # S15: 用 WriteGuard 包裹写操作，记录 before/after snapshot 供审计与回滚
        # P3 修复：整个写保护块（含审计轨迹读写）在工作线程执行——
        # trail 的同步 JSON 序列化不得阻塞事件循环（否则心跳无响应、前端断连）。
        write_guard = getattr(context, "write_guard", None)
        if write_guard is not None:

            def _guarded_update() -> Any:
                try:
                    before = repo.get_note(note_id)
                except Exception:
                    logger.exception("notes.update 读取更新前笔记快照失败")
                    before = None
                hit: Any = None
                with write_guard.protect(
                    "note",
                    str(note_id),
                    actor="user",
                    action_id="notes.update",
                    operation="update",
                    before_snapshot=before if isinstance(before, dict) else None,
                ) as guard:
                    hit = repo.update_note(note_id, new_info)
                    try:
                        after = repo.get_note(note_id)
                        guard.set_after(after if isinstance(after, dict) else None)
                    except Exception:
                        logger.exception("notes.update 读取更新后笔记快照失败")
                        pass
                return hit

            # S0-1：消费 update_note 的命中信号（bool）。显式 False（探活与写入
            # 之间笔记被并发删除等竞态）→ 如实失败，不广播不回响。
            # 仅认 False：旧式 None 返回实现（协议扩展前的第三方/测试仓储）不误伤。
            hit = await asyncio.to_thread(_guarded_update)
            if hit is False:
                logger.warning("notes.update: repo 报未命中 note_id=%s（竞态），如实返回 False", note_id)
                return False
            _notify_changed("update", [str(note_id)])
            # T7.2 记忆回响：更新后同样推送相关记忆（best-effort）
            try:
                await _emit_related_memories(str(new_info.get("content", "")))
            except Exception:
                degrade(logger, CODE_EVT_INT, "related memories (update) failed")
            return True

        # 审计未启用时的回退路径
        hit = await asyncio.to_thread(repo.update_note, note_id, new_info)
        if hit is False:
            logger.warning("notes.update: repo 报未命中 note_id=%s（竞态），如实返回 False", note_id)
            return False
        _notify_changed("update", [str(note_id)])
        # T7.2 记忆回响
        try:
            await _emit_related_memories(str(new_info.get("content", "")))
        except Exception:
            degrade(logger, CODE_EVT_INT, "related memories (update fallback) failed")
        return True

    registry.register_with_schema(
        "notes.update",
        notes_update,
        schema={
            "type": "object",
            "required": ["note_id"],
            "properties": {
                "note_id": {"type": ["integer", "string"]},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
    )

    # ── notes.delete ──
    async def notes_delete(note_id: int | str, **kwargs: Any) -> bool:
        repo = context.personal_note_repository
        if not repo:
            return False

        # QUA-3：int/str 类型归一命中真实记录；未命中如实 False（不落 ghost 审计、不广播、不级联）
        real_id = await asyncio.to_thread(_resolve_note_id, repo, note_id)
        if real_id is None:
            return False
        note_id = real_id

        # S15: 软删除前记录 before snapshot，支持后续回滚恢复
        # P3 修复：写保护块整体在工作线程执行，避免审计 JSON 阻塞事件循环。
        write_guard = getattr(context, "write_guard", None)
        if write_guard is not None:

            def _guarded_delete() -> None:
                try:
                    before = repo.get_note(note_id)
                except Exception:
                    logger.exception("notes.delete 读取删除前笔记快照失败")
                    before = None
                with write_guard.protect(
                    "note",
                    str(note_id),
                    actor="user",
                    action_id="notes.delete",
                    operation="delete",
                    before_snapshot=before if isinstance(before, dict) else None,
                ) as guard:
                    repo.delete_note(note_id)
                    # 软删除后记录 after=None 表示已删除
                    guard.set_after(None)

            await asyncio.to_thread(_guarded_delete)
            _notify_changed("delete", [str(note_id)])
            # P1.5: 级联清理 KG / 记忆索引 / 结构化记忆 relations
            # P1-4（2026-09-18）：接住返回值并上报失败项。旧代码丢弃返回值 →
            # "图谱 save failed"（内存删了、磁盘没落盘，重启复活）在 RPC 层完全消失。
            # 注意返回仍为 True：True 的语义是"笔记已删除"，级联是 best-effort 附加清理，
            # 改判 False 会把"删除失败"错误地告诉用户。可观测性走台账 + system.integrity。
            report_cascade_failures("notes.delete", note_id, await cascade_delete_note(context, note_id))
            return True

        # 审计未启用时的回退路径
        await asyncio.to_thread(repo.delete_note, note_id)
        _notify_changed("delete", [str(note_id)])
        # P1.5: 级联清理
        report_cascade_failures("notes.delete", note_id, await cascade_delete_note(context, note_id))
        return True

    registry.register_with_schema(
        "notes.delete",
        notes_delete,
        schema={
            "type": "object",
            "required": ["note_id"],
            "properties": {
                "note_id": {"type": ["integer", "string"]},
            },
        },
        risk=RISK_R4,
    )

    # ── notes.batch_delete ──
    # 批量软删除笔记（供AI整理笔记使用，避免单条频率限制）
    async def notes_batch_delete(
        note_ids: list[str | int] | None = None,
        empty_only: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        repo = context.personal_note_repository
        if not repo:
            return {"error": "note repository unavailable", "deleted_count": 0, "failed": []}

        ids_to_delete: list[Any] = []
        if note_ids:
            ids_to_delete = list(note_ids)
        elif empty_only:
            all_notes = await asyncio.to_thread(repo.list_notes)
            for n in all_notes:
                content = str(n.get("content") or "").strip()
                if not content:
                    ids_to_delete.append(n.get("id"))
        else:
            return {"error": "must provide note_ids or set empty_only=true", "deleted_count": 0, "failed": []}

        # B8 修复：批量删除增加 WriteGuard 审计保护，记录 before snapshot
        # P3 修复：每条的写保护块在工作线程执行，避免审计 JSON 阻塞事件循环。
        write_guard = getattr(context, "write_guard", None)
        deleted: list[Any] = []
        failed: list[Any] = []

        def _guarded_delete_one(nid: int | str) -> None:
            try:
                before = repo.get_note(nid)
            except Exception:
                logger.exception("notes.batch_delete 读取删除前笔记快照失败")
                before = None
            with write_guard.protect(
                "note",
                str(nid),
                actor="user",
                action_id="notes.batch_delete",
                operation="delete",
                before_snapshot=before if isinstance(before, dict) else None,
            ) as guard:
                repo.delete_note(nid)
                guard.set_after(None)

        for nid in ids_to_delete:
            # QUA-3：int/str 类型归一命中真实记录；未命中如实进 failed（不混入 deleted_count）
            real_id = await asyncio.to_thread(_resolve_note_id, repo, nid)
            if real_id is None:
                failed.append(nid)
                continue
            try:
                if write_guard is not None:
                    await asyncio.to_thread(_guarded_delete_one, real_id)
                else:
                    await asyncio.to_thread(repo.delete_note, real_id)
                deleted.append(real_id)
            except Exception as exc:
                logger.warning("notes.batch_delete failed for %s: %s", nid, exc)
                failed.append(nid)

        if deleted:
            _notify_changed("batch_delete", [str(x) for x in deleted])
            # P1.5: 批量级联清理
            for nid in deleted:
                try:
                    # P1-4（2026-09-18）：本处原本只 except 异常、仍丢弃正常返回值——
                    # 即"抛异常"可见、"如实返回 failed"不可见，只补了一半。
                    report_cascade_failures("notes.batch_delete", nid, await cascade_delete_note(context, nid))
                except Exception as exc:
                    logger.warning("cascade_delete_note(%s) failed: %s", nid, exc, exc_info=True)
        return {"deleted_count": len(deleted), "note_ids": [str(x) for x in deleted], "failed": failed}

    registry.register("notes.batch_delete", notes_batch_delete, risk=RISK_R4)

    # ── notes.list_trashed ──
    # 回收站列表：返回已软删除的笔记（deleted_at 非空）
    async def notes_list_trashed(**kwargs: Any) -> list[dict[str, Any]]:
        repo = context.personal_note_repository
        if not repo:
            return []
        all_notes = await asyncio.to_thread(repo.list_notes, True)
        trashed = [n for n in all_notes if n.get("deleted_at")]
        # 帧守卫（2026-10-15）：裸 list 契约不变，超 2000 条截断 + 告警
        return cap_list(trashed, method="notes.list_trashed")

    registry.register("notes.list_trashed", notes_list_trashed)

    # ── notes.restore ──
    # 从回收站恢复笔记（清除 deleted_at 标记）
    async def notes_restore(note_id: int | str = "", **kwargs: Any) -> bool:
        repo = context.personal_note_repository
        if not repo:
            return False
        if not note_id:
            # P2 修复（rpc-census-2026-08-30）：空参兜底——RPC 面由下方 schema
            # 拦截（-32602）；此处覆盖绕过 registry 的直接调用路径。
            return False
        await asyncio.to_thread(repo.restore_note, note_id)
        _notify_changed("restore", [str(note_id)])
        return True

    # P2 修复：note_id 必填——与 notes.get 同款注册层 schema，空参调用返回
    # 干净 -32602 InvalidParamsError（原为 dispatch(**{}) 展开时 TypeError）。
    registry.register_with_schema(
        "notes.restore",
        notes_restore,
        schema={
            "type": "object",
            "required": ["note_id"],
            "properties": {
                "note_id": {"type": ["string", "integer"]},
            },
        },
    )

    # ── notes.permanent_delete ──
    # 永久删除笔记（硬删除，不可恢复）——仅允许针对回收站中的软删笔记。
    async def notes_permanent_delete(note_id: int | str, **kwargs: Any) -> bool:
        repo = context.personal_note_repository
        if not repo:
            return False
        # QUA-3：int/str 类型归一命中真实记录；未命中如实 False（不广播、不级联）
        # S0-2：permanent_delete 的对象**正是**回收站里的软删笔记，而 get_note
        # 对软删笔记返回 None——探活必须 include_deleted=True，否则恒不命中恒 False。
        row = await asyncio.to_thread(_find_note_row, repo, note_id, True)
        if row is None:
            return False
        # S0-2：活笔记拒绝永久删除——它没进过回收站，调用方语义错误；
        # 顺带修复修复前「唯一能删的是活笔记」的倒挂行为。
        if not row.get("deleted_at"):
            return False
        note_id = row.get("id")
        await asyncio.to_thread(repo.delete_note, note_id, False)
        _notify_changed("permanent_delete", [str(note_id)])
        # P1.5: 永久删除也需级联清理外部索引
        # P1-4（2026-09-18）：接住返回值并上报失败项（同 notes.delete 的理由）
        report_cascade_failures("notes.permanent_delete", note_id, await cascade_delete_note(context, note_id))
        return True

    registry.register("notes.permanent_delete", notes_permanent_delete, risk=RISK_R4)

    # ── notes.ai_assist ──
    # F-6: AI 写作助手 — 根据 action 对选中或当前笔记文本生成建议
    # 注意：handler 参数名不能叫 context，否则会遮蔽 register_notes 注入的 DI context。
    _app_context = context
    _AI_ASSIST_PROMPTS: dict[str, str] = {
        "continue": "请根据上下文继续写作，保持风格一致。只输出续写内容，不要解释。\n\n上下文：\n{context}\n\n已选文本：\n{selected_text}",
        "shorter": "请将以下文本改写得更加简短精炼，保持原意不变。只输出改写后的文本，不要解释。\n\n{selected_text}",
        "longer": "请将以下文本扩写，增加细节和条理，保持原意。只输出扩写后的文本，不要解释。\n\n{selected_text}",
        "summarize": "请用简洁的语言总结以下文本（50字以内）。只输出总结，不要解释。\n\n{selected_text}",
        "fix_grammar": "请修正以下文本中的语法错误、错别字和不通顺之处。只输出修正后的文本，不要解释。\n\n{selected_text}",
        "change_tone": "请调整以下文本的语气{tone_hint}。只输出调整后的文本，不要解释。\n\n{selected_text}",
    }

    def _build_ai_assist_prompt(action: str, selected_text: str, context: str) -> str:
        template = _AI_ASSIST_PROMPTS.get(action)
        if not template:
            template = _AI_ASSIST_PROMPTS["continue"]
        tone_hint = ""
        if action == "change_tone" and context:
            # context 在 change_tone 场景下可携带目标语气，如 "更正式"
            tone_hint = f"，使其{context.strip()}"
        return template.format(
            selected_text=selected_text or "",
            context=context or "",
            tone_hint=tone_hint,
        )

    async def notes_ai_assist(
        note_id: int | str = "",
        action: str = "",
        selected_text: str = "",
        context: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """AI writing assistant: generate suggested text for the given action."""
        ai_client = getattr(_app_context, "ai_client", None)
        if not ai_client:
            return {"error": "ai client unavailable", "suggested_text": ""}
        if not action:
            return {"error": "action required", "suggested_text": ""}

        prompt = _build_ai_assist_prompt(action, selected_text, context)
        try:
            # P1: complete_text 是同步 AI 调用（含网络请求），用 to_thread 避免阻塞事件循环
            result = await asyncio.to_thread(
                ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=800,
            )
            return {"suggested_text": (result or "").strip()}
        except Exception as exc:
            logger.warning("notes.ai_assist failed: %s", exc)
            return {"error": str(exc), "suggested_text": ""}

    registry.register("notes.ai_assist", notes_ai_assist)

    # ── notes.asset.put / notes.asset.get（U0 方案 A：图片相对路径资产目录，2026-09-19）──
    # 图片落 <desknotes-root>/assets/，md 写标准相对路径 `assets/<name>`（Typora/Obsidian 兼容）。
    # 防护：basename 规范化（禁 ..、路径分隔符、非常用字符）+ 扩展名白名单 + 10MB 上限。
    _ASSET_EXT_MIME = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".bmp": "image/bmp",
    }
    _ASSET_MAX_BYTES = 10 * 1024 * 1024
    import base64 as _b64
    import os as _os
    import re as _re
    from pathlib import Path as _Path

    def _assets_dir() -> _Path:
        """资产目录跟随笔记的实际存储（U0 治本，2026-09-19）：
        desk-notes 桥可用 → <desknotes-root>/assets（Typora 兼容，md 旁边）；
        桥禁用/不可达（harness / 纯 SQLite 模式）→ <SIYI_DATA_DIR>/assets。
        两个形态都保证 md 引用的 `assets/<name>` 与存储同源。"""
        from app_runtime.desknotes_path import resolve_desknotes_dir

        root = resolve_desknotes_dir()
        if not root:
            data_dir = _os.environ.get("SIYI_DATA_DIR", "").strip()
            if not data_dir:
                raise FileNotFoundError("asset dir unavailable: no desknotes root and no SIYI_DATA_DIR")
            root = data_dir
        d = _Path(root) / "assets"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _safe_asset_name(name: str) -> str:
        if not name or ".." in name:
            return ""
        base = _os.path.basename(name.replace("\\", "/"))
        if not base or base in (".", ".."):
            return ""
        if not _re.fullmatch(r"[A-Za-z0-9._\-]+", base):
            return ""
        return base

    async def notes_asset_put(name: str, data_base64: str) -> dict[str, Any]:
        safe = _safe_asset_name(str(name))
        if not safe:
            return {"success": False, "error": "invalid asset name"}
        ext = _os.path.splitext(safe)[1].lower()
        if ext not in _ASSET_EXT_MIME:
            return {"success": False, "error": f"unsupported asset type: {ext}"}
        try:
            blob = _b64.b64decode(str(data_base64), validate=False)
        except Exception:
            return {"success": False, "error": "invalid base64 payload"}
        if not blob:
            return {"success": False, "error": "empty asset"}
        if len(blob) > _ASSET_MAX_BYTES:
            return {"success": False, "error": "asset exceeds 10MB limit"}

        def _write() -> dict[str, Any]:
            target = _assets_dir() / safe
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(blob)
            _os.replace(tmp, target)
            return {"success": True, "name": safe, "rel_path": f"assets/{safe}", "size": len(blob)}

        return await asyncio.to_thread(_write)

    async def notes_asset_get(name: str) -> dict[str, Any]:
        safe = _safe_asset_name(str(name))
        if not safe:
            return {"success": False, "error": "invalid asset name"}

        def _read() -> dict[str, Any]:
            target = _assets_dir() / safe
            if not target.is_file():
                return {"success": False, "error": "asset not found"}
            blob = target.read_bytes()
            mime = _ASSET_EXT_MIME.get(_os.path.splitext(safe)[1].lower(), "application/octet-stream")
            return {"success": True, "name": safe, "mime": mime, "data": _b64.b64encode(blob).decode("ascii")}

        return await asyncio.to_thread(_read)

    registry.register_with_schema(
        "notes.asset.put",
        notes_asset_put,
        schema={
            "type": "object",
            "required": ["name", "data_base64"],
            "properties": {
                "name": {"type": "string"},
                "data_base64": {"type": "string"},
            },
        },
    )
    registry.register_with_schema(
        "notes.asset.get",
        notes_asset_get,
        schema={
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
            },
        },
    )
