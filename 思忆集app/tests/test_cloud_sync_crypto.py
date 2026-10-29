"""cloud_sync_client 加密层单元测试。

验证 AES-256-GCM + PBKDF2 加密协议与手机端 cloudSync.ts 兼容：
- v4 格式（当前写入）: ``[0x04][salt_len][salt][iter(4 BE)][iv(12)][ct+tag]`` base64, 600K 迭代
  - sync_group_id 派生 salt，**同组不同设备可互解**
  - 信封头自带 KDF 参数，接收端读取后派生
- v3 格式（历史）: ``[0x03][iv(12)][ciphertext+tag]`` base64, 600K 迭代, per-device salt
  - 不同 device_id 不可互解（仅同设备解密兼容）
- v2 格式（历史）: ``[0x02][iv(12)][ciphertext+tag]`` base64, 100K 迭代
- per-device salt：``siyi-sync-v2:{device_id}``（v2/v3 共用）
- v1 legacy 格式向后兼容（固定 salt, 100K, 无版本字节）
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# 2026-08-27 R3：import 从真源 sync._cloud_sync_crypto 读取（cloud_sync_client 仅
# re-export 2 个符号，CRYPTO_ITERATIONS 等常量/函数不在其 facade —— 原 import
# 使本文件整体收集失败 ImportError，属既有假绿（登记册 992 passed 不实）。
from sync._cloud_sync_crypto import (
    CRYPTO_ITERATIONS,
    CRYPTO_ITERATIONS_V1V2,
    CRYPTO_ITERATIONS_V3,
    CRYPTO_ITERATIONS_V4,
    CRYPTO_SALT_V1,
    CRYPTO_SALT_V2_PREFIX,
    VERSION_BYTE_V2,
    VERSION_BYTE_V3,
    VERSION_BYTE_V4,
    CloudSyncCryptoError,
    decrypt_data,
    encrypt_data,
    _derive_key,
    _derive_sync_group_id,
    _device_salt,
)


# ── 基础 roundtrip ──

def test_encrypt_decrypt_roundtrip_simple():
    ct = encrypt_data("hello", "mytoken", "desktop-abc")
    assert decrypt_data(ct, "mytoken", "desktop-abc") == "hello"


def test_encrypt_decrypt_roundtrip_empty():
    ct = encrypt_data("", "tok", "dev1")
    assert decrypt_data(ct, "tok", "dev1") == ""


def test_encrypt_decrypt_roundtrip_unicode():
    plaintext = "你好世界 馃實 茅moji — na茂ve"
    ct = encrypt_data(plaintext, "tok", "dev1")
    assert decrypt_data(ct, "tok", "dev1") == plaintext


def test_encrypt_decrypt_roundtrip_large():
    plaintext = "x" * 10_000
    ct = encrypt_data(plaintext, "tok", "dev1")
    assert decrypt_data(ct, "tok", "dev1") == plaintext


def test_encrypt_decrypt_roundtrip_json():
    import json
    payload = json.dumps(
        {"id": 1, "title": "笔记", "tags": ["a", "b"]},
        ensure_ascii=False,
    )
    ct = encrypt_data(payload, "tok", "dev1")
    assert decrypt_data(ct, "tok", "dev1") == payload


# ── v4 格式结构（当前写入版本）──

def test_v4_format_has_version_byte():
    """P0.1: 传 sync_group_id 时写入 v4 格式。"""
    gid = _derive_sync_group_id("secret-A")
    ct = encrypt_data("hello", "tok", "dev1", gid)
    raw = base64.b64decode(ct)
    assert raw[0] == VERSION_BYTE_V4
    assert raw[0] == 0x04


def test_v4_format_envelope_structure():
    """v4 信封: [0x04][iter(4 BE)][iv(12)][ct+tag]（salt 不入信封）。"""
    gid = _derive_sync_group_id("secret-A")
    plaintext = "hello"
    ct = encrypt_data(plaintext, "tok", "dev1", gid)
    raw = base64.b64decode(ct)

    assert raw[0] == VERSION_BYTE_V4
    # iterations (4 字节 big-endian)
    iterations = int.from_bytes(raw[1:5], "big")
    assert iterations == CRYPTO_ITERATIONS_V4

    # iv (12 字节)
    iv = raw[5:17]
    assert len(iv) == 12

    # "hello" = 5 bytes plaintext → ciphertext = 5 bytes, tag = 16 bytes
    ct_with_tag = raw[17:]
    assert len(ct_with_tag) == 5 + 16


def test_v4_format_iv_length():
    gid = _derive_sync_group_id("secret-A")
    ct = encrypt_data("hello", "tok", "dev1", gid)
    raw = base64.b64decode(ct)
    iv = raw[5:17]
    assert len(iv) == 12


def test_v4_encrypt_produces_different_ciphertext_each_time():
    """随机 IV 保证相同明文每次加密结果不同。"""
    gid = _derive_sync_group_id("secret-A")
    ct1 = encrypt_data("hello", "tok", "dev1", gid)
    ct2 = encrypt_data("hello", "tok", "dev1", gid)
    assert ct1 != ct2


# ── v3 格式结构（历史写入版本，无 sync_group_id 时降级）──

def test_v3_format_has_version_byte_when_no_group():
    """无 sync_group_id 时降级为 v3（per-device salt）。"""
    ct = encrypt_data("hello", "tok", "dev1")
    raw = base64.b64decode(ct)
    assert raw[0] == VERSION_BYTE_V3
    assert raw[0] == 0x03


def test_v3_format_iv_length():
    ct = encrypt_data("hello", "tok", "dev1")
    raw = base64.b64decode(ct)
    # [version(1)] [iv(12)] [ciphertext+tag]
    iv = raw[1:13]
    assert len(iv) == 12


def test_v3_format_ciphertext_includes_gcm_tag():
    """AES-GCM tag 是 16 字节，附在密文末尾。"""
    ct = encrypt_data("hello", "tok", "dev1")
    raw = base64.b64decode(ct)
    # "hello" = 5 bytes plaintext → ciphertext = 5 bytes, tag = 16 bytes
    ciphertext_with_tag = raw[13:]
    assert len(ciphertext_with_tag) == 5 + 16


def test_encrypt_produces_different_ciphertext_each_time():
    """随机 IV 保证相同明文每次加密结果不同。"""
    ct1 = encrypt_data("hello", "tok", "dev1")
    ct2 = encrypt_data("hello", "tok", "dev1")
    assert ct1 != ct2


# ── sync_group_id 派生 ──

def test_derive_sync_group_id_deterministic():
    """同一 register_secret 多次派生结果一致。"""
    gid1 = _derive_sync_group_id("my-register-secret")
    gid2 = _derive_sync_group_id("my-register-secret")
    assert gid1 == gid2


def test_derive_sync_group_id_different_secret_different():
    """不同 register_secret 派生不同 group_id（跨组隔离的基础）。"""
    gid1 = _derive_sync_group_id("secret-A")
    gid2 = _derive_sync_group_id("secret-B")
    assert gid1 != gid2


def test_derive_sync_group_id_empty_returns_empty():
    assert _derive_sync_group_id("") == ""


def test_derive_sync_group_id_length():
    """sha256 前 16 hex 字符 = 64 bit，足够做 salt 差异化。"""
    gid = _derive_sync_group_id("any-secret")
    assert len(gid) == 16
    assert all(c in "0123456789abcdef" for c in gid)


# ── salt / key 派生 ──

def test_device_salt_format():
    assert _device_salt("desktop-abc") == CRYPTO_SALT_V2_PREFIX + "desktop-abc"
    assert _device_salt("desktop-abc") == "siyi-sync-v2:desktop-abc"


def test_derive_key_deterministic():
    key1 = _derive_key("tok", "salt1")
    key2 = _derive_key("tok", "salt1")
    assert key1 == key2
    assert len(key1) == 32  # 256-bit


def test_derive_key_different_salt_different_key():
    k1 = _derive_key("tok", "salt1")
    k2 = _derive_key("tok", "salt2")
    assert k1 != k2


def test_derive_key_different_token_different_key():
    k1 = _derive_key("tok1", "salt")
    k2 = _derive_key("tok2", "salt")
    assert k1 != k2


def test_crypto_iterations_constants():
    # 向后兼容别名仍指向 v1/v2 的 100K
    assert CRYPTO_ITERATIONS == 100_000
    assert CRYPTO_ITERATIONS_V1V2 == 100_000
    # v3/v4 新写入使用 600K（OWASP 2023）
    assert CRYPTO_ITERATIONS_V3 == 600_000
    assert CRYPTO_ITERATIONS_V4 == 600_000


# ── 跨设备 / 错误 token ──

def test_decrypt_wrong_token_raises():
    ct = encrypt_data("hello", "tok-correct", "dev1")
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data(ct, "tok-wrong", "dev1")


def test_v4_same_group_different_devices_can_decrypt():
    """P0.1 核心验收：同 sync_group_id 的不同设备可互解（修复跨设备同步的关键）。

    旧 v3 行为：device-A 加密的密文 device-B 解不开（per-device salt）。
    新 v4 行为：只要 device-A 和 device-B 共享 register_secret（即在同一
    Worker 部署中注册），它们派生出相同 sync_group_id → 相同 salt → 相同 AES 密钥 → 可互解。
    """
    gid = _derive_sync_group_id("shared-register-secret")
    plaintext = "hello from device A"
    # device A 加密（用 gid）
    ct = encrypt_data(plaintext, "shared-token", "device-A", gid)
    # device B 解密 —— B 派生出相同的 gid（共享 register_secret）
    assert decrypt_data(ct, "shared-token", "device-B", gid) == plaintext


def test_v4_cross_group_cannot_decrypt():
    """P0.1 验收：不同 sync_group_id 之间不可互解（跨组隔离）。

    即使 token 相同，group B 的设备用自己派生的 gid_b 解 group A 的密文：
    salt 不同 → 密钥不同 → AES-GCM 验证失败。
    """
    gid_a = _derive_sync_group_id("secret-group-A")
    gid_b = _derive_sync_group_id("secret-group-B")
    ct = encrypt_data("secret in group A", "tok", "device-A", gid_a)
    # B 用自己的 gid_b 解 —— salt 不同 → 密钥不同 → 解密失败
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data(ct, "tok", "device-B", gid_b)


def test_v4_decrypt_without_group_id_raises():
    """P0.1: 解 v4 密文时缺 sync_group_id 应明确报错（而非静默失败）。"""
    gid = _derive_sync_group_id("secret-A")
    ct = encrypt_data("hello", "tok", "dev1", gid)
    with pytest.raises(CloudSyncCryptoError, match="sync_group_id"):
        decrypt_data(ct, "tok", "dev1")  # 不传 gid


def test_v4_wrong_token_raises():
    gid = _derive_sync_group_id("secret-A")
    ct = encrypt_data("hello", "tok-correct", "dev1", gid)
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data(ct, "tok-wrong", "dev1", gid)


def test_v3_legacy_wrong_device_raises():
    """v3 历史格式：per-device salt，不同 device_id 不可互解（仅同设备兼容）。

    保留此测试确认 v3 行为不变 —— 旧密文只能由原加密设备解密。
    新写入应使用 v4（带 sync_group_id）以支持跨设备。
    """
    ct = encrypt_data("hello", "tok", "device-A")  # 不传 gid → 降级 v3
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data(ct, "tok", "device-B")


def test_decrypt_corrupted_base64_raises():
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data("!!!not-base64!!!", "tok", "dev1")


def test_decrypt_empty_ciphertext_raises():
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data("", "tok", "dev1")


def test_decrypt_tampered_ciphertext_raises():
    """AES-GCM 应检测到密文被篡改。"""
    ct = encrypt_data("hello", "tok", "dev1")
    raw = bytearray(base64.b64decode(ct))
    raw[-1] ^= 0xFF  # 翻转最后一个字节（tag 区域）
    tampered = base64.b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data(tampered, "tok", "dev1")


def test_decrypt_tampered_iv_raises():
    """篡改 IV 也应被 GCM 检测到。"""
    ct = encrypt_data("hello", "tok", "dev1")
    raw = bytearray(base64.b64decode(ct))
    raw[1] ^= 0xFF  # 翻转 IV 第一个字节
    tampered = base64.b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data(tampered, "tok", "dev1")


def test_v4_tampered_envelope_iterations_raises():
    """P0.1: 篡改 v4 信封头的 iterations 字段应导致解密失败。"""
    gid = _derive_sync_group_id("secret-A")
    ct = encrypt_data("hello", "tok", "dev1", gid)
    raw = bytearray(base64.b64decode(ct))
    # iterations 在 [1:5]，翻转第 2 字节（避免变成 0）
    raw[2] ^= 0xFF
    tampered = base64.b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data(tampered, "tok", "dev1", gid)


def test_v4_truncated_envelope_raises():
    """P0.1: 截断的 v4 信封头应被检测到。"""
    gid = _derive_sync_group_id("secret-A")
    ct = encrypt_data("hello", "tok", "dev1", gid)
    raw = bytearray(base64.b64decode(ct))
    # 截断到只有版本字节
    truncated = base64.b64encode(bytes(raw[:1])).decode("ascii")
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data(truncated, "tok", "dev1", gid)


# ── v1 legacy 兼容 ──

def test_decrypt_v1_legacy_format():
    """v1 格式无版本字节，IV 从 byte 0 开始，使用固定 salt, 100K 迭代。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import os

    token = "legacy-token"
    key = _derive_key(token, CRYPTO_SALT_V1)
    iv = os.urandom(12)
    aesgcm = AESGCM(key)
    plaintext = "legacy data"
    ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), associated_data=None)
    # v1: [iv(12)][ciphertext+tag]，无版本字节
    v1_combined = iv + ciphertext
    v1_b64 = base64.b64encode(v1_combined).decode("ascii")

    # 首字节既不是 0x03 也不是 0x02 → 走 v1 解密路径
    first_byte = base64.b64decode(v1_b64)[0]
    assert first_byte != VERSION_BYTE_V3
    assert first_byte != VERSION_BYTE_V2
    assert decrypt_data(v1_b64, token, "any-device") == plaintext


