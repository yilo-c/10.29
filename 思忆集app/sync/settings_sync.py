"""Settings sync helpers — PC is authoritative (REMOTE_WINS).

设置键级别的字段同步：每个设置项独立一行，key 作为同步主键，
冲突策略为 REMOTE_WINS（PC 端为准）。

注意：不应同步本地敏感字段（如 API Key、device_id）。模块提供
``SETTINGS_SYNC_BLOCKLIST`` 用于过滤。
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from .note_sync import (
    SYNC_STATE_DIRTY,
    SYNC_STATE_LOCAL_ONLY,
    SYNC_STATE_SYNCED,
    get_or_create_local_device_id,
)


# 这些 key 不会被同步（避免泄露/双向覆盖）：
SETTINGS_SYNC_BLOCKLIST: frozenset[str] = frozenset(
    {
        "siyi_device_id",
        "llm_api_key",
        "llm_local_api_key",
        "deepseek_api_key",
        "moonshot_api_key",
        "ollama_api_key",
        "openai_api_key",
        "cloud_sync_register_secret",
        "cloud_sync_token",
        "encryption_master_key",
    }
)


def is_syncable_settings_key(key: str) -> bool:
    if not key or not isinstance(key, str):
        return False
    lowered = key.lower()
    if key in SETTINGS_SYNC_BLOCKLIST or lowered in SETTINGS_SYNC_BLOCKLIST:
        return False
    # 兜底关键字过滤
    sensitive_tokens = ("api_key", "secret", "password", "token")
    return not any(tok in lowered for tok in sensitive_tokens)


def filter_settings_for_sync(settings: dict | None) -> dict:
    """剔除不可同步项后，返回可推送的设置子集。"""
    if not isinstance(settings, dict):
        return {}
    return {k: v for k, v in settings.items() if is_syncable_settings_key(k)}


def build_settings_fingerprint(entry: dict | None) -> str:
    payload = entry or {}
    raw = json.dumps(
        {
            "key": str(payload.get("key") or ""),
            "value": payload.get("value"),
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_setting_sync_metadata(entry, data_store, mark_dirty: bool = False) -> bool:
    """补齐单个设置项的同步元数据。

    entry 是 ``{"key": str, "value": Any, ...}`` 这种 key/value 行模型。
    """
    changed = False
    if not isinstance(entry, dict):
        return changed

    key = entry.get("key")
    if not is_syncable_settings_key(str(key or "")):
        # 不可同步项：什么都不补，避免被推送
        return False

    if not entry.get("device_id"):
        entry["device_id"] = get_or_create_local_device_id(data_store)
        changed = True

    if "remote_id" not in entry:
        entry["remote_id"] = None
        changed = True

    version = entry.get("version")
    if not isinstance(version, int) or version < 1:
        entry["version"] = 1
        changed = True

    if "last_synced_at" not in entry:
        entry["last_synced_at"] = None
        changed = True

    if "deleted_at" not in entry:
        entry["deleted_at"] = None
        changed = True

    fingerprint = build_settings_fingerprint(entry)
    if entry.get("fingerprint") != fingerprint:
        entry["fingerprint"] = fingerprint
        changed = True

    if not entry.get("sync_state"):
        entry["sync_state"] = SYNC_STATE_LOCAL_ONLY if not entry.get("remote_id") else SYNC_STATE_SYNCED
        changed = True

    if mark_dirty:
        expected = SYNC_STATE_LOCAL_ONLY if not entry.get("remote_id") else SYNC_STATE_DIRTY
        if entry.get("sync_state") != expected:
            entry["sync_state"] = expected
            changed = True

    return changed


def build_setting_sync_payload(entry) -> dict | None:
    """转为同步载荷；不可同步项返回 None（调用方应跳过）。"""
    payload = deepcopy(entry or {})
    key = payload.get("key")
    if not is_syncable_settings_key(str(key or "")):
        return None
    return {
        "id": payload.get("id") or key,
        "remote_id": payload.get("remote_id"),
        "fingerprint": payload.get("fingerprint") or build_settings_fingerprint(payload),
        "key": key,
        "value": payload.get("value"),
        "scope": payload.get("scope") or "user",
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "deleted_at": payload.get("deleted_at"),
        "version": payload.get("version"),
        "device_id": payload.get("device_id"),
        "sync_state": payload.get("sync_state"),
        "last_synced_at": payload.get("last_synced_at"),
    }
