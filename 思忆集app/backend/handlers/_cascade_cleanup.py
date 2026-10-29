"""P1.5 级联清理：删除笔记/任务时清理关联的 KG / 记忆索引 / 结构化记忆 / cron / reminder。

源码重建自 .pyc（原 .py 丢失），签名与调用方约定保持一致：
- ``cascade_delete_note(context, note_id)`` — async，清理笔记关联
- ``cascade_delete_task(context, task_id)`` — async，清理任务关联

调用方：
- ``backend/handlers/_notes.py``: notes.delete / notes.batch_delete / notes.permanent_delete
- ``backend/handlers/_tasks.py``: tasks.delete
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.integrity import KIND_CASCADE, record

logger = logging.getLogger("siyi.backend.handlers.cascade_cleanup")


def cascade_failures(results: dict[str, Any] | None) -> list[str]:
    """从级联清理返回值里挑出**失败项**（P1-4 治本，2026-09-18）。

    返回值约定（本模块所有清理项统一）：``"deleted:N"`` / ``"no-op"`` /
    ``"failed: <原因>"`` / ``"deleted:N (save failed: <原因>)"``。
    前两者是正常终态；后两者意味着"内存改了但没落盘"或"清理本身抛错"——
    前者尤其危险：**图谱实体在内存里没了、磁盘上还在，重启即复活**。

    旧病：调用方 ``_notes.py`` / ``_tasks.py`` 共 6 处 ``await cascade_delete_note(...)``
    **丢弃返回值**，于是"save failed"这类信号在 RPC 层彻底消失，用户看到的永远是成功。

    本函数只做**提取**（纯函数、无副作用），上报方式交给调用方——
    因为不同调用点的返回契约不同（``notes.delete`` 返回 bool 且语义是"笔记已删"，
    级联失败不改变该语义，不能改判 False 误报删除失败）。

    健壮性（与 :func:`core.integrity.record` 同一原则：**观测通道不得成为故障源**）：
    ``results`` 非映射时**不抛异常**，而是记一条异常形状事件后返回 ``[]``——
    调用点此时笔记/任务**已经删掉了**，若因诊断辅助函数崩溃而让 RPC 报失败，
    就是把"删除成功"谎报成"删除失败"，比不诊断更糟。
    """
    if not results:
        return []
    if not isinstance(results, dict):
        # 契约违规（cascade_delete_* 约定返回 dict[str, Any]）——留痕但不阻断。
        record(KIND_CASCADE, f"级联清理返回值形状异常（{type(results).__name__}），跳过失败项提取")
        return []
    failures: list[str] = []
    for key, value in results.items():
        text = str(value)
        if text.startswith("failed:") or "save failed" in text:
            failures.append(f"{key}: {text}")
    return failures


def report_cascade_failures(scope: str, target_id: Any, results: dict[str, Any] | None) -> list[str]:
    """提取失败项并如实上报（台账 + WARNING），返回失败项列表。

    ``scope`` 形如 ``"notes.delete"`` / ``"tasks.permanent_delete"``，用于事后定位是
    哪个 RPC 的级联出了问题。上报经 :func:`core.integrity.record` 单点出口，
    保证"入账"与"打日志"不分裂；``system.integrity`` RPC 可直接查到。
    """
    failures = cascade_failures(results)
    if failures:
        record(KIND_CASCADE, f"{scope} 级联清理部分失败：{'; '.join(failures)}", target=str(target_id))
    return failures


def _kg_cascade_sync(kg: Any, id_str: str, source_type: str) -> str:
    """PD-2（2026-09-17）：图谱级联删除的正确实现——三层删除 + 落盘 + 如实报告。

    旧病：调不存在的 ``kg.delete_entity`` 抛 AttributeError 被吞 / 兜底只 pop
    ``_entities[note_id_str]``（真实派生实体键是 ``ent_note_*``，note_id 根本不在
    键空间）/ 边字段名用 ``source/target``（真实为 ``source_id/target_id``）→
    全程 no-op 却谎报 deleted；且 remove_by_source 生效也不 save → 重启复活。

    新契约（同步执行，调用方负责 to_thread）：
    1. ``remove_by_source(source_type, id)`` —— 按 properties.source_id 派生属性删
    2. ``remove_entity(id)`` —— 按实体 id 直删（覆盖 entity_id==业务 id 的旧代际）
    3. 兜底：无公共 API 的对象才直接操作 ``_entities/_relations``（边两种字段名都滤）
    任一有删除 → ``save()`` 落盘并返回 ``deleted:N``；全无命中 → ``no-op``（不再谎报）。
    """
    removed = 0
    errors: list[str] = []
    fn_src = getattr(kg, "remove_by_source", None)
    if callable(fn_src):
        try:
            removed += int(fn_src(source_type, id_str) or 0)
        except Exception as exc:
            logger.warning("KG remove_by_source failed for %s:%s: %s", source_type, id_str, exc, exc_info=True)
            errors.append(f"remove_by_source: {exc}")
    fn_ent = getattr(kg, "remove_entity", None)
    if callable(fn_ent):
        try:
            if fn_ent(id_str):
                removed += 1
        except Exception as exc:
            logger.warning("KG remove_entity failed for %s: %s", id_str, exc, exc_info=True)
            errors.append(f"remove_entity: {exc}")
    if not callable(fn_src) and not callable(fn_ent) and hasattr(kg, "_entities"):
        # 兜底仅给无公共 API 的对象（真实 KnowledgeGraphStore 永远走上面两层）。
        # 边字段名双兼容：source_id/target_id（真实）+ source/target（旧测试形态）。
        if kg._entities.pop(id_str, None) is not None:
            removed += 1
        if hasattr(kg, "_relations"):
            kg._relations = [r for r in kg._relations if id_str not in (r.get("source_id"), r.get("target_id"), r.get("source"), r.get("target"))]
    if removed > 0:
        # PD-2b：级联删除必须落盘，否则重启后实体复活
        save = getattr(kg, "save", None)
        if callable(save):
            try:
                save()
            except Exception as exc:
                logger.warning("KG save after cascade delete failed for %s: %s", id_str, exc, exc_info=True)
                return f"deleted:{removed} (save failed: {exc})"
        return f"deleted:{removed}"
    if errors:
        # 真实 API 调用失败（非"无命中"）→ 如实报 failed，供 best-effort 判定
        return "failed: " + "; ".join(errors)
    return "no-op"


async def cascade_delete_note(context: Any, note_id: str | int) -> dict[str, Any]:
    """级联删除笔记关联的数据。

    清理范围：
    - 知识图谱节点（entity_id == note_id）
    - 记忆索引条目（source_id == note_id）
    - 结构化记忆 relations（target_id == note_id）

    所有清理都是 best-effort：单个失败不阻塞其他清理。
    返回各清理项的状态。
    """
    results: dict[str, Any] = {}
    note_id_str = str(note_id)

    # 1. 知识图谱节点
    # R0-B21：属性名对齐生产注入（knowledge_graph_store）；曾用 knowledge_graph/graph_store
    # 两个都不存在的名字 → KG 级联清理静默跳过（删除笔记留下孤立图谱节点）。
    # PD-2（2026-09-17）：方法名/键空间/边字段名三错致 no-op 谎报 deleted → 改用 _kg_cascade_sync。
    kg = getattr(context, "knowledge_graph_store", None)
    if kg is not None:
        try:
            results["knowledge_graph"] = await asyncio.to_thread(_kg_cascade_sync, kg, note_id_str, "note")
        except Exception as exc:
            logger.warning("cascade_delete_note: KG cleanup failed for %s: %s", note_id_str, exc, exc_info=True)
            results["knowledge_graph"] = f"failed: {exc}"

    # 2. 记忆索引条目
    # R0-B21：属性名对齐生产注入（memory_index_service）；曾用 memory_index →
    # 记忆索引级联清理静默跳过（删除笔记/任务后 recall 仍命中已删内容）。
    memory_index = getattr(context, "memory_index_service", None)
    if memory_index is not None:
        try:
            if hasattr(memory_index, "delete_entries_by_source"):
                # P1.5 修复（T5.1）：delete_entries_by_source 签名是 (source_type, source_id)，
                # 此前只传 note_id 一个参数导致 TypeError 被 except 吞掉，级联删除从未真正生效。
                removed = await asyncio.to_thread(memory_index.delete_entries_by_source, "note", note_id_str)
                results["memory_index"] = f"deleted:{removed}"
        except Exception as exc:
            logger.warning("cascade_delete_note: memory index cleanup failed for %s: %s", note_id_str, exc, exc_info=True)
            results["memory_index"] = f"failed: {exc}"

    # 3. 结构化记忆 relations
    # R0-B21：structured_store 兜底名从未被注入（生产只有 structured_memory_store），删除死兜底。
    structured_store = getattr(context, "structured_memory_store", None)
    if structured_store is not None:
        try:
            if hasattr(structured_store, "cleanup_relations_for_target"):
                await asyncio.to_thread(structured_store.cleanup_relations_for_target, note_id_str)
                results["structured_memory"] = "deleted"
        except Exception as exc:
            logger.warning("cascade_delete_note: structured memory cleanup failed for %s: %s", note_id_str, exc, exc_info=True)
            results["structured_memory"] = f"failed: {exc}"

    return results


async def cascade_delete_task(context: Any, task_id: str | int) -> dict[str, Any]:
    """级联删除任务关联的数据。

    清理范围：
    - Cron 任务（task_id 关联的 cron job）
    - Reminder（task_id 关联的提醒）
    - 知识图谱节点
    - 记忆索引条目
    - 结构化记忆 relations

    所有清理都是 best-effort。
    """
    results: dict[str, Any] = {}
    task_id_str = str(task_id)

    # 1. Cron 任务
    cron_service = getattr(context, "cron_service", None)
    if cron_service is not None:
        try:
            # 尝试按 task_id 删除关联的 cron job
            if hasattr(cron_service, "remove_by_task_id"):
                await asyncio.to_thread(cron_service.remove_by_task_id, task_id_str)
                results["cron"] = "deleted"
            elif hasattr(cron_service, "list_jobs"):
                # 兜底：列出所有 job，过滤 task_id 匹配的
                def _cron_cleanup():
                    jobs = cron_service.list_jobs() or []
                    removed = 0
                    for job in jobs:
                        # 匹配 params.task_id 或 metadata.task_id
                        params = job.get("params") or {}
                        metadata = job.get("metadata") or {}
                        if (
                            params.get("task_id") == task_id_str
                            or metadata.get("task_id") == task_id_str
                            or str(job.get("task_id", "")) == task_id_str
                        ):
                            cron_id = job.get("id") or job.get("cron_id")
                            if cron_id and hasattr(cron_service, "remove"):
                                cron_service.remove(cron_id)
                                removed += 1
                    return removed

                removed = await asyncio.to_thread(_cron_cleanup)
                results["cron"] = f"deleted:{removed}"
        except Exception as exc:
            logger.warning("cascade_delete_task: cron cleanup failed for %s: %s", task_id_str, exc, exc_info=True)
            results["cron"] = f"failed: {exc}"

    # 2. Reminder
    reminder_service = getattr(context, "reminder_service", None)
    if reminder_service is not None:
        try:
            if hasattr(reminder_service, "remove_by_task_id"):
                await asyncio.to_thread(reminder_service.remove_by_task_id, task_id_str)
                results["reminder"] = "deleted"
            elif hasattr(reminder_service, "list_alarms"):

                def _reminder_cleanup():
                    alarms = reminder_service.list_alarms() or []
                    removed = 0
                    for alarm in alarms:
                        if str(alarm.get("task_id", "")) == task_id_str:
                            alarm_id = alarm.get("id") or alarm.get("alarm_id")
                            if alarm_id and hasattr(reminder_service, "delete_alarm"):
                                reminder_service.delete_alarm(alarm_id)
                                removed += 1
                    return removed

                removed = await asyncio.to_thread(_reminder_cleanup)
                results["reminder"] = f"deleted:{removed}"
        except Exception as exc:
            logger.warning("cascade_delete_task: reminder cleanup failed for %s: %s", task_id_str, exc, exc_info=True)
            results["reminder"] = f"failed: {exc}"

    # 3. 知识图谱节点（与 note 共用逻辑）
    # R0-B21：属性名对齐生产注入。PD-2（2026-09-17）：同 note 分支修复（task 分支旧码
    # 只有 delete_entity 一层，方法名不存在时整个分支静默跳过）。
    kg = getattr(context, "knowledge_graph_store", None)
    if kg is not None:
        try:
            results["knowledge_graph"] = await asyncio.to_thread(_kg_cascade_sync, kg, task_id_str, "task")
        except Exception as exc:
            logger.warning("cascade_delete_task: KG cleanup failed for %s: %s", task_id_str, exc, exc_info=True)
            results["knowledge_graph"] = f"failed: {exc}"

    # 4. 记忆索引条目
    # R0-B21：属性名对齐生产注入。
    memory_index = getattr(context, "memory_index_service", None)
    if memory_index is not None:
        try:
            if hasattr(memory_index, "delete_entries_by_source"):
                # P1.5 修复（T5.1）：补 source_type="task"，同 cascade_delete_note。
                removed = await asyncio.to_thread(memory_index.delete_entries_by_source, "task", task_id_str)
                results["memory_index"] = f"deleted:{removed}"
        except Exception as exc:
            logger.warning("cascade_delete_task: memory index cleanup failed for %s: %s", task_id_str, exc, exc_info=True)
            results["memory_index"] = f"failed: {exc}"

    # 5. 结构化记忆 relations
    # R0-B21：删除从未注入的 structured_store 死兜底。
    structured_store = getattr(context, "structured_memory_store", None)
    if structured_store is not None:
        try:
            if hasattr(structured_store, "cleanup_relations_for_target"):
                await asyncio.to_thread(structured_store.cleanup_relations_for_target, task_id_str)
                results["structured_memory"] = "deleted"
        except Exception as exc:
            logger.warning("cascade_delete_task: structured memory cleanup failed for %s: %s", task_id_str, exc, exc_info=True)
            results["structured_memory"] = f"failed: {exc}"

    return results
