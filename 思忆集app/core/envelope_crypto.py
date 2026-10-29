"""Envelope-bound encryption helpers — extends cloud_sync_client AES-GCM with
Additional Authenticated Data (AAD) binding to prevent ciphertext-reuse attacks.

Why this exists (P4.2-P2):
    The base ``sync.cloud_sync_client.encrypt_data()`` uses
    ``AESGCM.encrypt(iv, pt, associated_data=None)``. With AAD absent, a
    ciphertext for one envelope (e.g. a "notes" payload) can theoretically be
    spliced into another envelope's slot (e.g. as a "todos" payload) since
    the GCM authentication tag only covers the plaintext, not the surrounding
    transport metadata. By binding envelope_id + resource_type into AAD, we
    refuse decryption when the ciphertext is moved between envelopes.

Format (v4, current write):
    [0x04][iv(12)][ciphertext+tag]   base64-encoded
    AAD = sha256("v4:" + envelope_id + ":" + resource_type)

Backward compatibility:
    - Reading v3 (0x03) / v2 (0x02) / v1 ciphertexts continues to work via
      ``sync.cloud_sync_client.decrypt_data()``. v4 explicitly fails for any
      ciphertext lacking the 0x04 version byte; callers that want generic
      decode should keep using the v1/v2/v3-tolerant path.
    - PBKDF2 iteration count, key derivation, and per-device salt scheme are
      identical to v3 (600K iterations, "siyi-sync-v2:" + device_id salt).

This module is intentionally NOT wired into cloud_sync_client.py yet — it's a
forward-compatible building block. cloud_sync_client.py keeps emitting v3 to
preserve interop with the existing Worker + mobile encryptData() until the
mobile side ships matching v4 support.
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# 注意：必须从真源 ``sync._cloud_sync_crypto`` 导入。
# B5 把加密实现抽到该模块后，``sync.cloud_sync_client`` 只 re-export 了
# CRYPTO_SALT_V4_PREFIX，导致本文件导入整体失败（模块不可导入，2026-08-29 修复）。
# facade 的 re-export 清单会随时间腐烂，消费者一律指向真源。
from sync._cloud_sync_crypto import (
    CRYPTO_ITERATIONS_V3,
    CRYPTO_SALT_V2_PREFIX,
    CloudSyncCryptoError,
    _derive_key,
)

VERSION_BYTE_V4 = 0x04
AAD_PREFIX = "v4:"


def _build_aad(envelope_id: str, resource_type: str) -> bytes:
    """Construct AAD from envelope identity.

    Hash both fields so AAD is fixed-length and not affected by null bytes /
    delimiter ambiguity (``envelope_id`` may contain UUIDs with hyphens, etc).
    """
    canonical = f"{AAD_PREFIX}{envelope_id}:{resource_type}"
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def encrypt_envelope_payload(
    plaintext: str,
    token: str,
    device_id: str,
    envelope_id: str,
    resource_type: str,
) -> str:
    """Encrypt envelope payload with AAD binding.

    Returns base64-encoded ``[0x04][iv(12)][ct+tag]``. The AAD is derived
    from ``envelope_id`` + ``resource_type``; mismatching either during
    decryption raises :class:`CloudSyncCryptoError`.
    """
    if not envelope_id:
        raise CloudSyncCryptoError("envelope_id required for v4 encryption")
    if not resource_type:
        raise CloudSyncCryptoError("resource_type required for v4 encryption")

    salt = CRYPTO_SALT_V2_PREFIX + device_id
    key = _derive_key(token, salt, CRYPTO_ITERATIONS_V3)
    iv = os.urandom(12)
    aad = _build_aad(envelope_id, resource_type)

    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(iv, plaintext.encode("utf-8"), associated_data=aad)
    combined = bytes([VERSION_BYTE_V4]) + iv + ct
    return base64.b64encode(combined).decode("ascii")


def decrypt_envelope_payload(
    ciphertext_b64: str,
    token: str,
    device_id: str,
    envelope_id: str,
    resource_type: str,
) -> str:
    """Decrypt a v4 envelope-bound ciphertext.

    Raises :class:`CloudSyncCryptoError` if:
      - the version byte is not 0x04 (callers must route v1/v2/v3 elsewhere);
      - the AAD (envelope_id / resource_type) doesn't match the original;
      - the AES-GCM tag verification fails for any other reason.
    """
    try:
        combined = base64.b64decode(ciphertext_b64)
    except Exception as exc:
        raise CloudSyncCryptoError(f"base64 decode failed: {exc}") from exc

    if len(combined) < 1 + 12 + 16:
        raise CloudSyncCryptoError("ciphertext too short for v4")

    if combined[0] != VERSION_BYTE_V4:
        raise CloudSyncCryptoError(
            f"unsupported version byte 0x{combined[0]:02x}; v4 envelope decrypt"
            " expects 0x04. Use sync.cloud_sync_client.decrypt_data for v1-v3."
        )

    iv = combined[1:13]
    data = combined[13:]

    salt = CRYPTO_SALT_V2_PREFIX + device_id
    key = _derive_key(token, salt, CRYPTO_ITERATIONS_V3)
    aad = _build_aad(envelope_id, resource_type)

    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(iv, data, associated_data=aad)
    except Exception as exc:
        # cryptography raises InvalidTag — could be wrong token OR wrong AAD.
        raise CloudSyncCryptoError(
            f"AES-GCM v4 decrypt failed (wrong token or envelope rebinding "
            f"attempt for envelope_id={envelope_id!r}, "
            f"resource_type={resource_type!r}): {exc}"
        ) from exc
    return plaintext.decode("utf-8")
