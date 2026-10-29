"""Reminder JSON-RPC handlers — delegates to tasks repository.

Maps reminder.{list,create,delete} to the existing task system,
where reminders are tasks with non-null reminder_time.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from typing import Any

from backend.handlers._list_guard import cap_list

logger = logging.getLogger("siyi.backend.handlers.reminder")

_TIME_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def _task_field(task: Any, name: str) -> Any:
    """任务可能是 dict（kv 仓储）或对象（ORM/端口）——统一取值。"""
    if isinstance(task, dict):
        return task.get(name)
    return getattr(task, name, None)


def _to_ms(value: Any) -> "float | None":
    """reminder_time 归一化为毫秒时间戳；无法解析返回 None。

    仓储层 ``serialize_reminder_time`` 会把时间写成 "yyyy-MM-dd HH:mm" 字符串
    （data/local_storage.py:662），而客户端传的是毫秒数——原 list 直接拿两者做
    ``<=`` 比较，Python3 会抛 TypeError 被 except 吞成空列表。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp() * 1000
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).timestamp() * 1000
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).timestamp() * 1000
    except ValueError:
        return None


def register_reminder(registry, context) -> None:
    """Register reminder-related JSON-RPC methods."""

    def _get_repo():
        return getattr(context, "personal_task_repository", None)

    async def reminder_list(**kwargs: Any) -> list[dict[str, Any]]:
        repo = _get_repo()
        if not repo:
            return []
        try:
            # P1 修复：list_tasks 是同步仓储方法（文件 I/O），用 to_thread 避免阻塞事件循环
            all_tasks = await asyncio.to_thread(repo.list_tasks)
            now = datetime.now().timestamp() * 1000
            due: list[dict[str, Any]] = []
            for t in all_tasks:
                # D1 邻接修复（2026-09-17）：reminder_time 已归一化为毫秒再比较。
                # 原实现拿仓储的 "yyyy-MM-dd HH:mm" 字符串与 now(float) 直接 <=，
                # TypeError 被 except 吞掉 → reminder.list 恒空（写后读必 MISS）。
                ms = _to_ms(_task_field(t, "reminder_time"))
                if ms is None or ms > now:
                    continue
                if _task_field(t, "done") or _task_field(t, "is_completed") or _task_field(t, "completed"):
                    continue
                due.append(
                    {
                        "id": _task_field(t, "id"),
                        "title": _task_field(t, "title") or _task_field(t, "name") or "",
                        "done": False,
                        "due_date": _task_field(t, "due_date"),
                        "reminder_time": int(ms),
                    }
                )
            # 帧守卫（2026-10-15）：裸 list 契约不变，超 2000 条截断 + 告警
            return cap_list(due, method="reminder.list")
        except Exception as exc:
            logger.warning("reminder.list failed: %s", exc)
            return []

    async def reminder_create(**params: Any) -> dict[str, Any]:
        repo = _get_repo()
        if not repo:
            return {"success": False, "message": "task repository not available"}
        # PD-H-2（2026-09-17，r2-verify 实测真相④）：reminder_time 传非 None 但无法
        # 解析的值（"not-a-time"、空串、纯空白、[]、True 等）时，原样落库并返回
        # success=True，而 list 侧 _to_ms 解析失败静默跳过 → "创建成功但提醒永不
        # 触发"的静默假成功。现如实拒绝、不落库。
        # 口径：不传该键 / 传 None = 合法（不设提醒）；传了值（含空串）而解析失败
        # = 非法参数拒绝。合法形态与 _to_ms 能力一致：int/float 毫秒、
        # "yyyy-MM-dd HH:mm[:ss]"、ISO 字符串（含带时区）。
        rt = params.get("reminder_time")
        if rt is not None:
            rt_ms = _to_ms(rt)
            # NaN（如 "NaN" 字符串）与 ±inf 能穿过 _to_ms 的 float() 但语义非法：
            # NaN 落库后 list 侧 int(nan) 必崩、inf 永不到期=另一种静默。
            if rt_ms is None or not math.isfinite(rt_ms):
                return {
                    "success": False,
                    "code": "invalid_params",
                    "error": f"invalid reminder_time: {rt!r}"[:200],
                    "message": "提醒时间格式无效（接受毫秒时间戳或 'yyyy-MM-dd HH:mm' / ISO 字符串）",
                }
        try:
            # D1 修复（2026-09-17）：原实现调 ``repo.create_task``（仓储根本没有该
            # 方法）+ 单个位置参数，AttributeError 被 except 吞掉——创建提醒必然
            # 失败却返回笼统提示，面板不报错。改调真实存在的 ``add_task``
            # （repositories/_task.py:89，单 dict 参数，内部已含 reminder_time
            # 序列化路径），并按项目铁律 keyword 传参。
            result = await asyncio.to_thread(
                repo.add_task,
                task_info={
                    "title": params.get("title", ""),
                    "due_date": params.get("due_date"),
                    "reminder_time": params.get("reminder_time"),
                    "priority": params.get("priority", 1),
                },
            )
            new_id = result.get("id") if isinstance(result, dict) else result
            return {"success": True, "id": new_id}
        except Exception as exc:
            logger.error("reminder.create failed", exc_info=True)
            # 如实报错：不再吞掉真实失败原因（reminder_time 序列化失败等）
            return {"success": False, "message": "创建提醒失败", "error": str(exc)}

    async def reminder_delete(**params: Any) -> dict[str, Any]:
        repo = _get_repo()
        if not repo:
            return {"success": False, "message": "task repository not available"}
        try:
            task_id = params.get("id", "")
            if not task_id:
                return {"success": False, "message": "缺少 id 参数"}
            # P1 修复：delete_task 是同步仓储方法（文件 I/O），用 to_thread 避免阻塞事件循环
            await asyncio.to_thread(repo.delete_task, task_id)

            # 2026-10-15（T1 复验修复）：本方法原先无条件 success=True——而底层
            # `delete_task` 在 id 映射失败时**静默 return**（adapter 无命中信号），
            # 与 tasks.delete 同源同病（reminder 与 task 共用 personal_task_repository
            # / DeskNotes todos **同表**）。补删后复核，使失败可见。
            def _verify_deleted() -> bool:
                try:
                    still = repo.get_task(task_id)
                except Exception:
                    logger.exception("reminder.delete 复核失败（视作未删除）")
                    return False
                if not still:
                    return True
                return bool(str(still.get("deleted_at") or "").strip())

            if not await asyncio.to_thread(_verify_deleted):
                logger.warning("reminder.delete 未生效（删除后条目仍在且无墓碑位）: id=%s", task_id)
                return {"success": False, "message": "删除未生效（条目仍在，可能 id 映射异常）"}
            return {"success": True}
        except Exception:
            logger.error("reminder.delete failed", exc_info=True)
            return {"success": False, "message": "操作失败，请查看后端日志"}

    # 2026-10-15（T1 复验补强）：delete 原先无 schema → 任何参数都放行。实测
    # `reminder.delete {"reminder_id": "…"}`（错误参数名）照样入队审批，产生
    # "空 id 删除"垃圾审批项。补 schema 后 id 缺失直接 -32602，不进审批队列。
    #
    # ⚠️ create **故意不加 schema**：PD-H-2（2026-09-17）要求非法 reminder_time
    # 由 handler 返回结构化 `{"success": False, "code": "invalid_params", ...}`
    # （比 JSON Schema 类型拒绝更细：区分 NaN / ±inf / 不可解析串）。加 schema 会把
    # 错误形态变成 -32602，破坏该契约——test_r2_verify 的 illegal_time 变体测试
    # 正锁定此行为（本批实测撞红后撤回 create 的 schema）。list 零参只读，无需 schema。
    registry.register("reminder.list", reminder_list)
    registry.register("reminder.create", reminder_create)
    registry.register_with_schema(
        "reminder.delete",
        reminder_delete,
        schema={
            "type": "object",
            "properties": {"id": {"type": ["integer", "string"]}},
            "required": ["id"],
        },
    )
