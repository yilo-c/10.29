# -*- coding: utf-8 -*-
"""C-P1：内核 context bundle（opt-in）单测，纯函数 + 开关门控，离线。"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from backend.handlers.chat_paths.gateway_path import (  # noqa: E402
    _KERNEL_CONTEXT_BUNDLE_MAX,
    _build_kernel_prompt,
    _kernel_context_bundle_enabled,
)


def test_default_no_bundle_keeps_history_only():
    assert _build_kernel_prompt("hi", {}) == "hi"
    p = _build_kernel_prompt("hi", {"context_messages": [{"role": "user", "content": "earlier"}]})
    assert "近期对话记录" in p and "earlier" in p
    assert "用户画像" not in p  # 未开启时不注入 bundle


def test_bundle_prepended_before_core():
    out = _build_kernel_prompt("帮我整理本周笔记", {}, context_bundle="画像：极简主义\n记忆：偏好列表")
    assert out.startswith("以下是用户画像与相关记忆")
    assert "画像：极简主义" in out
    assert out.rstrip().endswith("帮我整理本周笔记")


def test_bundle_truncated_to_max():
    big = "z" * (_KERNEL_CONTEXT_BUNDLE_MAX + 3000)
    out = _build_kernel_prompt("msg", {}, context_bundle=big)
    assert out.count("z") <= _KERNEL_CONTEXT_BUNDLE_MAX


@pytest.mark.asyncio
async def test_enabled_default_off_and_variants():
    # 无 settings repo → off
    assert await _kernel_context_bundle_enabled(types.SimpleNamespace()) is False
    # bool True → on
    on = types.SimpleNamespace(settings_repository=types.SimpleNamespace(get_setting=lambda k, d=None: True))
    assert await _kernel_context_bundle_enabled(on) is True
    # 字符串 "on" → on
    son = types.SimpleNamespace(settings_repository=types.SimpleNamespace(get_setting=lambda k, d=None: "on"))
    assert await _kernel_context_bundle_enabled(son) is True
    # 读取异常 → 视为 off，绝不阻断内核
    def boom(k, d=None):
        raise RuntimeError("settings down")

    err = types.SimpleNamespace(settings_repository=types.SimpleNamespace(get_setting=boom))
    assert await _kernel_context_bundle_enabled(err) is False
