"""七维-安C3 MCP 审批闭环（P0）——三段验收：

(a) _router_mcp 风险/确认映射表：不再恒 medium/first_time ——
    server 配置 > 工具注解 > 名称破坏性关键词（缺省 destructive→high）> 保守 medium；
    confirmation 相应映射 low→auto / medium→first_time / high→every_time。
(b) ActionBroker 确认必需集合加入 FIRST_TIME（every_time/strong 原语义不变）。
(c) mcp_server/tools.py policy=None（缺省）fail-close：RW/DANGER 默认
    require_approval，RO 与审批闭环工具（siyi_confirm_*）放行；被拦调用
    复用 tool_runner._McpAudit 写入既有 audit_mcp.db。

隔离：全部使用 tmp_path（SiyiStore / ConfirmationStore / audit_mcp.db 均指向
tmp），不触碰思忆集app/data/ 真实数据；不调用经 get_confirmation_store()
单例的桌面工具 handler（其缺省数据目录指向真实数据）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import types
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from mcp_server.store import SiyiStore
from services.confirmation_store import ConfirmationStore


# ── (a) 风险/确认映射表 ────────────────────────────────────────────


class TestMcpRiskMapping:
    def _map(self, tool, server_config=None):
        from assistant._router_mcp import map_mcp_tool_risk

        return map_mcp_tool_risk(tool, server_config)

    def test_read_only_hint_maps_low_auto(self):
        tool = {"name": "search_web", "annotations": {"readOnlyHint": True}}
        assert self._map(tool) == ("low", "auto")

    def test_destructive_hint_maps_high_every_time(self):
        tool = {"name": "upsert_row", "annotations": {"destructiveHint": True}}
        assert self._map(tool) == ("high", "every_time")

    def test_plain_tool_defaults_medium_first_time(self):
        # 未知/读写类：保守 medium + first_time（不再无脑恒 medium，而是映射结果恰好一致）
        assert self._map({"name": "upsert_row"}) == ("medium", "first_time")

    def test_destructive_name_maps_high(self):
        # 缺省 destructive→high：名称含破坏性关键词即映射 high
        assert self._map({"name": "delete_path"}) == ("high", "every_time")

    def test_server_config_risk_override_beats_annotations(self):
        tool = {"name": "search_web", "annotations": {"readOnlyHint": True}}
        assert self._map(tool, {"risk": "high"}) == ("high", "every_time")

    def test_server_config_risk_aliases(self):
        tool = {"name": "upsert_row"}
        assert self._map(tool, {"risk": "destructive"}) == ("high", "every_time")
        assert self._map(tool, {"risk": "read"}) == ("low", "auto")

    def test_invalid_server_risk_ignored(self):
        tool = {"name": "upsert_row"}
        assert self._map(tool, {"risk": "bogus"}) == ("medium", "first_time")

    def test_register_mcp_capabilities_applies_mapping(self):
        from assistant._router_mcp import MCPRouterMixin
        from services.capability_types import CapabilityRegistry

        class _Host(MCPRouterMixin):
            capability_registry = CapabilityRegistry()

        host = _Host()
        host._register_mcp_capabilities(
            [
                {"action_id": "mcp.fs.delete", "name": "delete_path", "description": "", "inputSchema": {}},
                {"action_id": "mcp.web.search", "name": "search_web", "description": "", "inputSchema": {}, "annotations": {"readOnlyHint": True}},
                {"action_id": "mcp.db.write", "name": "upsert_row", "description": "", "inputSchema": {}},
            ]
        )
        reg = host.capability_registry
        deleted = reg.get_capability("mcp.fs.delete")
        assert deleted is not None
        assert (deleted.risk_level, deleted.confirmation_level) == ("high", "every_time")
        search = reg.get_capability("mcp.web.search")
        assert (search.risk_level, search.confirmation_level) == ("low", "auto")
        write = reg.get_capability("mcp.db.write")
        assert (write.risk_level, write.confirmation_level) == ("medium", "first_time")


# ── (b) Broker 确认集合 ────────────────────────────────────────────


class _AllowGuard:
    def validate(self, action_id, payload=None, source="", approved=False):
        return types.SimpleNamespace(allowed=True, reason="")


class TestBrokerConfirmationSet:
    def test_confirm_required_levels_constant(self):
        from services.action_broker import CONFIRM_REQUIRED_LEVELS

        assert {"first_time", "every_time", "strong"} <= set(CONFIRM_REQUIRED_LEVELS)
        assert "auto" not in CONFIRM_REQUIRED_LEVELS

    def _broker(self, confirmation_level):
        from services.capability_types import (
            CapabilityDescriptor,
            CapabilityRegistry,
        )
        from services.action_broker import ActionBroker

        registry = CapabilityRegistry()
        registry.register_capability(
            CapabilityDescriptor(
                capability_id="mcp.test.write",
                risk_level="high",
                confirmation_level=confirmation_level,
            )
        )
        return ActionBroker(
            capability_registry=registry,
            action_guard=_AllowGuard(),
            executor=lambda cid, inp: {"ok": True},
        )

    def _run(self, broker, source="llm", approved=False):
        from services.action_broker import ActionRequest

        return broker.execute_sync(
            ActionRequest(
                capability_id="mcp.test.write",
                input={"path": "x"},
                source=source,
                approved=approved,
            )
        )

    def test_first_time_blocks_unapproved_llm_call(self):
        broker = self._broker("first_time")
        result = self._run(broker)
        assert result.blocked is True
        assert result.blocked_reason == "requires_confirmation"

    def test_approved_first_time_executes(self):
        broker = self._broker("first_time")
        result = self._run(broker, approved=True)
        assert result.success is True
        assert result.blocked is False

    def test_every_time_strong_still_block_auto_not(self):
        assert self._run(self._broker("every_time")).blocked_reason == "requires_confirmation"
        assert self._run(self._broker("strong")).blocked_reason == "requires_confirmation"
        auto = self._run(self._broker("auto"))
        assert auto.success is True and auto.blocked is False

    def test_user_source_not_blocked(self):
        # 确认强制仅针对 llm/assistant/agent 来源（原语义保持）
        result = self._run(self._broker("first_time"), source="user")
        assert result.success is True and result.blocked is False


# ── (c) policy=None fail-close + 审计 ─────────────────────────────


@pytest.fixture()
def store(tmp_path):
    return SiyiStore(data_dir=str(tmp_path))


def _spec(specs, name):
    return next(s for s in specs if s.name == name)


class TestPolicyNoneFailClose:
    def test_gate_decision_fail_close_table(self):
        from mcp_server.tools import _resolve_gate_decision

        danger = types.SimpleNamespace(name="siyi_app_launch", level="DANGER")
        write = types.SimpleNamespace(name="siyi_notes_create", level="RW")
        read = types.SimpleNamespace(name="siyi_health", level="RO")
        loop = types.SimpleNamespace(name="siyi_confirm_request", level="RW")
        assert _resolve_gate_decision(danger, None, "") == "require_approval"
        assert _resolve_gate_decision(write, None, "") == "require_approval"
        assert _resolve_gate_decision(read, None, "") == "allow"
        assert _resolve_gate_decision(loop, None, "") == "allow"
        # 显式策略保持既有 resolve_policy 语义
        assert _resolve_gate_decision(write, {"mode": "allow_all"}, "") == "allow"
        assert _resolve_gate_decision(write, {"mode": "restrict", "allow": ["siyi_notes_create"]}, "") == "allow"
        assert _resolve_gate_decision(write, {"mode": "restrict"}, "") == "deny"

    def test_ro_tool_allowed_without_policy(self, store):
        from mcp_server.tools import build_tool_specs

        specs = build_tool_specs(store)
        out = json.loads(_spec(specs, "siyi_notes_search").handler({"query": "x"}))
        assert out == []

    def test_rw_tool_requires_approval_and_no_side_effect(self, store):
        from mcp_server.tools import build_tool_specs

        specs = build_tool_specs(store)  # policy=None（缺省）
        out = json.loads(_spec(specs, "siyi_notes_create").handler({"title": "t", "content": "c"}))
        assert out["status"] == "requires_approval", out
        assert out.get("confirmation_id"), out
        assert store.notes.list_notes() == []  # 无副作用

    def test_blocked_write_audited_to_audit_mcp_db(self, store, tmp_path):
        from mcp_server.tools import build_tool_specs

        specs = build_tool_specs(store)
        json.loads(_spec(specs, "siyi_notes_create").handler({"title": "t", "content": "c"}))
        db = sqlite3.connect(str(Path(tmp_path) / "audit_mcp.db"))
        try:
            rows = db.execute("SELECT tool_id, command, success, detail FROM mcp_tool_audit WHERE tool_id='siyi_notes_create'").fetchall()
        finally:
            db.close()
        assert rows, "被拦调用未写入 audit_mcp.db"
        tool_id, command, success, detail = rows[-1]
        assert (tool_id, command, success) == ("siyi_notes_create", "policy_gate", 0)
        assert "requires_approval" in detail

    def test_approval_then_execute_closes_loop(self, store, tmp_path):
        from mcp_server.tools import build_tool_specs

        specs = build_tool_specs(store)
        handler = _spec(specs, "siyi_notes_create").handler
        first = json.loads(handler({"title": "t1", "content": "c1"}))
        assert first["status"] == "requires_approval"
        cs = ConfirmationStore(data_dir=str(tmp_path))
        assert cs.resolve(first["confirmation_id"], approved=True) is not None
        second = json.loads(handler({"title": "t1", "content": "c1"}))
        assert second.get("id"), second  # 批准后放行执行

    def test_confirm_request_tool_exempt_from_gate(self, store):
        # 审批闭环自身不可被门控（否则内核永远无法发起审批）
        from mcp_server.tools import build_tool_specs

        specs = build_tool_specs(store)
        out = json.loads(_spec(specs, "siyi_confirm_request").handler({"action_id": "notes.delete", "args": {"note_id": "n1"}}))
        assert out["status"] == "pending", out
        assert out.get("confirmation_id"), out

    def test_explicit_allow_all_policy_keeps_passthrough(self, store):
        from mcp_server.tools import build_tool_specs

        specs = build_tool_specs(store, policy={"mode": "allow_all"})
        out = json.loads(_spec(specs, "siyi_notes_create").handler({"title": "t", "content": "c"}))
        assert out.get("id"), out
