# -*- coding: utf-8 -*-
"""策略引擎单元测试 — test_policy_engine

Coverage matrix (AH-S10-04):
  - decide_execute: scope check, tool disabled, risk level, args schema
"""
from __future__ import annotations

import json

import pytest

from automation_hub.api.policy.engine import decide_execute, Decision


def test_decide_missing_scope_returns_denied():
    """缺少 tool:execute 权限时返回 denied"""
    decision = decide_execute(["tool:read"], {"is_enabled": 1, "risk_level": "exec_low"}, {})
    assert decision.allowed is False
    assert decision.requires_approval is False
    assert "scope" in decision.reason.lower()


def test_decide_disabled_tool_returns_denied():
    """工具被禁用时返回 denied"""
    decision = decide_execute(
        ["tool:execute"], {"id": "my-tool", "is_enabled": 0, "risk_level": "exec_low"}, {}
    )
    assert decision.allowed is False
    assert decision.requires_approval is False
    assert "disabled" in decision.reason.lower()


def test_decide_high_risk_requires_approval():
    """高风险工具需要审批"""
    for risk in ("exec_high", "write"):
        decision = decide_execute(
            ["tool:execute"], {"id": "risky-tool", "is_enabled": 1, "risk_level": risk}, {}
        )
        assert decision.allowed is True
        assert decision.requires_approval is True
        assert "requires approval" in decision.reason


def test_decide_low_risk_is_allowed():
    """低风险工具直接放行"""
    decision = decide_execute(
        ["tool:execute"], {"id": "safe-tool", "is_enabled": 1, "risk_level": "exec_low"}, {}
    )
    assert decision.allowed is True
    assert decision.requires_approval is False
    assert decision.reason == "ok"


def test_decide_default_risk_is_allowed():
    """未指定 risk_level 时默认为放行"""
    decision = decide_execute(
        ["tool:execute"], {"id": "no-risk-tool", "is_enabled": 1}, {}
    )
    assert decision.allowed is True
    assert decision.requires_approval is False


def test_decide_with_args_schema_does_not_crash():
    """args_schema_json 为合法 JSON 时不崩溃"""
    schema = {"type": "object", "properties": {"url": {"type": "string"}}}
    decision = decide_execute(
        ["tool:execute"],
        {
            "id": "schema-tool",
            "is_enabled": 1,
            "risk_level": "exec_low",
            "args_schema_json": json.dumps(schema),
        },
        {"url": "https://example.com"},
    )
    assert decision.allowed is True
