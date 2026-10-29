"""安全凭据存储模块。

提供：
- API Key 加密存储（使用 Fernet 对称加密）
- 凭据的增删改查
- 自动迁移明文凭据为加密存储
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from core import crypto_utils

logger = logging.getLogger("siyi.core.credential_store")


class CredentialStore:
    """安全凭据存储。

    加密策略：
    - 使用 Fernet 对称加密（密钥由 core/crypto_utils.py 管理并存储在 data_dir/.siyi_key）

    存储格式：
    - 凭据以 JSON 文件存储
    - 每个凭据包含：加密后的值、版本号
    """
    
    def __init__(self, data_dir: str | Path | None = None):
        self._data_dir = Path(data_dir) if data_dir else self._default_data_dir()
        self._credentials_file = self._data_dir / "credentials.json"
        self._credentials: dict[str, dict[str, Any]] = {}
        self._fernet: Fernet | None = None
        self._load()
        self._init_fernet()
    
    @staticmethod
    def _default_data_dir() -> Path:
        """获取默认数据目录。"""
        # 优先使用环境变量
        if env_dir := os.getenv("SIYI_DATA_DIR"):
            return Path(env_dir)
        # 默认在用户目录
        return Path.home() / ".siyi" / "data"
    
    def _load(self) -> None:
        """加载凭据文件。"""
        if self._credentials_file.exists():
            try:
                with open(self._credentials_file, "r", encoding="utf-8") as f:
                    self._credentials = json.load(f)
                logger.debug("Loaded %d credentials", len(self._credentials))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load credentials: %s", e)
                self._credentials = {}
        else:
            self._credentials = {}
    
    def _save(self) -> None:
        """保存凭据文件。"""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        
        # 原子写入
        temp_file = self._credentials_file.with_suffix(".json.tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self._credentials, f, indent=2)
            temp_file.replace(self._credentials_file)
            logger.debug("Saved %d credentials", len(self._credentials))
        except OSError as e:
            logger.error("Failed to save credentials: %s", e)
            if temp_file.exists():
                temp_file.unlink()
    
    def _init_fernet(self) -> None:
        """初始化 Fernet 实例。"""
        key = crypto_utils._load_or_create_key(str(self._data_dir))
        if key is None:
            raise RuntimeError("无法初始化凭据加密：cryptography 未安装或密钥创建失败")
        self._fernet = Fernet(key)


    def _encrypt(self, plaintext: str) -> dict[str, str]:
        """加密明文。

        Returns:
            包含加密数据的字典：{ciphertext, version}
        """
        if self._fernet is None:
            raise RuntimeError("Fernet 未初始化")
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return {
            "ciphertext": token.decode("ascii"),
            "version": "2",
        }

    def _decrypt(self, data: dict[str, str]) -> str:
        """解密密文。

        Args:
            data: 包含加密数据的字典

        Returns:
            解密后的明文
        """
        if data.get("version") == "1":
            return self._decrypt_legacy(data)
        if self._fernet is None:
            raise RuntimeError("Fernet 未初始化")
        try:
            token = data["ciphertext"].encode("ascii")
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken:
            raise ValueError("凭证解密失败：密钥不匹配或数据已损坏")

    def _decrypt_legacy(self, data: dict[str, str]) -> str:
        """兼容旧版 XOR 加密（V1）。新写入全部使用 V2 Fernet。"""
        import base64
        import hashlib
        import secrets

        key = self._legacy_get_encryption_key()
        ciphertext = base64.b64decode(data["ciphertext"])
        iv = base64.b64decode(data["iv"])
        stored_hmac = base64.b64decode(data.get("hmac", ""))

        enc_key = hashlib.sha256(key + iv + b"encryption").digest()
        hmac_key = hashlib.sha256(key + iv + b"hmac").digest()

        computed_hmac = hashlib.sha256(hmac_key + ciphertext).digest()
        if not stored_hmac:
            raise ValueError("旧版凭证缺少 HMAC，拒绝解密")
        if not hmac.compare_digest(computed_hmac, stored_hmac):
            raise ValueError("HMAC verification failed - data may be corrupted")

        decrypted = bytes(
            c ^ enc_key[i % len(enc_key)]
            for i, c in enumerate(ciphertext)
        )
        return decrypted.decode("utf-8")

    def _legacy_get_encryption_key(self) -> bytes:
        """旧版密钥派生（仅用于迁移旧凭证）。"""
        import hashlib
        import platform

        machine_id = "|".join([
            platform.node(),
            os.getenv("USERNAME", os.getenv("USER", "")),
            platform.platform(),
        ])
        salt = b"siyi_credential_store_v1"
        return hashlib.pbkdf2_hmac(
            "sha256",
            machine_id.encode("utf-8"),
            salt,
            iterations=100000,
        )
    
    def set(self, key: str, value: str) -> None:
        """存储凭据。
        
        Args:
            key: 凭据标识（如 "api_key_deepseek"）
            value: 凭据值
        """
        encrypted = self._encrypt(value)
        self._credentials[key] = {
            "encrypted": True,
            "data": encrypted,
        }
        self._save()
        logger.info("Stored credential: %s", key)
    
    def get(self, key: str) -> str | None:
        """获取凭据。
        
        Args:
            key: 凭据标识
        
        Returns:
            凭据值，如果不存在则返回 None
        """
        entry = self._credentials.get(key)
        if not entry:
            return None
        
        # 检查是否是加密格式
        if entry.get("encrypted"):
            try:
                return self._decrypt(entry["data"])
            except Exception as e:
                logger.error("Failed to decrypt credential %s: %s", key, e)
                return None
        
        # 兼容旧的明文格式
        if "value" in entry:
            # 自动迁移为加密格式
            logger.info("Migrating plaintext credential to encrypted: %s", key)
            self.set(key, entry["value"])
            return entry["value"]
        
        return None
    
    def delete(self, key: str) -> bool:
        """删除凭据。
        
        Args:
            key: 凭据标识
        
        Returns:
            是否成功删除
        """
        if key in self._credentials:
            del self._credentials[key]
            self._save()
            logger.info("Deleted credential: %s", key)
            return True
        return False
    
    def list_keys(self) -> list[str]:
        """列出所有凭据标识。"""
        return list(self._credentials.keys())
    
    def has(self, key: str) -> bool:
        """检查凭据是否存在。"""
        return key in self._credentials
    
    def migrate_from_settings(self, settings: dict[str, Any]) -> int:
        """从 settings 字典迁移 API Key 到加密存储。
        
        Args:
            settings: 设置字典，可能包含明文的 API Key
        
        Returns:
            迁移的凭据数量
        """
        migrated = 0
        
        # 已知的 API Key 字段
        api_key_fields = [
            ("llm_api_key", "api_key_deepseek"),
            ("llm_fallback_api_key", "api_key_fallback"),
            ("moonshot_api_key", "api_key_moonshot"),
            ("openai_api_key", "api_key_openai"),
        ]
        
        for settings_key, credential_key in api_key_fields:
            value = settings.get(settings_key)
            if value and not self.has(credential_key):
                self.set(credential_key, value)
                migrated += 1
                logger.info("Migrated %s to encrypted storage", settings_key)
        
        return migrated


# 全局实例
_credential_store: CredentialStore | None = None


def get_credential_store(data_dir: str | Path | None = None) -> CredentialStore:
    """获取全局凭据存储实例。"""
    global _credential_store
    if _credential_store is None:
        _credential_store = CredentialStore(data_dir)
    return _credential_store


def reset_credential_store() -> None:
    """重置全局实例（用于测试）。"""
    global _credential_store
    _credential_store = None