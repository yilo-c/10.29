from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from cross_device.capability_router import (
    DEVICE_DESKTOP,
    DEVICE_ANDROID,
    CrossDeviceCapabilityRouter,
)


def _make_router():
    return CrossDeviceCapabilityRouter()


def test_desktop_can_handle_workflow_edit():
    router = _make_router()
    router.set_current_device(DEVICE_DESKTOP)
    result = router.route("workflow_edit")
    assert result.can_handle_current is True
    assert result.preferred_device == DEVICE_DESKTOP


def test_android_cannot_handle_docker():
    router = _make_router()
    router.set_current_device(DEVICE_ANDROID)
    result = router.route("docker")
    assert result.can_handle_current is False
    assert result.preferred_device == DEVICE_DESKTOP
    assert "桌面端" in result.hint


def test_desktop_cannot_handle_camera():
    router = _make_router()
    router.set_current_device(DEVICE_DESKTOP)
    result = router.route("camera")
    assert result.can_handle_current is False
    assert result.preferred_device == DEVICE_ANDROID
    assert "手机端" in result.hint


def test_both_devices_can_chat():
    router = _make_router()
    router.set_current_device(DEVICE_DESKTOP)
    result = router.route("chat")
    assert result.can_handle_current is True
    assert result.preferred_device is None  # both can handle


def test_get_device_capabilities():
    router = _make_router()
    desktop_caps = router.get_device_capabilities(DEVICE_DESKTOP)
    assert "docker" in desktop_caps[DEVICE_DESKTOP]
    assert "chat" in desktop_caps[DEVICE_DESKTOP]

    android_caps = router.get_device_capabilities(DEVICE_ANDROID)
    assert "camera" in android_caps[DEVICE_ANDROID]
    assert "chat" in android_caps[DEVICE_ANDROID]


def test_all_capabilities():
    router = _make_router()
    caps = router.get_device_capabilities()
    assert DEVICE_DESKTOP in caps
    assert DEVICE_ANDROID in caps


def test_capability_details():
    router = _make_router()
    details = router.get_capability_details("workflow_edit")
    assert details["capability"] == "workflow_edit"
    assert details["preferred_device"] == DEVICE_DESKTOP
    assert DEVICE_DESKTOP in details["available_on"]


def test_set_and_get_current():
    router = _make_router()
    router.set_current_device(DEVICE_ANDROID)
    snapshot = router.get_status_snapshot()
    assert snapshot["current_device"] == DEVICE_ANDROID


def test_status_snapshot():
    router = _make_router()
    snapshot = router.get_status_snapshot()
    assert "current_device" in snapshot
    assert "current_capabilities" in snapshot
