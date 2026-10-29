"""行为级测试：sync.cloud_sync_client 的纯函数（加解密 / 冲突检测 / 时间归一）。

不依赖网络或数据库。重点覆盖端到端加密契约（v4 同组跨设备可互解、跨组/错 token 必失败）
与同步边界判据（_detect_conflict 真冲突、_normalize_ts 多格式归一），为后续拆分大文件提供安全网。
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

# 加密内部符号一律从真源导入：sync.cloud_sync_client 的 re-export 清单不完整
# （_detect_conflict 未 re-export → 本文件曾整体 ImportError，2026-08-29 修复）。
from sync._cloud_sync_crypto import (
    CloudSyncCryptoError,
    _detect_conflict,
    _normalize_ts,
)
from sync.cloud_sync_client import decrypt_data, encrypt_data

pytestmark = pytest.mark.unit

TOKEN = "unit-test-token"


# ── 加解密往返与跨设备契约（P0.1 v4 group salt）─────────────────

def test_encrypt_decrypt_roundtrip_v4():
    ct = encrypt_data("hello 思忆集 ☁", TOKEN, "device-A", "group-1")
    assert decrypt_data(ct, TOKEN, "device-A", "group-1") == "hello 思忆集 ☁"


def test_v4_cross_device_same_group_decryptable():
    """同组不同设备须可互解：v4 的 salt 仅由 sync_group_id 派生，与 device_id 无关。"""
    ct = encrypt_data("shared note across devices", TOKEN, "device-A", "group-1")
    assert decrypt_data(ct, TOKEN, "device-B", "group-1") == "shared note across devices"


def test_v4_cross_group_raises():
    """跨组设备 salt 不同 → 派生密钥不同 → 必须解密失败。"""
    ct = encrypt_data("secret", TOKEN, "device-A", "group-1")
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data(ct, TOKEN, "device-A", "group-2")


def test_v4_wrong_token_raises():
    ct = encrypt_data("secret", "token-1", "device-A", "group-1")
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data(ct, "token-2", "device-A", "group-1")


def test_v3_fallback_roundtrip():
    """无 sync_group_id → 降级 v3 per-device salt，同设备可往返。"""
    ct = encrypt_data("fallback payload", TOKEN, "device-A")
    assert decrypt_data(ct, TOKEN, "device-A") == "fallback payload"


def test_v3_different_device_raises():
    ct = encrypt_data("fallback payload", TOKEN, "device-A")
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data(ct, TOKEN, "device-B")


def test_decrypt_empty_raises():
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data("", TOKEN, "device-A")


def test_decrypt_garbage_raises():
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data("not-a-valid-ciphertext!!", TOKEN, "device-A")


# ── 冲突检测（真冲突 = 本地与远端自上次同步后都被改过）──────────

def test_detect_conflict_true_when_both_modified():
    local = {
        "updated_at": "2026-06-22T12:00:00+00:00",
        "last_synced_at": "2026-06-22T10:00:00+00:00",
    }
    remote_time = _normalize_ts("2026-06-22T11:00:00+00:00")
    assert _detect_conflict(local, remote_time) is True


def test_detect_conflict_never_synced_false():
    """从未同步过（last_synced 为空）→ 无法判定真冲突，回退纯 LWW。"""
    local = {
        "updated_at": "2026-06-22T12:00:00+00:00",
        "last_synced_at": None,
    }
    remote_time = _normalize_ts("2026-06-22T11:00:00+00:00")
    assert _detect_conflict(local, remote_time) is False


def test_detect_conflict_local_clean_false():
    """本地自上次同步后未改动 → 非真冲突。"""
    local = {
        "updated_at": "2026-06-22T09:00:00+00:00",
        "last_synced_at": "2026-06-22T10:00:00+00:00",
    }
    remote_time = _normalize_ts("2026-06-22T11:00:00+00:00")
    assert _detect_conflict(local, remote_time) is False


def test_detect_conflict_remote_old_false():
    """远端自上次同步后未改动 → 非真冲突。"""
    local = {
        "updated_at": "2026-06-22T12:00:00+00:00",
        "last_synced_at": "2026-06-22T10:00:00+00:00",
    }
    remote_time = _normalize_ts("2026-06-22T09:30:00+00:00")
    assert _detect_conflict(local, remote_time) is False


def test_detect_conflict_empty_local_false():
    assert _detect_conflict({}, 12345.0) is False


# ── 时间归一（LWW 比较需统一 epoch 秒）────────────────────────

def test_normalize_ts_none_and_blank():
    assert _normalize_ts(None) == 0.0
    assert _normalize_ts("") == 0.0
    assert _normalize_ts("   ") == 0.0


def test_normalize_ts_seconds_passthrough():
    assert _normalize_ts(1719064800) == 1719064800.0


def test_normalize_ts_millis_divided():
    """手机端 Date.now() 毫秒级 → 除以 1000。"""
    assert _normalize_ts(1719064800000) == 1719064800.0


def test_normalize_ts_iso_with_offset():
    expected = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    assert _normalize_ts("2026-06-22T12:00:00+00:00") == expected


def test_normalize_ts_iso_with_z():
    expected = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    assert _normalize_ts("2026-06-22T12:00:00Z") == expected


def test_normalize_ts_invalid_string_zero():
    """无法解析时返回 0（视为最旧），确保类型错误不中断同步。"""
    assert _normalize_ts("not-a-date") == 0.0
