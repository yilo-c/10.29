"""自动简报开关持久化修复（2026-10-16 用户拍板①）。

历史 bug：InfoScheduler.configure() 只改内存 + restart，前端开关
（info.auto_config → configure）跨后端重启即丢失——恢复成启动默认
enabled=True。修复：configure 落盘 info_scheduler_enabled/interval/topics
三键（与 headless_main 启动恢复同键名），settings 注入走 set_settings。

另清理旧死键：settings 里的 auto_info_enabled/auto_info_interval 是旧版本
遗留（现行代码读 info_scheduler_*，见 10-15/16 日志误读教训）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


class _FakeSettings:
    """最小 settings_repository 替身（get/set dict 语义）。"""

    def __init__(self) -> None:
        self.store: dict = {}

    def get_setting(self, key: str, default=None):  # noqa: ANN001
        return self.store.get(key, default)

    def set_setting(self, key: str, value):  # noqa: ANN001
        self.store[key] = value


@pytest.fixture(autouse=True)
def reset_scheduler():
    from backend import info_scheduler

    info_scheduler.cancel()
    sched = info_scheduler._scheduler
    sched._enabled = False
    sched._collector = None
    sched._task = None
    sched._settings = None
    yield
    info_scheduler.cancel()
    sched._enabled = False
    sched._collector = None
    sched._task = None
    sched._settings = None


def test_configure_persists_enabled_true():
    from backend import info_scheduler

    async def _run():
        fake = _FakeSettings()
        info_scheduler.set_settings(fake)
        info_scheduler.set_collector(object())
        info_scheduler.configure(enabled=True, interval=600, topics=["AI"])
        await asyncio.sleep(0)
        assert fake.store["info_scheduler_enabled"] is True
        assert fake.store["info_scheduler_interval"] == 600
        assert fake.store["info_scheduler_topics"] == ["AI"]

    asyncio.run(_run())


def test_configure_persists_disabled():
    from backend import info_scheduler

    async def _run():
        fake = _FakeSettings()
        info_scheduler.set_settings(fake)
        info_scheduler.set_collector(object())
        info_scheduler.configure(enabled=False, interval=3600)
        await asyncio.sleep(0)
        assert fake.store["info_scheduler_enabled"] is False

    asyncio.run(_run())


def test_min_interval_clamped_before_persist():
    from backend import info_scheduler

    async def _run():
        fake = _FakeSettings()
        info_scheduler.set_settings(fake)
        info_scheduler.set_collector(object())
        info_scheduler.configure(enabled=True, interval=10)  # 低于下限 300
        await asyncio.sleep(0)
        assert fake.store["info_scheduler_interval"] == 300

    asyncio.run(_run())


def test_no_settings_injection_no_crash():
    """未注入 settings（单测/旧调用方）时 configure 不崩、仅不落盘。"""
    from backend import info_scheduler

    async def _run():
        info_scheduler.set_collector(object())
        info_scheduler.configure(enabled=False, interval=300)
        await asyncio.sleep(0)
        assert info_scheduler.is_enabled() is False

    asyncio.run(_run())


def test_persist_failure_does_not_break_runtime():
    """落盘异常被吞（warning），运行时状态照常生效。"""
    from backend import info_scheduler

    class _BoomSettings:
        def set_setting(self, key, value):  # noqa: ANN001
            raise RuntimeError("disk full")

    async def _run():
        info_scheduler.set_settings(_BoomSettings())
        info_scheduler.set_collector(object())
        info_scheduler.configure(enabled=False, interval=300)
        await asyncio.sleep(0)
        assert info_scheduler.is_enabled() is False

    asyncio.run(_run())
