"""Integration tests for pattern.execute RPC handler — local operations."""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def test_word_count():
    from services.pattern_actions import get_registry

    reg = get_registry()
    result = reg.execute("word_count", "Hello world\n你好世界")
    assert "中文字数" in result
    assert "英文词数" in result
    assert "总字符" in result
    assert "行数" in result


def test_json_format_valid():
    from services.pattern_actions import get_registry

    reg = get_registry()
    result = reg.execute("json_format", '{"a":1,"b":[2,3]}')
    assert '"a"' in result
    assert "1" in result


def test_json_format_invalid():
    from services.pattern_actions import get_registry

    reg = get_registry()
    result = reg.execute("json_format", "not json")
    assert "解析失败" in result


def test_base64_roundtrip():
    from services.pattern_actions import get_registry

    reg = get_registry()
    original = "Hello 思忆集!"
    encoded = reg.execute("base64_encode", original)
    assert len(encoded) > 0
    decoded = reg.execute("base64_decode", encoded)
    assert decoded == original


def test_date_convert_iso():
    from services.pattern_actions import get_registry

    reg = get_registry()
    result = reg.execute("date_convert", "2024-01-15")
    assert "2024-01-15" in result
    assert "2024年01月15日" in result


def test_date_convert_unrecognized():
    from services.pattern_actions import get_registry

    reg = get_registry()
    result = reg.execute("date_convert", "not a date")
    assert "未识别" in result


def test_fetch_url_no_url():
    from services.pattern_actions import get_registry

    reg = get_registry()
    result = reg.execute("fetch_url", "no url here")
    assert "未检测到链接" in result


def test_keywords_extraction():
    from services.pattern_actions import get_registry

    reg = get_registry()
    result = reg.execute("keywords", "人工智能正在改变世界，机器学习和深度学习是核心技术")
    assert "关键词" in result


def test_code_format_markdown():
    from services.pattern_actions import get_registry

    reg = get_registry()
    result = reg.execute("code_format", "```python\nprint('hello')\n```")
    assert "python" in result
    assert "print" in result


def test_ai_action_delegated():
    """AI actions (translate, summarize, etc.) should not be executable directly —
    they must go through the backend RPC handler which provides ai_client."""
    from services.pattern_actions import get_registry

    reg = get_registry()
    result = reg.execute("translate", "hello")
    # When service_registry is unavailable, returns delegation message.
    # When service_registry is available but AI call fails, returns error message.
    # Either way, the action should be "handled" (not crash with unhandled exception).
    assert "后端 AI 引擎处理" in result or "翻译失败" in result


def test_unknown_action():
    """未知 action 必须走异常通道（2026-10-16 契约修复）：
    旧行为把"未知操作: xxx"当 result 返回，前端 NoteEditor 会把
    错误文案当成整理结果写回笔记（数据破坏风险）。"""
    import pytest

    from services.pattern_actions import get_registry

    reg = get_registry()
    with pytest.raises(ValueError, match="未知操作"):
        reg.execute("nonexistent", "text")


def test_organize_registered():
    """NoteEditor「整理」走 pattern.execute action_id=organize——
    2026-10-16 前后端白名单都缺此动作，导致"未知操作: organize"被当
    整理结果展示甚至写回笔记（E2E 走查实证）。"""
    from services.pattern_actions import get_registry

    reg = get_registry()
    ids = [a.action_id for a in reg._actions.values()]
    assert "organize" in ids


def test_menu_items_include_ai_actions():
    """Menu should still list AI actions for UI display."""
    from services.pattern_actions import get_registry

    reg = get_registry()
    items = reg.menu_items()
    ids = {item[0] for item in items}
    assert "translate" in ids
    assert "summarize" in ids
    assert "polish" in ids
    assert "continue" in ids
    assert "explain" in ids
    assert "save_note" in ids
    assert "word_count" in ids
