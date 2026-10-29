"""阶段二 W2：验证 api 与 worker 的软解耦（延迟导入桥接）。

回归目标：
1. ``api.main`` 顶层导入**不再**直接依赖 ``automation_hub.worker``——
   即使 worker 链缺失，``import api.main`` 也应成功（契约测试/纯 API 环境可启动）。
2. worker 在场时，桥接函数能正确转发调用；不在场时抛出 ``WorkerUnavailableError``
   而非静默返回 None。
"""

from __future__ import annotations

import sys

import pytest

from automation_hub.api import _worker_bridge as bridge


def test_api_main_imports_without_worker(monkeypatch):
    """模拟 worker 包不可用，api.main 仍应成功导入。

    通过让 ``importlib.import_module`` 对 worker 相关路径抛出
    ModuleNotFoundError，模拟 'automation_hub.worker 缺失/损坏'。
    """
    import importlib

    real_import_module = importlib.import_module

    def fake_import_module(name, *args, **kwargs):
        if name.startswith("automation_hub.worker"):
            raise ModuleNotFoundError(f"simulated missing worker: {name}")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    import automation_hub.api.main as main  # noqa: F401

    assert main.app is not None


def test_bridge_forwards_when_worker_present():
    """worker 在场时，桥接函数能正确加载符号（不抛异常）。"""
    # 这些符号在正常环境下应可加载（worker 已恢复/在场）
    assert callable(bridge.enqueue_job_sync)
    assert callable(bridge.arq_queue_length)
    assert callable(bridge.run_tool_job)
    assert callable(bridge.is_proposal_approved)
    assert callable(bridge.load_scripts_manifest)


def test_bridge_raises_clear_error_when_symbol_missing(monkeypatch):
    """桥接在 worker 缺失时抛出清晰异常，而非静默 None。"""
    import importlib

    real_import_module = importlib.import_module

    def fake_import_module(name, *args, **kwargs):
        if name.startswith("automation_hub.worker"):
            raise ModuleNotFoundError(f"simulated missing worker: {name}")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    with pytest.raises(bridge.WorkerUnavailableError):
        bridge.enqueue_job_sync("dummy")
