# -*- coding: utf-8 -*-
"""Regression tests for services.sandbox_manager SandboxManager (P3-8).

The global singleton's ``set_tool_executor`` previously overwrote a
prior executor silently, so two ``build_siyi_tools(ctx)`` calls would
end up routing approvals against the wrong context without any signal.
Now it warns on overwrite and exposes ``unset_tool_executor``.
"""
from __future__ import annotations

import logging

from services.sandbox_manager import SandboxManager, get_sandbox_manager


def test_set_executor_first_time_silent(caplog) -> None:
    sm = SandboxManager()
    with caplog.at_level(logging.WARNING, logger="siyi.services.sandbox_manager"):
        sm.set_tool_executor(lambda *a, **kw: "ok")
    assert all("replacing existing executor" not in rec.message for rec in caplog.records)


def test_set_executor_same_instance_no_warning(caplog) -> None:
    sm = SandboxManager()
    exe = lambda *a, **kw: "ok"
    sm.set_tool_executor(exe)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="siyi.services.sandbox_manager"):
        sm.set_tool_executor(exe)
    assert all("replacing existing executor" not in rec.message for rec in caplog.records)


def test_set_executor_overwrite_logs_warning(caplog) -> None:
    sm = SandboxManager()
    sm.set_tool_executor(lambda *a, **kw: "A")
    with caplog.at_level(logging.WARNING, logger="siyi.services.sandbox_manager"):
        sm.set_tool_executor(lambda *a, **kw: "B")
    warnings = [r for r in caplog.records if "replacing existing executor" in r.message]
    assert len(warnings) == 1


def test_set_executor_none_after_existing_no_warning(caplog) -> None:
    sm = SandboxManager()
    sm.set_tool_executor(lambda *a, **kw: "A")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="siyi.services.sandbox_manager"):
        sm.set_tool_executor(None)
    # 显式清空不应被视为可疑覆盖
    assert all("replacing existing executor" not in rec.message for rec in caplog.records)


def test_unset_tool_executor_clears() -> None:
    sm = SandboxManager()
    sm.set_tool_executor(lambda *a, **kw: "ok")
    assert sm._tool_executor is not None
    sm.unset_tool_executor()
    assert sm._tool_executor is None


def test_global_sandbox_manager_is_singleton() -> None:
    """get_sandbox_manager 必须始终返回同一实例（已有约定）。"""
    assert get_sandbox_manager() is get_sandbox_manager()
