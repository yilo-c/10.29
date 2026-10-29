# -*- coding: utf-8 -*-
"""批3（2026-09-16）：D-9 补跑生效后浮出的 3 个 handler AttributeError 死信回归。

现场证据（data/data/task_queue.db，2026-09-16 02:14:31 补跑队列）：
- system.auto_backup    → 'LocalDataStore' object has no attribute 'create_backup'
- tech_watch.judge      → 'JudgeScheduler' object has no attribute 'judge'
- evolution.daily_scan  → 'EvolutionOrchestrator' object has no attribute 'daily_scan'

共同模式：M-cron 迁移时按 cron 任务名臆造方法名 / 绑错类，静态检查抓不到、
运行时 100% 抛 AttributeError 进死信。修复：
- auto_backup 按旧 capability_router._system_auto_backup 移植 BackupService；
- tech_watch_judge 在 _tooling_provider 的错误绑定（路由用 JudgeScheduler）改回
  services.tech_watch.TechWatchJudge；
- evolution.daily_scan 调真方法名 scan_and_report()。
"""
from __future__ import annotations

import types


import services.cron_actions as ca
from evolution.orchestrator import EvolutionOrchestrator
from services.tech_watch import TechWatchJudge


def _handler_for(action_id: str):
    """从注册表取 action 的执行函数。"""
    spec = ca._REGISTRY[action_id]
    assert callable(spec.handler), f"注册表项 {action_id} 无可调用 handler: {spec!r}"
    return spec.handler


class _Ctx:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# ---------------------------------------------------- evolution.daily_scan


def test_evolution_daily_scan_calls_real_method() -> None:
    """EvolutionOrchestrator 只有 scan_and_report，没有 daily_scan。"""
    assert hasattr(EvolutionOrchestrator, "scan_and_report")
    assert not hasattr(EvolutionOrchestrator, "daily_scan"), (
        "类上出现了 daily_scan——请同步确认 handler 调用名"
    )

    calls = []

    class _Orch:
        def scan_and_report(self):
            calls.append(1)
            return types.SimpleNamespace(to_dict=lambda: {"findings": 2})

    out = _handler_for("evolution.daily_scan")(_Ctx(evolution_orchestrator=_Orch()), {})
    assert calls == [1], f"未调用 scan_and_report: {out}"
    assert out["status"] == "completed"
    assert out["findings"] == {"findings": 2}


def test_evolution_daily_scan_skips_without_orchestrator() -> None:
    out = _handler_for("evolution.daily_scan")(_Ctx(), {})
    assert out["status"] == "skipped"


# ------------------------------------------------------- tech_watch.judge


def test_tech_watch_judge_class_has_judge() -> None:
    """绑定类的契约：TechWatchJudge 必须有 judge()（死信根因即绑错类）。"""
    assert hasattr(TechWatchJudge, "judge")
    import inspect

    sig = inspect.signature(TechWatchJudge.judge)
    # handler 以关键字传这两个参数
    assert "deepseek_client" in sig.parameters and "agent_gateway" in sig.parameters


def test_tooling_provider_binds_techwatchjudge_not_routescheduler() -> None:
    """结构断言：_tooling_provider 不得再把 tech_watch_judge 绑成 JudgeScheduler。"""
    import pathlib

    src = (
        pathlib.Path(ca.__file__).resolve().parent.parent
        / "app_runtime"
        / "providers"
        / "_tooling_provider.py"
    ).read_text(encoding="utf-8")
    assert "self.tech_watch_judge = JudgeScheduler" not in src, (
        "错误绑定回归：tech_watch_judge 被绑回路由用 JudgeScheduler（无 .judge()）"
    )
    assert "TechWatchJudge(store=" in src


def test_tech_watch_judge_handler_defends_missing_method() -> None:
    """context 上对象缺 judge 方法 → skipped 留痕，不再裸抛 AttributeError。"""
    class _NoJudge:
        pass

    out = _handler_for("tech_watch.judge")(_Ctx(tech_watch_judge=_NoJudge()), {})
    assert out["status"] == "skipped"
    assert "judge" in out["reason"]

    calls = []

    class _Good:
        def judge(self, *, deepseek_client=None, agent_gateway=None):
            calls.append((deepseek_client, agent_gateway))
            return {"assessed": 3}

    out = _handler_for("tech_watch.judge")(_Ctx(tech_watch_judge=_Good()), {})
    assert out["status"] == "completed" and out["result"] == {"assessed": 3}
    assert calls == [(None, None)]


# -------------------------------------------------------- system.auto_backup


def test_auto_backup_does_not_call_missing_store_method(monkeypatch) -> None:
    """回归锁定：不得再调 LocalDataStore.create_backup（该方法不存在）。"""
    from data.local_storage import LocalDataStore

    assert not hasattr(LocalDataStore, "create_backup"), (
        "LocalDataStore 新增了 create_backup？若是，请同步简化 handler 并更新本测试"
    )

    # monkeypatch BackupService，验证 handler 走它并正确映射结果
    import services.backup_service as bs

    captured = {}

    class _FakeService:
        def __init__(self, *, data_dir, config=None, data_store=None):
            captured["data_dir"] = str(data_dir)
            captured["data_store"] = data_store

        async def create_backup(self):
            return bs.BackupResult(
                success=True, backup_id="b-1", file_path="/tmp/b-1.gz",
                size_bytes=42, duration_seconds=0.5,
            )

    monkeypatch.setattr(bs, "BackupService", _FakeService)
    store = types.SimpleNamespace(base_dir="/tmp/siyi-test-data")
    out = _handler_for("system.auto_backup")(_Ctx(local_data_store=store), {})
    assert out["status"] == "completed", out
    assert out["backup_id"] == "b-1"
    assert captured["data_store"] is store
    assert "siyi-test-data" in captured["data_dir"]


def test_auto_backup_reports_failure_not_swallow(monkeypatch) -> None:
    import services.backup_service as bs

    class _FakeService:
        def __init__(self, *, data_dir, config=None, data_store=None):
            pass

        async def create_backup(self):
            return bs.BackupResult(success=False, error="disk full")

    monkeypatch.setattr(bs, "BackupService", _FakeService)
    store = types.SimpleNamespace(base_dir="/tmp/siyi-test-data")
    out = _handler_for("system.auto_backup")(_Ctx(local_data_store=store), {})
    assert out["status"] == "failed"
    assert "disk full" in out["error"]
