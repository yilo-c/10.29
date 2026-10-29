# -*- coding: utf-8 -*-
"""Tests for AI behavior security (Layer 5).

Verifies that the action_guard gateway correctly enforces:
- Whitelist-only tool_id execution
- LLM source elevated action blocking
- Risk level enforcement
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from automation_hub.api.policy.engine import decide_execute


class TestPolicyEngine:
    """Test the execution policy engine (Layer 5 enforcement point)."""

    def test_execute_requires_tool_execute_scope(self):
        """Missing tool:execute scope should be blocked."""
        decision = decide_execute(
            scopes=["tool:read"],
            tool={"id": "test_tool", "is_enabled": 1, "risk_level": "read"},
            args={},
        )
        assert decision.allowed is False
        assert decision.reason == "missing scope tool:execute"

    def test_disabled_tool_is_blocked(self):
        """Disabled tools should be blocked regardless of scope."""
        decision = decide_execute(
            scopes=["tool:execute"],
            tool={"id": "test_tool", "is_enabled": 0, "risk_level": "read"},
            args={},
        )
        assert decision.allowed is False
        assert "disabled" in decision.reason

    def test_low_risk_tool_allowed_without_approval(self):
        """Low risk tools should be allowed without requiring approval."""
        decision = decide_execute(
            scopes=["tool:execute"],
            tool={"id": "test_tool", "is_enabled": 1, "risk_level": "read"},
            args={},
        )
        assert decision.allowed is True
        assert decision.requires_approval is False

    def test_exec_low_is_allowed_without_approval(self):
        decision = decide_execute(
            scopes=["tool:execute"],
            tool={"id": "test_tool", "is_enabled": 1, "risk_level": "exec_low"},
            args={},
        )
        assert decision.allowed is True
        assert decision.requires_approval is False

    def test_write_risk_requires_approval(self):
        """Write risk tools should require approval."""
        decision = decide_execute(
            scopes=["tool:execute"],
            tool={"id": "test_tool", "is_enabled": 1, "risk_level": "write"},
            args={},
        )
        assert decision.allowed is True
        assert decision.requires_approval is True
        assert "risk=write" in decision.reason

    def test_exec_high_requires_approval(self):
        decision = decide_execute(
            scopes=["tool:execute"],
            tool={"id": "test_tool", "is_enabled": 1, "risk_level": "exec_high"},
            args={},
        )
        assert decision.allowed is True
        assert decision.requires_approval is True
        assert "risk=exec_high" in decision.reason


class TestAssistantSecurityLayer:
    """Endpoint-level tests for assistant security layer."""

    def test_tool_run_creation_requires_execute_scope(self, tmp_path, monkeypatch):
        """POST /tool-runs should require tool:execute scope."""
        from tests.support import bootstrap_headers, configure_test_env, make_client

        configure_test_env(tmp_path, monkeypatch)
        client = make_client()
        headers = bootstrap_headers(client)

        # Create readonly token (no tool:execute)
        device_resp = client.post("/auth/devices", headers=headers, json={"name": "ro-dev", "platform": "windows"})
        assert device_resp.status_code == 200
        device_id = device_resp.json()["id"]

        token_resp = client.post("/auth/tokens", headers=headers, json={"device_id": device_id, "template": "readonly"})
        assert token_resp.status_code == 200
        ro_headers = {"Authorization": f"Bearer {token_resp.json()['token']}"}

        # Should be blocked from creating tool runs
        resp = client.post("/tool-runs", headers=ro_headers, json={"tool_id": "any_tool", "args": {}})
        assert resp.status_code == 403, resp.text

    def test_tool_run_retry_requires_execute_scope(self, tmp_path, monkeypatch):
        """POST /tool-runs/{id}/retry should require tool:execute scope."""
        from tests.support import bootstrap_headers, configure_test_env, make_client

        configure_test_env(tmp_path, monkeypatch)
        client = make_client()
        headers = bootstrap_headers(client)

        # Create readonly token
        device_resp = client.post("/auth/devices", headers=headers, json={"name": "ro-dev2", "platform": "windows"})
        assert device_resp.status_code == 200
        token_resp = client.post("/auth/tokens", headers=headers,
                                 json={"device_id": device_resp.json()["id"], "template": "readonly"})
        assert token_resp.status_code == 200
        ro_headers = {"Authorization": f"Bearer {token_resp.json()['token']}"}

        # readonly token (no tool:execute) should get 403 when trying to retry
        retry_resp = client.post("/tool-runs/nonexistent-run/retry", headers=ro_headers, json={"reason": "test"})
        assert retry_resp.status_code == 403, retry_resp.text

    def test_proposal_create_requires_write_scope(self, tmp_path, monkeypatch):
        """POST /proposals should require proposal:write scope."""
        from tests.support import bootstrap_headers, configure_test_env, make_client

        configure_test_env(tmp_path, monkeypatch)
        client = make_client()
        headers = bootstrap_headers(client)

        # Create readonly token (no proposal:write)
        device_resp = client.post("/auth/devices", headers=headers, json={"name": "ro-dev3", "platform": "windows"})
        assert device_resp.status_code == 200
        token_resp = client.post("/auth/tokens", headers=headers,
                                 json={"device_id": device_resp.json()["id"], "template": "readonly"})
        assert token_resp.status_code == 200
        ro_headers = {"Authorization": f"Bearer {token_resp.json()['token']}"}

        resp = client.post("/proposals", headers=ro_headers, json={
            "title": "test",
            "summary": "test",
            "plan_md": "test",
        })
        assert resp.status_code == 403, resp.text

    def test_approval_decide_requires_approval_decide_scope(self, tmp_path, monkeypatch):
        """POST /approvals/{id}/approve should require approval:decide scope."""
        from tests.support import bootstrap_headers, configure_test_env, make_client

        configure_test_env(tmp_path, monkeypatch)
        client = make_client()
        headers = bootstrap_headers(client)

        # Create agent token (no approval:decide)
        device_resp = client.post("/auth/devices", headers=headers, json={"name": "agent-dev", "platform": "windows"})
        assert device_resp.status_code == 200
        token_resp = client.post("/auth/tokens", headers=headers,
                                 json={"device_id": device_resp.json()["id"], "template": "agent"})
        assert token_resp.status_code == 200
        agent_headers = {"Authorization": f"Bearer {token_resp.json()['token']}"}

        resp = client.post("/approvals/fake-id/approve", headers=agent_headers)
        assert resp.status_code == 403, resp.text
