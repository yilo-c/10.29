"""认证/凭证链路（auth/token）RPC handler 行为测试。

覆盖 backend/handlers/_api_keys.py 的 handler 闭包层（此前 0% 覆盖）：
  - api_keys.list / create / update / delete / test 的正常与优雅拒绝分支
  - api_keys.rotation.get/list/history/set_policy 的安全可用分支 + get 缺参错误分支
这些 handler 是凭证（token）生命周期的 RPC 入口，属审计方案要求补覆盖的
认证链路。隔离：纯内存 FakeSettingsRepo + 临时 data_dir，不依赖真实后端进程。
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


class FakeSettingsRepo:
    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    def get_setting(self, key: str, default=None):
        return self._data.get(key, default)

    def set_setting(self, key: str, value) -> None:
        self._data[key] = value


class CapturingRegistry:
    """捕获 register/register_with_schema 的 (name -> fn) 映射，便于直接调用 handler。"""

    def __init__(self) -> None:
        self.methods: dict[str, object] = {}

    def register(self, name: str, fn, **_kwargs) -> None:
        self.methods[name] = fn

    def register_with_schema(self, name: str, fn, schema=None, risk=None, risk_resolver=None, **_kwargs) -> None:
        self.methods[name] = fn


class _Ctx(SimpleNamespace):
    pass


@pytest.fixture
def ak_service():
    from services.api_key_service import ApiKeyService

    repo = FakeSettingsRepo()
    repo.set_setting("provider_configs", [])
    tmp = tempfile.mkdtemp(prefix="siyi-ak-rpc-")
    svc = ApiKeyService(repo, data_dir=tmp)
    yield svc
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def reg(ak_service):
    from backend.handlers._api_keys import register_api_keys

    ctx = _Ctx(
        settings_repository=FakeSettingsRepo(),
        recreate_ai_client=lambda: None,
    )
    capturing = CapturingRegistry()
    register_api_keys(capturing, ctx, ak_service)
    return capturing


def _run(coro):
    return asyncio.run(coro)


# ── api_keys.list / create / update / delete / test ──────────────────────────


def test_create_then_list_masks_secret(reg) -> None:
    created = _run(reg.methods["api_keys.create"](
        name="DeepSeek", key_value="sk-secret-12345",
        base_url="https://api.deepseek.com/v1", model="deepseek-chat",
        is_provider=True, purpose="primary",
    ))
    assert created.get("id"), "create 应返回 id"
    listing = _run(reg.methods["api_keys.list"]())
    matches = [k for k in listing if k.get("name") == "DeepSeek"]
    assert len(matches) == 1
    # 列表必须脱敏，不得泄露明文 token
    assert "sk-secret" not in matches[0].get("key_value", "")


def test_create_missing_key_value_rejected(reg) -> None:
    out = _run(reg.methods["api_keys.create"]())
    assert out.get("success") is False
    assert "key_value" in out.get("error", "")


def test_update_requires_key_id(reg) -> None:
    out = _run(reg.methods["api_keys.update"](key_id="", description="x"))
    assert out.get("success") is False
    assert "key_id" in out.get("error", "")


def test_update_then_verify(reg) -> None:
    created = _run(reg.methods["api_keys.create"](
        name="Upd", key_value="sk-orig", base_url="https://api.deepseek.com/v1",
        model="deepseek-chat", is_provider=True,
    ))
    kid = created.get("id", "")
    out = _run(reg.methods["api_keys.update"](key_id=kid, description="desc", enabled=False))
    assert out.get("success") is not False
    listing = _run(reg.methods["api_keys.list"]())
    upd = [k for k in listing if k.get("id") == kid]
    assert len(upd) == 1
    assert upd[0].get("description") == "desc"
    assert upd[0].get("enabled") is False


def test_delete_requires_key_id(reg) -> None:
    out = _run(reg.methods["api_keys.delete"](key_id=""))
    assert out.get("success") is False
    assert "key_id" in out.get("error", "")


def test_delete_then_gone(reg) -> None:
    created = _run(reg.methods["api_keys.create"](
        name="Del", key_value="sk-del", base_url="https://api.deepseek.com/v1",
        model="deepseek-chat", is_provider=True,
    ))
    kid = created.get("id", "")
    _run(reg.methods["api_keys.delete"](key_id=kid))
    listing = _run(reg.methods["api_keys.list"]())
    assert kid not in {k.get("id") for k in listing}


def test_test_missing_key_reports_absent(reg) -> None:
    out = _run(reg.methods["api_keys.test"](key_id="provider:nope"))
    assert out.get("success") is False
    assert "不存在" in out.get("error", "")


# ── api_keys.rotation.* 安全可用分支 ────────────────────────────────────────


def test_rotation_get_requires_provider_name(reg) -> None:
    out = _run(reg.methods["api_keys.rotation.get"](provider_name=""))
    assert "provider_name" in out.get("error", "")


def test_rotation_get_available_returns_state(reg) -> None:
    out = _run(reg.methods["api_keys.rotation.get"](provider_name="provider:deepseek"))
    assert out.get("provider_name") == "provider:deepseek"
    assert "keys" in out


def test_rotation_list_returns_state_list(reg) -> None:
    out = _run(reg.methods["api_keys.rotation.list"]())
    assert isinstance(out, list)


def test_rotation_history_safe_default(reg) -> None:
    out = _run(reg.methods["api_keys.rotation.history"](provider_name="provider:deepseek", limit=20))
    assert isinstance(out, dict)
    assert "history" in out


def test_rotation_set_policy_writes_state(reg) -> None:
    out = _run(reg.methods["api_keys.rotation.set_policy"](
        provider_name="provider:deepseek", auto_rotate=True))
    assert out.get("auto_rotate") is True
