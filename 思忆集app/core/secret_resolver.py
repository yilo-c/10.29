# -*- coding: utf-8 -*-
"""SecretResolver — 统一的密钥解析入口。

P1.8: 将分散多处的密钥读取逻辑收敛为单一入口：
  - ai/client/config.py:_decrypt_setting_value
  - core/provider_config.py:_build_provider_decrypt_fn
  - services/api_key_service.py:_decrypt_flat_key / _decrypt_provider_key
  - services/tooling/generic_tool_bridge.py:_default_secret_resolver
  - desktop/web_bridge.py:_decrypt_if_secure

解析优先级（四段式）：
  1. 系统环境变量（如 DEEPSEEK_API_KEY, SIYI_LLM_API_KEY, GH_TOKEN 等）
  2. SecureKeyStorage.retrieve（keyring 后端可用时）
  3. settings 中的安全 token 解密
  4. 旧版 Fernet token 解密（透明迁移到新安全存储）

迁移策略：
  - 解密旧版 Fernet token 后，自动用新 SecureKeyStorage 重新加密并写回 settings
  - 明文凭据同样透明迁移到加密 token
  - 仅当 settings_repository + key_name 同时可用时才执行迁移写回

设计原则：
  - 所有调用方共享单一真实源 LLM_FLAT_KEY_ENV_MAP，消除两份不一致的环境变量映射
  - 复用 core.secure_key_storage.get_default_storage() 单例，避免 3 处独立实例化
  - 解密失败时返回原 token，避免数据丢失（上层可继续走明文路径）
"""
from __future__ import annotations

import logging
import os
from typing import Any

from core.secure_key_storage import SecureKeyStorage, get_default_storage

logger = logging.getLogger("siyi.core.secret_resolver")

#: 扁平键 → 环境变量候选列表（唯一真实源，按顺序优先）
LLM_FLAT_KEY_ENV_MAP: dict[str, list[str]] = {
    "llm_api_key": ["SIYI_LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"],
    "llm_api_base": ["SIYI_LLM_BASE_URL", "OPENAI_BASE_URL"],
    "llm_model": ["SIYI_LLM_MODEL", "OPENAI_MODEL"],
    "llm_fallback_api_key": ["SIYI_LLM_FALLBACK_API_KEY", "OPENAI_FALLBACK_API_KEY"],
    "llm_fallback_api_base": ["SIYI_LLM_FALLBACK_BASE_URL", "OPENAI_FALLBACK_BASE_URL"],
    "llm_fallback_model": ["SIYI_LLM_FALLBACK_MODEL", "OPENAI_FALLBACK_MODEL"],
    "llm_local_api_key": ["SIYI_LLM_LOCAL_API_KEY", "OPENAI_LOCAL_API_KEY"],
    "llm_local_api_base": ["SIYI_LLM_LOCAL_BASE_URL", "OPENAI_LOCAL_BASE_URL"],
    "llm_local_model": ["SIYI_LLM_LOCAL_MODEL", "OPENAI_LOCAL_MODEL"],
}


