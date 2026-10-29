"""Cloud Sync JSON-RPC handlers.

提供 ``sync.*`` 系列方法，让前端可以查询同步状态、手动触发同步、
配置/启用/禁用云同步。

方法：
- ``sync.status``           — 查询当前同步状态
- ``sync.sync_now``         — 立即执行一次 push + pull
- ``sync.enable``           — 启用云同步（需先配置 worker_url + token）
- ``sync.disable``          — 禁用云同步
- ``sync.configure``        — 配置 worker_url / token / register_secret
- ``sync.register_device``  — 向 Worker 注册当前设备
- ``sync.conflicts.list``   — P0-2: 列出待裁决的同步冲突
- ``sync.resolve_conflict`` — P0-2: 裁决单条冲突（remote/local）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.handlers._list_guard import capped_payload

logger = logging.getLogger("siyi.backend.handlers.sync")


def _get_bridge(context) -> Any | None:
    """从 context 获取 SyncBridge 实例（由 headless_main 注入）。"""
    return getattr(context, "cloud_sync_bridge", None)


def register_sync(registry, context) -> None:
    """注册云同步相关 JSON-RPC 方法。"""

    # ── sync.status ──
    async def sync_status(**kwargs: Any) -> dict[str, Any]:
        bridge = _get_bridge(context)
        if bridge is None:
            return {
                "enabled": False,
                "configured": False,
                "running": False,
                "message": "cloud sync bridge not initialized",
            }
        return bridge.get_status()

    registry.register("sync.status", sync_status)

    # ── sync.sync_now ──
    async def sync_sync_now(**kwargs: Any) -> dict[str, Any]:
        bridge = _get_bridge(context)
        if bridge is None or bridge.client is None:
            return {"success": False, "error": "cloud sync not started"}
        try:
            ok = await bridge.client.sync_once()
            return {"success": ok, "status": bridge.get_status()}
        except Exception as exc:
            logger.warning("sync_now failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("sync.sync_now", sync_sync_now)

    # ── sync.configure ──
    async def sync_configure(
        worker_url: str = "",
        token: str = "",
        register_secret: str = "",
        interval_seconds: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """配置云同步参数。所有参数可选，仅传入的字段会被更新。"""
        settings = context.settings_repository
        if not settings:
            return {"success": False, "error": "settings repository unavailable"}

        # 4 次写 + 4 次读 SQLite，整段包裹 to_thread 避免阻塞事件循环
        def _apply_config():
            if worker_url:
                settings.set_setting("cloud_sync_worker_url", worker_url.rstrip("/"))
            if token:
                settings.set_setting("cloud_sync_token", token)
            if register_secret:
                settings.set_setting("cloud_sync_register_secret", register_secret)
            if interval_seconds > 0:
                settings.set_setting("cloud_sync_interval_seconds", interval_seconds)
            return {
                "success": True,
                "worker_url": settings.get_setting("cloud_sync_worker_url", ""),
                "has_token": bool(settings.get_setting("cloud_sync_token", "")),
                "has_register_secret": bool(settings.get_setting("cloud_sync_register_secret", "")),
                "interval_seconds": settings.get_setting("cloud_sync_interval_seconds", 300),
            }

        return await asyncio.to_thread(_apply_config)

    registry.register("sync.configure", sync_configure)

    # ── sync.enable ──
    async def sync_enable(**kwargs: Any) -> dict[str, Any]:
        """启用云同步。需要先配置 worker_url + token。"""
        settings = context.settings_repository
        if not settings:
            return {"success": False, "error": "settings repository unavailable"}

        # 读 SQLite + 写 SQLite 包裹 to_thread
        def _read_and_enable():
            worker_url = settings.get_setting("cloud_sync_worker_url", "")
            token = settings.get_setting("cloud_sync_token", "")
            if not worker_url or not token:
                return None
            settings.set_setting("cloud_sync_enabled", True)
            return True

        enabled = await asyncio.to_thread(_read_and_enable)
        if enabled is None:
            return {
                "success": False,
                "error": "worker_url and token must be configured first",
            }

        bridge = _get_bridge(context)
        if bridge is None:
            return {"success": False, "error": "cloud sync bridge not initialized"}

        # 如果客户端尚未启动，则启动
        # B-18 附带：P1-17 规范 —— 不得直接访问私有属性 `_sync_task`，
        # 用已存在的 is_running()（原 `bridge.client._sync_task and not
        # bridge.client._sync_task.done()` 违反项目自立规范）。
        if bridge.client is None or not bridge.client.is_running():
            try:
                await bridge.start()
            except Exception as exc:
                logger.exception("sync enable start failed")
                return {"success": False, "error": str(exc)}

        return {"success": True, "status": bridge.get_status()}

    registry.register("sync.enable", sync_enable)

    # ── sync.disable ──
    async def sync_disable(**kwargs: Any) -> dict[str, Any]:
        """禁用云同步（停止循环，但保留配置）。"""
        settings = context.settings_repository
        if settings:
            await asyncio.to_thread(settings.set_setting, "cloud_sync_enabled", False)

        bridge = _get_bridge(context)
        if bridge is not None:
            try:
                await bridge.stop()
            except Exception as exc:
                logger.warning("sync disable stop failed: %s", exc)

        return {"success": True, "enabled": False}

    registry.register("sync.disable", sync_disable)

    # ── sync.register_device ──
    async def sync_register_device(name: str = "Desktop", **kwargs: Any) -> dict[str, Any]:
        """向 Worker 注册当前设备。需要先配置 register_secret。"""
        bridge = _get_bridge(context)
        if bridge is None:
            return {"success": False, "error": "cloud sync bridge not initialized"}

        settings = context.settings_repository
        register_secret = await asyncio.to_thread(settings.get_setting, "cloud_sync_register_secret", "") if settings else ""
        if not register_secret:
            return {
                "success": False,
                "error": "register_secret not configured (set via sync.configure)",
            }

        # 临时构造 client（若尚未启动）
        client = bridge.client or bridge.build_client()
        if client is None:
            return {"success": False, "error": "cloud sync not configured"}

        try:
            ok = await client.register_device(name)
            return {"success": ok, "device_id": client.device_id}
        except Exception as exc:
            logger.warning("register_device failed: %s", exc)
            return {"success": False, "error": str(exc)}
        finally:
            # 如果 client 是临时构造的（bridge.client 为 None），关闭它
            if bridge.client is None:
                await client.close()

    registry.register("sync.register_device", sync_register_device)

    # ── sync.conflicts.list（P0-2：冲突可见可恢复） ──
    async def sync_conflicts_list(**kwargs: Any) -> dict[str, Any]:
        """列出所有 sync_state=conflict 的笔记/任务，供前端展示裁决入口。"""
        result: list[dict[str, Any]] = []
        # R0-B17：属性名对齐生产注入（personal_*；AppContext 无 note_repository/task_repository）
        note_repo = getattr(context, "personal_note_repository", None)
        task_repo = getattr(context, "personal_task_repository", None)
        try:
            if note_repo is not None:
                notes = await asyncio.to_thread(note_repo.list_notes)
                for n in notes:
                    if n.get("sync_state") == "conflict":
                        result.append(
                            {
                                "resource": "note",
                                "id": n.get("id"),
                                "title": str(n.get("title") or "")[:80],
                                "updated_at": n.get("updated_at"),
                                "has_local_version": bool(n.get("versions")),
                            }
                        )
            if task_repo is not None:
                tasks = await asyncio.to_thread(task_repo.list_tasks)
                for t in tasks:
                    if t.get("sync_state") == "conflict":
                        result.append(
                            {
                                "resource": "todo",
                                "id": t.get("id"),
                                "title": str(t.get("title") or "")[:80],
                                "updated_at": t.get("updated_at"),
                                "has_local_version": bool(t.get("versions")),
                            }
                        )
        except Exception as exc:
            logger.warning("sync.conflicts.list failed: %s", exc)
            return {"conflicts": result, "count": len(result), "error": str(exc)}
        # 帧守卫（2026-10-15）：count 保持全量语义，conflicts 截断 + truncated 标记
        conflicts, truncated = capped_payload(result, method="sync.conflicts.list")
        return {"conflicts": conflicts, "count": len(result), "truncated": truncated}

    registry.register("sync.conflicts.list", sync_conflicts_list)

    # ── sync.resolve_conflict（P0-2：冲突裁决） ──
    async def sync_resolve_conflict(resource: str = "", id: str = "", choice: str = "", **kwargs: Any) -> dict[str, Any]:
        """裁决单条同步冲突。

        choice=remote：接受当前（远端覆盖后的）内容，清除冲突标记；
        choice=local：从 versions[0] 恢复本地快照并标记 dirty，
        下次同步推回远端（桌面端裁决优先，手机端只收冲突通知）。
        """
        if resource not in ("note", "todo"):
            return {"success": False, "error": "resource must be 'note' or 'todo'"}
        if not id:
            return {"success": False, "error": "missing id"}
        if choice not in ("remote", "local"):
            return {"success": False, "error": "choice must be 'remote' or 'local'"}

        try:
            if resource == "note":
                note_repo = getattr(context, "personal_note_repository", None)
                if note_repo is None:
                    return {"success": False, "error": "note repository unavailable"}
                note = await asyncio.to_thread(note_repo.get_note, id)
                if not note:
                    return {"success": False, "error": f"note {id} not found"}
                if note.get("sync_state") != "conflict":
                    return {"success": False, "error": "note is not in conflict state"}
                if choice == "remote":
                    await asyncio.to_thread(note_repo.update_note, id, {"sync_state": "synced"})
                else:
                    versions = note.get("versions") or []
                    if not versions:
                        return {"success": False, "error": "no local version preserved"}
                    prev = versions[0]
                    # V2.1 批次3：local 完整快照恢复（含时间戳/版本，防丢元数据）
                    await asyncio.to_thread(
                        note_repo.update_note,
                        id,
                        {
                            "title": prev.get("title", ""),
                            "content": prev.get("content", ""),
                            "type": prev.get("type", "note"),
                            "folder": prev.get("folder"),
                            "tags": prev.get("tags", []),
                            "created_at": prev.get("created_at"),
                            "updated_at": prev.get("updated_at"),
                            "version": max(int(note.get("version") or 0), 1) + 1,
                            "sync_state": "dirty",
                        },
                    )
            else:
                task_repo = getattr(context, "personal_task_repository", None)
                if task_repo is None:
                    return {"success": False, "error": "task repository unavailable"}
                tasks = await asyncio.to_thread(task_repo.list_tasks)
                task = next((t for t in tasks if t.get("id") == id), None)
                if not task:
                    return {"success": False, "error": f"todo {id} not found"}
                if task.get("sync_state") != "conflict":
                    return {"success": False, "error": "todo is not in conflict state"}
                if choice == "remote":
                    await asyncio.to_thread(task_repo.update_task, id, {"sync_state": "synced"})
                else:
                    versions = task.get("versions") or []
                    if not versions:
                        return {"success": False, "error": "no local version preserved"}
                    prev = versions[0]
                    # V2.1 批次3：local 完整快照恢复（含时间戳/版本，防丢元数据）
                    await asyncio.to_thread(
                        task_repo.update_task,
                        id,
                        {
                            "title": prev.get("title", ""),
                            "completed": bool(prev.get("completed", prev.get("is_completed", False))),
                            "category": prev.get("category", ""),
                            "priority": prev.get("priority", "normal"),
                            "due_date": prev.get("due_date"),
                            "created_at": prev.get("created_at"),
                            "updated_at": prev.get("updated_at"),
                            "version": max(int(task.get("version") or 0), 1) + 1,
                            "sync_state": "dirty",
                        },
                    )
        except Exception as exc:
            logger.warning("sync.resolve_conflict failed: %s", exc)
            return {"success": False, "error": str(exc)}

        logger.info("sync conflict resolved: resource=%s id=%s choice=%s", resource, id, choice)
        return {"success": True, "resource": resource, "id": id, "choice": choice}

    registry.register("sync.resolve_conflict", sync_resolve_conflict)

    # ── sync.reset_watermark（V2.1 批次3 逃生舱 / B-18 生效化）──
    # 水位错乱（漏拉/重复拉取）时，把本地水位重置为 0 → 下次 pull 全量重拉对齐。
    # 不删除任何数据；仅影响游标。走 dispatch 层 R3（非破坏性，无需审批）。
    # B-18 修复：原实现只写零消费方的 WatermarkStore（sync_watermark.json），
    # 运行中 client._cursor 不受影响 → 逃生舱返回 success 但无效（需重启）。
    # 现直接调 client.reset_cursor()：内存 + 持久层（settings cloud_sync_cursor）
    # 双清，运行中同步循环下次 pull 即以 since=0 全量重拉。
    async def sync_reset_watermark(**kwargs: Any) -> dict[str, Any]:
        bridge = _get_bridge(context)
        if bridge is None or bridge.client is None:
            return {"success": False, "error": "cloud sync bridge not initialized"}
        try:
            watermark = bridge.client.reset_cursor()
            logger.warning("sync.reset_watermark executed (escape hatch)")
            return {"success": True, "watermark": watermark}
        except Exception as exc:
            logger.warning("sync.reset_watermark failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("sync.reset_watermark", sync_reset_watermark)
