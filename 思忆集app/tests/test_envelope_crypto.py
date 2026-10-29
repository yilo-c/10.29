"""Unit tests for core.envelope_crypto — AAD-bound AES-GCM v4 envelopes."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.envelope_crypto import (
    VERSION_BYTE_V4,
    decrypt_envelope_payload,
    encrypt_envelope_payload,
)
from sync.cloud_sync_client import CloudSyncCryptoError, encrypt_data


TOKEN = "test-sync-token-32-chars-or-so--"
DEVICE = "desktop-aaaa1111bbbb2222"
ENV_ID = "01890000-1111-7000-8000-aaaa00001111"
RES_TYPE = "note"


# ── Happy path ────────────────────────────────────────────────────────

def test_v4_roundtrip():
    pt = "{'hello': 'world'}"
    ct = encrypt_envelope_payload(pt, TOKEN, DEVICE, ENV_ID, RES_TYPE)
    assert ct
    out = decrypt_envelope_payload(ct, TOKEN, DEVICE, ENV_ID, RES_TYPE)
    assert out == pt


def test_v4_version_byte():
    """Ensure v4 emits the 0x04 marker (interop with mobile router)."""
    import base64

    ct = encrypt_envelope_payload("x", TOKEN, DEVICE, ENV_ID, RES_TYPE)
    raw = base64.b64decode(ct)
    assert raw[0] == VERSION_BYTE_V4
    assert raw[0] == 0x04
    # 1 (ver) + 12 (iv) + at least 16 (gcm tag) + payload bytes
    assert len(raw) >= 1 + 12 + 16 + 1


# ── AAD rebinding refusal ─────────────────────────────────────────────

def test_v4_rejects_envelope_id_swap():
    ct = encrypt_envelope_payload("secret", TOKEN, DEVICE, ENV_ID, RES_TYPE)
    other_env = "01890000-1111-7000-8000-bbbb00002222"
    with pytest.raises(CloudSyncCryptoError):
        decrypt_envelope_payload(ct, TOKEN, DEVICE, other_env, RES_TYPE)


def test_v4_rejects_resource_type_swap():
    """Splice attack: take a "note" ciphertext, claim it's "personal_task"."""
    ct = encrypt_envelope_payload("malicious-data", TOKEN, DEVICE, ENV_ID, "note")
    with pytest.raises(CloudSyncCryptoError):
        decrypt_envelope_payload(ct, TOKEN, DEVICE, ENV_ID, "personal_task")


def test_v4_rejects_wrong_token():
    ct = encrypt_envelope_payload("x", TOKEN, DEVICE, ENV_ID, RES_TYPE)
    with pytest.raises(CloudSyncCryptoError):
        decrypt_envelope_payload(ct, "different-token", DEVICE, ENV_ID, RES_TYPE)


def test_v4_rejects_wrong_device():
    ct = encrypt_envelope_payload("x", TOKEN, DEVICE, ENV_ID, RES_TYPE)
    with pytest.raises(CloudSyncCryptoError):
        decrypt_envelope_payload(ct, TOKEN, "desktop-other", ENV_ID, RES_TYPE)


# ── Version byte routing ──────────────────────────────────────────────

def test_v4_decrypt_refuses_v3_ciphertext():
    """v3 ciphertext (no AAD) must not be decryptable via v4 path —
    the version byte gate catches it before AES touches the buffer."""
    v3 = encrypt_data("legacy-payload", TOKEN, DEVICE)
    with pytest.raises(CloudSyncCryptoError) as exc:
        decrypt_envelope_payload(v3, TOKEN, DEVICE, ENV_ID, RES_TYPE)
    assert "unsupported version byte" in str(exc.value)


# ── Input validation ──────────────────────────────────────────────────

def test_v4_rejects_empty_envelope_id():
    with pytest.raises(CloudSyncCryptoError):
        encrypt_envelope_payload("x", TOKEN, DEVICE, "", RES_TYPE)


def test_v4_rejects_empty_resource_type():
    with pytest.raises(CloudSyncCryptoError):
        encrypt_envelope_payload("x", TOKEN, DEVICE, ENV_ID, "")


def test_v4_rejects_corrupted_ciphertext():
    ct = encrypt_envelope_payload("x", TOKEN, DEVICE, ENV_ID, RES_TYPE)
    # flip a byte after base64-decoding and re-encoding
    import base64

    raw = bytearray(base64.b64decode(ct))
    # corrupt byte inside the auth tag region (last 16 bytes)
    raw[-1] ^= 0xFF
    corrupted = base64.b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(CloudSyncCryptoError):
        decrypt_envelope_payload(corrupted, TOKEN, DEVICE, ENV_ID, RES_TYPE)


def test_v4_rejects_truncated_ciphertext():
    with pytest.raises(CloudSyncCryptoError):
        decrypt_envelope_payload("AQIDBA==", TOKEN, DEVICE, ENV_ID, RES_TYPE)


def test_v4_iv_is_unique_per_call():
    """Confirm randomness: two encrypts of the same plaintext yield different IVs."""
    import base64

    a = encrypt_envelope_payload("same", TOKEN, DEVICE, ENV_ID, RES_TYPE)
    b = encrypt_envelope_payload("same", TOKEN, DEVICE, ENV_ID, RES_TYPE)
    assert a != b
    iv_a = base64.b64decode(a)[1:13]
    iv_b = base64.b64decode(b)[1:13]
    assert iv_a != iv_b
