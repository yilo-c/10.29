"""P1-3: info_scheduler 必须在 backend 启动时被实际启用。

历史 bug：headless_main.py 只调 `info_scheduler.set_collector(...)` +
`info_scheduler.restart()`，但 `_enabled` 默认 False，restart() 在
`_enabled=False` 时不创建任务 → 后台资讯采集永不工作。

修复后：headless_main 在启动时读 settings → 调 `configure(enabled=True, ...)`，
configure 内部会设置 `_enabled` 并调用 restart()。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


@pytest.fixture(autouse=True)
def reset_scheduler():
    """info_scheduler 是模块级单例，每个测试间需复位。"""
    from backend import info_scheduler
    info_scheduler.cancel()
    sched = info_scheduler._scheduler
    sched._enabled = False
    sched._collector = None
    sched._task = None
    yield
    info_scheduler.cancel()
    sched._enabled = False
    sched._collector = None
    sched._task = None


def test_configure_enabled_true_actually_starts_loop():
    """configure(enabled=True) 之后 _scheduler_task 必须被创建。"""
    from backend import info_scheduler

    async def _run():
        info_scheduler.set_collector(object())
        info_scheduler.configure(enabled=True, interval=300)
        # 让事件循环跑一轮
        await asyncio.sleep(0)
        assert info_scheduler.is_enabled() is True
        assert info_scheduler._scheduler._task is not None
        assert not info_scheduler._scheduler._task.done()

    asyncio.run(_run())


def test_restart_alone_without_configure_does_nothing():
    """回归：仅 restart() 不调 configure 时，调度器不启动 — 这是历史 bug 的本因。"""
    from backend import info_scheduler

    async def _run():
        info_scheduler.set_collector(object())
        info_scheduler.restart()
        await asyncio.sleep(0)
        # _enabled 默认 False → restart() 不创建任务
        assert info_scheduler._scheduler._task is None
        assert info_scheduler.is_enabled() is False

    asyncio.run(_run())


def test_configure_enabled_false_cancels_existing_task():
    """先开后关：configure(enabled=False) 必须取消任务。"""
    from backend import info_scheduler

    async def _run():
        info_scheduler.set_collector(object())
        info_scheduler.configure(enabled=True, interval=300)
        await asyncio.sleep(0)
        assert info_scheduler._scheduler._task is not None
        first_task = info_scheduler._scheduler._task

        info_scheduler.configure(enabled=False)
        await asyncio.sleep(0)
        assert info_scheduler._scheduler._task is None
        assert first_task.cancelled() or first_task.done()

    asyncio.run(_run())


def test_configure_clamps_interval_to_minimum():
    """interval < 300 应被夹到 300 防止过频抓取。"""
    from backend import info_scheduler

    async def _run():
        info_scheduler.set_collector(object())
        info_scheduler.configure(enabled=True, interval=10)
        assert info_scheduler.get_config()["interval"] == 300

    asyncio.run(_run())


def test_get_config_reflects_state():
    from backend import info_scheduler

    async def _run():
        info_scheduler.set_collector(object())
        info_scheduler.configure(enabled=True, interval=600, topics=["python", "rust"])
        cfg = info_scheduler.get_config()
        assert cfg["enabled"] is True
        assert cfg["interval"] == 600
        assert cfg["topics"] == ["python", "rust"]

    asyncio.run(_run())


def test_headless_main_calls_configure_not_just_restart():
    """grep headless_main.py 源码确认调用 configure() 而不仅是 restart()。"""
    src = (APP_ROOT / "backend" / "headless_main.py").read_text(encoding="utf-8")
    assert "info_scheduler.configure(" in src, (
        "P1-3 回归：headless_main.py 必须调用 info_scheduler.configure() 才能"
        "实际启动调度器；仅调 restart() 时 _enabled=False 不会创建任务。"
    )
