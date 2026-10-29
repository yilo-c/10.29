"""验证 _chat_memory._react_catalog_to_openai_tools 正确识别必填字段。

历史 bug：文件因 mojibake 把检测字符串写成 "(必填)"，使所有 OpenAI
tool 定义的 required 字段永远为空，LLM 频繁省略关键参数。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._chat_memory import _react_catalog_to_openai_tools  # noqa: E402


def test_required_fields_detected_from_correct_utf8():
    """正常 UTF-8 catalog 必须能识别 (必填) 标记。"""
    catalog = [
        {
            "action_id": "file.read",
            "description": "读取文件",
            "payload_schema": {
                "path": "string (必填) 文件路径",
                "encoding": "string (可选, 默认 utf-8)",
            },
        },
    ]
    tools = _react_catalog_to_openai_tools(catalog)
    assert len(tools) == 1
    fn = tools[0]["function"]
    assert fn["name"] == "file_read"
    params = fn["parameters"]
    assert params["required"] == ["path"]
    assert "encoding" in params["properties"]
    assert "path" in params["properties"]


def test_required_fields_detected_from_legacy_mojibake_with_warning(caplog):
    """必填字段应被识别。"""
    catalog = [
        {
            "action_id": "web.fetch",
            "description": "获取网页",
            "payload_schema": {
                "url": "string (必填) 完整 URL",
            },
        },
    ]
    tools = _react_catalog_to_openai_tools(catalog)
    assert tools[0]["function"]["parameters"]["required"] == ["url"]


def test_no_required_when_all_optional():
    catalog = [
        {
            "action_id": "system.info",
            "description": "状态",
            "payload_schema": {"detail": "string (可选)"},
        },
    ]
    tools = _react_catalog_to_openai_tools(catalog)
    assert tools[0]["function"]["parameters"]["required"] == []


def test_dot_in_action_id_replaced_with_underscore():
    """OpenAI 强制 ^[a-zA-Z0-9_-]+$，必须把 file.read → file_read。"""
    catalog = [{"action_id": "file.read", "description": "", "payload_schema": {}}]
    tools = _react_catalog_to_openai_tools(catalog)
    assert tools[0]["function"]["name"] == "file_read"


def test_real_react_catalog_has_required_fields():
    """端到端：真实 REACT_TOOL_CATALOG 应至少有几个有 required 字段的工具。

    回归保护：如果有人重新保存 react_executor.py 又触发 mojibake，
    这条测试会立刻失败而不是悄悄退化。
    """
    from services.react_tool_catalog import REACT_TOOL_CATALOG

    tools = _react_catalog_to_openai_tools(REACT_TOOL_CATALOG)
    tools_with_required = [t for t in tools if t["function"]["parameters"]["required"]]
    assert len(tools_with_required) >= 6, (
        f"只有 {len(tools_with_required)} 个工具有 required 字段，"
        f"REACT_TOOL_CATALOG 中至少有 file.read/write/find/copy/web.fetch/web.search 等"
        f"应有 required 标记。检查 react_executor.py 是否仍含 (必填) 字符串。"
    )
    # 确认关键工具的关键参数都被识别为 required
    by_name = {t["function"]["name"]: t for t in tools}
    if "file_read" in by_name:
        assert "path" in by_name["file_read"]["function"]["parameters"]["required"]
    if "web_fetch" in by_name:
        assert "url" in by_name["web_fetch"]["function"]["parameters"]["required"]
