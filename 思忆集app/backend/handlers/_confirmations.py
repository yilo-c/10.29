from __future__ import annotations

from typing import Any

from core.action_guard import KEY_ACTION_ALWAYS_ALLOW
from services.confirmation_store import get_confirmation_store
from backend.handlers._list_guard import capped_payload


async def handle_confirmation_list(
    params: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    # P2 修复（rpc-census-2026-08-30）：registry.dispatch 以 handler(**params)
    # 展开调用，原签名要求必填位置参数 params，空参调用直接 TypeError。
    # 本方法无必填参数，改为可选 params + **kwargs 双兼容（既有内部/测试
    # 直接以位置 dict 调用的路径不变）。
    confirmations = get_confirmation_store().list()
    # 帧守卫（2026-10-15）：待审队列可累积，超 2000 条截断 + truncated 标记
    entries, truncated = capped_payload(list(confirmations), method="confirmation.list")
    return {"confirmations": entries, "truncated": truncated}


async def handle_confirmation_status(
    params: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    # P2 修复：双兼容取参（位置 dict 与 **kwargs 展开两条路径合并）。
    # 缺 confirmation_id 由注册层 JSON Schema 拦截（-32602）。
    data: dict[str, Any] = {**(params or {}), **kwargs}
    confirmation_id = data.get("confirmation_id")
    entry = get_confirmation_store().get(confirmation_id)
    if entry is None:
        return {"confirmation_id": confirmation_id, "status": "expired_or_not_found"}
    return {
        "confirmation_id": entry["confirmation_id"],
        "action_id": entry["action_id"],
        "status": entry["status"],
        "source": entry.get("source"),
        "reason": entry.get("reason"),
        "created_at": entry.get("created_at"),
        "expires_at": entry.get("expires_at"),
        "result": entry.get("result"),
    }


async def handle_confirmation_resolve(
    params: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    # P2 修复：双兼容取参（同 handle_confirmation_status）。
    data: dict[str, Any] = {**(params or {}), **kwargs}
    confirmation_id = data.get("confirmation_id")
    approved = bool(data.get("approved", False))
    reason = str(data.get("reason") or "").strip()
    # 审批理由必填（H 项）：批准操作必须带理由（前端必填 + 后端校验），
    # 不带理由被拦；拒绝操作不强制理由（缺省空串，向后兼容既有调用）。
    if approved and not reason:
        return {
            "success": False,
            "approved": True,
            "reason": "approval_reason_required",
            "message": "批准操作必须填写理由",
        }
    # 审批编排委托稳定层（依赖倒置：services 不再反向依赖 assistant；
    # 执行 handler 由 butler 模块尾部经 services.confirmation_flow 注册，按需调用）。
    from services.confirmation_flow import resolve_confirmation

    return await resolve_confirmation(confirmation_id, approved, reason=reason)


def register_confirmations(registry, context) -> None:
    """注册 confirmation.* RPC（list/status/resolve）。

    注意：``confirmation.status`` 同时作为 butler 工具 action_id 存在，与 RPC
    方法名分属不同命名空间，互不冲突。
    """
    # P2 修复：status/resolve 的 confirmation_id 必填——注册层 schema 校验，
    # 空参调用返回干净 -32602 InvalidParamsError（而非 handler 内 TypeError）。
    _required_confirmation_id: dict[str, Any] = {
        "type": "object",
        "required": ["confirmation_id"],
    }

    async def _resolve_with_always_allow(params: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        """B2（2026-09-11）：支持 always_allow 参数——审批时点「始终允许」。

        前置写回 ``assistant_confirm_always_allow`` 名单（下次起该能力不再
        触发审批），再委托原 resolve 流程完成本次批准/续跑。
        """
        data: dict[str, Any] = {**(params or {}), **kwargs}
        if data.get("always_allow"):
            entry = get_confirmation_store().get(data.get("confirmation_id"))
            action_id = str((entry or {}).get("action_id") or "").strip()
            repo = getattr(context, "settings_repository", None)
            if entry is not None and action_id and repo is not None:
                try:
                    current = set(repo.get_setting(KEY_ACTION_ALWAYS_ALLOW, []) or [])
                    if action_id not in current:
                        repo.set_setting(KEY_ACTION_ALWAYS_ALLOW, sorted(current | {action_id}))
                except Exception:
                    import logging

                    logging.getLogger("siyi.backend.handlers.confirmations").warning("写入 always_allow 失败: %s", action_id, exc_info=True)
        return await handle_confirmation_resolve(params, **kwargs)

    async def _revoke_always_allow(params: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        """B2 收尾（2026-09-11）：把能力移出「始终允许」名单。

        安全模型对称性——授予有一等公民路径（审批卡一键），收回也必须有：
        设置页「AI 权限」分组的名单管理走本 RPC。幂等：移除不在名单中的项
        返回 revoked=False 而非报错。
        """
        data: dict[str, Any] = {**(params or {}), **kwargs}
        action_id = str(data.get("action_id") or "").strip()
        if not action_id:
            return {"success": False, "error": "action_id required"}
        repo = getattr(context, "settings_repository", None)
        if repo is None:
            return {"success": False, "error": "settings_repository unavailable"}
        try:
            current = set(repo.get_setting(KEY_ACTION_ALWAYS_ALLOW, []) or [])
            if action_id not in current:
                return {"success": True, "action_id": action_id, "revoked": False}
            repo.set_setting(KEY_ACTION_ALWAYS_ALLOW, sorted(current - {action_id}))
            return {"success": True, "action_id": action_id, "revoked": True}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}

    registry.register("confirmation.list", handle_confirmation_list)
    registry.register_with_schema("confirmation.status", handle_confirmation_status, _required_confirmation_id)
    registry.register_with_schema("confirmation.resolve", _resolve_with_always_allow, _required_confirmation_id)
    registry.register("confirmation.revoke_always", _revoke_always_allow)
