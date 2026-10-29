"""回归测试 — OpenAI tool schema 合法性。

历史 bug：``capability_registry.ToolDef.to_openai_tool()`` 把
``CAPABILITY_CATALOG`` 里的 ``payload_schema``（形如 ``{"query": "搜索关键词"}``
的描述字符串字典）原样当作 ``parameters`` 传给 OpenAI，导致：
  - DeepSeek: ``Invalid schema for function 'news_lookup': schema must be a
    JSON Schema of 'type: "object"', got 'type: null'``
  - Ollama:  ``Unrecognized schema: {"type":"","properties":null}``
  - ReAct 整条 chat 链路 400，AI 对话完全不可用。

本测试断言：
  1. 每个工具的 ``parameters`` 都是合法 JSON Schema（顶层 ``type == "object"``，
     且有 ``properties`` 字典）。
  2. 每个属性 schema 必须含 ``type``（不能是 ``""`` / ``null`` / 中文描述字符串）。
  3. 报错点 ``news_lookup`` 和 ``file_find``（曾用参数名 ``type``）能正确生成。
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.capability_types import (
    CapabilityDescriptor,
    CapabilityRegistry,
    ToolDef,
    _normalize_parameters_schema,
)
from assistant.agents.planner import AssistantPlannerLayer


# ═══════════════════════════════════════════════════════════════
# _normalize_parameters_schema 单元测试
# ═══════════════════════════════════════════════════════════════


class TestNormalizeParametersSchema:
    def test_empty_returns_object_schema(self):
        schema = _normalize_parameters_schema({})
        assert schema == {"type": "object", "properties": {}}

    def test_none_returns_object_schema(self):
        schema = _normalize_parameters_schema(None)
        assert schema == {"type": "object", "properties": {}}

    def test_description_string_dict_converted(self):
        """描述字符串字典 → 合法 JSON Schema。"""
        schema = _normalize_parameters_schema({"query": "搜索关键词", "limit": "数量(1-20)"})
        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert schema["properties"]["query"] == {"type": "string", "description": "搜索关键词"}
        # 含"数量"的描述应识别为 integer
        assert schema["properties"]["limit"]["type"] == "integer"

    def test_already_json_schema_passthrough(self):
        """已是合法 JSON Schema（含 type）应原样返回。"""
        original = {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "文件路径"}},
            "required": ["path"],
        }
        schema = _normalize_parameters_schema(original)
        assert schema == original
        # 返回的是拷贝，修改不影响原对象
        schema["properties"]["path"]["type"] = "integer"
        assert original["properties"]["path"]["type"] == "string"

    def test_boolean_hint(self):
        schema = _normalize_parameters_schema({"recursive": "是否递归(true/false)"})
        assert schema["properties"]["recursive"]["type"] == "boolean"

    def test_required_marker_stripped(self):
        schema = _normalize_parameters_schema({"path": "文件路径(必填)"})
        assert schema.get("required") == ["path"]
        assert "必填" not in schema["properties"]["path"]["description"]

    def test_no_type_collision_for_param_named_type(self):
        """参数名恰好叫 type（如 file.find 历史参数）时，不应污染顶层 schema。

        参数 type 应作为 properties 的一个普通 key 存在，顶层 type 仍是 object。
        """
        schema = _normalize_parameters_schema({"pattern": "文件名模式", "type": "file|dir(可选)"})
        assert schema["type"] == "object"
        assert "type" in schema["properties"]
        assert schema["properties"]["type"]["type"] == "string"

    def test_non_dict_input_fallback(self):
        """非字典输入兜底返回空 object schema，绝不漏非法结构给模型。"""
        schema = _normalize_parameters_schema(["not", "a", "dict"])
        assert schema == {"type": "object", "properties": {}}


# ═══════════════════════════════════════════════════════════════
# ToolDef.to_openai_tool 测试
# ═══════════════════════════════════════════════════════════════


class TestToolDefToOpenaiTool:
    def test_produces_valid_schema_for_string_dict_parameters(self):
        """核心回归：描述字符串字典必须生成合法的 OpenAI tool schema。"""
        td = ToolDef(
            name="news.lookup",
            description="检索新闻资讯",
            parameters={"query": "搜索关键词", "limit": "数量(1-20)"},
        )
        tool = td.to_openai_tool()
        assert tool["type"] == "function"
        func = tool["function"]
        # news.lookup → news_lookup（OpenAI 要求 ^[a-zA-Z0-9_-]+$）
        assert func["name"] == "news_lookup"
        params = func["parameters"]
        assert params["type"] == "object", "顶层 type 必须是 object"
        assert isinstance(params["properties"], dict)
        assert params["properties"]["query"] == {"type": "string", "description": "搜索关键词"}
        assert params["properties"]["limit"]["type"] == "integer"

    def test_top_level_type_never_null(self):
        """回归 DeepSeek 报错：'got type: null'。"""
        td = ToolDef(name="x.y", description="d", parameters={"a": "b"})
        params = td.to_openai_tool()["function"]["parameters"]
        assert params.get("type") == "object"

    def test_empty_parameters_still_valid(self):
        """空参数（如 clipboard.read）也要生成合法 schema。"""
        td = ToolDef(name="clipboard.read", description="读剪贴板", parameters={})
        params = td.to_openai_tool()["function"]["parameters"]
        assert params == {"type": "object", "properties": {}}


# ═══════════════════════════════════════════════════════════════
# 针对真实 CAPABILITY_CATALOG 的回归测试
# ═══════════════════════════════════════════════════════════════


def _build_registry_from_catalog() -> CapabilityRegistry:
    """模拟 capability_router._register_builtin_capabilities 的注册过程。"""
    registry = CapabilityRegistry()
    for entry in AssistantPlannerLayer.CAPABILITY_CATALOG:
        registry.register_capability(
            CapabilityDescriptor(
                capability_id=entry["action_id"],
                description=entry["description"],
                risk_level=entry.get("risk", "low"),
                payload_schema=dict(entry.get("payload_schema") or {}),
            )
        )
    return registry


class TestRealCatalogProducesValidSchemas:
    """对真实 catalog 跑一遍 build_openai_tools，所有工具 schema 必须合法。"""

    def setup_method(self):
        self.registry = _build_registry_from_catalog()
        self.tools = self.registry.build_openai_tools()

    def test_all_tools_have_object_parameters(self):
        """每个工具的 parameters 必须是 type=object 的合法 JSON Schema。"""
        assert len(self.tools) > 0, "catalog 应注册了若干工具"
        for tool in self.tools:
            func = tool["function"]
            params = func["parameters"]
            assert params.get("type") == "object", f"{func['name']}: parameters.type 必须是 'object'，实际 {params.get('type')!r}"
            assert isinstance(params.get("properties"), dict), f"{func['name']}: properties 必须是 dict"

    def test_all_properties_have_valid_type(self):
        """每个属性 schema 必须含 type 且非空字符串（回归 Ollama 'type: null' / 'type: ""'）。"""
        for tool in self.tools:
            func = tool["function"]
            for prop_name, prop_schema in func["parameters"]["properties"].items():
                ptype = prop_schema.get("type")
                assert ptype in {"string", "integer", "number", "boolean", "array", "object", "null"}, (
                    f"{func['name']}.{prop_name}: 非法 type={ptype!r}"
                )
                assert ptype != "", f"{func['name']}.{prop_name}: type 不能为空字符串"

    def test_news_lookup_schema_valid(self):
        """直接点名触发 bug 的 news_lookup 工具。"""
        names = [t["function"]["name"] for t in self.tools]
        assert "news_lookup" in names
        tool = next(t for t in self.tools if t["function"]["name"] == "news_lookup")
        params = tool["function"]["parameters"]
        assert params["type"] == "object"
        assert set(params["properties"].keys()) == {"query", "limit", "source", "category"}
        for prop in params["properties"].values():
            assert prop.get("type") in {"string", "integer"}

    def test_file_find_uses_entry_type_not_type(self):
        """file.find 的 type 过滤参数应改名为 entry_type，避免与 schema 关键字冲突。"""
        tool = next(t for t in self.tools if t["function"]["name"] == "file_find")
        prop_names = set(tool["function"]["parameters"]["properties"].keys())
        assert "entry_type" in prop_names, "file.find 应使用 entry_type 参数名"
        assert "type" not in prop_names, "file.find 不应再用 type 作为参数名"


if __name__ == "__main__":
    # 支持 python tests/test_capability_schema_fix.py 直接运行
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