def test_decrypt_v2_historical_format():
    """v2 格式 [0x02][iv][ct+tag], 100K 迭代, per-device salt — 历史数据可解。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import os

    token = "v2-token"
    device_id = "dev2"
    key = _derive_key(token, _device_salt(device_id), CRYPTO_ITERATIONS_V1V2)
    iv = os.urandom(12)
    aesgcm = AESGCM(key)
    plaintext = "v2 historical data"
    ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), associated_data=None)
    v2_combined = bytes([VERSION_BYTE_V2]) + iv + ciphertext
    v2_b64 = base64.b64encode(v2_combined).decode("ascii")
    assert decrypt_data(v2_b64, token, device_id) == plaintext


def test_v1_v2_v3_can_coexist():
    """v1/v2/v3（历史）都能被 decrypt_data 处理。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import os

    token = "shared-token"
    device_id = "dev1"

    # v3（历史 encrypt_data 写入，无 sync_group_id 时降级）
    v3_ct = encrypt_data("v3 data", token, device_id)
    assert decrypt_data(v3_ct, token, device_id) == "v3 data"
    # 确认降级写入确实是 v3
    assert base64.b64decode(v3_ct)[0] == VERSION_BYTE_V3

    # v2（历史）
    key2 = _derive_key(token, _device_salt(device_id), CRYPTO_ITERATIONS_V1V2)
    iv2 = os.urandom(12)
    v2_ct = base64.b64encode(
        bytes([VERSION_BYTE_V2]) + iv2 + AESGCM(key2).encrypt(iv2, b"v2 data", associated_data=None)
    ).decode("ascii")
    assert decrypt_data(v2_ct, token, device_id) == "v2 data"

    # v1（legacy）
    key1 = _derive_key(token, CRYPTO_SALT_V1)
    iv1 = os.urandom(12)
    v1_ct = base64.b64encode(
        iv1 + AESGCM(key1).encrypt(iv1, b"v1 data", associated_data=None)
    ).decode("ascii")
    assert decrypt_data(v1_ct, token, device_id) == "v1 data"


