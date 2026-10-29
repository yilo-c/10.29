"""API keys JSON-RPC handlers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.api_keys")


def register_api_keys(registry, context, ak_service) -> None:
    """Register API key-related JSON-RPC methods."""

    def _recreate_ai_client() -> None:
        try:
            context.recreate_ai_client()
        except Exception as exc:
            logger.warning("Failed to recreate AI client: %s", exc)

    # ── P2.5 API Key 轮换服务（延迟构造，避免无 provider 时报错）──
    def _get_rotation_service():
        from services.api_key_rotation_service import ApiKeyRotationService

        secure = getattr(ak_service, "_secure", None)
        if secure is None:
            return None
        return ApiKeyRotationService(
            settings_repository=context.settings_repository,
            secure_storage=secure,
        )

    # ── api_keys.list ──
    async def api_keys_list(**kwargs: Any) -> list[dict[str, Any]]:
        return await asyncio.to_thread(ak_service.list_keys)

    registry.register("api_keys.list", api_keys_list)

    # ── api_keys.create ──
    async def api_keys_create(**kwargs: Any) -> dict[str, Any]:
        # P0-2026-08-17: 空/缺必需参数时优雅拒绝，避免 create_key 空参抛异常被映射成 -32603
        if not kwargs or not kwargs.get("key_value"):
            return {"success": False, "error": "key_value is required"}
        result = await asyncio.to_thread(ak_service.create_key, kwargs)
        await asyncio.to_thread(_recreate_ai_client)
        return result

    registry.register("api_keys.create", api_keys_create)

    # ── api_keys.update ──
    async def api_keys_update(key_id: str = "", **kwargs: Any) -> dict[str, Any]:
        # P0-2026-08-17: 空 key_id 优雅拒绝
        if not key_id:
            return {"success": False, "error": "key_id is required"}
        result = await asyncio.to_thread(ak_service.update_key, key_id, kwargs)
        await asyncio.to_thread(_recreate_ai_client)
        return result

    registry.register("api_keys.update", api_keys_update)

    # ── api_keys.delete ──
    async def api_keys_delete(key_id: str = "", **kwargs: Any) -> dict[str, Any]:
        # P0-2026-08-17: 空 key_id 优雅拒绝
        if not key_id:
            return {"success": False, "error": "key_id is required"}
        result = await asyncio.to_thread(ak_service.delete_key, key_id)
        await asyncio.to_thread(_recreate_ai_client)
        return result

    registry.register("api_keys.delete", api_keys_delete)

    # ── api_keys.test ──
    async def api_keys_test(key_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """Test API key connectivity — test target provider directly, bypassing AI routing."""
        entry = await asyncio.to_thread(ak_service.get_key, key_id)
        if not entry:
            return {"success": False, "error": "密钥不存在"}
        if not entry.get("key_value") and not entry.get("is_local"):
            return {"success": False, "error": "密钥为空"}

        if entry.get("is_provider"):
            from core.provider_config import load_providers

            # load_providers 是同步函数（文件 I/O + 解密），用 to_thread 避免阻塞事件循环
            providers = await asyncio.to_thread(load_providers, context.settings_repository)
            pname = key_id[len("provider:") :]
            target = next((p for p in providers if p.name == pname), None)
            if not target:
                return {"success": False, "error": f"Provider '{pname}' 未找到"}
            ok, msg = await target.test_connectivity()
            return {
                "success": ok,
                "error": None if ok else msg,
                "message": msg,
                "inference_ok": ok,
                "billing_error": "账户欠费" in msg or "配额耗尽" in msg,
                "is_local": target.is_local,
            }
        else:
            ai_client = getattr(context, "ai_client", None)
            return await ak_service.test_key(key_id, ai_client=ai_client)

    registry.register("api_keys.test", api_keys_test)

    # ──────────────────────────────────────────────────────────────
    # P2.5: API Key 轮换
    # ──────────────────────────────────────────────────────────────

    # ── api_keys.rotation.get ──
    # 获取指定 provider 的轮换状态（脱敏，不暴露密文 token）
    async def api_keys_rotation_get(
        provider_name: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not provider_name:
            return {"error": "provider_name is required"}
        svc = _get_rotation_service()
        if svc is None:
            return {"error": "rotation service unavailable (no secure storage)"}
        return await asyncio.to_thread(svc.get_rotation_state, provider_name)

    registry.register("api_keys.rotation.get", api_keys_rotation_get)

    # ── api_keys.rotation.list ──
    # 列出所有 provider 的轮换状态
    async def api_keys_rotation_list(**kwargs: Any) -> list[dict[str, Any]]:
        svc = _get_rotation_service()
        if svc is None:
            return []
        return await asyncio.to_thread(svc.list_all_rotation_states)

    registry.register("api_keys.rotation.list", api_keys_rotation_list)

    # ── api_keys.rotation.add_backup ──
    # 添加备份密钥；若无主密钥则成为主密钥并同步写回 provider_configs
    async def api_keys_rotation_add_backup(
        provider_name: str = "",
        key_value: str = "",
        label: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        svc = _get_rotation_service()
        if svc is None:
            return {"error": "rotation service unavailable (no secure storage)"}
        result = await asyncio.to_thread(svc.add_backup_key, provider_name, key_value, label)
        if result.get("status") == "active":
            await asyncio.to_thread(_recreate_ai_client)
        return result

    registry.register("api_keys.rotation.add_backup", api_keys_rotation_add_backup)

    # ── api_keys.rotation.remove_key ──
    # 删除备份密钥（不允许删除当前主密钥）
    async def api_keys_rotation_remove_key(
        provider_name: str = "",
        key_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        svc = _get_rotation_service()
        if svc is None:
            return {"error": "rotation service unavailable (no secure storage)"}
        return await asyncio.to_thread(svc.remove_key, provider_name, key_id)

    registry.register("api_keys.rotation.remove_key", api_keys_rotation_remove_key)

    # ── api_keys.rotation.rotate ──
    # 手动轮换：将主密钥切换到下一个备份密钥
    async def api_keys_rotation_rotate(
        provider_name: str = "",
        reason: str = "manual",
        **kwargs: Any,
    ) -> dict[str, Any]:
        svc = _get_rotation_service()
        if svc is None:
            return {"error": "rotation service unavailable (no secure storage)"}
        result = await asyncio.to_thread(svc.rotate, provider_name, reason)
        if result.get("rotated"):
            await asyncio.to_thread(_recreate_ai_client)
        return result

    registry.register("api_keys.rotation.rotate", api_keys_rotation_rotate)

    # ── api_keys.rotation.set_policy ──
    # 启用/禁用自动轮换
    async def api_keys_rotation_set_policy(
        provider_name: str = "",
        auto_rotate: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        svc = _get_rotation_service()
        if svc is None:
            return {"error": "rotation service unavailable (no secure storage)"}
        return await asyncio.to_thread(svc.set_auto_rotate, provider_name, auto_rotate)

    registry.register("api_keys.rotation.set_policy", api_keys_rotation_set_policy)

    # ── api_keys.rotation.history ──
    # 获取轮换历史
    async def api_keys_rotation_history(
        provider_name: str = "",
        limit: int = 20,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not provider_name:
            return {"error": "provider_name is required"}
        svc = _get_rotation_service()
        if svc is None:
            return {"error": "rotation service unavailable (no secure storage)"}
        return await asyncio.to_thread(svc.get_history, provider_name, limit)

    registry.register("api_keys.rotation.history", api_keys_rotation_history)

    logger.info(
        "api_keys handlers registered: list/create/update/delete/test + rotation (get/list/add_backup/remove_key/rotate/set_policy/history) [P2.5]"
    )
