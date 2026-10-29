"""V3-03 验收测试：SecretResolver.resolve_secret 三段优先级。

覆盖 GenericToolBridge._default_secret_resolver 背后的密钥解析链路：
  1. 系统环境变量（优先级最高）
  2. SecureKeyStorage.retrieve（keyring 类后端）
  3. settings 加密 token 解密（SecureKeyStorage.decrypt；失败返回 None，不抛）

另覆盖桥接入口 GenericToolBridge._default_secret_resolver 的委托与兜底行为。
"""
from __future__ import annotations

from typing import Any

from core.secret_resolver import (
    SecretResolver,
    set_default_resolver,
    reset_default_resolver,
)
import pytest

from services.tooling.generic_tool_bridge import GenericToolBridge


class FakeSecureStorage:
    """SecureKeyStorage 桩：token 形如 "secure:xxx"，可配置解密失败。"""

    backend_name = "keyring"

    def __init__(
        self,
        keyring_values: dict[str, str] | None = None,
        plaintext: str = "settings-decrypted",
        fail_decrypt: bool = False,
        backend_name: str = "keyring",
    ) -> None:
        self._keyring = keyring_values or {}
        self._plaintext = plaintext
        self._fail_decrypt = fail_decrypt
        self.backend_name = backend_name

    def retrieve(self, key_id: str) -> str:
        if key_id not in self._keyring:
            raise KeyError(key_id)
        return self._keyring[key_id]

    def is_secure_token(self, value: str) -> bool:
        return isinstance(value, str) and value.startswith("secure:")

    def decrypt(self, token: str) -> str:
        if self._fail_decrypt:
            raise ValueError("decrypt boom")
        return self._plaintext

    def encrypt(self, plaintext: str, key_id: str = "") -> str:
        return f"secure:{plaintext}"


class FakeSettings:
    """settings_repository 桩：dict 读写。"""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self._values = values or {}

    def get_setting(self, key: str, default: Any = "") -> Any:
        return self._values.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        self._values[key] = value


SECURE_TOKEN = "secure:abc123"


def _resolver(
    *,
    environ: dict[str, str] | None = None,
    keyring_values: dict[str, str] | None = None,
    settings_values: dict[str, Any] | None = None,
    fail_decrypt: bool = False,
    backend_name: str = "keyring",
) -> SecretResolver:
    return SecretResolver(
        secure_storage=FakeSecureStorage(  # type: ignore[arg-type]
            keyring_values=keyring_values,
            fail_decrypt=fail_decrypt,
            backend_name=backend_name,
        ),
        settings_repository=FakeSettings(settings_values),
        environ=environ or {},
    )


# ── 三段优先级 ──────────────────────────────────────────────


def test_env_has_highest_priority() -> None:
    """env > keyring > settings：三段同时命中时取环境变量。"""
    r = _resolver(
        environ={"GH_TOKEN": "env-val"},
        keyring_values={"GH_TOKEN": "keyring-val"},
        settings_values={"GH_TOKEN": SECURE_TOKEN},
    )
    assert r.resolve_secret("GH_TOKEN") == "env-val"


def test_keyring_beats_settings() -> None:
    """keyring > settings：env 缺失、keyring 与 settings 同时命中时取 keyring。"""
    r = _resolver(
        keyring_values={"GH_TOKEN": "keyring-val"},
        settings_values={"GH_TOKEN": SECURE_TOKEN},
    )
    assert r.resolve_secret("GH_TOKEN") == "keyring-val"


def test_settings_decrypt_third_segment() -> None:
    """第三段：env/keyring 均未命中，settings 存安全 token → 解密返回明文。"""
    r = _resolver(settings_values={"GH_TOKEN": SECURE_TOKEN})
    assert r.resolve_secret("GH_TOKEN") == "settings-decrypted"


def test_settings_decrypt_failure_returns_none_without_raising() -> None:
    """解密失败返回 None，不抛异常（V3-03 明确要求）。"""
    r = _resolver(settings_values={"GH_TOKEN": SECURE_TOKEN}, fail_decrypt=True)
    assert r.resolve_secret("GH_TOKEN") is None


# ── 边界行为 ────────────────────────────────────────────────


def test_non_keyring_backend_skips_retrieve_and_falls_to_settings() -> None:
    """dpapi/fernet 后端不支持命名检索，直接落到 settings 第三段。"""
    r = _resolver(
        keyring_values={"GH_TOKEN": "keyring-val"},  # 即使有值也不应被读取
        settings_values={"GH_TOKEN": SECURE_TOKEN},
        backend_name="dpapi",
    )
    assert r.resolve_secret("GH_TOKEN") == "settings-decrypted"


def test_plaintext_settings_value_is_not_returned() -> None:
    """settings 明文（非安全 token）不经第三段返回——只解密安全 token。"""
    r = _resolver(settings_values={"GH_TOKEN": "raw-plaintext"})
    assert r.resolve_secret("GH_TOKEN") is None


def test_missing_everywhere_returns_none() -> None:
    r = _resolver()
    assert r.resolve_secret("GH_TOKEN") is None


def test_keyring_retrieve_missing_key_falls_to_settings() -> None:
    """keyring 后端在位但该 key 不存在（retrieve 抛 KeyError）→ 落到 settings。"""
    r = _resolver(settings_values={"GH_TOKEN": SECURE_TOKEN})
    assert r.resolve_secret("GH_TOKEN") == "settings-decrypted"


# ── 桥接入口：GenericToolBridge._default_secret_resolver ────


@pytest.fixture(autouse=True)
def _reset_global_resolver():
    reset_default_resolver()
    yield
    reset_default_resolver()


def test_bridge_default_resolver_delegates_to_global_resolver() -> None:
    """桥接默认解析器委托全局 SecretResolver，继承三段优先级。"""
    set_default_resolver(
        _resolver(settings_values={"GH_TOKEN": SECURE_TOKEN})
    )
    assert GenericToolBridge._default_secret_resolver("GH_TOKEN") == "settings-decrypted"


def test_bridge_default_resolver_swallows_exceptions() -> None:
    """全局解析器抛异常时桥接入口返回 None，不向上传播。"""

    class ExplodingResolver:
        def resolve_secret(self, key_name: str) -> str | None:
            raise RuntimeError("boom")

    set_default_resolver(ExplodingResolver())  # type: ignore[arg-type]
    assert GenericToolBridge._default_secret_resolver("GH_TOKEN") is None
