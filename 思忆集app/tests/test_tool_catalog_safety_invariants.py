# -*- coding: utf-8 -*-
"""工具目录/门控 安全不变量（A-P2 普查固化，离线零执行）。

防回归：白名单漂移、高危被误暴露给 LLM、目录动作未归类。任一条破了就该红。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.react_tool_catalog import REACT_TOOL_CATALOG, SAFE_TOOL_IDS_BASE  # noqa: E402
from backend.handlers._sandbox import _handle_sandbox_action_probe  # noqa: E402

CATALOG = {e["action_id"] for e in REACT_TOOL_CATALOG}


def test_safe_base_is_subset_of_catalog():
    # 白名单里出现的动作必须都在目录中（否则 LLM 看得到却无实现定义）
    assert SAFE_TOOL_IDS_BASE <= CATALOG, f"SAFE 漂移出目录: {SAFE_TOOL_IDS_BASE - CATALOG}"


def test_no_high_risk_action_is_llm_visible():
    """高危动作绝不能进 LLM 可见白名单（须走审批）。"""
    from core.action_guard import _HIGH_RISK_ACTIONS

    leaked = _HIGH_RISK_ACTIONS & SAFE_TOOL_IDS_BASE
    assert not leaked, f"高危却被 LLM 直接可见: {leaked}"


@pytest.mark.parametrize("name", ["shell.run", "app.launch", "browser.click", "browser.type", "browser.automate"])
def test_actuation_gated_actions_not_llm_visible(name):
    assert name in CATALOG
    assert name not in SAFE_TOOL_IDS_BASE, f"{name} 不应 LLM 直接可见"


@pytest.mark.asyncio
async def test_every_catalog_action_classified_not_unknown():
    for a in sorted(CATALOG):
        r = await _handle_sandbox_action_probe(a)
        assert r["in_catalog"] is True
        assert r["tier"] in {"read_only", "write", "high_risk"}, f"{a} 归类异常: {r['tier']}"


@pytest.mark.asyncio
async def test_probe_rejects_missing_action_id():
    assert "error" in await _handle_sandbox_action_probe("")