def test_v1_v2_v3_v4_can_coexist():
    """P0.1: v1/v2/v3/v4 都能被 decrypt_data 处理（向后兼容 + 新写入）。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import os

    token = "shared-token"
    device_id = "dev1"
    gid = _derive_sync_group_id("shared-register-secret")

    # v4（当前 encrypt_data 写入，传 sync_group_id）
    v4_ct = encrypt_data("v4 data", token, device_id, gid)
    assert decrypt_data(v4_ct, token, device_id, gid) == "v4 data"
    assert base64.b64decode(v4_ct)[0] == VERSION_BYTE_V4

    # v3（历史）
    v3_ct = encrypt_data("v3 data", token, device_id)
    assert decrypt_data(v3_ct, token, device_id) == "v3 data"
    assert base64.b64decode(v3_ct)[0] == VERSION_BYTE_V3

    # v2（历史）
    key2 = _derive_key(token, _device_salt(device_id), CRYPTO_ITERATIONS_V1V2)
    iv2 = os.urandom(12)
    v2_ct = base64.b64encode(
        bytes([VERSION_BYTE_V2]) + iv2 + AESGCM(key2).encrypt(iv2, b"v2 data", associated_data=None)
    ).decode("ascii")
    assert decrypt_data(v2_ct, token, device_id) == "v2 data"

    # v1（legacy）
    key1 = _derive_key(token, CRYPTO_SALT_V1)
    iv1 = os.urandom(12)
    v1_ct = base64.b64encode(
        iv1 + AESGCM(key1).encrypt(iv1, b"v1 data", associated_data=None)
    ).decode("ascii")
    assert decrypt_data(v1_ct, token, device_id) == "v1 data"


# ── P0.1 端到端：A push → Worker → B pull → 明文一致 ──

def test_end_to_end_cross_device_decrypt():
    """P0.1 核心验收：模拟 A push → Worker → B pull 的完整链路。

    场景：
    - 设备 A 和设备 B 都向同一 Worker 注册（共享 register_secret）
    - 设备 A 用 sync_token + sync_group_id 加密笔记
    - 密文经 Worker 中转（这里用 base64 字符串模拟）
    - 设备 B 用同一个 sync_token + 自己派生的 sync_group_id 解密

    旧 v3 实现下此测试会失败（B 解不开 A 的密文）。
    """
    # 两台设备在配置云同步时输入相同的 register_secret
    register_secret = "my-worker-register-secret-2026"
    sync_token = "user-chosen-sync-password"

    device_a = "desktop-abc123"
    device_b = "mobile-xyz789"

    # A 派生出 sync_group_id（在 CloudSyncClient.__init__ 中自动完成）
    gid_a = _derive_sync_group_id(register_secret)
    # B 派生出相同的 sync_group_id
    gid_b = _derive_sync_group_id(register_secret)
    assert gid_a == gid_b  # 关键不变量

    # A 加密笔记
    note_payload = '{"id":"n1","title":"跨设备笔记","content":"hello from A"}'
    ct_from_a = encrypt_data(note_payload, sync_token, device_a, gid_a)

    # 模拟 Worker 中转：base64 字符串原样传递
    # B 解密：B 用自己派生的 gid_b（与 gid_a 相同）来派生 salt
    decrypted_at_b = decrypt_data(ct_from_a, sync_token, device_b, gid_b)
    assert decrypted_at_b == note_payload


def test_end_to_end_cross_device_roundtrip_both_directions():
    """P0.1: 双向跨设备解密都工作 —— A→B 和 B→A。"""
    register_secret = "shared-secret"
    sync_token = "shared-token"
    device_a = "desktop-aaa"
    device_b = "mobile-bbb"
    gid = _derive_sync_group_id(register_secret)

    # A → B
    ct_a = encrypt_data("message from A", sync_token, device_a, gid)
    assert decrypt_data(ct_a, sync_token, device_b, gid) == "message from A"

    # B → A
    ct_b = encrypt_data("reply from B", sync_token, device_b, gid)
    assert decrypt_data(ct_b, sync_token, device_a, gid) == "reply from B"


# ── P0.1 旧密文迁移路径（信封 version 判定）──

def test_v3_legacy_ciphertext_still_decryptable_by_same_device():
    """P0.1 迁移路径：旧 v3 密文可被原设备解密（向后兼容）。

    场景：用户在 P0.1 升级前已用 v3 推送过数据到 Worker。升级后，
    同一设备仍能解密这些旧密文（因为 v3 走 per-device salt，原设备
    的 device_id 不变）。但跨设备解密旧 v3 密文仍会失败 —— 这部分
    数据需要原设备重新 push 为 v4 才能跨设备共享。
    """
    token = "shared-token"
    device_id = "desktop-original"

    # 模拟旧的 v3 密文（无 sync_group_id）
    old_v3_ct = encrypt_data("legacy v3 data", token, device_id)
    assert base64.b64decode(old_v3_ct)[0] == VERSION_BYTE_V3

    # 升级后，原设备仍能解密
    assert decrypt_data(old_v3_ct, token, device_id) == "legacy v3 data"


def test_v3_legacy_ciphertext_not_decryptable_cross_device():
    """P0.1 迁移路径：旧 v3 密文无法跨设备解密（已知限制）。

    这是 v3 设计的固有限制 —— per-device salt 使得只有原设备能解密。
    迁移策略：原设备重新 push 数据为 v4 即可让其他设备拉取。
    不做自动迁移（无法在不知道原 device_id 的情况下重新加密）。
    """
    token = "shared-token"

    # 旧 v3 密文（device-A 加密，无 sync_group_id）
    old_v3_ct = encrypt_data("legacy v3 data", token, "device-A")
    assert base64.b64decode(old_v3_ct)[0] == VERSION_BYTE_V3

    # device-B（即使有 sync_group_id）也无法解密 device-A 的 v3 密文
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data(old_v3_ct, token, "device-B")


def test_v4_envelope_version_byte_detection():
    """P0.1: decrypt_data 通过首字节识别 v4，不会误判为 v3/v2/v1。"""
    gid = _derive_sync_group_id("test-secret")
    ct = encrypt_data("v4 data", "tok", "dev1", gid)
    raw = base64.b64decode(ct)

    # v4 首字节是 0x04，与 v3(0x03)/v2(0x02) 不同，与 v1（首字节是 IV 的一部分）也不同
    assert raw[0] == VERSION_BYTE_V4
    assert raw[0] != VERSION_BYTE_V3
    assert raw[0] != VERSION_BYTE_V2

    # 解密成功
    assert decrypt_data(ct, "tok", "dev1", gid) == "v4 data"


def test_v4_envelope_zero_iterations_raises():
    """P0.1: v4 信封头 iterations 被改成 1（与原 600K 不同）应导致密钥不匹配。"""
    gid = _derive_sync_group_id("secret-A")
    ct = encrypt_data("hello", "tok", "dev1", gid)
    raw = bytearray(base64.b64decode(ct))
    # iterations 在 [1:5]，改成 1（与原 600K 不同 → 密钥不同）
    raw[1:5] = (1).to_bytes(4, "big")
    tampered = base64.b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(CloudSyncCryptoError):
        decrypt_data(tampered, "tok", "dev1", gid)


# ── B-08: v4 iterations 硬上限（防篡改密文 CPU DoS）──

def test_v4_decrypt_rejects_absurd_iterations_before_derivation(monkeypatch):
    """B-08: iterations=0xFFFFFFFF 信封必须在校验层快速拒绝，PBKDF2 不得执行。

    先红后绿：修复前 `_derive_key` 会被调用（断言失败→红）；
    修复后上限校验先于派生触发 raise（_derive_key 未调用→绿）。
    """
    from sync import _cloud_sync_crypto as mod

    called: list[int] = []

    def _spy_key(*args, **kwargs):
        called.append(args)
        return b"\x00" * 32  # 返回假 key，避免真实 PBKDF2 长跑

    monkeypatch.setattr(mod, "_derive_key", _spy_key)

    # 手工构造 v4 信封头：ver(0x04) + iter=0xFFFFFFFF + iv(12 零) + 任意 ct
    envelope = b"\x04" + (0xFFFFFFFF).to_bytes(4, "big") + b"\x00" * 12 + b"\x00" * 16
    ct = base64.b64encode(envelope).decode("ascii")

    with pytest.raises(CloudSyncCryptoError):
        mod.decrypt_data(ct, "tok", "dev1", sync_group_id="grp")

    assert called == [], (
        "iterations=0xFFFFFFFF 不应进入 _derive_key（PBKDF2 长跑 = CPU DoS）；"
        "必须在派生前被上限校验拒绝"
    )


def test_v4_decrypt_accepts_normal_iterations(monkeypatch):
    """B-08 反向：正常 600K iterations 必须照常走派生（上限校验不得误伤）。"""
    from sync import _cloud_sync_crypto as mod

    called: list[int] = []

    def _spy_key(token, salt, iterations, **kwargs):
        called.append(iterations)
        return b"\x00" * 32

    monkeypatch.setattr(mod, "_derive_key", _spy_key)

    envelope = b"\x04" + (600_000).to_bytes(4, "big") + b"\x00" * 12 + b"\x00" * 16
    ct = base64.b64encode(envelope).decode("ascii")

    # 正常 iterations 应进入派生（派生后 AES 解密失败属预期，raise 也接受）
    try:
        mod.decrypt_data(ct, "tok", "dev1", sync_group_id="grp")
    except CloudSyncCryptoError:
        pass

    assert called == [600_000], "正常 600K iterations 必须到达 _derive_key"
