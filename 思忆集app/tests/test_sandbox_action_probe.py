# -*- coding: utf-8 -*-
"""A-P0：sandbox.action_probe 单测（离线直调，零执行、零副作用）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._sandbox import _handle_sandbox_action_probe  # noqa: E402


@pytest.mark.asyncio
async def test_readonly_action_visible_no_approval():
    r = await _handle_sandbox_action_probe("web.search", query="x")
    assert r["in_catalog"] is True
    assert r["llm_visible"] is True
    assert r["tier"] == "read_only"
    assert r["approval_required_default_policy"] is False


@pytest.mark.asyncio
async def test_high_risk_shell_requires_approval_and_not_visible():
    r = await _handle_sandbox_action_probe("shell.run", command="dir /b")
    assert r["in_catalog"] is True
    assert r["llm_visible"] is False          # 高危不进 LLM 白名单
    assert r["high_risk"] is True
    assert r["approval_required_default_policy"] is True


@pytest.mark.asyncio
async def test_shell_unsafe_token_detected():
    ok = await _handle_sandbox_action_probe("shell.run", command="ls && rm -rf /")
    bad = await _handle_sandbox_action_probe("shell.run", command="echo hi")
    assert ok["shell_unsafe_tokens_present"] is True
    assert bad["shell_unsafe_tokens_present"] is False


@pytest.mark.asyncio
async def test_write_action_tier_and_dry_run_flag():
    w = await _handle_sandbox_action_probe("file.write", path="x", content="y")
    assert w["tier"] == "write"
    assert w["dry_run_supported"] is True
    nw = await _handle_sandbox_action_probe("file.read", path="x")
    assert nw["dry_run_supported"] is False   # 预览仅 file.write/copy


@pytest.mark.asyncio
async def test_unknown_action_not_in_catalog():
    r = await _handle_sandbox_action_probe("totally.not.an.action")
    assert r["in_catalog"] is False
    assert r["tier"] == "unknown"


@pytest.mark.asyncio
async def test_missing_action_id_errors():
    r = await _handle_sandbox_action_probe("")
    assert "error" in r
