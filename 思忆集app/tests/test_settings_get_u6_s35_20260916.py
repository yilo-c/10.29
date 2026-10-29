"""U6（S3-5，2026-09-16）：settings.get 空 key 错误话术清洗回归测试。

旧行为：空 key 返回 {"message": "use settings.get with a specific key",
"hint": "/api/v1/settings in future"} —— 伪成功 dict（客户端无法判断成败）
且 hint 是作废的 REST 迁移残留话术。

新行为：返回 {"success": False, "error": <i18n 缺少参数文案>}，与仓内其它
handler 的错误结构（_agent_tasks.py / _remote.py 的 error.missing_param）一致。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.handlers._system_settings import register_system_settings
from backend.method_registry import MethodRegistry


def _make_registry():
    reg = MethodRegistry()
    ctx = SimpleNamespace(
        settings_repository=MagicMock(),
        recreate_ai_client=MagicMock(),
        agent_gateway=MagicMock(),
        local_data_store=MagicMock(),
    )
    ctx.settings_repository.get_setting = lambda k, d=None: d
    register_system_settings(reg, ctx, 18765, MagicMock())
    return reg, ctx


@pytest.mark.asyncio
async def test_empty_key_returns_honest_error_not_pseudo_success():
    """空 key 必须返回 success=False + error，且不再含 message/hint。"""
    reg, _ctx = _make_registry()
    handler = reg._handlers["settings.get"]
    result = await handler(key="")
    assert isinstance(result, dict)
    assert result.get("success") is False
    assert result.get("error"), "错误结构必须带 error 文案"
    assert "hint" not in result, "不得再泄露 /api/v1 迁移残留话术"
    assert "message" not in result


@pytest.mark.asyncio
async def test_missing_key_param_same_as_empty():
    """完全不传 key（默认值路径）与传空串行为一致。"""
    reg, _ctx = _make_registry()
    handler = reg._handlers["settings.get"]
    result = await handler()
    assert result.get("success") is False
    assert result.get("error")


@pytest.mark.asyncio
async def test_error_text_is_i18n_localized():
    """error 文案走 i18n（含中文默认话术「缺少 key 参数」）。"""
    pytest.importorskip("backend.i18n")
    from backend.i18n import t

    reg, _ctx = _make_registry()
    handler = reg._handlers["settings.get"]
    result = await handler(key="")
    assert result["error"] == t("error.missing_param", name="key")
    assert "key" in result["error"]


@pytest.mark.asyncio
async def test_with_key_normal_value_unaffected():
    """带 key 的正常读取路径不受 U6 改动影响。"""
    reg, ctx = _make_registry()
    ctx.settings_repository.get_setting = lambda k, d=None: "deepseek-v4-flash"
    handler = reg._handlers["settings.get"]
    result = await handler(key="llm_model")
    assert result == "deepseek-v4-flash"
