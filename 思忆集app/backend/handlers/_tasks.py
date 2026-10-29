"""Tasks-related JSON-RPC handlers: list, create, get, update, delete, toggle."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.handlers._cascade_cleanup import cascade_delete_task, report_cascade_failures
from backend.handlers._list_guard import cap_list, clamp_limit
from backend.method_registry import RISK_R4

logger = logging.getLogger("siyi.backend.handlers.tasks")


def _find_task_row(repo, task_id: int | str, include_deleted: bool = False) -> dict[str, Any] | None:
    """按候选 id（原值/int/str 归一）查找任务行，返回真实 dict（含 id）。

    对照 _notes._find_note_row（S0-2）。get_task 对软删任务返回 None（防幽灵读，
    与运行时 desknotes_task_adapter 同款语义）——permanent_delete 探活必须走
    include_deleted=True 的 list_tasks 直查，否则回收站条目恒探活失败。
    """
    candidates: list[Any] = [task_id]
    try:
        candidates.append(int(task_id))
    except (TypeError, ValueError):
        pass
    try:
        candidates.append(str(task_id))
    except Exception:
        pass
    if include_deleted:
        try:
            all_tasks = repo.list_tasks(include_deleted=True)
        except TypeError:
            all_tasks = repo.list_tasks()
        for cid in candidates:
            for task in all_tasks:
                if isinstance(task, dict) and task.get("id") == cid:
                    return task
        return None
    for cid in candidates:
        row = repo.get_task(cid)
        if isinstance(row, dict) and row.get("id") is not None:
            return row
    return None


def _resolve_task_id(repo, task_id: int | str, include_deleted: bool = False) -> int | str | None:
    """QUA-3：int/str 归一命中真实任务 id；未命中返回 None。含 include_deleted 语义。"""
    row = _find_task_row(repo, task_id, include_deleted=include_deleted)
    return row.get("id") if row is not None else None


def register_tasks(registry, context) -> None:
    """Register tasks-related JSON-RPC methods."""

    # ── tasks.list ──
    async def tasks_list(limit: int = 50, **kwargs: Any) -> list[dict[str, Any]]:
        repo = context.personal_task_repository
        if not repo:
            return []
        tasks = await asyncio.to_thread(repo.list_tasks)
        # 帧守卫（2026-10-15）：limit<=0 从"全量"收紧为默认 50，上限 clamp 2000
        tasks = tasks[: clamp_limit(limit, default=50)]
        return [t if isinstance(t, dict) else {"id": str(getattr(t, "id", ""))} for t in tasks]

    registry.register("tasks.list", tasks_list)

    # ── tasks.list_trashed ──
    # 回收站列表：返回已软删除的任务（deleted_at 非空）。
    # 批次4 b4-m2：手机端 contentSync 靠它拉取任务墓碑做 LWW 删除传播，
    # 与 notes.list_trashed 对称（见 _notes.py）。
    async def tasks_list_trashed(**kwargs: Any) -> list[dict[str, Any]]:
        repo = context.personal_task_repository
        if not repo:
            return []
        all_tasks = await asyncio.to_thread(repo.list_tasks, True)
        trashed = [t for t in all_tasks if t.get("deleted_at")]
        # 帧守卫（2026-10-15）：裸 list 契约不变，超 2000 条截断 + 告警
        return cap_list(trashed, method="tasks.list_trashed")

    registry.register("tasks.list_trashed", tasks_list_trashed)

    # ── tasks.create ──
    async def tasks_create(title: str = "", **kwargs: Any) -> dict[str, Any]:
        repo = context.personal_task_repository
        if not repo:
            return {"error": "task repository unavailable"}
        # L8 手机/桌面 schema 对齐：手机端 pushTask 传 {text, done, priority}，
        # 完成态字段名是 done；桌面端仓库只认 is_completed/completed。
        # 显式映射 done → completed，保证"已完成"任务拉回后 completed=True。
        if "done" in kwargs and "is_completed" not in kwargs and "completed" not in kwargs:
            kwargs["completed"] = bool(kwargs.pop("done"))
        task_info: dict[str, Any] = {"title": title, **kwargs}
        task_id = await asyncio.to_thread(repo.add_task, task_info)
        return {"id": task_id, **task_info}

    registry.register_with_schema(
        "tasks.create",
        tasks_create,
        schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                # L8 手机端契约：pushTask 传 {text, done, priority}；
                # done 在 handler 内映射到 completed（additionalProperties 保持放行，
                # 允许 text 等移动端扩展字段透传落库）。
                "done": {"type": "boolean"},
                "completed": {"type": "boolean"},
                "is_completed": {"type": "boolean"},
                "priority": {"type": ["string", "integer"]},
                "due_date": {"type": "string"},
                "description": {"type": "string"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
    )

    # ── tasks.get ──
    async def tasks_get(task_id: int | str = 0, **kwargs: Any) -> dict[str, Any] | None:
        if not task_id:
            return {"error": "task_id required"}
        repo = context.personal_task_repository
        if not repo:
            # 与其他 handler 一致：repo 不可用时返回 None 而非报错 dict
            return None
        # P0 修复：使用 get_task 按 ID 直接查找，避免全量 list_tasks 的 O(n) 扫描
        return await asyncio.to_thread(repo.get_task, task_id)

    registry.register("tasks.get", tasks_get)

    # ── tasks.update ──
    async def tasks_update(task_id: int | str, **new_info: Any) -> bool:
        repo = context.personal_task_repository
        if not repo:
            return False

        # S0-1 同款（task 面此前为假成功温床）：先归一探活（默认不带软删行）——
        # 不存在/回收站软删均探不到 → 如实 False，不进 WriteGuard（免 before=None
        # 假审计）。软删任务 get_task 恒 None（防幽灵读）即天然拒绝回收站编辑（AN-2）。
        real_id = await asyncio.to_thread(_resolve_task_id, repo, task_id)
        if real_id is None:
            return False
        task_id = real_id

        # C4 修复：与 notes.update 一致，增加 WriteGuard 审计保护
        # P3 修复：写保护块（含审计轨迹读写）整体在工作线程执行，
        # 避免同步 JSON 序列化阻塞事件循环导致前端心跳断连。
        write_guard = getattr(context, "write_guard", None)
        if write_guard is not None:

            def _guarded_update() -> Any:
                try:
                    before = repo.get_task(task_id)
                except Exception:
                    logger.exception("tasks.update 读取更新前任务快照失败")
                    before = None
                hit: Any = None
                with write_guard.protect(
                    "task",
                    str(task_id),
                    actor="user",
                    action_id="tasks.update",
                    operation="update",
                    before_snapshot=before if isinstance(before, dict) else None,
                ) as guard:
                    hit = repo.update_task(task_id, new_info)
                    try:
                        after = repo.get_task(task_id)
                        guard.set_after(after if isinstance(after, dict) else None)
                    except Exception:
                        logger.exception("tasks.update 读取更新后任务快照失败")
                return hit

            hit = await asyncio.to_thread(_guarded_update)
        else:
            hit = await asyncio.to_thread(repo.update_task, task_id, new_info)

        # S1-2：消费 update_task 命中信号。仅认 False（旧式 None 实现不误伤）——
        # 未命中/回收站拒编辑（AN-2）不得再无条件 return True。
        if hit is False:
            logger.warning("tasks.update: repo 报未命中或回收站拒编辑 task_id=%s", task_id)
            return False
        return True

    registry.register_with_schema(
        "tasks.update",
        tasks_update,
        schema={
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": ["integer", "string"]},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "completed": {"type": "boolean"},
                "due": {"type": "string"},
            },
        },
    )

    # ── tasks.delete ──
    async def tasks_delete(task_id: int | str = 0, **kwargs: Any) -> bool:
        repo = context.personal_task_repository
        if not repo or not task_id:
            return False

        # 2026-10-15（T1 实测修复）：delete_task 在 id 映射失败时**静默 return**
        # （desknotes_task_adapter.delete_task: `uuid = to_uuid(...); if not uuid: return`），
        # 旧实现无条件 return True → 上层（含 sandbox.approve 的 R4 重放）以为删除成功，
        # 用户看到"批准成功"而任务还在（实测 3 批产物全残留在真实库）。
        # 修法：删除后复核——任务仍存在且无墓碑位 → 如实返回 False。
        def _verify_deleted() -> bool:
            try:
                still = repo.get_task(task_id)
            except Exception:
                logger.exception("tasks.delete 复核失败（视作未删除）")
                return False
            if not still:
                return True
            deleted_at = str(still.get("deleted_at") or "").strip()
            return bool(deleted_at)

        # C4 修复：与 notes.delete 一致，增加 WriteGuard 审计保护
        # P3 修复：写保护块整体在工作线程执行，避免审计 JSON 阻塞事件循环。
        write_guard = getattr(context, "write_guard", None)
        if write_guard is not None:

            def _guarded_delete() -> None:
                try:
                    before = repo.get_task(task_id)
                except Exception:
                    logger.exception("tasks.delete 读取删除前任务快照失败")
                    before = None
                with write_guard.protect(
                    "task",
                    str(task_id),
                    actor="user",
                    action_id="tasks.delete",
                    operation="delete",
                    before_snapshot=before if isinstance(before, dict) else None,
                ) as guard:
                    repo.delete_task(task_id)
                    guard.set_after(None)

            await asyncio.to_thread(_guarded_delete)
            # P1.5: 级联清理 cron / reminder / 记忆索引 / KG / 结构化记忆
            # P1-4（2026-09-18）：接住返回值并上报失败项（旧代码丢弃 →
            # "KG save failed" 等信号在 RPC 层消失，重启后孤立实体复活）。
            report_cascade_failures("tasks.delete", task_id, await cascade_delete_task(context, task_id))
            if not await asyncio.to_thread(_verify_deleted):
                logger.warning("tasks.delete 未生效（删除后任务仍在且无墓碑位）: id=%s", task_id)
                return False
            return True

        await asyncio.to_thread(repo.delete_task, task_id)
        # P1.5: 级联清理
        report_cascade_failures("tasks.delete", task_id, await cascade_delete_task(context, task_id))
        if not await asyncio.to_thread(_verify_deleted):
            logger.warning("tasks.delete 未生效（删除后任务仍在且无墓碑位）: id=%s", task_id)
            return False
        return True

    registry.register_with_schema(
        "tasks.delete",
        tasks_delete,
        schema={
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": ["integer", "string"]},
            },
        },
    )

    # ── tasks.permanent_delete ──
    # S1-2/AN-2：物理摘除回收站中的软删任务（对照 notes.permanent_delete，
    # _notes.py:655 的 RISK_R4 注册形状）。活任务拒绝——语义错误；
    # 优先消费仓库层 permanent_delete_task（含 id_map 清映射），旧式仓储
    # 回退 delete_task(hard)。
    async def tasks_permanent_delete(task_id: int | str, **kwargs: Any) -> bool:
        repo = context.personal_task_repository
        if not repo:
            return False
        # 探活必须 include_deleted=True：软删行 get_task 恒 None（防幽灵读），
        # 否则回收站条目恒不命中恒 False（S0-2 同款三重死）。
        row = await asyncio.to_thread(_find_task_row, repo, task_id, True)
        if row is None:
            return False
        # 活任务拒绝永久删除（与 S0-2 notes 语义严格对齐）。
        if not row.get("deleted_at"):
            return False
        task_id = row.get("id")
        if hasattr(repo, "permanent_delete_task"):
            ok = await asyncio.to_thread(repo.permanent_delete_task, task_id)
            if ok is False:
                # 探活与摘除之间被并发删除等竞态 → 如实失败，不级联不回响
                return False
        else:
            # 旧式/测试仓储无终删方法 → 回退硬删（delete_task soft=False）
            await asyncio.to_thread(repo.delete_task, task_id, False)
        # P1-4（2026-09-18）：接住返回值并上报失败项（同 tasks.delete 的理由）
        report_cascade_failures("tasks.permanent_delete", task_id, await cascade_delete_task(context, task_id))
        return True

    registry.register_with_schema(
        "tasks.permanent_delete",
        tasks_permanent_delete,
        schema={
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": ["integer", "string"]},
            },
        },
        risk=RISK_R4,
    )

    # ── tasks.toggle ──
    async def tasks_toggle(task_id: int | str = 0, completed: bool = True, **kwargs: Any) -> bool:
        repo = context.personal_task_repository
        if not repo or not task_id:
            return False
        await asyncio.to_thread(repo.toggle_task_completion, task_id, completed)
        return True

    registry.register_with_schema(
        "tasks.toggle",
        tasks_toggle,
        schema={
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": ["integer", "string"]},
                "completed": {"type": "boolean"},
            },
        },
    )

    # ── tasks.schedule ──
    async def tasks_schedule(
        task_id: "str | int" = "",
        schedule: str = "",
        run_at: str = "",
        repeat: str = "none",
        reminder_text: str = "",
        max_retries: int = 0,
        retry_backoff: int = 60,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """为任务（或外部任务ID）注册 CronService 定时提醒/执行。"""
        # 2026-09-18（风险台账 #181）：``(task_id or "").strip()`` 对 **int** 传参
        # 直接 AttributeError → -32603。同一文件 ``_find_task_row`` / ``_resolve_task_id``
        # 早已接受 ``int | str``（:15/:48），这里却仍假设字符串。归一为 str 再 strip。
        task_id = str(task_id or "").strip()
        if not task_id:
            return {"success": False, "error": "task_id required"}

        # 优先使用 TaskManager 的桥接方法
        task_manager = getattr(context, "task_manager", None)
        if task_manager is None:
            autonomy = getattr(context, "autonomous_assistant_manager", None)
            if autonomy is not None:
                task_manager = getattr(autonomy, "task_manager", None)

        if task_manager is None:
            return {"success": False, "error": "task manager not available"}

        if not hasattr(task_manager, "schedule_task_reminder"):
            return {"success": False, "error": "task manager does not support scheduling"}

        try:
            result = await asyncio.to_thread(
                task_manager.schedule_task_reminder,
                task_id,
                schedule=schedule,
                run_at=run_at,
                repeat=repeat,
                reminder_text=reminder_text,
                max_retries=int(max_retries or 0),
                retry_backoff=int(retry_backoff or 60),
            )
            return result
        except Exception as exc:
            logger.warning("tasks.schedule failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("tasks.schedule", tasks_schedule)

    # ── tasks.unschedule ──
    async def tasks_unschedule(task_id: "str | int" = "", **kwargs: Any) -> dict[str, Any]:
        """移除指定任务关联的所有 cron job。"""
        # 2026-09-18（风险台账 #181）：``(task_id or "").strip()`` 对 **int** 传参
        # 直接 AttributeError → -32603。同一文件 ``_find_task_row`` / ``_resolve_task_id``
        # 早已接受 ``int | str``（:15/:48），这里却仍假设字符串。归一为 str 再 strip。
        task_id = str(task_id or "").strip()
        if not task_id:
            return {"success": False, "error": "task_id required"}

        task_manager = getattr(context, "task_manager", None)
        if task_manager is None:
            autonomy = getattr(context, "autonomous_assistant_manager", None)
            if autonomy is not None:
                task_manager = getattr(autonomy, "task_manager", None)

        if task_manager is None or not hasattr(task_manager, "unschedule_task"):
            return {"success": False, "error": "task manager scheduling unavailable"}

        try:
            return await asyncio.to_thread(task_manager.unschedule_task, task_id)
        except Exception as exc:
            logger.warning("tasks.unschedule failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("tasks.unschedule", tasks_unschedule)
