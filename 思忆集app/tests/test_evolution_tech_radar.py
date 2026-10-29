from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from evolution.tech_radar import (
    LEVEL_CONFIG,
    LEVEL_FEATURE,
    LEVEL_KNOWLEDGE,
    TechRadar,
    RadarFinding,
)


@pytest.fixture(autouse=True)
def _offline_radar(monkeypatch):
    """将 _fetch_latest_version 打桩为确定性版本。

    原测试会经 ``scan()`` 真实访问 pypi.org / api.github.com（urllib，5s 超时×
    每 target×每次 scan），在慢/受限网络下每个用例拖到数十秒且结果抖
    （两次 scan 版本不一致导致 test_scan_no_duplicate_on_no_change 偶发失败），
    进而使全量基线超过 45 分钟。打桩后单测纯本地、快速且确定。
    """
    monkeypatch.setattr(
        TechRadar,
        "_fetch_latest_version",
        lambda self, name, source, url="": "9.9.9",
    )


def _make_radar():
    return TechRadar()


def test_scan_detects_changes():
    radar = _make_radar()
    # 第一次扫描：应该有发现（缓存空）
    findings = radar.scan()
    assert len(findings) > 0
    assert findings[0].name is not None


def test_scan_no_duplicate_on_no_change():
    radar = _make_radar()
    radar.scan()  # 首次填充缓存
    findings = radar.scan()  # 再次扫描，版本无变化
    assert len(findings) == 0


def test_set_version_then_scan():
    radar = _make_radar()
    radar.scan()
    # 手动设置一个低版本，触发新发现
    before = len(radar.get_all_findings())
    radar.set_version("chromadb", "0.4.0")
    findings = radar.scan("chromadb")
    assert len(findings) >= 1
    assert len(radar.get_all_findings()) > before


def test_finding_has_correct_fields():
    radar = _make_radar()
    findings = radar.scan()
    if findings:
        f = findings[0]
        assert isinstance(f.name, str)
        assert isinstance(f.current_version, str)
        assert isinstance(f.latest_version, str)
        assert f.source in ("pypi", "github_release", "model_provider")


def test_major_version_detection():
    radar = _make_radar()
    assert radar._is_major_version_change("0.4.0", "0.5.0") is False  # same major
    assert radar._is_major_version_change("1.0.0", "2.0.0") is True   # major bump
    assert radar._is_major_version_change("v1", "v3") is True         # major bump with v prefix


def test_get_all_findings():
    radar = _make_radar()
    findings = radar.scan()
    all_f = radar.get_all_findings()
    assert len(all_f) >= len(findings)


def test_status_snapshot():
    radar = _make_radar()
    radar.scan()
    snapshot = radar.get_status_snapshot()
    assert snapshot["targets_count"] > 0
    assert snapshot["last_scan"] is not None
