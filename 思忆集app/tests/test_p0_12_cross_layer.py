"""P0-12 跨层依赖回归测试：

- ``assistant/`` 不允许 ``from backend.*``（结构守护测试）
- 工具目录纯函数（``react_catalog_to_openai_tools`` / ``build_tool_name_map``）能正确处理一条
  最小化的 catalog 条目（行为兼容性测试）
"""
from __future__ import annotations

import ast
import pathlib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ASSISTANT_DIR = REPO_ROOT / "思忆集app" / "assistant"


def _iter_assistant_python_files():
    for p in ASSISTANT_DIR.rglob("*.py"):
        # 跳过 __pycache__ 之类
        if "__pycache__" in p.parts:
            continue
        yield p


def test_assistant_does_not_import_backend():
    """守护测试：assistant/ 下任何模块都不得 import backend.*。

    backend 是上层（编排 + WebSocket 入口），如果 assistant/ 反向 import，会产生
    层级循环且让 services/ 失去"共同下游"地位。本测试在 import 解析层（含 AST）
    直接拒绝任何 `from backend.x import y` 或 `import backend.x`。
    """
    violations: list[tuple[str, int, str]] = []
    for path in _iter_assistant_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            # 不阻塞测试，但记录
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == "backend" or mod.startswith("backend."):
                    violations.append((str(path.relative_to(REPO_ROOT)), node.lineno, f"from {mod} import ..."))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name == "backend" or name.startswith("backend."):
                        violations.append((str(path.relative_to(REPO_ROOT)), node.lineno, f"import {name}"))
    assert not violations, (
        "assistant/* 出现反向 backend.* import；请下沉共享代码到 services/：\n"
        + "\n".join(f"  {f}:{ln}  {stmt}" for f, ln, stmt in violations)
    )


def test_react_catalog_to_openai_tools_minimal_shape():
    """对一条最小化的 catalog 条目，输出结构必须可被 OpenAI tool 协议消费。"""
    from services.react_tool_catalog import react_catalog_to_openai_tools

    catalog = [
        {
            "action_id": "demo.action",
            "description": "示例工具",
            "payload_schema": {
                "name": "目标名称(必填)",
                "limit": "条数",
            },
        }
    ]
    tools = react_catalog_to_openai_tools(catalog)
    assert isinstance(tools, list) and len(tools) == 1
    tool = tools[0]
    assert tool["type"] == "function"
    fn = tool["function"]
    # 点号必须被替换为下划线（OpenAI/DeepSeek name 校验）
    assert fn["name"] == "demo_action"
    assert fn["description"] == "示例工具"
    params = fn["parameters"]
    assert params["type"] == "object"
    assert set(params["properties"].keys()) == {"name", "limit"}
    # 含 "(必填)" 的字段进 required
    assert params["required"] == ["name"]


def test_build_tool_name_map_basic():
    from services.react_tool_catalog import build_tool_name_map
    mapping = build_tool_name_map([
        {"action_id": "file.read"},
        {"action_id": "web.search"},
        {"action_id": "plain"},
    ])
    assert mapping == {
        "file_read": "file.read",
        "web_search": "web.search",
        "plain": "plain",
    }


def test_multi_agent_orchestrator_carries_no_executor_leg():
    """波次② C-1 执行器收敛：orchestrator 不再自带任何工具执行体。

    收敛前本用例是**正向**断言——强制 orchestrator 必须从 ``services.react_executor``
    引入执行器（P0.3 后为 brokered 变体）。第三套执行体（Planner+Worker 工作流）
    生产调用方为零，已随步骤 5 整体下线，故断言**反转**为：这些符号不得再出现。
    工具执行的唯一入口是 ``assistant/capability_router.CapabilityRouter.dispatch``
    （sandbox 腿经 ``services/react_executor.make_react_executor`` 适配转发）。

    原有的 backend 反向依赖守卫保留。
    """
    path = ASSISTANT_DIR / "multi_agent_orchestrator.py"
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    dead_legs = {
        "run_planner_workflow",
        "_plan_task",
        "_parse_plan_response",
        "_validate_plan",
        "_execute_subagent",
        "_execute_subagent_with_progress",
        "_run_react_loop",
        "_aggregate_results",
    }
    assert not (defined & dead_legs), f"orchestrator 残留已下线执行腿: {sorted(defined & dead_legs)}"

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imported.add(alias.name)
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
    assert "services.react_executor" not in imported, "orchestrator 不得再引入 ReAct 执行器工厂"
    assert not (imported & {"make_react_executor", "make_brokered_react_executor"})

    # 反向：不得依赖 backend 层
    assert "from backend.react_executor" not in src
    assert "from backend.handlers._chat_memory" not in src


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
