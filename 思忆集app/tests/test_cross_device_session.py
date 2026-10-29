from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from cross_device.session import (
    DEVICE_DESKTOP,
    DEVICE_ANDROID,
    CrossDeviceSessionManager,
)


def _make_manager():
    return CrossDeviceSessionManager()


def test_save_and_restore_session():
    mgr = _make_manager()
    mgr.save_session(
        session_id="session-1",
        device_type=DEVICE_DESKTOP,
        topic="讨论技术方案",
        last_message="我觉得可以用FastAPI",
        message_count=5,
    )
    restored = mgr.restore_session("session-1")
    assert restored is not None
    assert restored.topic == "讨论技术方案"
    assert restored.last_message == "我觉得可以用FastAPI"
    assert restored.message_count == 5


def test_detect_device_switch():
    mgr = _make_manager()
    mgr.save_session(
        session_id="session-1",
        device_type=DEVICE_DESKTOP,
        topic="技术方案讨论",
    )
    # 同一设备不触发切换
    assert mgr.detect_device_switch(DEVICE_DESKTOP) is None

    # 切换设备应检测到
    switch_info = mgr.detect_device_switch(DEVICE_ANDROID)
    assert switch_info is not None
    assert switch_info["switch_detected"] is True
    assert switch_info["from_device"] == DEVICE_DESKTOP
    assert switch_info["to_device"] == DEVICE_ANDROID
    assert "要继续吗" in switch_info["prompt"]


def test_add_pending_action():
    mgr = _make_manager()
    mgr.save_session(session_id="session-1", device_type=DEVICE_DESKTOP, topic="审批测试")
    mgr.add_pending_action("session-1", {"action": "approve_delete", "item": "file.txt"})
    restored = mgr.restore_session("session-1")
    assert restored is not None
    assert len(restored.pending_actions) == 1
    assert restored.pending_actions[0]["action"] == "approve_delete"


def test_close_session():
    mgr = _make_manager()
    mgr.save_session(session_id="session-1", device_type=DEVICE_DESKTOP, topic="测试")
    mgr.close_session("session-1")
    restored = mgr.restore_session("session-1")
    assert restored is None  # closed sessions not in active


def test_no_switch_on_first_call():
    mgr = _make_manager()
    result = mgr.detect_device_switch(DEVICE_ANDROID)
    assert result is None  # first call, no previous device


def test_session_context_includes_topic():
    mgr = _make_manager()
    mgr.save_session(session_id="s1", device_type=DEVICE_DESKTOP, topic="我的测试话题")
    restored = mgr.restore_session("s1")
    assert restored is not None
    assert restored.topic == "我的测试话题"
    assert restored.device_type == DEVICE_DESKTOP


def test_multiple_sessions():
    mgr = _make_manager()
    mgr.save_session(session_id="s1", device_type=DEVICE_DESKTOP, topic="话题1")
    mgr.save_session(session_id="s2", device_type=DEVICE_ANDROID, topic="话题2")
    assert mgr._active_sessions["s1"].topic == "话题1"
    assert mgr._active_sessions["s2"].topic == "话题2"


def test_status_snapshot():
    mgr = _make_manager()
    mgr.save_session(session_id="s1", device_type=DEVICE_DESKTOP, topic="测试")
    snapshot = mgr.get_status_snapshot()
    assert snapshot["active_sessions"] >= 1
    assert snapshot["last_active_device"] == DEVICE_DESKTOP
