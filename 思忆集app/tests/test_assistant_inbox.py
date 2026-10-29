"""AssistantInboxService 单元测试 '"统一交付通道。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.inbox import (
    CHANNEL_BOTH,
    CHANNEL_CHAT,
    CHANNEL_FLOATING,
    CHANNEL_INBOX,
    CHANNEL_NOTIFICATION,
    CHANNEL_SUBTITLE,
    PRIORITY_HIGH,
    PRIORITY_NORMAL,
    PRIORITY_URGENT,
    SOURCE_ANOMALY,
    SOURCE_PROACTIVE,
    SOURCE_SCHEDULED,
    SOURCE_TASK_RESULT,
    AssistantInboxService,
)
from data.local_storage import LocalDataStore


def _make_service(tmp_path, *, notification_cb=None, chat_cb=None,
                  floating_cb=None, subtitle_cb=None,
                  user_profile_svc=None, max_items=500):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    return AssistantInboxService(
        data_store=data_store,
        notification_callback=notification_cb,
        chat_callback=chat_cb,
        floating_callback=floating_cb,
        subtitle_callback=subtitle_cb,
        user_profile_service=user_profile_svc,
        max_items=max_items,
    )


# 鈹€鈹€ deliver 鍩虹 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_deliver_creates_inbox_item(tmp_path):
    svc = _make_service(tmp_path)
    result = svc.deliver(title="测试鏍囬", content="测试鍐呭")
    assert result["title"] == "测试鏍囬"
    assert result["content"] == "测试鍐呭"
    assert result["channel"] == CHANNEL_INBOX
    assert result["status"] == "unread"
    assert result["item_id"]


def test_deliver_explicit_channel(tmp_path):
    svc = _make_service(tmp_path)
    result = svc.deliver(title="t", channel=CHANNEL_NOTIFICATION)
    assert result["channel"] == CHANNEL_NOTIFICATION


def test_deliver_auto_channel_high_priority(tmp_path):
    svc = _make_service(tmp_path)
    result = svc.deliver(title="t", priority=PRIORITY_HIGH)
    assert result["channel"] == CHANNEL_BOTH


def test_deliver_auto_channel_urgent(tmp_path):
    svc = _make_service(tmp_path)
    result = svc.deliver(title="t", priority=PRIORITY_URGENT)
    assert result["channel"] == CHANNEL_BOTH


def test_deliver_auto_channel_anomaly_source(tmp_path):
    svc = _make_service(tmp_path)
    result = svc.deliver(title="t", source=SOURCE_ANOMALY)
    assert result["channel"] == CHANNEL_BOTH


# 鈹€鈹€ 通知回调 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_deliver_fires_notification_callback_for_notification_channel(tmp_path):
    cb = MagicMock()
    svc = _make_service(tmp_path, notification_cb=cb)
    svc.deliver(title="t", channel=CHANNEL_NOTIFICATION)
    cb.assert_called_once()
    assert cb.call_args[0][0]["title"] == "t"


def test_deliver_fires_notification_callback_for_both(tmp_path):
    cb = MagicMock()
    svc = _make_service(tmp_path, notification_cb=cb)
    svc.deliver(title="t", channel=CHANNEL_BOTH)
    cb.assert_called_once()


def test_deliver_does_not_fire_notification_for_inbox_only(tmp_path):
    cb = MagicMock()
    svc = _make_service(tmp_path, notification_cb=cb)
    svc.deliver(title="t", channel=CHANNEL_INBOX)
    cb.assert_not_called()


def test_notification_callback_exception_is_swallowed(tmp_path):
    cb = MagicMock(side_effect=RuntimeError("boom"))
    svc = _make_service(tmp_path, notification_cb=cb)
    result = svc.deliver(title="t", channel=CHANNEL_NOTIFICATION)
    assert result["title"] == "t"  # 不应抛异带"


# 鈹€鈹€ deliver_task_result 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_deliver_task_result_success(tmp_path):
    svc = _make_service(tmp_path)
    result = svc.deliver_task_result(
        goal="查天姘",
        result={"summary": "北京晴天"},
        success=True,
        action_id="weather.lookup",
    )
    assert "✅" in result["title"]
    assert result["source"] == SOURCE_TASK_RESULT
    assert "task_result" in result["tags"]


def test_deliver_task_result_failure_has_high_priority(tmp_path):
    svc = _make_service(tmp_path)
    result = svc.deliver_task_result(
        goal="发邮件",
        result={"reason": "SMTP 超时"},
        success=False,
    )
    assert "❌" in result["title"]
    assert result["priority"] == PRIORITY_HIGH


# 鈹€鈹€ deliver_scheduled 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_deliver_scheduled_normal_goes_to_inbox(tmp_path):
    svc = _make_service(tmp_path)
    result = svc.deliver_scheduled(rule_name="日报", content="今天完成3椤")
    assert result["channel"] == CHANNEL_INBOX
    assert result["source"] == SOURCE_SCHEDULED


def test_deliver_scheduled_high_goes_to_both(tmp_path):
    svc = _make_service(tmp_path)
    result = svc.deliver_scheduled(
        rule_name="绱褎€ュ憡璀", content="磁盘婊", priority=PRIORITY_HIGH,
    )
    assert result["channel"] == CHANNEL_BOTH


# 鈹€鈹€ deliver_proactive 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_deliver_proactive_normal(tmp_path):
    svc = _make_service(tmp_path)
    result = svc.deliver_proactive(
        title="推荐闃呰", content="鏂拌鏂", category="high_value_match",
    )
    assert result["source"] == SOURCE_PROACTIVE
    assert "proactive" in result["tags"]


def test_deliver_proactive_blocked_by_profile_disabled(tmp_path):
    profile = MagicMock()
    profile.is_proactive_allowed.return_value = False
    svc = _make_service(tmp_path, user_profile_svc=profile)
    result = svc.deliver_proactive(
        title="推荐", content="x", category="high_value_match",
    )
    assert result.get("delivered") is False
    assert "proactive_disabled" in result.get("reason", "")


def test_deliver_proactive_blocked_by_category(tmp_path):
    profile = MagicMock()
    profile.is_proactive_allowed.return_value = True
    profile.get_proactive_categories.return_value = ["scheduled"]
    svc = _make_service(tmp_path, user_profile_svc=profile)
    result = svc.deliver_proactive(
        title="推荐", content="x", category="high_value_match",
    )
    assert result.get("delivered") is False
    assert "category_not_allowed" in result.get("reason", "")


# 鈹€鈹€ 安静鏃舵降级 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_quiet_hours_downgrades_notification_to_inbox(tmp_path):
    profile = MagicMock()
    profile.is_in_quiet_hours.return_value = True
    svc = _make_service(tmp_path, user_profile_svc=profile)
    result = svc.deliver(title="t", channel=CHANNEL_NOTIFICATION)
    assert result["channel"] == CHANNEL_INBOX


def test_quiet_hours_downgrades_both_to_inbox(tmp_path):
    profile = MagicMock()
    profile.is_in_quiet_hours.return_value = True
    svc = _make_service(tmp_path, user_profile_svc=profile)
    result = svc.deliver(title="t", channel=CHANNEL_BOTH)
    assert result["channel"] == CHANNEL_INBOX


def test_not_in_quiet_hours_keeps_channel(tmp_path):
    profile = MagicMock()
    profile.is_in_quiet_hours.return_value = False
    profile.get_preferred_delivery.return_value = "inbox"
    svc = _make_service(tmp_path, user_profile_svc=profile)
    result = svc.deliver(title="t", channel=CHANNEL_NOTIFICATION)
    assert result["channel"] == CHANNEL_NOTIFICATION


# 鈹€鈹€ list / mark / dismiss / count 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_list_items_empty(tmp_path):
    svc = _make_service(tmp_path)
    assert svc.list_items() == []


def test_list_items_returns_delivered(tmp_path):
    svc = _make_service(tmp_path)
    svc.deliver(title="a")
    svc.deliver(title="b")
    items = svc.list_items()
    assert len(items) == 2
    assert items[0]["title"] == "a"


def test_list_items_filter_by_status(tmp_path):
    svc = _make_service(tmp_path)
    r = svc.deliver(title="a")
    svc.deliver(title="b")
    svc.mark_read(r["item_id"])
    unread = svc.list_items(status="unread")
    assert len(unread) == 1
    assert unread[0]["title"] == "b"


def test_list_items_filter_by_source(tmp_path):
    svc = _make_service(tmp_path)
    svc.deliver(title="a", source=SOURCE_SCHEDULED)
    svc.deliver(title="b", source=SOURCE_TASK_RESULT)
    scheduled = svc.list_items(source=SOURCE_SCHEDULED)
    assert len(scheduled) == 1
    assert scheduled[0]["title"] == "a"


def test_list_items_limit(tmp_path):
    svc = _make_service(tmp_path)
    for i in range(10):
        svc.deliver(title=f"item-{i}")
    items = svc.list_items(limit=3)
    assert len(items) == 3


def test_mark_read(tmp_path):
    svc = _make_service(tmp_path)
    r = svc.deliver(title="a")
    assert svc.mark_read(r["item_id"]) is True
    items = svc.list_items()
    assert items[0]["status"] == "read"
    assert items[0]["read_at"] != ""


def test_mark_read_nonexistent_returns_false(tmp_path):
    svc = _make_service(tmp_path)
    assert svc.mark_read("nonexistent") is False


def test_dismiss(tmp_path):
    svc = _make_service(tmp_path)
    r = svc.deliver(title="a")
    assert svc.dismiss(r["item_id"]) is True
    items = svc.list_items()
    assert items[0]["status"] == "dismissed"
    assert items[0]["dismissed_at"] != ""


def test_unread_count(tmp_path):
    svc = _make_service(tmp_path)
    svc.deliver(title="a")
    svc.deliver(title="b")
    svc.deliver(title="c")
    assert svc.unread_count() == 3
    r = svc.deliver(title="d")
    svc.mark_read(r["item_id"])
    assert svc.unread_count() == 3


# 鈹€鈹€ max_items 容量限制 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_persist_respects_max_items(tmp_path):
    svc = _make_service(tmp_path, max_items=5)
    for i in range(10):
        svc.deliver(title=f"item-{i}")
    items = svc.list_items()
    assert len(items) <= 6  # 5 + 当前 batch 鍙兘鐣ュ


# 鈹€鈹€ user_profile preferred_delivery 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_resolve_channel_uses_profile_preference(tmp_path):
    profile = MagicMock()
    profile.is_in_quiet_hours.return_value = False
    profile.get_preferred_delivery.return_value = CHANNEL_NOTIFICATION
    svc = _make_service(tmp_path, user_profile_svc=profile)
    result = svc.deliver(title="t", priority=PRIORITY_NORMAL)
    assert result["channel"] == CHANNEL_NOTIFICATION


# 鈹€鈹€ 鏂板通道: chat_output 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_deliver_chat_channel_fires_callback(tmp_path):
    cb = MagicMock()
    svc = _make_service(tmp_path, chat_cb=cb)
    result = svc.deliver(title="对话鍥炲", channel=CHANNEL_CHAT)
    assert result["channel"] == CHANNEL_CHAT
    cb.assert_called_once()


def test_deliver_chat_channel_persists_item(tmp_path):
    svc = _make_service(tmp_path)
    svc.deliver(title="对话鍥炲", channel=CHANNEL_CHAT)
    assert svc.unread_count() == 1


# 鈹€鈹€ 鏂板通道: floating_output 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_deliver_floating_channel_fires_callback(tmp_path):
    cb = MagicMock()
    svc = _make_service(tmp_path, floating_cb=cb)
    result = svc.deliver(title="气泡", channel=CHANNEL_FLOATING)
    assert result["channel"] == CHANNEL_FLOATING
    cb.assert_called_once()


def test_deliver_floating_channel_persists_item(tmp_path):
    svc = _make_service(tmp_path)
    svc.deliver(title="气泡", channel=CHANNEL_FLOATING)
    assert svc.unread_count() == 1


# 鈹€鈹€ 鏂板通道: subtitle_output 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_deliver_subtitle_channel_fires_callback(tmp_path):
    cb = MagicMock()
    svc = _make_service(tmp_path, subtitle_cb=cb)
    result = svc.deliver(title="字幕提示", channel=CHANNEL_SUBTITLE)
    assert result["channel"] == CHANNEL_SUBTITLE
    cb.assert_called_once()


def test_deliver_subtitle_channel_does_not_persist(tmp_path):
    svc = _make_service(tmp_path)
    svc.deliver(title="字幕提示", channel=CHANNEL_SUBTITLE)
    assert svc.unread_count() == 0  # 字幕不入收件绠"


# 鈹€鈹€ 安静鏃舵降级鏂伴€氶亾 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_quiet_hours_downgrades_floating_to_inbox(tmp_path):
    profile = MagicMock()
    profile.is_in_quiet_hours.return_value = True
    svc = _make_service(tmp_path, user_profile_svc=profile)
    result = svc.deliver(title="t", channel=CHANNEL_FLOATING)
    assert result["channel"] == CHANNEL_INBOX


def test_quiet_hours_downgrades_subtitle_to_inbox(tmp_path):
    profile = MagicMock()
    profile.is_in_quiet_hours.return_value = True
    svc = _make_service(tmp_path, user_profile_svc=profile)
    result = svc.deliver(title="t", channel=CHANNEL_SUBTITLE)
    assert result["channel"] == CHANNEL_INBOX


# 鈹€鈹€ profile preference 鍙€夋柊通道 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

# 鈹€鈹€ set_notification_callback 晚绑瀹"鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_set_notification_callback_late_binding(tmp_path):
    cb = MagicMock()
    svc = _make_service(tmp_path)  # 创建时不浼"callback
    svc.set_notification_callback(cb)  # 晚绑瀹"
    svc.deliver(title="t", channel=CHANNEL_NOTIFICATION)
    cb.assert_called_once()


def test_set_notification_callback_overwrites_previous(tmp_path):
    cb1 = MagicMock()
    cb2 = MagicMock()
    svc = _make_service(tmp_path, notification_cb=cb1)
    svc.set_notification_callback(cb2)  # 覆盖
    svc.deliver(title="t", channel=CHANNEL_NOTIFICATION)
    cb1.assert_not_called()
    cb2.assert_called_once()


def test_set_notification_callback_can_clear(tmp_path):
    svc = _make_service(tmp_path)
    svc.set_notification_callback(None)  # 不触发异带"
    svc.deliver(title="t", channel=CHANNEL_NOTIFICATION)


def test_resolve_channel_supports_chat_preference(tmp_path):
    profile = MagicMock()
    profile.is_in_quiet_hours.return_value = False
    profile.get_preferred_delivery.return_value = CHANNEL_CHAT
    svc = _make_service(tmp_path, user_profile_svc=profile)
    result = svc.deliver(title="t", priority=PRIORITY_NORMAL)
    assert result["channel"] == CHANNEL_CHAT
