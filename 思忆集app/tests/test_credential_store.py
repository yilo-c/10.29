"""Tests for core.credential_store."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.credential_store import CredentialStore, reset_credential_store


@pytest.fixture(autouse=True)
def _reset_global_store():
    """Reset global singleton before and after each test."""
    reset_credential_store()
    yield
    reset_credential_store()


class TestCredentialStore:
    """CredentialStore 核心行为测试。"""

    def test_set_get_roundtrip(self, tmp_path):
        """set/get 往返应返回原始明文。"""
        store = CredentialStore(tmp_path)
        plaintext = "deepseek-api-key-12345"
        store.set("api_key_deepseek", plaintext)
        assert store.get("api_key_deepseek") == plaintext

    def test_get_missing_returns_none(self, tmp_path):
        """不存在的 key 返回 None。"""
        store = CredentialStore(tmp_path)
        assert store.get("nonexistent") is None

    def test_persistence_and_reload(self, tmp_path):
        """数据应持久化到 credentials.json 并能被新实例重新加载解密。"""
        plaintext = "my-secret-value"
        store1 = CredentialStore(tmp_path)
        store1.set("secret_key", plaintext)

        credentials_file = tmp_path / "credentials.json"
        assert credentials_file.exists()
        raw = json.loads(credentials_file.read_text(encoding="utf-8"))
        assert raw["secret_key"]["encrypted"] is True
        assert raw["secret_key"]["data"]["version"] == "2"
        assert "ciphertext" in raw["secret_key"]["data"]

        store2 = CredentialStore(tmp_path)
        assert store2.get("secret_key") == plaintext

    def test_empty_value_handling(self, tmp_path):
        """空字符串值应能正常 set/get。"""
        store = CredentialStore(tmp_path)
        store.set("empty_key", "")
        assert store.get("empty_key") == ""

    def test_legacy_v1_decryption(self, tmp_path):
        """手动构造的 V1 XOR 加密条目应能被 _decrypt_legacy 正确解密。"""
        store = CredentialStore(tmp_path)
        plaintext = "legacy-secret"

        key = store._legacy_get_encryption_key()
        iv = secrets.token_bytes(16)
        enc_key = hashlib.sha256(key + iv + b"encryption").digest()
        hmac_key = hashlib.sha256(key + iv + b"hmac").digest()

        ciphertext = bytes(
            c ^ enc_key[i % len(enc_key)]
            for i, c in enumerate(plaintext.encode("utf-8"))
        )
        stored_hmac = hashlib.sha256(hmac_key + ciphertext).digest()

        v1_entry = {
            "encrypted": True,
            "data": {
                "version": "1",
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
                "iv": base64.b64encode(iv).decode("ascii"),
                "hmac": base64.b64encode(stored_hmac).decode("ascii"),
            },
        }

        store._credentials["legacy_key"] = v1_entry
        assert store.get("legacy_key") == plaintext

    def test_key_rotation_returns_none(self, tmp_path):
        """删除并重新生成密钥文件后，旧密文应无法解密，get 返回 None。"""
        plaintext = "will-be-orphaned"
        store1 = CredentialStore(tmp_path)
        store1.set("rotated_key", plaintext)
        assert store1.get("rotated_key") == plaintext

        key_path = tmp_path / ".siyi_key"
        assert key_path.exists()
        os.remove(key_path)

        store2 = CredentialStore(tmp_path)
        assert key_path.exists()
        assert store2.get("rotated_key") is None
