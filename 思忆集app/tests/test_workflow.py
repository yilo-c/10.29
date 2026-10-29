"""Workflow 引擎关键路径测试。

覆盖：
- models.py: validate_dag / 序列化往返 / get_node / get_connections_for_node
- execution_engine.py: _evaluate_condition 安全边界 / _build_chain_plan / _wf_node_to_chain
- repository.py: CRUD / duplicate_workflow
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from workflow.models import (
    Connection,
    Workflow,
    WorkflowNode,
    NODE_TYPE_TRIGGER,
    NODE_TYPE_AI_CALL,
    NODE_TYPE_TOOL_ACTION,
    NODE_TYPE_CONDITION,
    NODE_TYPE_DELIVERY,
)
from workflow.execution_engine import WorkflowExecutionEngine
from workflow.repository import WorkflowRepository


# ── FakeDataStore for repository tests ──


class FakeDataStore:
    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    def load_json(self, key: str, default=None):
        return self._data.get(key, default if default is not None else {})

    def save_json(self, key: str, value) -> None:
        self._data[key] = value


# ═══════════════════════════════════════════════════════════════
# Models: validate_dag
# ═══════════════════════════════════════════════════════════════


class TestValidateDag:
    def test_empty_workflow_is_valid(self):
        wf = Workflow(name="empty")
        ok, msg = wf.validate_dag()
        assert ok is True
        assert msg == "ok"

    def test_linear_dag_is_valid(self):
        wf = Workflow(name="linear")
        n1 = WorkflowNode(node_id="n1", node_type=NODE_TYPE_TRIGGER)
        n2 = WorkflowNode(node_id="n2", node_type=NODE_TYPE_AI_CALL)
        n3 = WorkflowNode(node_id="n3", node_type=NODE_TYPE_DELIVERY)
        wf.nodes = [n1, n2, n3]
        wf.connections = [
            Connection(source_node_id="n1", target_node_id="n2"),
            Connection(source_node_id="n2", target_node_id="n3"),
        ]
        ok, _ = wf.validate_dag()
        assert ok is True

    def test_cycle_detected(self):
        wf = Workflow(name="cycle")
        n1 = WorkflowNode(node_id="n1", node_type=NODE_TYPE_AI_CALL)
        n2 = WorkflowNode(node_id="n2", node_type=NODE_TYPE_AI_CALL)
        wf.nodes = [n1, n2]
        wf.connections = [
            Connection(source_node_id="n1", target_node_id="n2"),
            Connection(source_node_id="n2", target_node_id="n1"),
        ]
        ok, msg = wf.validate_dag()
        assert ok is False
        assert "环" in msg

    def test_self_loop_detected(self):
        wf = Workflow(name="self_loop")
        n1 = WorkflowNode(node_id="n1", node_type=NODE_TYPE_AI_CALL)
        wf.nodes = [n1]
        wf.connections = [
            Connection(source_node_id="n1", target_node_id="n1"),
        ]
        ok, _ = wf.validate_dag()
        assert ok is False

    def test_disconnected_components_valid(self):
        wf = Workflow(name="disconnected")
        n1 = WorkflowNode(node_id="n1", node_type=NODE_TYPE_TRIGGER)
        n2 = WorkflowNode(node_id="n2", node_type=NODE_TYPE_AI_CALL)
        n3 = WorkflowNode(node_id="n3", node_type=NODE_TYPE_TRIGGER)
        n4 = WorkflowNode(node_id="n4", node_type=NODE_TYPE_DELIVERY)
        wf.nodes = [n1, n2, n3, n4]
        wf.connections = [
            Connection(source_node_id="n1", target_node_id="n2"),
            Connection(source_node_id="n3", target_node_id="n4"),
        ]
        ok, _ = wf.validate_dag()
        assert ok is True


# ═══════════════════════════════════════════════════════════════
# Models: serialization round-trip
# ═══════════════════════════════════════════════════════════════


class TestSerialization:
    def test_workflow_node_roundtrip(self):
        node = WorkflowNode(
            node_id="abc123",
            node_type=NODE_TYPE_AI_CALL,
            label="AI 分析",
            config={"system_prompt": "test", "temperature": 0.5},
            risk_level="medium",
        )
        d = node.to_dict()
        restored = WorkflowNode.from_dict(d)
        assert restored.node_id == "abc123"
        assert restored.node_type == NODE_TYPE_AI_CALL
        assert restored.label == "AI 分析"
        assert restored.config["system_prompt"] == "test"
        assert restored.risk_level == "medium"

    def test_connection_roundtrip(self):
        conn = Connection(
            conn_id="c1",
            source_node_id="n1",
            target_node_id="n2",
            source_port="true",
            target_port="in",
            label="条件成立",
        )
        d = conn.to_dict()
        restored = Connection.from_dict(d)
        assert restored.conn_id == "c1"
        assert restored.source_port == "true"
        assert restored.label == "条件成立"

    def test_workflow_roundtrip(self):
        wf = Workflow(name="测试工作流", description="desc")
        wf.nodes = [
            WorkflowNode(node_id="t1", node_type=NODE_TYPE_TRIGGER, config={"cron_expr": "0 9 * * *"}),
            WorkflowNode(node_id="a1", node_type=NODE_TYPE_AI_CALL, config={"system_prompt": "hi"}),
        ]
        wf.connections = [Connection(source_node_id="t1", target_node_id="a1")]
        d = wf.to_dict()
        restored = Workflow.from_dict(d)
        assert restored.name == "测试工作流"
        assert len(restored.nodes) == 2
        assert restored.nodes[0].node_id == "t1"
        assert restored.nodes[0].config["cron_expr"] == "0 9 * * *"
        assert len(restored.connections) == 1


# ═══════════════════════════════════════════════════════════════
# Models: get_node / get_connections_for_node
# ═══════════════════════════════════════════════════════════════


class TestWorkflowHelpers:
    def test_get_node_found(self):
        wf = Workflow()
        n = WorkflowNode(node_id="find_me")
        wf.nodes = [n]
        assert wf.get_node("find_me") is n

    def test_get_node_not_found(self):
        wf = Workflow()
        assert wf.get_node("nope") is None

    def test_get_connections_for_node(self):
        wf = Workflow()
        wf.connections = [
            Connection(conn_id="c1", source_node_id="n1", target_node_id="n2"),
            Connection(conn_id="c2", source_node_id="n2", target_node_id="n3"),
            Connection(conn_id="c3", source_node_id="n3", target_node_id="n4"),
        ]
        conns = wf.get_connections_for_node("n2")
        conn_ids = {c.conn_id for c in conns}
        assert conn_ids == {"c1", "c2"}


# ═══════════════════════════════════════════════════════════════
# ExecutionEngine: _evaluate_condition 安全边界
# ═══════════════════════════════════════════════════════════════


class TestEvaluateCondition:
    @pytest.fixture
    def engine(self):
        return WorkflowExecutionEngine()

    def test_empty_expression_returns_true(self, engine):
        assert engine._evaluate_condition("", {}) is True
        assert engine._evaluate_condition("   ", {}) is True

    def test_literal_true(self, engine):
        assert engine._evaluate_condition("True", {}) is True

    def test_literal_false(self, engine):
        assert engine._evaluate_condition("False", {}) is False

    def test_simple_comparison(self, engine):
        assert engine._evaluate_condition("1 > 0", {}) is True
        assert engine._evaluate_condition("1 < 0", {}) is False

    def test_boolean_combination(self, engine):
        assert engine._evaluate_condition("True and False", {}) is False
        assert engine._evaluate_condition("True or False", {}) is True

    def test_context_output_injection(self, engine):
        ctx = {"node1": {"count": 5}}
        # node1 → variable "node1" (dict), access via subscript
        assert engine._evaluate_condition('node1["count"] > 3', ctx) is True
        assert engine._evaluate_condition('node1["count"] > 10', ctx) is False

    def test_dangerous_import_blocked(self, engine):
        with pytest.raises(ValueError, match="禁止"):
            engine._evaluate_condition("__import__('os').system('rm -rf /')", {})

    def test_dangerous_exec_blocked(self, engine):
        with pytest.raises(ValueError, match="禁止"):
            engine._evaluate_condition("exec('print(1)')", {})

    def test_dangerous_eval_blocked(self, engine):
        with pytest.raises(ValueError, match="禁止"):
            engine._evaluate_condition("eval('1+1')", {})

    def test_dangerous_open_blocked(self, engine):
        with pytest.raises(ValueError, match="禁止"):
            engine._evaluate_condition("open('/etc/passwd')", {})

    def test_dangerous_os_blocked(self, engine):
        with pytest.raises(ValueError, match="禁止"):
            engine._evaluate_condition("os.system('ls')", {})

    def test_dangerous_subprocess_blocked(self, engine):
        with pytest.raises(ValueError, match="禁止"):
            engine._evaluate_condition("subprocess.run(['ls'])", {})

    def test_dunder_blocked(self, engine):
        with pytest.raises(ValueError, match="禁止"):
            engine._evaluate_condition("__builtins__", {})

    def test_invalid_expression_raises_name_error(self, engine):
        # Review fix #5: 未定义变量应抛出 NameError，让调用方标记节点为 error，
        # 而非静默返回 False（用户配置错误需要明确反馈）
        with pytest.raises(NameError, match="未定义"):
            engine._evaluate_condition("undefined_var + 1", {})

    def test_len_function_allowed(self, engine):
        ctx = {"node1": [1, 2, 3]}
        assert engine._evaluate_condition("len(node1) > 0", ctx) is True
        assert engine._evaluate_condition("len(node1) > 5", ctx) is False


# ═══════════════════════════════════════════════════════════════
# ExecutionEngine: _build_chain_plan / _wf_node_to_chain
# ═══════════════════════════════════════════════════════════════


class TestBuildChainPlan:
    @pytest.fixture
    def engine(self):
        return WorkflowExecutionEngine()

    def test_trigger_node_skipped(self, engine):
        wf = Workflow(name="test")
        wf.nodes = [
            WorkflowNode(node_id="trig", node_type=NODE_TYPE_TRIGGER),
            WorkflowNode(node_id="ai", node_type=NODE_TYPE_AI_CALL, config={"system_prompt": "hi"}),
        ]
        plan = engine._build_chain_plan(wf)
        # trigger 不转为执行节点
        assert len(plan.nodes) == 1
        assert plan.nodes[0].node_id == "wf_ai"

    def test_disabled_node_skipped(self, engine):
        wf = Workflow(name="test")
        wf.nodes = [
            WorkflowNode(node_id="ai1", node_type=NODE_TYPE_AI_CALL, enabled=False),
            WorkflowNode(node_id="ai2", node_type=NODE_TYPE_AI_CALL, enabled=True),
        ]
        plan = engine._build_chain_plan(wf)
        assert len(plan.nodes) == 1
        assert plan.nodes[0].node_id == "wf_ai2"

    def test_depends_on_mapped(self, engine):
        wf = Workflow(name="test")
        wf.nodes = [
            WorkflowNode(node_id="trig", node_type=NODE_TYPE_TRIGGER),
            WorkflowNode(node_id="a", node_type=NODE_TYPE_AI_CALL),
            WorkflowNode(node_id="b", node_type=NODE_TYPE_DELIVERY),
        ]
        wf.connections = [
            Connection(source_node_id="trig", target_node_id="a"),
            Connection(source_node_id="a", target_node_id="b"),
        ]
        plan = engine._build_chain_plan(wf)
        node_b = [n for n in plan.nodes if n.node_id == "wf_b"][0]
        assert "wf_a" in node_b.depends_on

    def test_ai_call_payload(self, engine):
        wf = Workflow(name="test")
        wf.nodes = [
            WorkflowNode(
                node_id="ai",
                node_type=NODE_TYPE_AI_CALL,
                config={"system_prompt": "analyze", "temperature": 0.3, "model_override": "deepseek-chat"},
            ),
        ]
        plan = engine._build_chain_plan(wf)
        node = plan.nodes[0]
        assert node.action_id == "reasoning"
        assert node.delegate_to == "cloud"
        assert node.payload["system_prompt"] == "analyze"
        assert node.payload["temperature"] == 0.3
        assert node.payload["model"] == "deepseek-chat"

    def test_tool_action_payload(self, engine):
        wf = Workflow(name="test")
        wf.nodes = [
            WorkflowNode(
                node_id="tool",
                node_type=NODE_TYPE_TOOL_ACTION,
                config={"action_id": "web.search", "action_payload": {"query": "test"}},
            ),
        ]
        plan = engine._build_chain_plan(wf)
        node = plan.nodes[0]
        assert node.action_id == "web.search"
        assert node.delegate_to == "automation"
        assert node.payload["query"] == "test"

    def test_condition_payload(self, engine):
        wf = Workflow(name="test")
        wf.nodes = [
            WorkflowNode(
                node_id="cond",
                node_type=NODE_TYPE_CONDITION,
                config={"expression": "True"},
            ),
        ]
        plan = engine._build_chain_plan(wf)
        node = plan.nodes[0]
        assert node.action_id == "condition"
        assert node.delegate_to == "local"
        assert node.payload["expression"] == "True"

    def test_delivery_payload(self, engine):
        wf = Workflow(name="test")
        wf.nodes = [
            WorkflowNode(
                node_id="del",
                node_type=NODE_TYPE_DELIVERY,
                config={"channel": "email", "message_template": "Hello"},
            ),
        ]
        plan = engine._build_chain_plan(wf)
        node = plan.nodes[0]
        assert node.action_id == "deliver"
        assert node.payload["channel"] == "email"
        assert node.payload["message_template"] == "Hello"


# ═══════════════════════════════════════════════════════════════
# ExecutionEngine: run_workflow (sync, no agent_chain)
# ═══════════════════════════════════════════════════════════════


class TestRunWorkflowSync:
    def test_cycle_returns_error(self):
        engine = WorkflowExecutionEngine()
        wf = Workflow(name="cycle")
        n1 = WorkflowNode(node_id="n1", node_type=NODE_TYPE_AI_CALL)
        n2 = WorkflowNode(node_id="n2", node_type=NODE_TYPE_AI_CALL)
        wf.nodes = [n1, n2]
        wf.connections = [
            Connection(source_node_id="n1", target_node_id="n2"),
            Connection(source_node_id="n2", target_node_id="n1"),
        ]
        result = engine.run_workflow(wf)
        assert result["status"] == "error"

    def test_no_capability_router_skips(self):
        engine = WorkflowExecutionEngine()
        wf = Workflow(name="test")
        wf.nodes = [
            WorkflowNode(node_id="trig", node_type=NODE_TYPE_TRIGGER),
            WorkflowNode(node_id="ai", node_type=NODE_TYPE_AI_CALL),
        ]
        result = engine.run_workflow(wf)
        assert result["status"] == "completed"
        assert len(result["steps"]) == 1
        assert result["steps"][0]["status"] == "skipped"


# ═══════════════════════════════════════════════════════════════
# Repository: CRUD
# ═══════════════════════════════════════════════════════════════


class TestWorkflowRepository:
    @pytest.fixture
    def repo(self):
        return WorkflowRepository(FakeDataStore())

    def test_list_empty(self, repo):
        assert repo.list_workflows() == []

    def test_save_and_get(self, repo):
        wf = Workflow(name="my workflow")
        repo.save_workflow(wf)
        got = repo.get_workflow(wf.workflow_id)
        assert got is not None
        assert got.name == "my workflow"

    def test_save_replaces_existing(self, repo):
        wf = Workflow(name="original")
        repo.save_workflow(wf)
        wf.name = "updated"
        repo.save_workflow(wf)
        got = repo.get_workflow(wf.workflow_id)
        assert got.name == "updated"
        assert len(repo.list_workflows()) == 1

    def test_delete(self, repo):
        wf = Workflow(name="to delete")
        repo.save_workflow(wf)
        assert repo.delete_workflow(wf.workflow_id) is True
        assert repo.get_workflow(wf.workflow_id) is None

    def test_delete_not_found(self, repo):
        assert repo.delete_workflow("nonexistent") is False

    def test_duplicate(self, repo):
        wf = Workflow(name="original")
        wf.nodes = [WorkflowNode(node_id="n1", node_type=NODE_TYPE_AI_CALL)]
        repo.save_workflow(wf)
        dup = repo.duplicate_workflow(wf.workflow_id)
        assert dup is not None
        assert dup.workflow_id != wf.workflow_id
        assert "副本" in dup.name
        assert dup.enabled is False
        assert len(dup.nodes) == 1
        # 原始仍然存在
        assert repo.get_workflow(wf.workflow_id) is not None
        # 副本也已保存
        assert repo.get_workflow(dup.workflow_id) is not None

    def test_duplicate_not_found(self, repo):
        assert repo.duplicate_workflow("nonexistent") is None
