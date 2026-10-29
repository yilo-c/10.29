"""DeliveryRoute 单元测试 — 通道路由与字幕文案构建。"""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.delivery_route import (
    CANONICAL_CHANNEL_ALIASES,
    DELIVERY_CHANNEL_LABELS,
    CHANNEL_INBOX,
    CHANNEL_NOTIFICATION,
    CHANNEL_BOTH,
    CHANNEL_CHAT,
    CHANNEL_FLOATING,
    CHANNEL_SUBTITLE,
    build_subtitle_tone,
    build_task_progress_subtitle,
    is_visible_channel,
    resolve_channel,
)


# ── resolve_channel ─────────────────────────────────────────

def test_resolve_channel_accepts_full_name():
    assert resolve_channel("inbox") == CHANNEL_INBOX
    assert resolve_channel("notification") == CHANNEL_NOTIFICATION
    assert resolve_channel("subtitle") == CHANNEL_SUBTITLE
    assert resolve_channel("floating") == CHANNEL_FLOATING
    assert resolve_channel("chat") == CHANNEL_CHAT
    assert resolve_channel("both") == CHANNEL_BOTH


def test_resolve_channel_accepts_underscore_suffix():
    assert resolve_channel("chat_output") == CHANNEL_CHAT
    assert resolve_channel("subtitle_output") == CHANNEL_SUBTITLE
    assert resolve_channel("floating_output") == CHANNEL_FLOATING


def test_resolve_channel_unknown_defaults_to_inbox():
    assert resolve_channel("unknown_channel") == CHANNEL_INBOX
    assert resolve_channel("") == CHANNEL_INBOX
    assert resolve_channel(None) == CHANNEL_INBOX


def test_resolve_channel_case_insensitive():
    assert resolve_channel("INBOX") == CHANNEL_INBOX
    assert resolve_channel("Subtitle") == CHANNEL_SUBTITLE
    assert resolve_channel("Chat_Output") == CHANNEL_CHAT


# ── is_visible_channel ──────────────────────────────────────

def test_visible_channels():
    assert is_visible_channel(CHANNEL_NOTIFICATION) is True
    assert is_visible_channel(CHANNEL_BOTH) is True
    assert is_visible_channel(CHANNEL_FLOATING) is True
    assert is_visible_channel(CHANNEL_SUBTITLE) is True
    assert is_visible_channel(CHANNEL_INBOX) is False
    assert is_visible_channel(CHANNEL_CHAT) is False


# ── build_task_progress_subtitle ────────────────────────────

def test_subtitle_all_success_with_steps():
    text = build_task_progress_subtitle(goal="查天气", total_steps=3, ok_steps=3)
    assert "✅" in text
    assert "查天气" in text
    assert "3/3" in text


def test_subtitle_all_success_zero_steps():
    text = build_task_progress_subtitle(goal="已读文件")
    assert "✅" in text
    assert "已读文件" in text


def test_subtitle_partial_failure():
    text = build_task_progress_subtitle(goal="发邮件", total_steps=3, ok_steps=2, failed_steps=1)
    assert "⚠️" in text
    assert "2/3" in text
    assert "1 步失败" in text


def test_subtitle_with_blocked():
    text = build_task_progress_subtitle(goal="执行", total_steps=4, ok_steps=2, failed_steps=1, blocked_steps=1)
    assert "⚠️" in text
    assert "2/4" in text
    assert "1 步失败" in text
    assert "1 步被拦截" in text


def test_subtitle_with_delivery_channel():
    text = build_task_progress_subtitle(goal="备份", total_steps=1, ok_steps=1, delivery_channel="inbox")
    # 成功时不追加通道信息
    assert "✅" in text


def test_subtitle_with_delivery_channel_partial_failure():
    text = build_task_progress_subtitle(
        goal="备份", total_steps=2, ok_steps=1, failed_steps=1,
        delivery_channel="inbox",
    )
    assert "结果进入收件箱" in text


def test_subtitle_empty_goal():
    text = build_task_progress_subtitle(goal="")
    assert "✅" in text
    assert "任务" in text


# ── build_subtitle_tone ─────────────────────────────────────

def test_tone_success():
    assert build_subtitle_tone({"failed": 0, "blocked": 0, "degraded": 0}) == "success"


def test_tone_alert_on_failure():
    assert build_subtitle_tone({"failed": 1, "blocked": 0, "degraded": 0}) == "alert"


def test_tone_alert_on_blocked():
    assert build_subtitle_tone({"failed": 0, "blocked": 2, "degraded": 0}) == "alert"


def test_tone_thinking_on_degraded():
    assert build_subtitle_tone({"failed": 0, "blocked": 0, "degraded": 1}) == "thinking"


# ── DELIVERY_CHANNEL_LABELS ─────────────────────────────────

def test_all_channels_have_labels():
    for ch in (CHANNEL_INBOX, CHANNEL_NOTIFICATION, CHANNEL_BOTH,
               CHANNEL_CHAT, CHANNEL_FLOATING, CHANNEL_SUBTITLE):
        assert ch in DELIVERY_CHANNEL_LABELS
        assert DELIVERY_CHANNEL_LABELS[ch]


def test_all_aliases_resolve_to_known():
    for alias, resolved in CANONICAL_CHANNEL_ALIASES.items():
        assert resolved in DELIVERY_CHANNEL_LABELS
