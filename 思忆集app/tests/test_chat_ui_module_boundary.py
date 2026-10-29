"""P3-12 回归：assistant.chat_ui 不应该在 headless backend 被透传加载。

之前 ``ai/__init__.py`` 有一行 ``from assistant.chat_ui import ...``，
而 ``assistant/__init__.py:_MODULE_NAMES`` 也把 ``chat_ui`` 列入 lazy 加载
列表 —— 两条路径都会让 headless 启动透传到 PyQt6 widget 模块。

修复后：
- ``ai.__init__`` 不再 re-export chat_ui
- ``assistant.__init__._MODULE_NAMES`` 移除 chat_ui
- 但 ``ui/ai_chat_page.py`` 显式 ``from assistant.chat_ui import AtTextEdit`` 仍然
  能正常工作
"""
from __future__ import annotations

import sys

import pytest

from assistant import _MODULE_NAMES  # type: ignore[attr-defined]


def test_chat_ui_not_in_lazy_module_names() -> None:
    """assistant.__init__._MODULE_NAMES 不应再包含 chat_ui。"""
    assert "chat_ui" not in _MODULE_NAMES, (
        "assistant.chat_ui 是 PyQt6 widget，不能在 assistant 包的 __getattr__ "
        "walker 列表里，否则任何 headless backend 上的未知属性访问都会拉起 PyQt6。"
    )


def test_ai_package_does_not_reexport_chat_ui() -> None:
    """ai 包 import 后，不应该把 assistant.chat_ui 拖进 sys.modules。"""
    # 注意：用例运行前可能已经因别的测试加载过 ai 包；只检查 ai 包公开属性
    import ai

    assert not hasattr(ai, "AtTextEdit"), (
        "ai 包不应再 re-export PyQt6 widget；这是 P3-12 修复的核心。"
    )
    assert not hasattr(ai, "AtReferencePopup")


@pytest.mark.skipif(
    "assistant.chat_ui" in sys.modules,
    reason="本进程已经显式 import 过 chat_ui（其他测试触发）—— 这里只保护未触碰的进程。",
)
def test_importing_assistant_package_does_not_load_chat_ui() -> None:
    """``import assistant`` 不应触发 ``assistant.chat_ui`` 加载。"""
    # 触发包初始化（如果之前没加载）
    import importlib
    importlib.import_module("assistant")
    assert "assistant.chat_ui" not in sys.modules


def test_direct_import_still_works() -> None:
    """``from assistant.chat_ui import AtTextEdit`` 仍然是 ui/ai_chat_page.py 的正常用法。"""
    # 这一步真的会拉 PyQt6 —— 在测试环境是允许的
    pytest.importorskip("PyQt6")
    from assistant.chat_ui import AtTextEdit, AtReferencePopup

    assert AtTextEdit.__name__ == "AtTextEdit"
    assert AtReferencePopup.__name__ == "AtReferencePopup"
