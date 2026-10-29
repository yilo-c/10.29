"""Tests for core.crypto_utils."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.crypto_utils import _FERNET_PREFIX, decrypt, encrypt, is_encrypted


class TestCryptoUtils:
    """加密/解密工具函数的 pytest 测试。"""

    def test_roundtrip_encrypt_decrypt(self, tmp_path):
        """加密后再解密应得到原文。"""
        data_dir = str(tmp_path / "roundtrip")
        plaintext = "hello world"
        ciphertext = encrypt(plaintext, data_dir)
        assert is_encrypted(ciphertext)
        assert decrypt(ciphertext, data_dir) == plaintext

    def test_is_encrypted(self, tmp_path):
        """is_encrypted 对加密值返回 True，对普通文本返回 False。"""
        data_dir = str(tmp_path / "is_encrypted")
        encrypted = encrypt("secret", data_dir)
        assert is_encrypted(encrypted) is True
        assert is_encrypted("plain text") is False
        assert is_encrypted("") is False
        assert is_encrypted("gAAAAA") is True

    def test_decrypt_plain_text_returns_unchanged(self, tmp_path):
        """解密普通文本时原样返回。"""
        data_dir = str(tmp_path / "plain")
        plain = "not encrypted"
        assert decrypt(plain, data_dir) == plain

    def test_empty_string_unchanged(self, tmp_path):
        """空字符串在加密/解密后保持为空。"""
        data_dir = str(tmp_path / "empty")
        assert encrypt("", data_dir) == ""
        assert decrypt("", data_dir) == ""
        assert is_encrypted("") is False

    def test_different_data_dir_creates_different_keys(self, tmp_path):
        """不同 data_dir 生成不同密钥，导致同一明文加密结果不同。"""
        dir_a = str(tmp_path / "dir_a")
        dir_b = str(tmp_path / "dir_b")
        plaintext = "same input"
        cipher_a = encrypt(plaintext, dir_a)
        cipher_b = encrypt(plaintext, dir_b)
        assert cipher_a != cipher_b
        assert cipher_a.startswith(_FERNET_PREFIX)
        assert cipher_b.startswith(_FERNET_PREFIX)

    def test_key_persistence(self, tmp_path):
        """同一 data_dir 下，先前加密的数据能用持久化密钥解密。"""
        data_dir = str(tmp_path / "persist")
        plaintext = "persistent secret"
        ciphertext = encrypt(plaintext, data_dir)
        # 再次调用 decrypt，应加载同一密钥文件并还原原文
        assert decrypt(ciphertext, data_dir) == plaintext
        # 密钥文件已持久化到磁盘
        assert (tmp_path / "persist" / ".siyi_key").is_file()