class SecretResolver:
    """统一的密钥解析器。

    通过单一入口收敛 6 处密钥读取逻辑，提供：
    - 四段式优先级解析（env → keyring → settings token → legacy Fernet）
    - 旧版格式自动迁移
    - 可注入 settings_repository / data_dir 以支持迁移写回

    使用方式：
        resolver = get_default_resolver()
        api_key = resolver.resolve("llm_api_key")             # 扁平键
        secret = resolver.resolve_secret("GH_TOKEN")          # 环境变量名 / keyring key_id
        plaintext = resolver.decrypt_token("dpapi:...")       # 仅解密已知 token
        api_key = resolver.resolve_provider_key(provider_dict)  # provider_configs 条目
    """

    def __init__(
        self,
        secure_storage: SecureKeyStorage | None = None,
        settings_repository: Any = None,
        data_dir: str | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._secure = secure_storage or get_default_storage()
        self._settings = settings_repository
        self._data_dir = data_dir
        self._environ = environ if environ is not None else os.environ

    def resolve(
        self,
        key_name: str,
        *,
        prefer_env: bool = True,
        decrypt: bool = True,
        migrate: bool = True,
    ) -> str:
        """解析扁平键对应的密钥值（四段式）。

        Args:
            key_name: settings 中的扁平键名（如 "llm_api_key"）
            prefer_env: True 时优先读环境变量（默认）
            decrypt: True 时对存储的 token 做解密
            migrate: True 时对旧版 Fernet / 明文做透明迁移

        Returns:
            明文密钥值（找不到返回空字符串）
        """
        if prefer_env:
            env_vars = LLM_FLAT_KEY_ENV_MAP.get(key_name, [key_name])
            for env_name in env_vars:
                val = self._environ.get(env_name, "")
                if val:
                    return val
        if self._settings is None:
            return ""
        try:
            stored = str(self._settings.get_setting(key_name, "") or "")
        except Exception:
            logger.debug("settings.get_setting failed for %s", key_name, exc_info=True)
            return ""
        if not stored:
            return ""
        if not decrypt:
            return stored
        return self.decrypt_token(stored, key_name=key_name, migrate=migrate)

    def resolve_secret(self, key_name: str, *, prefer_env: bool = True) -> str | None:
        """解析环境变量样式的 secret（如 GH_TOKEN / ANTHROPIC_API_KEY）。

        与 resolve() 不同：此方法假定 key_name 同时是环境变量名和
        SecureKeyStorage.retrieve 的 key_id；不涉及 settings 扁平键。

        用于 GenericToolBridge._default_secret_resolver 等场景。

        Args:
            key_name: 环境变量名（也是 keyring key_id）
            prefer_env: True 时优先读环境变量

        Returns:
            明文 secret，找不到返回 None
        """
        if prefer_env and self._environ.get(key_name):
            return self._environ[key_name]

        # keyring 类后端支持命名检索（dpapi/fernet 不支持）
        if self._secure.backend_name not in ("dpapi", "fernet"):
            try:
                val = self._secure.retrieve(key_name)
                if val:
                    return val
            except Exception:
                logger.debug("secure storage retrieve failed for %s", key_name, exc_info=True)

        if self._settings is not None:
            try:
                stored = str(self._settings.get_setting(key_name, "") or "")
                if stored and self._secure.is_secure_token(stored):
                    try:
                        return self._secure.decrypt(stored)
                    except Exception:
                        logger.warning(
                            "secure token decrypt failed for %s", key_name, exc_info=True
                        )
            except Exception:
                logger.debug("settings decrypt failed for %s", key_name, exc_info=True)
        return None

    def decrypt_token(self, token: str, *, key_name: str = "", migrate: bool = True) -> str:
        """解密安全存储 token；可选迁移旧版 Fernet / 明文。

        Args:
            token: 待解密的 token（可能是 dpapi:xxx / fernet:xxx / 明文）
            key_name: 用于迁移时的 key_id（推荐提供）
            migrate: True 时遇到旧版格式自动迁移到新安全存储

        Returns:
            明文值（空 token 返回空字符串；解密失败返回原 token 避免数据丢失）
        """
        if not token:
            return token
        # 新版安全 token：直接解密
        if self._secure.is_secure_token(token):
            try:
                return self._secure.decrypt(token)
            except Exception:
                logger.warning("secure token decrypt failed for %s", key_name, exc_info=True)
                return token
        # 旧版 Fernet token：解密并透明迁移
        if self._secure.is_legacy_fernet(token):
            plaintext = self._decrypt_legacy_fernet(token)
            if migrate and key_name and plaintext and self._settings is not None:
                self._migrate_to_secure(plaintext, key_name)
            return plaintext
        # 明文：透明迁移到加密 token
        if migrate and key_name and token and self._settings is not None:
            self._migrate_to_secure(token, key_name)
        return token

    def resolve_provider_key(self, provider: dict[str, Any]) -> str:
        """解析 provider_configs 中单个 provider 的 api_key。

        Args:
            provider: provider 配置 dict（含 name + api_key）

        Returns:
            明文 api_key
        """
        stored = provider.get("api_key", "") or ""
        if not stored:
            return stored
        provider_name = provider.get("name", "unnamed")
        key_id = f"provider/{provider_name}"
        return self.decrypt_token(stored, key_name=key_id, migrate=True)

    def is_secure_token(self, value: str) -> bool:
        """判断 value 是否为 SecureKeyStorage 生成的安全 token。"""
        return self._secure.is_secure_token(value)

    def is_legacy_fernet(self, value: str) -> bool:
        """判断 value 是否为旧版 core.crypto_utils Fernet token。"""
        return self._secure.is_legacy_fernet(value)

    def _decrypt_legacy_fernet(self, token: str) -> str:
        """使用旧版 core.crypto_utils 解密 Fernet token。"""
        if not self._data_dir:
            return token
        try:
            from core.crypto_utils import decrypt as _legacy_decrypt

            return _legacy_decrypt(token, self._data_dir)
        except Exception:
            logger.debug("legacy Fernet decrypt failed", exc_info=True)
            return token

    def _migrate_to_secure(self, plaintext: str, key_name: str) -> None:
        """将明文迁移到安全存储 token 并写回 settings。"""
        if not plaintext or not key_name or self._settings is None:
            return
        try:
            migrated = self._secure.encrypt(plaintext, key_id=key_name)
            self._settings.set_setting(key_name, migrated)
            logger.info("已迁移密钥到安全存储: %s", key_name)
        except Exception:
            logger.debug("migrate to secure failed for %s", key_name, exc_info=True)


_default_resolver: SecretResolver | None = None


def get_default_resolver() -> SecretResolver:
    """获取全局默认 SecretResolver。

    单例延迟创建，便于测试通过 set_default_resolver() 替换。
    """
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = SecretResolver()
    return _default_resolver


def set_default_resolver(resolver: SecretResolver) -> None:
    """设置全局默认实例（测试钩子）。"""
    global _default_resolver
    _default_resolver = resolver


def reset_default_resolver() -> None:
    """重置全局默认实例（测试钩子）。"""
    global _default_resolver
    _default_resolver = None


__all__ = [
    "SecretResolver",
    "LLM_FLAT_KEY_ENV_MAP",
    "get_default_resolver",
    "set_default_resolver",
    "reset_default_resolver",
]
