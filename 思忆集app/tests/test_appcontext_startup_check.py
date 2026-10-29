"""Task #6 — AppContext 启动 fail-fast 自检单元测试。

直接验证 AppContextBuilder.validate_critical_attributes 对关键属性缺失 /
为 None 的 fail-fast 行为，避免依赖完整 AppContext 重型构造（aiohttp /
transformers 等）。

同时验证 P0-2 根因属性 assistant_inbox_service 在白名单中——若将来被改名
或漏赋值，启动即报错，不会再推迟到首个 cron tick 才崩溃。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _make_builder() -> object:
    from app_runtime._context_builder import AppContextBuilder

    # 用一个最小伪 ctx 承载属性
    class _FakeCtx:
        pass

    return AppContextBuilder(_FakeCtx())


def _healthy_builder():
    from app_runtime._context_builder import AppContextBuilder

    b = _make_builder()
    for name in AppContextBuilder._CRITICAL_ATTRIBUTES:
        setattr(b.ctx, name, object())  # 非 None 占位
    return b


def test_passes_when_all_critical_present():
    from app_runtime._context_builder import AppContextBuilder

    b = _healthy_builder()
    # 不应抛异常
    b.validate_critical_attributes()


def test_fails_on_missing_attribute():
    from app_runtime._context_builder import AppContextStartupError, AppContextBuilder

    b = _healthy_builder()
    # 模拟 P0-2 根因：assistant_inbox_service 被漏赋值
    delattr(b.ctx, "assistant_inbox_service")
    with pytest.raises(AppContextStartupError) as exc:
        b.validate_critical_attributes()
    assert "assistant_inbox_service" in str(exc.value)


def test_fails_on_none_attribute():
    from app_runtime._context_builder import AppContextStartupError, AppContextBuilder

    b = _healthy_builder()
    # 模拟属性被显式置 None（hasattr 仍 True，但业务上失效）
    setattr(b.ctx, "cron_service", None)
    with pytest.raises(AppContextStartupError) as exc:
        b.validate_critical_attributes()
    assert "cron_service" in str(exc.value)


def test_p0_2_root_cause_attribute_in_whitelist():
    from app_runtime._context_builder import AppContextBuilder

    # 防止回归：P0-2 的失效属性名 inbox_service 不在白名单，
    # 而真正有效的 assistant_inbox_service 必须在。
    assert "assistant_inbox_service" in AppContextBuilder._CRITICAL_ATTRIBUTES
    assert "inbox_service" not in AppContextBuilder._CRITICAL_ATTRIBUTES
