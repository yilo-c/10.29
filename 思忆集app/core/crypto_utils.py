"""密钥加密工具 — 使用 Fernet 对称加密保护存储的 API 密钥。"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("siyi.core.crypto")

# Fernet token 总是以 gAAAAA 开头
_FERNET_PREFIX = "gAAAAA"

try:
    from cryptography.fernet import Fernet, InvalidToken

    _HAS_CRYPTO = True
except ImportError:
    Fernet = None  # type: ignore[assignment,misc]
    InvalidToken = None  # type: ignore[assignment,misc]
    _HAS_CRYPTO = False


def _get_key_path(data_dir: str) -> str:
    return os.path.join(data_dir, ".siyi_key")


def _load_or_create_key(data_dir: str) -> bytes | None:
    """加载或生成加密密钥。"""
    if not _HAS_CRYPTO:
        logger.warning("cryptography 未安装，密钥加密不可用")
        return None
    key_path = _get_key_path(data_dir)
    if os.path.isfile(key_path):
        with open(key_path, "rb") as f:
            key = f.read().strip()
        if len(key) == 44:  # Fernet key 是 44 字节 base64
            return key
        logger.warning("加密密钥文件损坏，将重新生成: %s", key_path)
    try:
        os.makedirs(os.path.dirname(key_path), exist_ok=True)
        key = Fernet.generate_key()  # type: ignore[union-attr]
        with open(key_path, "wb") as f:
            f.write(key)
        logger.info("已生成新加密密钥: %s", key_path)
        return key
    except Exception as exc:
        logger.error("生成加密密钥失败: %s", exc)
        return None


def encrypt(plaintext: str, data_dir: str) -> str:
    """加密字符串。空值或不可用时返回原文。"""
    if not plaintext or not _HAS_CRYPTO:
        return plaintext
    key = _load_or_create_key(data_dir)
    if key is None:
        return plaintext
    try:
        f = Fernet(key)
        return f.encrypt(plaintext.encode()).decode()
    except Exception as exc:
        logger.warning("加密失败: %s", exc)
        return plaintext


def decrypt(ciphertext: str, data_dir: str) -> str:
    """解密字符串。非加密文本（无 Fernet 前缀）原样返回。"""
    if not ciphertext or not _HAS_CRYPTO:
        return ciphertext
    if not ciphertext.startswith(_FERNET_PREFIX):
        return ciphertext  # 未加密的旧数据
    key = _load_or_create_key(data_dir)
    if key is None:
        return ciphertext
    try:
        f = Fernet(key)
        return f.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        logger.warning("解密失败：密钥可能已变更，保留原值")
        return ciphertext
    except Exception as exc:
        logger.warning("解密异常: %s", exc)
        return ciphertext


def is_encrypted(value: str) -> bool:
    """判断值是否已加密（通过 Fernet 前缀检测）。"""
    return bool(value and value.startswith(_FERNET_PREFIX))
