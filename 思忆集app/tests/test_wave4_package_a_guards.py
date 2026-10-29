"""波次④ 包 A 契约守卫（守则#7：先红后绿）。

覆盖蓝本 §3-包A "需新增断言" 1-4：
  #1 info_scheduler 受 cloud_sync_enabled 门控（注入 settings + _cloud_sync_crypto 判定）
  #2 backend.sync.cloud_push 死岛删除守卫
  #3 web_bridge SyncService.trigger_sync 假成功反转
  #4 cross_device.session 幻影 orchestrator 调用清除（显式本地 only）
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


# ── 公共 fake ──────────────────────────────────────────────────


class _FakeSettingsRepo:
    """最小 settings_repository：只认 cloud_sync_enabled 开关。"""

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled
        self.read_keys: list[str] = []

    def get_setting(self, key, default=None):
        self.read_keys.append(key)
        if key == "cloud_sync_enabled":
            return self._enabled
        return default


# ── #1a info_scheduler 注入 settings（蓝本 §2-③）────────────────


async def test_info_scheduler_injects_settings(monkeypatch):
    from backend import info_scheduler as sched_mod

    captured: dict = {}

    async def _fake_push(overview, section_briefs, full_brief, source_count, settings=None):
        captured["settings"] = settings
        captured["source_count"] = source_count
        return True

    monkeypatch.setattr(
        "sync.cloud_sync_client.push_info_brief_standalone", _fake_push
    )

    sched = sched_mod.InfoScheduler()
    repo = _FakeSettingsRepo(False)
    sched.set_context(SimpleNamespace(settings_repository=repo))

    await sched._push_brief_to_cloud(
        {"overview": "o", "full_brief": "f", "section_briefs": {}, "items": [1, 2]}
    )

    # settings 必须被注入（当前实现未传 → 先红）
    assert captured.get("settings") is repo
    assert captured.get("source_count") == 2


# ── #1b push_info_brief_standalone 云同步总开关门控 ──────────────


async def test_push_info_brief_standalone_respects_cloud_sync_enabled(monkeypatch):
    import sync._cloud_sync_crypto as crypto

    monkeypatch.setenv("SIYI_WORKER_URL", "https://worker.example")
    monkeypatch.setenv("SIYI_SYNC_TOKEN", "tok")
    monkeypatch.setenv("SIYI_REGISTER_SECRET", "sec")

    net: dict = {}

    async def _record_network(*args, **kwargs):
        net["called"] = True
        return True

    monkeypatch.setattr(crypto, "_push_standalone", _record_network)

    # 关闭云同步 → 即使 env 三键齐备也必须不推、返回 False（当前无门控 → 先红）
    res_off = await crypto.push_info_brief_standalone(
        "o", {}, "f", 0, settings=_FakeSettingsRepo(False)
    )
    assert res_off is False
    assert "called" not in net

    # 开启云同步 → 正常推送（正向对照，防过度收紧）
    res_on = await crypto.push_info_brief_standalone(
        "o", {}, "f", 0, settings=_FakeSettingsRepo(True)
    )
    assert res_on is True
    assert net.get("called") is True


# ── #2 死岛删除守卫 ────────────────────────────────────────────


def test_cloud_push_module_deleted():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("backend.sync.cloud_push")


def test_delta_sync_island_deleted():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("sync.delta_sync")


def test_sync_services_island_deleted():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("sync.services")


# ── #3 web_bridge 假成功反转 ───────────────────────────────────


class _FakeOrchestrator:
    def __init__(self, raise_exc: bool = False) -> None:
        self.raise_exc = raise_exc
        self.sync_all_calls: list = []

    def sync_all(self, resources=None, limit: int = 100):  # noqa: ARG002
        self.sync_all_calls.append(resources)
        if self.raise_exc:
            raise RuntimeError("sync boom")
        return {"provider": "automation_hub", "resources": []}

    def get_sync_summary(self):
        return {"last_sync_time": "2026-01-01T00:00:00Z", "total_pending": 0}


def _make_sync_service(orchestrator):
    from desktop.web_bridge import SyncService

    svc = SyncService()
    svc._orchestrator = orchestrator
    return svc


def test_web_bridge_trigger_sync_error_not_fake_success():
    svc = _make_sync_service(_FakeOrchestrator(raise_exc=True))
    result = svc.trigger_sync()
    # orchestrator 抛错时不得返回 started:True（假成功反转，当前恒 True → 先红）
    assert result.get("started") is False


def test_web_bridge_trigger_sync_no_orchestrator_not_fake_success():
    svc = _make_sync_service(None)
    result = svc.trigger_sync()
    assert result.get("started") is False


def test_web_bridge_trigger_sync_real_call_on_success():
    orch = _FakeOrchestrator(raise_exc=False)
    svc = _make_sync_service(orch)
    result = svc.trigger_sync(domain="all")
    assert result.get("started") is True
    assert orch.sync_all_calls == [None]  # 真调 sync_all()


def test_web_bridge_get_status_reads_last_sync_time_key():
    svc = _make_sync_service(_FakeOrchestrator())
    status = svc.get_status()
    # 真源键是 last_sync_time（当前读 last_sync → 恒 None → 先红）
    assert status.get("last_sync") == "2026-01-01T00:00:00Z"


# ── #4 cross_device 幻影 orchestrator 调用清除 ─────────────────


class _PhantomOrchestrator:
    """宽松 mock：记录被调，验证不再被幻影调用。"""

    def __init__(self) -> None:
        self.sync_resource_calls: list = []
        self.fetch_resource_calls: list = []

    def sync_resource(self, **kwargs):
        self.sync_resource_calls.append(kwargs)

    def fetch_resource(self, **kwargs):
        self.fetch_resource_calls.append(kwargs)


class _FakeDataStore:
    def __init__(self) -> None:
        self._data: dict = {}

    def load_json(self, key, default=None):
        if key in self._data:
            return self._data[key]
        return default if default is not None else {}

    def save_json(self, key, value):
        self._data[key] = value


def test_cross_device_session_no_phantom_orchestrator_call():
    from cross_device.session import (
        DEVICE_DESKTOP,
        CrossDeviceSessionManager,
    )

    orch = _PhantomOrchestrator()
    ds = _FakeDataStore()
    mgr = CrossDeviceSessionManager(data_store=ds, sync_orchestrator=orch)

    mgr.save_session(session_id="s1", device_type=DEVICE_DESKTOP, topic="讨论方案")

    # 不得调用签名不匹配的 orchestrator.sync_resource（幻影上云）
    assert orch.sync_resource_calls == []
    # 会话应落到本地 data_store
    assert "s1" in ds._data.get("cross_device_sessions", {})

    loaded = mgr._load_from_hub("s1")
    assert orch.fetch_resource_calls == []  # 不得调用不存在的 fetch_resource
    assert loaded is not None
    assert loaded.topic == "讨论方案"
