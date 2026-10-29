# -*- coding: utf-8 -*-
"""跨平台安全密钥存储抽象。

优先使用 OS 原生安全机制：
  - Windows: DPAPI (CryptProtectData / CryptUnprotectData) via ctypes
  - macOS: Keychain Services via ctypes (Security.framework)
  - Linux: keyring 库（如已安装）
不可用时回退到 Fernet，密钥从机器特征派生。

提供两种使用模式：
  1. 命名存储：store(key_id, value) / retrieve(key_id) / delete(key_id)
  2. 值级加密：encrypt(value, key_id=None) -> token / decrypt(token) -> value
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import logging
import os
import platform
import sys
import uuid
from typing import Any

try:
    from cryptography.fernet import Fernet, InvalidToken

    _HAS_CRYPTO = True
except ImportError:  # pragma: no cover - cryptography 缺失环境
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment, misc]
    _HAS_CRYPTO = False

logger = logging.getLogger("siyi.core.secure_key_storage")

# ── token 前缀：标识加密来源，解密时按前缀路由 ──
_PREFIX_DPAPI = "dpapi:"
_PREFIX_FERNET = "fernet:"
_PREFIX_KEYCHAIN = "keychain:"
_PREFIX_SECRET = "secret:"
#: 旧版 core.crypto_utils 生成的裸 Fernet token（无前缀）
_FERNET_LEGACY_PREFIX = "gAAAAA"


class SecureKeyStorageError(Exception):
    """安全存储操作异常。"""


class _Backend:
    """后端抽象。"""

    name = "unknown"

    def encrypt(self, value: str, key_id: str | None = None) -> str:
        raise NotImplementedError

    def decrypt(self, token: str) -> str:
        raise NotImplementedError

    def store(self, key_id: str, value: str) -> None:
        raise NotImplementedError

    def retrieve(self, key_id: str) -> str | None:
        raise NotImplementedError

    def delete(self, key_id: str) -> bool:
        raise NotImplementedError


class _DpapiBackend(_Backend):
    """Windows DPAPI 后端。"""

    name = "dpapi"

    def __init__(self) -> None:
        self._crypt32 = ctypes.windll.crypt32
        self._get_last_error = ctypes.get_last_error

        class _DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.c_uint32),
                ("pbData", ctypes.POINTER(ctypes.c_byte)),
            ]

        self._DATA_BLOB = _DATA_BLOB

    def _protect(self, data: bytes) -> bytes:
        buf = ctypes.create_string_buffer(data)
        blob_in = self._DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
        blob_out = self._DATA_BLOB()
        if not self._crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            err = self._get_last_error()
            raise SecureKeyStorageError(f"CryptProtectData failed: {err}")
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)

    def _unprotect(self, data: bytes) -> bytes:
        buf = ctypes.create_string_buffer(data)
        blob_in = self._DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
        blob_out = self._DATA_BLOB()
        if not self._crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            err = self._get_last_error()
            raise SecureKeyStorageError(f"CryptUnprotectData failed: {err}")
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)

    def encrypt(self, value: str, key_id: str | None = None) -> str:
        blob = self._protect(value.encode("utf-8"))
        return _PREFIX_DPAPI + base64.b64encode(blob).decode("ascii")

    def decrypt(self, token: str) -> str:
        raw = token[len(_PREFIX_DPAPI):]
        blob = base64.b64decode(raw)
        return self._unprotect(blob).decode("utf-8")

    def store(self, key_id: str, value: str) -> None:
        raise SecureKeyStorageError("DPAPI backend does not support named store; use encrypt/decrypt")

    def retrieve(self, key_id: str) -> str | None:
        raise SecureKeyStorageError("DPAPI backend does not support named retrieve; use encrypt/decrypt")

    def delete(self, key_id: str) -> bool:
        raise SecureKeyStorageError("DPAPI backend does not support named delete; use encrypt/decrypt")


class _KeychainBackend(_Backend):
    """macOS Keychain Services 后端。"""

    name = "keychain"
    _ERR_DUPLICATE = -25299
    _ERR_ITEM_NOT_FOUND = -25300

    def __init__(self, namespace: str) -> None:
        import ctypes
        import ctypes.util

        self._namespace = namespace
        self._sec = ctypes.CDLL(ctypes.util.find_library("Security") or "Security")
        self._cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation") or "CoreFoundation")

        self._CFStringCreateWithCString = self._cf.CFStringCreateWithCString
        self._CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        self._CFStringCreateWithCString.restype = ctypes.c_void_p

        self._CFDataCreate = self._cf.CFDataCreate
        self._CFDataCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8), ctypes.c_long]
        self._CFDataCreate.restype = ctypes.c_void_p

        self._CFDataGetBytePtr = self._cf.CFDataGetBytePtr
        self._CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
        self._CFDataGetBytePtr.restype = ctypes.POINTER(ctypes.c_uint8)

        self._CFDataGetLength = self._cf.CFDataGetLength
        self._CFDataGetLength.argtypes = [ctypes.c_void_p]
        self._CFDataGetLength.restype = ctypes.c_long

        self._CFRelease = self._cf.CFRelease
        self._CFRelease.argtypes = [ctypes.c_void_p]

        self._CFDictionaryCreateMutable = self._cf.CFDictionaryCreateMutable
        self._CFDictionaryCreateMutable.argtypes = [
            ctypes.c_void_p,
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._CFDictionaryCreateMutable.restype = ctypes.c_void_p

        self._CFDictionaryAddValue = self._cf.CFDictionaryAddValue
        self._CFDictionaryAddValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]

        self._SecItemAdd = self._sec.SecItemAdd
        self._SecItemAdd.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._SecItemAdd.restype = ctypes.c_int

        self._SecItemCopyMatching = self._sec.SecItemCopyMatching
        self._SecItemCopyMatching.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._SecItemCopyMatching.restype = ctypes.c_int

        self._SecItemUpdate = self._sec.SecItemUpdate
        self._SecItemUpdate.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._SecItemUpdate.restype = ctypes.c_int

        self._SecItemDelete = self._sec.SecItemDelete
        self._SecItemDelete.argtypes = [ctypes.c_void_p]
        self._SecItemDelete.restype = ctypes.c_int

        self._kCFBooleanTrue = ctypes.c_void_p.in_dll(self._cf, "kCFBooleanTrue")
        self._kCFTypeDictionaryKeyCallBacks = ctypes.c_void_p.in_dll(
            self._cf, "kCFTypeDictionaryKeyCallBacks"
        )
        self._kCFTypeDictionaryValueCallBacks = ctypes.c_void_p.in_dll(
            self._cf, "kCFTypeDictionaryValueCallBacks"
        )

        # Sec* 属性键（CFString 常量）
        self._kSecClass = self._cfstr("kSecClass")
        self._kSecClassGenericPassword = self._cfstr("genp")
        self._kSecAttrService = self._cfstr("svce")
        self._kSecAttrAccount = self._cfstr("acct")
        self._kSecValueData = self._cfstr("v_Data")
        self._kSecReturnData = self._cfstr("r_Data")
        self._kSecMatchLimit = self._cfstr("m_Limit")
        self._kSecMatchLimitOne = self._cfstr("one")

    def _cfstr(self, s: str) -> ctypes.c_void_p:
        # kCFStringEncodingUTF8 = 0x08000100
        return self._CFStringCreateWithCString(None, s.encode("utf-8"), 134217984)

    def _make_query(self, key_id: str, include_value: bool = False) -> ctypes.c_void_p:
        query = self._CFDictionaryCreateMutable(
            None, 0,
            self._kCFTypeDictionaryKeyCallBacks,
            self._kCFTypeDictionaryValueCallBacks,
        )
        self._CFDictionaryAddValue(query, self._kSecClass, self._kSecClassGenericPassword)
        self._CFDictionaryAddValue(query, self._kSecAttrService, self._cfstr(self._namespace))
        self._CFDictionaryAddValue(query, self._kSecAttrAccount, self._cfstr(key_id))
        if include_value:
            self._CFDictionaryAddValue(query, self._kSecReturnData, self._kCFBooleanTrue)
            self._CFDictionaryAddValue(query, self._kSecMatchLimit, self._kSecMatchLimitOne)
        return query

    def _set_password(self, key_id: str, value: str) -> None:
        query = self._make_query(key_id)
        data = value.encode("utf-8")
        buf = (ctypes.c_uint8 * len(data))(*data)
        cf_data = self._CFDataCreate(None, buf, len(data))
        self._CFDictionaryAddValue(query, self._kSecValueData, cf_data)
        status = self._SecItemAdd(query, None)
        if status == self._ERR_DUPLICATE:
            # 已存在则更新
            update_query = self._CFDictionaryCreateMutable(
                None, 0,
                self._kCFTypeDictionaryKeyCallBacks,
                self._kCFTypeDictionaryValueCallBacks,
            )
            cf_data_update = self._CFDataCreate(None, buf, len(data))
            self._CFDictionaryAddValue(update_query, self._kSecValueData, cf_data_update)
            search = self._make_query(key_id)
            status = self._SecItemUpdate(search, update_query)
            self._CFRelease(update_query)
            self._CFRelease(cf_data_update)
        self._CFRelease(cf_data)
        if status != 0:
            raise SecureKeyStorageError(f"Keychain SecItemAdd/Update failed: {status}")

    def _get_password(self, key_id: str) -> str | None:
        query = self._make_query(key_id, include_value=True)
        result = ctypes.c_void_p()
        status = self._SecItemCopyMatching(query, ctypes.byref(result))
        if status == self._ERR_ITEM_NOT_FOUND:
            return None
        if status != 0:
            raise SecureKeyStorageError(f"Keychain SecItemCopyMatching failed: {status}")
        length = self._CFDataGetLength(result)
        ptr = self._CFDataGetBytePtr(result)
        value = bytes(ptr[i] for i in range(length)).decode("utf-8")
        self._CFRelease(result)
        return value

    def _delete_password(self, key_id: str) -> bool:
        query = self._make_query(key_id)
        status = self._SecItemDelete(query)
        return status == 0 or status == self._ERR_ITEM_NOT_FOUND

    def encrypt(self, value: str, key_id: str | None = None) -> str:
        key_id = key_id or self._generate_id()
        self._set_password(key_id, value)
        return _PREFIX_KEYCHAIN + key_id

    def decrypt(self, token: str) -> str:
        key_id = token[len(_PREFIX_KEYCHAIN):]
        value = self._get_password(key_id)
        if value is None:
            raise SecureKeyStorageError(f"Keychain item not found: {key_id}")
        return value

    def store(self, key_id: str, value: str) -> None:
        self._set_password(key_id, value)

    def retrieve(self, key_id: str) -> str | None:
        return self._get_password(key_id)

    def delete(self, key_id: str) -> bool:
        return self._delete_password(key_id)

    @staticmethod
    def _generate_id() -> str:
        return uuid.uuid4().hex


class _KeyringBackend(_Backend):
    """跨平台 keyring 库后端。"""

    name = "keyring"

    def __init__(self, namespace: str) -> None:
        import keyring

        self._namespace = namespace
        self._keyring = keyring

    def encrypt(self, value: str, key_id: str | None = None) -> str:
        key_id = key_id or uuid.uuid4().hex
        self._keyring.set_password(self._namespace, key_id, value)
        return _PREFIX_SECRET + key_id

    def decrypt(self, token: str) -> str:
        key_id = token[len(_PREFIX_SECRET):]
        value = self._keyring.get_password(self._namespace, key_id)
        if value is None:
            raise SecureKeyStorageError(f"Keyring item not found: {key_id}")
        return value

    def store(self, key_id: str, value: str) -> None:
        self._keyring.set_password(self._namespace, key_id, value)

    def retrieve(self, key_id: str) -> str | None:
        return self._keyring.get_password(self._namespace, key_id)

    def delete(self, key_id: str) -> bool:
        try:
            self._keyring.delete_password(self._namespace, key_id)
            return True
        except Exception:
            return False


class _FernetBackend(_Backend):
    """Fernet 回退后端，密钥从机器特征派生。"""

    name = "fernet"

    def __init__(self, data_dir: str | None = None) -> None:
        self._data_dir = data_dir
        self._fernet = Fernet(self._derive_key()) if _HAS_CRYPTO else None

    def _derive_key(self) -> bytes:
        # 机器特征：主机名 | 用户名 | 平台串
        machine_id = "|".join([
            platform.node(),
            os.getenv("USERNAME") or os.getenv("USER") or "",
            platform.platform(),
        ])
        salt = b"siyi_secure_key_storage_v1"
        key = hashlib.pbkdf2_hmac("sha256", machine_id.encode("utf-8"), salt, 100000, dklen=32)
        return base64.urlsafe_b64encode(key)

    def encrypt(self, value: str, key_id: str | None = None) -> str:
        if self._fernet is None:
            raise SecureKeyStorageError("cryptography 未安装，无法使用 Fernet 回退")
        token = self._fernet.encrypt(value.encode("utf-8"))
        return _PREFIX_FERNET + token.decode("ascii")

    def decrypt(self, token: str) -> str:
        if self._fernet is None:
            raise SecureKeyStorageError("cryptography 未安装，无法使用 Fernet 回退")
        raw = token[len(_PREFIX_FERNET):]
        try:
            return self._fernet.decrypt(raw.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SecureKeyStorageError("Fernet token 无效或机器特征已变更") from exc

    def store(self, key_id: str, value: str) -> None:
        raise SecureKeyStorageError("Fernet backend does not support named store; use encrypt/decrypt")

    def retrieve(self, key_id: str) -> str | None:
        raise SecureKeyStorageError("Fernet backend does not support named retrieve; use encrypt/decrypt")

    def delete(self, key_id: str) -> bool:
        raise SecureKeyStorageError("Fernet backend does not support named delete; use encrypt/decrypt")


class SecureKeyStorage:
    """跨平台安全密钥存储门面。"""

    def __init__(
        self,
        namespace: str = "com.siyi.apikey",
        fallback_data_dir: str | None = None,
        prefer_keyring: bool = True,
    ) -> None:
        self.namespace = namespace
        self.fallback_data_dir = fallback_data_dir
        self._backend = self._create_backend(prefer_keyring)

    def _create_backend(self, prefer_keyring: bool) -> _Backend:
        if prefer_keyring:
            try:
                import keyring

                return _KeyringBackend(self.namespace)
            except Exception as exc:
                logger.debug("keyring backend unavailable: %s", exc)
        if sys.platform == "win32":
            try:
                return _DpapiBackend()
            except Exception as exc:
                logger.warning("DPAPI backend unavailable: %s", exc)
        elif sys.platform == "darwin":
            try:
                return _KeychainBackend(self.namespace)
            except Exception as exc:
                logger.warning("Keychain backend unavailable: %s", exc)
        logger.warning("OS keychain unavailable, falling back to Fernet with machine-derived key")
        return _FernetBackend(self.fallback_data_dir)

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def encrypt(self, value: str, key_id: str | None = None) -> str:
        """加密字符串，返回带前缀的 token。

        如提供了 key_id（推荐），则 macOS/Linux keyring 会使用稳定的 key_id；
        否则每次生成新条目。Windows DPAPI 不使用 key_id。
        """
        if not value:
            return value
        try:
            return self._backend.encrypt(value, key_id)
        except Exception as exc:
            logger.warning("%s encrypt failed: %s", self._backend.name, exc)
            if self._backend.name != "fernet":
                logger.warning("Falling back to Fernet encryption")
                fallback = _FernetBackend(self.fallback_data_dir)
                return fallback.encrypt(value, key_id)
            raise

    def decrypt(self, token: str) -> str:
        """解密 token。非安全 token（未知前缀）原样返回。"""
        if not token:
            return token
        if token.startswith(_PREFIX_DPAPI):
            return self._backend.decrypt(token)
        if token.startswith(_PREFIX_KEYCHAIN):
            return self._backend.decrypt(token)
        if token.startswith(_PREFIX_SECRET):
            return self._backend.decrypt(token)
        if token.startswith(_PREFIX_FERNET):
            return self._backend.decrypt(token)
        # 未知前缀：可能是明文或旧格式，原样返回
        return token

    def store(self, key_id: str, value: str) -> None:
        """以 value 以 key_id 为标识存入安全存储（仅 keyring 系）。"""
        if self._backend.name in ("dpapi", "fernet"):
            raise SecureKeyStorageError(
                f"{self._backend.name} backend does not support named store; use encrypt/decrypt"
            )
        self._backend.store(key_id, value)

    def retrieve(self, key_id: str) -> str | None:
        """按 key_id 读取安全存储中的值。"""
        if self._backend.name in ("dpapi", "fernet"):
            raise SecureKeyStorageError(
                f"{self._backend.name} backend does not support named retrieve; use encrypt/decrypt"
            )
        return self._backend.retrieve(key_id)

    def delete(self, key_id: str) -> bool:
        """按 key_id 删除安全存储中的值。"""
        if self._backend.name in ("dpapi", "fernet"):
            raise SecureKeyStorageError(
                f"{self._backend.name} backend does not support named delete; use encrypt/decrypt"
            )
        return self._backend.delete(key_id)

    def is_secure_token(self, value: str) -> bool:
        """判断 value 是否为本模块生成的安全 token。"""
        if not isinstance(value, str):
            return False
        return any(
            value.startswith(prefix)
            for prefix in (_PREFIX_DPAPI, _PREFIX_KEYCHAIN, _PREFIX_SECRET, _PREFIX_FERNET)
        )

    def is_legacy_fernet(self, value: str) -> bool:
        """判断 value 是否为旧版 core.crypto_utils Fernet token。"""
        return isinstance(value, str) and bool(value) and value.startswith(_FERNET_LEGACY_PREFIX)


_default_storage: SecureKeyStorage | None = None


def get_default_storage() -> SecureKeyStorage:
    """获取全局默认 SecureKeyStorage 实例。"""
    global _default_storage
    if _default_storage is None:
        _default_storage = SecureKeyStorage()
    return _default_storage


def set_default_storage(storage: SecureKeyStorage) -> None:
    """设置全局默认实例（测试钩子）。"""
    global _default_storage
    _default_storage = storage


def reset_default_storage() -> None:
    """重置全局默认实例（测试钩子）。"""
    global _default_storage
    _default_storage = None


def encrypt(value: str, key_id: str | None = None) -> str:
    """使用全局默认实例加密。"""
    return get_default_storage().encrypt(value, key_id)


def decrypt(token: str) -> str:
    """使用全局默认实例解密。"""
    return get_default_storage().decrypt(token)


__all__ = [
    "SecureKeyStorage",
    "SecureKeyStorageError",
    "get_default_storage",
    "set_default_storage",
    "reset_default_storage",
    "encrypt",
    "decrypt",
]
