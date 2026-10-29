# -*- coding: utf-8 -*-
"""接缝契约测试（批次1 防复发门禁）。

守卫两条架构接缝不被回归破坏：

1. ``EventBus.emit`` 单参契约：任何 ``.emit(...)`` 调用都必须是
   ``.emit(AppEvent(...))`` 形式，禁止旧的 ``.emit("type", payload)`` 双参写法
   （旧写法会被 try/except 静默吞掉，导致事件永不广播）。
2. 启动自检模块可导入且 ``run_startup_selfcheck`` 可调用（fail-fast 防线）。
"""
from __future__ import annotations

import ast
import os
import pathlib

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]

# 剪枝目录：必须走 os.walk 自顶向下剪枝，绝不能用无剪枝的 rglob——
# basetemp（.pytest-tmp）一旦落在 BACKEND_ROOT 内（相对路径 --basetemp 的事故
# 遗留），rglob 会把上千个测试临时目录全部走一遍，单测卡死 20 分钟+。
_SKIP_DIRS = {"tests", ".venv", "venv", ".pytest-tmp", "node_modules", ".git"}


def _iter_backend_py() -> "list[pathlib.Path]":
    out: list[pathlib.Path] = []
    for root, dirs, files in os.walk(BACKEND_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if name.endswith(".py"):
                out.append(pathlib.Path(root) / name)
    return out


def test_no_legacy_double_arg_emit() -> None:
    """扫描后端源码，禁止旧式 ``emit("type", payload)`` 双参调用。"""
    bad: list[str] = []
    for p in _iter_backend_py():
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for i, raw in enumerate(lines, 1):
            norm = raw.replace(" ", "")
            # 仅关心 EventBus 的 emit；Qt 信号（error_occurred.emit(...) 等）是合法用法
            if "event_bus" in norm and ".emit(" in norm and ("emit(\"" in norm or "emit('" in norm):
                bad.append(f"{p.relative_to(BACKEND_ROOT)}:{i}: {raw.strip()}")
    assert not bad, "发现旧式双参 emit(type, payload) 调用（应改为 emit(AppEvent(...))）：\n" + "\n".join(bad)


def _is_event_bus_emit(node: ast.Call) -> bool:
    """只关心 EventBus 风格的 emit（接收者为属性链或裸名）。

    2026-09-13 守卫通电普查发现盲区：``event_bus.emit("x", y)``（裸 Name 接收者）
    逃过原「属性链才匹配」的逻辑，仅靠正则测试兜底——补上裸名形态。
    裸名分支刻意只拦**双参**调用：单参 ``emit(ev)`` 传变量无法静态证明非
    AppEvent（零误报原则），交给运行期与审查兜底。
    """
    if getattr(node.func, "attr", "") != "emit":
        return False
    value = getattr(node.func, "value", None)
    if isinstance(value, ast.Name):
        if not ("event_bus" in value.id or value.id == "bus"):
            return False
        return len(node.args) >= 2
    if isinstance(value, ast.Attribute):
        return "event_bus" in value.attr or value.attr == "bus"
    return False


def test_emit_is_single_arg_in_ast() -> None:
    """用 AST 确认所有 EventBus emit 调用都是单参且参数为 AppEvent 构造。"""
    violations: list[str] = []
    for p in _iter_backend_py():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _is_event_bus_emit(node)):
                continue
            if len(node.args) != 1:
                violations.append(f"{p.relative_to(BACKEND_ROOT)}:{node.lineno} 参数数={len(node.args)}")
                continue
            arg = node.args[0]
            is_app_event = (
                isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Name)
                and arg.func.id == "AppEvent"
            )
            if not is_app_event:
                violations.append(f"{p.relative_to(BACKEND_ROOT)}:{node.lineno} 参数非 AppEvent")
    assert not violations, "emit 单参 AppEvent 契约违规：\n" + "\n".join(violations)


def test_startup_selfcheck_importable() -> None:
    from backend.startup_selfcheck import run_startup_selfcheck

    assert callable(run_startup_selfcheck)
