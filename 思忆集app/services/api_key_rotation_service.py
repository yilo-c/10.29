"""API Key rotation service (P2.5).

为每个 provider 维护一份密钥池（primary + backup），在检测到不可恢复错误
（billing_error / auth_error）时自动轮换到下一个备份密钥，并支持手动轮换。

存储模型：
    settings[KEY_PROVIDER_KEY_ROTATION] = {
        "<provider_name>": {
            "auto_rotate": True,
            "primary_key_id": "uuid-1",
            "keys": [
                {
                    "key_id": "uuid-1",
                    "label": "main",
                    "encrypted_value": "fernet:...",   # SecureKeyStorage.encrypt 后的 token
                    "status": "active",                # active / backup / disabled
                    "created_at": "2024-01-15T10:00:00Z",
                    "last_used_at": "...",
                    "last_rotated_at": "...",
                    "failure_count": 0,
                },
                ...
            ],
            "history": [
                {"at": "...", "from": "uuid-1", "to": "uuid-2", "reason": "billing_error", "error": "..."}
            ],
        },
        ...
    }

轮换触发条件（来自 ``ai.llm_resilience.classify_error``）：
    - ``billing_error`` (402 / 欠费 / 配额耗尽) → 轮换
    - ``auth_error`` (401 / 403) → 轮换
    - ``rate_limit`` / ``server_error`` / ``network_error`` → **不轮换**（瞬时错误）

注意：
    - Windows 平台 ``SecureKeyStorage.store/retrieve/delete`` 不可用（DPAPI 限制），
      但 ``encrypt(value, key_id)`` / ``decrypt(token)`` 值级 API 可用，因此本服务
      将密文 token 存储在 settings 中，而非命名存储。
    - 轮换后将新密钥写回 ``provider_configs[*].api_key`` 并由调用方触发
      ``context.recreate_ai_client()`` 重建 AI 客户端。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from core.settings_keys import KEY_PROVIDER_CONFIGS, KEY_PROVIDER_KEY_ROTATION

logger = logging.getLogger("siyi.services.api_key_rotation")

# 会触发自动轮换的错误类型
ROTATION_TRIGGER_KINDS: frozenset[str] = frozenset({
    "billing_error",
    "auth_error",
})

# 每个 provider 保留的最大轮换历史条数
MAX_HISTORY = 50

# 每个 provider 允许的最大密钥数
MAX_KEYS_PER_PROVIDER = 5


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _new_key_id() -> str:
    """生成新的密钥 ID。"""
    return uuid.uuid4().hex[:12]


class ApiKeyRotationService:
    """API Key 轮换服务。

    依赖：
        - ``settings_repository``：读写 ``KEY_PROVIDER_KEY_ROTATION`` 与 ``KEY_PROVIDER_CONFIGS``
        - ``secure_storage``：``SecureKeyStorage`` 实例，用于 ``encrypt`` / ``decrypt`` 值级 API
    """

    def __init__(
        self,
        settings_repository: Any,
        secure_storage: Any,
    ) -> None:
        self._settings = settings_repository
        self._secure = secure_storage

    # ------------------------------------------------------------------
    # 存储读写
    # ------------------------------------------------------------------

    def _load_rotation_state(self) -> dict[str, Any]:
        """加载全部 provider 的轮换状态。"""
        raw = self._settings.get_setting(KEY_PROVIDER_KEY_ROTATION, {})
        if not isinstance(raw, dict):
            return {}
        return raw

    def _save_rotation_state(self, state: dict[str, Any]) -> None:
        """保存全部 provider 的轮换状态。"""
        self._settings.set_setting(KEY_PROVIDER_KEY_ROTATION, state)

    def _load_providers(self) -> list[dict[str, Any]]:
        raw = self._settings.get_setting(KEY_PROVIDER_CONFIGS, [])
        if not isinstance(raw, list):
            return []
        return [p for p in raw if isinstance(p, dict)]

    def _save_providers(self, providers: list[dict[str, Any]]) -> None:
        self._settings.set_setting(KEY_PROVIDER_CONFIGS, providers)

    def _find_provider(self, name: str) -> dict[str, Any] | None:
        for p in self._load_providers():
            if p.get("name") == name:
                return p
        return None

    # ------------------------------------------------------------------
    # 加解密
    # ------------------------------------------------------------------

    def _encrypt_value(self, value: str, provider_name: str) -> str:
        if not value:
            return ""
        return self._secure.encrypt(value, key_id=f"rotation/{provider_name}")

    def _decrypt_value(self, token: str) -> str:
        if not token:
            return ""
        # 非密文（历史明文值）直接返回
        if not self._secure.is_secure_token(token):
            return token
        try:
            return self._secure.decrypt(token)
        except Exception as exc:
            logger.warning("rotation key decrypt failed: %s", exc)
            return token

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def get_rotation_state(self, provider_name: str) -> dict[str, Any]:
        """获取指定 provider 的轮换状态。

        返回的密钥列表中 ``encrypted_value`` 会被脱敏为 ``has_value: bool``，
        不会暴露密文 token。
        """
        state = self._load_rotation_state()
        entry = state.get(provider_name)
        if not entry:
            return {
                "provider_name": provider_name,
                "enabled": False,
                "auto_rotate": False,
                "primary_key_id": "",
                "keys": [],
                "history": [],
            }

        safe_keys: list[dict[str, Any]] = []
        for k in entry.get("keys", []):
            safe_keys.append({
                "key_id": k.get("key_id", ""),
                "label": k.get("label", ""),
                "status": k.get("status", "backup"),
                "has_value": bool(k.get("encrypted_value")),
                "created_at": k.get("created_at", ""),
                "last_used_at": k.get("last_used_at", ""),
                "last_rotated_at": k.get("last_rotated_at", ""),
                "failure_count": k.get("failure_count", 0),
            })

        return {
            "provider_name": provider_name,
            "enabled": bool(entry.get("keys")),
            "auto_rotate": bool(entry.get("auto_rotate", False)),
            "primary_key_id": entry.get("primary_key_id", ""),
            "keys": safe_keys,
            "history": entry.get("history", [])[-MAX_HISTORY:],
        }

    def set_auto_rotate(self, provider_name: str, auto_rotate: bool) -> dict[str, Any]:
        """启用/禁用指定 provider 的自动轮换。"""
        state = self._load_rotation_state()
        entry = state.setdefault(provider_name, {
            "auto_rotate": False,
            "primary_key_id": "",
            "keys": [],
            "history": [],
        })
        entry["auto_rotate"] = bool(auto_rotate)
        self._save_rotation_state(state)
        logger.info("rotation auto_rotate set: provider=%s auto=%s", provider_name, auto_rotate)
        return {"provider_name": provider_name, "auto_rotate": entry["auto_rotate"]}

    # ------------------------------------------------------------------
    # 密钥管理
    # ------------------------------------------------------------------

    def add_backup_key(
        self,
        provider_name: str,
        key_value: str,
        label: str = "",
    ) -> dict[str, Any]:
        """为 provider 添加一个备份密钥。

        若 provider 当前没有主密钥，则新添加的密钥成为主密钥并同步写回
        ``provider_configs[*].api_key``。
        """
        if not key_value or not key_value.strip():
            return {"error": "key_value is required"}
        if not provider_name:
            return {"error": "provider_name is required"}

        # provider 必须已存在于 provider_configs
        provider = self._find_provider(provider_name)
        if provider is None:
            return {"error": f"provider '{provider_name}' not found"}

        state = self._load_rotation_state()
        entry = state.setdefault(provider_name, {
            "auto_rotate": False,
            "primary_key_id": "",
            "keys": [],
            "history": [],
        })
        keys = entry.get("keys", [])
        if len(keys) >= MAX_KEYS_PER_PROVIDER:
            return {"error": f"max {MAX_KEYS_PER_PROVIDER} keys per provider exceeded"}

        new_key_id = _new_key_id()
        encrypted = self._encrypt_value(key_value.strip(), provider_name)
        new_key = {
            "key_id": new_key_id,
            "label": label or f"key-{len(keys) + 1}",
            "encrypted_value": encrypted,
            "status": "backup",
            "created_at": _now_iso(),
            "last_used_at": "",
            "last_rotated_at": "",
            "failure_count": 0,
        }

        # 首个密钥（或主密钥缺失）→ 成为主密钥
        primary_id = entry.get("primary_key_id", "")
        if not primary_id or not any(k.get("key_id") == primary_id for k in keys):
            new_key["status"] = "active"
            entry["primary_key_id"] = new_key_id
            # 同步写回 provider_configs
            self._update_provider_api_key(provider_name, key_value.strip())

        keys.append(new_key)
        entry["keys"] = keys
        self._save_rotation_state(state)

        logger.info(
            "rotation backup key added: provider=%s key_id=%s label=%s status=%s",
            provider_name, new_key_id, new_key["label"], new_key["status"],
        )
        return {
            "key_id": new_key_id,
            "label": new_key["label"],
            "status": new_key["status"],
            "provider_name": provider_name,
        }

    def remove_key(self, provider_name: str, key_id: str) -> dict[str, Any]:
        """删除一个备份密钥。

        不允许删除当前的主密钥（必须先轮换到其他密钥）。
        """
        if not provider_name or not key_id:
            return {"error": "provider_name and key_id are required"}

        state = self._load_rotation_state()
        entry = state.get(provider_name)
        if not entry:
            return {"error": "provider has no rotation state"}

        keys = entry.get("keys", [])
        primary_id = entry.get("primary_key_id", "")
        if key_id == primary_id:
            return {"error": "cannot remove primary key; rotate first"}

        new_keys = [k for k in keys if k.get("key_id") != key_id]
        if len(new_keys) == len(keys):
            return {"error": f"key_id '{key_id}' not found"}

        entry["keys"] = new_keys
        self._save_rotation_state(state)
        logger.info("rotation key removed: provider=%s key_id=%s", provider_name, key_id)
        return {"removed": True, "key_id": key_id, "provider_name": provider_name}

    def rotate(
        self,
        provider_name: str,
        reason: str = "manual",
        error: str = "",
    ) -> dict[str, Any]:
        """手动轮换：将主密钥切换到下一个可用的备份密钥。

        Args:
            provider_name: provider 名称
            reason: 轮换原因（manual / billing_error / auth_error / ...）
            error: 触发轮换的错误信息（可选）

        Returns:
            ``{"rotated": bool, "new_key_id": str, "reason": str}``
        """
        if not provider_name:
            return {"error": "provider_name is required"}

        state = self._load_rotation_state()
        entry = state.get(provider_name)
        if not entry:
            return {"error": f"provider '{provider_name}' has no rotation state"}

        keys = entry.get("keys", [])
        primary_id = entry.get("primary_key_id", "")

        # 标记当前主密钥为 disabled 并累加失败计数
        current = next((k for k in keys if k.get("key_id") == primary_id), None)
        if current:
            current["status"] = "disabled"
            current["failure_count"] = int(current.get("failure_count", 0)) + 1
            current["last_rotated_at"] = _now_iso()

        # 选择下一个可用备份密钥
        backup = next(
            (k for k in keys if k.get("status") == "backup" and k.get("encrypted_value")),
            None,
        )
        if backup is None:
            # 没有可用备份 → 记录失败历史
            entry["primary_key_id"] = ""
            history = entry.get("history", [])
            history.append({
                "at": _now_iso(),
                "from": primary_id,
                "to": "",
                "reason": reason,
                "error": error,
                "success": False,
            })
            entry["history"] = history[-MAX_HISTORY:]
            self._save_rotation_state(state)
            logger.warning(
                "rotation failed (no backup): provider=%s reason=%s",
                provider_name, reason,
            )
            return {
                "rotated": False,
                "reason": "no backup key available",
                "provider_name": provider_name,
            }

        # 切换主密钥
        backup["status"] = "active"
        backup["last_used_at"] = _now_iso()
        entry["primary_key_id"] = backup["key_id"]

        # 写回 provider_configs
        plaintext = self._decrypt_value(backup.get("encrypted_value", ""))
        if not plaintext:
            return {"error": "failed to decrypt backup key"}
        self._update_provider_api_key(provider_name, plaintext)

        # 记录成功历史
        history = entry.get("history", [])
        history.append({
            "at": _now_iso(),
            "from": primary_id,
            "to": backup["key_id"],
            "reason": reason,
            "error": error,
            "success": True,
        })
        entry["history"] = history[-MAX_HISTORY:]
        self._save_rotation_state(state)

        logger.info(
            "rotation success: provider=%s from=%s to=%s reason=%s",
            provider_name, primary_id, backup["key_id"], reason,
        )
        return {
            "rotated": True,
            "from_key_id": primary_id,
            "new_key_id": backup["key_id"],
            "label": backup.get("label", ""),
            "reason": reason,
            "provider_name": provider_name,
        }

    def get_history(self, provider_name: str, limit: int = 20) -> dict[str, Any]:
        """获取轮换历史。"""
        state = self._load_rotation_state()
        entry = state.get(provider_name, {})
        history = entry.get("history", [])
        clamped_limit = min(max(int(limit), 1), MAX_HISTORY)
        return {
            "provider_name": provider_name,
            "history": history[-clamped_limit:],
            "total": len(history),
        }

    def handle_error(
        self,
        provider_name: str,
        error_kind: str,
        error_message: str = "",
    ) -> dict[str, Any]:
        """根据错误类型决定是否触发自动轮换。

        Args:
            provider_name: provider 名称
            error_kind: ``classify_error()`` 返回的 ``kind``
            error_message: 原始错误信息

        Returns:
            ``{"rotated": bool, "reason": str}`` — 若未触发轮换，``rotated=False``
        """
        if error_kind not in ROTATION_TRIGGER_KINDS:
            return {
                "rotated": False,
                "reason": f"error_kind '{error_kind}' does not trigger rotation",
                "provider_name": provider_name,
            }

        state = self._load_rotation_state()
        entry = state.get(provider_name)
        if not entry:
            return {
                "rotated": False,
                "reason": "provider has no rotation state",
                "provider_name": provider_name,
            }

        if not entry.get("auto_rotate", False):
            return {
                "rotated": False,
                "reason": "auto_rotate disabled",
                "provider_name": provider_name,
            }

        return self.rotate(
            provider_name,
            reason=error_kind,
            error=error_message,
        )

    def list_all_rotation_states(self) -> list[dict[str, Any]]:
        """列出所有 provider 的轮换状态（脱敏）。"""
        state = self._load_rotation_state()
        return [self.get_rotation_state(name) for name in state.keys()]

    # ------------------------------------------------------------------
    # provider_configs 同步
    # ------------------------------------------------------------------

    def _update_provider_api_key(self, provider_name: str, plaintext_key: str) -> None:
        """将新密钥（明文）写回 provider_configs。

        使用 ``ApiKeyService`` 的加密约定：``SecureKeyStorage.encrypt(value, key_id=f"provider/{name}")``。
        """
        if not plaintext_key:
            return
        providers = self._load_providers()
        target = next((p for p in providers if p.get("name") == provider_name), None)
        if target is None:
            logger.warning("provider not found when updating api_key: %s", provider_name)
            return
        encrypted = self._secure.encrypt(plaintext_key, key_id=f"provider/{provider_name}")
        target["api_key"] = encrypted
        self._save_providers(providers)
        logger.info("provider api_key updated after rotation: %s", provider_name)
