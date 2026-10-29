"""云同步加密契约 · AES-GCM v4 往返 + 跨组隔离 + LWW 冲突合并原语（进程内，离线）

不打真实 Cloudflare Worker，直接测 sync 协议的真源算法（与手机端 cloudSync.ts 同源契约）：
信封格式、版本字节识别、PBKDF2 派生、group salt 跨组不可互解、篡改/错钥失败、LWW 归一化与真冲突判定。
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from sync._cloud_sync_crypto import (  # noqa: E402
    VERSION_BYTE_V4,
    _derive_sync_group_id,
    _detect_conflict,
    _normalize_ts,
    decrypt_data,
    encrypt_data,
)

DEVICE = "desktop-main"
TOKEN = "correct horse battery staple"


def test_v4_envelope_roundtrip_and_version_byte():
    """v4（带 sync_group_id）：信封首字节 0x04，正确钥可完整解密回原文。"""
    gid = _derive_sync_group_id("register-secret-alpha")
    plaintext = "笔记正文 📖 含 unicode"
    ct = encrypt_data(plaintext, TOKEN, DEVICE, sync_group_id=gid)

    envelope = base64.b64decode(ct)
    assert envelope[0] == VERSION_BYTE_V4, "v4 信封版本字节应为 0x04"

    assert decrypt_data(ct, TOKEN, DEVICE, sync_group_id=gid) == plaintext


def test_wrong_token_cannot_decrypt():
    """AES-GCM 认证标签：错误 token 解密必须失败（不可静默返回垃圾）。"""
    gid = _derive_sync_group_id("register-secret-beta")
    ct = encrypt_data("秘密数据", TOKEN, DEVICE, sync_group_id=gid)
    with pytest.raises(Exception):
        decrypt_data(ct, "wrong-token", DEVICE, sync_group_id=gid)


def test_cross_group_isolation_even_with_same_token():
    """P0.1 组隔离：同 token、不同 sync 组（不同 register_secret）→ 派生不同密钥 → 不可互解。"""
    gid_a = _derive_sync_group_id("secret-group-A")
    gid_b = _derive_sync_group_id("secret-group-B")
    assert gid_a and gid_b and gid_a != gid_b

    ct = encrypt_data("只在 A 组可读", TOKEN, DEVICE, sync_group_id=gid_a)
    with pytest.raises(Exception):
        decrypt_data(ct, TOKEN, DEVICE, sync_group_id=gid_b)


def test_v3_fallback_roundtrip():
    """无 register_secret → 降级 v3（per-device salt），仍可自解密（向后兼容）。"""
    ct = encrypt_data("v3 兼容数据", TOKEN, DEVICE, sync_group_id="")
    env = base64.b64decode(ct)
    assert env[0] == 0x03, "无 group 时应落 v3 版本字节 0x03"
    assert decrypt_data(ct, TOKEN, DEVICE, sync_group_id="") == "v3 兼容数据"


def test_derive_sync_group_id_deterministic():
    """group id 稳定派生：同 secret 恒定；空 secret 返回空；截断为 16 hex。"""
    assert _derive_sync_group_id("same") == _derive_sync_group_id("same")
    assert _derive_sync_group_id("") == ""
    assert len(_derive_sync_group_id("xyz")) == 16


def test_normalize_ts_three_sources_agree():
    """LWW 时间戳归一：ISO / 毫秒 epoch / 秒 epoch / 数字串 统一到同一 epoch 秒。"""
    seconds = 1719064800
    assert _normalize_ts(seconds) == pytest.approx(seconds)
    assert _normalize_ts(seconds * 1000) == pytest.approx(seconds)  # 毫秒
    assert _normalize_ts(str(seconds)) == pytest.approx(seconds)  # 数字串
    assert _normalize_ts(None) == 0.0
    iso = _normalize_ts("2026-06-22T12:00:00Z")
    assert iso > 0


def test_detect_conflict_semantics():
    """真冲突判定：双方自上次同步后都改过 → True；否则 False（回退纯 LWW）。"""
    last = 1000.0
    # 双方都有未同步的新修改 → 冲突
    assert _detect_conflict({"updated_at": 2000.0, "last_synced_at": last}, remote_time=3000.0) is True
    # 本地自上次同步后未改（local<=last_synced）→ 非冲突，接受远端
    assert _detect_conflict({"updated_at": 900.0, "last_synced_at": last}, remote_time=3000.0) is False
    # 远端未更新（remote<=last_synced）→ 非冲突
    assert _detect_conflict({"updated_at": 2000.0, "last_synced_at": last}, remote_time=900.0) is False
    # 从未同步（last_synced=0）→ 无法判定冲突
    assert _detect_conflict({"updated_at": 2000.0, "last_synced_at": 0}, remote_time=3000.0) is False
    # 本地无副本 → False
    assert _detect_conflict(None, remote_time=3000.0) is False
