from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

logger = logging.getLogger("siyi.data_protection_service")

# 哨兵：区分「load_json 返回 None / 空容器」与「SQLite 里根本没有这个键」。
_MISSING = object()


class DataProtectionService:
    """本地配置与关键数据备份、恢复、重置服务。

    P1-6 / P2-18 之后底层数据移到 SQLite (``local_storage.db``)，但备份
    仍保持「每键一个 JSON 文件」的可读快照形态：
    * :meth:`backup_now` 通过 ``data_store.load_json(key, _MISSING)`` 取值后
      写出 ``<snapshot>/<namespace>/<key>.json``；
    * :meth:`restore_namespace` / :meth:`restore_snapshot_by_scope` 读快照
      JSON 后调用 ``data_store.save_json(key, payload)`` 回灌进 SQLite。
    这样诊断脚本仍能直接看快照文件，恢复时一次写回 SQLite。
    """

    BACKUP_ROOT_NAME = "backups"
    SNAPSHOT_MANIFEST_FILE = "snapshot_manifest.json"
    NAMESPACE_RULES = {
        "settings": {"settings"},
        "tasks": {"tasks", "cloud_tasks", "remote_snapshot_tasks", "reminders"},
        "notes": {"notes", "cloud_notes", "remote_snapshot_notes", "folders"},
        "translations": {"translations", "cloud_translations", "remote_snapshot_translations"},
        "vocabulary": {"words", "cloud_words", "remote_snapshot_words", "wordbooks", "cloud_wordbooks", "remote_snapshot_wordbooks"},
        "news": {"news", "cloud_news", "remote_snapshot_news", "news_cache", "news_history"},
        "workbench": {"inbox_entries", "improvement_proposals"},
    }

    def __init__(self, data_store):
        self.data_store = data_store

    # ── 路径与命名 ────────────────────────────────────────────────────

    def _backup_root(self):
        root = os.path.join(self.data_store.base_dir, self.BACKUP_ROOT_NAME)
        os.makedirs(root, exist_ok=True)
        return root

    def _snapshot_dir(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self._backup_root(), f"snapshot_{stamp}")
        os.makedirs(path, exist_ok=True)
        return path

    def _resolve_namespace(self, key):
        for namespace, keys in self.NAMESPACE_RULES.items():
            if key in keys:
                return namespace
        return "misc"

    def _snapshot_file_for_key(self, snapshot_dir, key, live_path):
        """优先查找 ``<snapshot>/<namespace>/<basename>``，回落到旧版平铺
        ``<snapshot>/<basename>``，最后返回新版路径作为「应在」位置。"""
        namespace = self._resolve_namespace(key)
        namespace_path = os.path.join(snapshot_dir, namespace, os.path.basename(live_path))
        if os.path.isfile(namespace_path):
            return namespace_path
        legacy_path = os.path.join(snapshot_dir, os.path.basename(live_path))
        if os.path.isfile(legacy_path):
            return legacy_path
        return namespace_path

    # ── 读 data_store / 读写快照 JSON ────────────────────────────────

    def _read_key_payload(self, key):
        """通过 ``load_json(key, _MISSING)`` 拿底层 SQLite 中的真实数据；
        如果该键根本未写过，返回 ``_MISSING`` 让上层跳过。"""
        try:
            data = self.data_store.load_json(key, _MISSING)
        except Exception:
            logger.debug("backup: 读取 %s 失败", key, exc_info=True)
            return _MISSING
        return data

    @staticmethod
    def _write_snapshot_file(target_path, payload):
        parent = os.path.dirname(target_path) or "."
        os.makedirs(parent, exist_ok=True)
        tmp = f"{target_path}.tmp"
        with open(tmp, "w", encoding="utf-8") as file_handle:
            json.dump(payload, file_handle, ensure_ascii=False, indent=2)
        os.replace(tmp, target_path)

    @staticmethod
    def _read_snapshot_file(snapshot_path):
        try:
            with open(snapshot_path, encoding="utf-8") as file_handle:
                return json.load(file_handle)
        except (OSError, json.JSONDecodeError):
            logger.debug("restore: 读取快照 %s 失败", snapshot_path, exc_info=True)
            return _MISSING

    # ── backup ───────────────────────────────────────────────────────

    def backup_now(self):
        snapshot = self._snapshot_dir()
        copied = 0
        manifest = {
            "created_at": datetime.now(UTC).isoformat(),
            "schema_version": 2,
            "namespaces": {},
            "files": {},
        }
        for key, src_path in self.data_store.paths.items():
            if key == self.BACKUP_ROOT_NAME:
                continue
            payload = self._read_key_payload(key)
            if payload is _MISSING:
                continue
            namespace = self._resolve_namespace(key)
            target_path = os.path.join(snapshot, namespace, os.path.basename(src_path))
            try:
                self._write_snapshot_file(target_path, payload)
            except (OSError, TypeError, ValueError):
                logger.debug("backup: 写入快照 %s 失败", target_path, exc_info=True)
                continue
            copied += 1
            manifest["files"][key] = {
                "namespace": namespace,
                "file_name": os.path.basename(src_path),
            }
            manifest["namespaces"].setdefault(namespace, []).append(key)

        with open(os.path.join(snapshot, self.SNAPSHOT_MANIFEST_FILE), "w", encoding="utf-8") as file_handle:
            json.dump(manifest, file_handle, ensure_ascii=False, indent=2)

        latest_marker = os.path.join(self._backup_root(), "latest_snapshot.txt")
        with open(latest_marker, "w", encoding="utf-8") as file_handle:
            file_handle.write(snapshot)
        return {"snapshot_dir": snapshot, "file_count": copied}

    def get_latest_snapshot_dir(self):
        marker = os.path.join(self._backup_root(), "latest_snapshot.txt")
        if not os.path.isfile(marker):
            return ""
        with open(marker, encoding="utf-8") as file_handle:
            value = file_handle.read().strip()
        return value if value and os.path.isdir(value) else ""

    # ── restore ──────────────────────────────────────────────────────

    def restore_latest_snapshot(self):
        return self.restore_snapshot_by_scope(include_settings=True, include_data=True)

    def preview_latest_snapshot_diff(self):
        """快照与当前 SQLite 数据对比；live_path 仅用于显示，文件系统不再持久。"""
        snapshot_dir = self.get_latest_snapshot_dir()
        if not snapshot_dir:
            return {"snapshot_dir": "", "items": []}
        items = []
        for key, live_path in self.data_store.paths.items():
            snapshot_path = self._snapshot_file_for_key(snapshot_dir, key, live_path)
            in_snapshot = os.path.isfile(snapshot_path)
            live_payload = self._read_key_payload(key)
            in_live = live_payload is not _MISSING
            if not in_snapshot and not in_live:
                continue
            category = self._resolve_namespace(key)
            if in_snapshot and in_live:
                snapshot_payload = self._read_snapshot_file(snapshot_path)
                status = "same" if snapshot_payload == live_payload else "update"
            elif in_snapshot and not in_live:
                status = "create"
            else:
                status = "missing-in-snapshot"
            items.append(
                {
                    "key": key,
                    "category": category,
                    "status": status,
                    "live_path": live_path,
                    "snapshot_path": snapshot_path,
                }
            )
        return {"snapshot_dir": snapshot_dir, "items": items}

    def restore_snapshot_by_scope(self, include_settings=True, include_data=True):
        snapshot_dir = self.get_latest_snapshot_dir()
        if not snapshot_dir:
            return {"restored": 0, "snapshot_dir": "", "settings_restored": 0, "data_restored": 0}
        restored = 0
        settings_restored = 0
        data_restored = 0
        for key, dst_path in self.data_store.paths.items():
            category = "settings" if key == "settings" else "data"
            if category == "settings" and not include_settings:
                continue
            if category == "data" and not include_data:
                continue
            src_path = self._snapshot_file_for_key(snapshot_dir, key, dst_path)
            if not os.path.isfile(src_path):
                continue
            payload = self._read_snapshot_file(src_path)
            if payload is _MISSING:
                continue
            try:
                self.data_store.save_json(key, payload)
            except Exception:
                logger.debug("restore: 写回 %s 失败", key, exc_info=True)
                continue
            restored += 1
            if category == "settings":
                settings_restored += 1
            else:
                data_restored += 1
        return {
            "restored": restored,
            "snapshot_dir": snapshot_dir,
            "settings_restored": settings_restored,
            "data_restored": data_restored,
        }

    def restore_namespace(self, namespace):
        snapshot_dir = self.get_latest_snapshot_dir()
        normalized_namespace = str(namespace or "").strip()
        if not snapshot_dir or not normalized_namespace:
            return {"restored": 0, "snapshot_dir": snapshot_dir}

        restored = 0
        for key, dst_path in self.data_store.paths.items():
            if self._resolve_namespace(key) != normalized_namespace:
                continue
            src_path = self._snapshot_file_for_key(snapshot_dir, key, dst_path)
            if not os.path.isfile(src_path):
                continue
            payload = self._read_snapshot_file(src_path)
            if payload is _MISSING:
                continue
            try:
                self.data_store.save_json(key, payload)
            except Exception:
                logger.debug("restore: 写回 %s 失败", key, exc_info=True)
                continue
            restored += 1
        return {"restored": restored, "snapshot_dir": snapshot_dir, "namespace": normalized_namespace}

    # ── reset ────────────────────────────────────────────────────────

    def safe_reset(self):
        """安全重置：仅重置 settings，保留业务数据。"""
        self.data_store.save_json("settings", {})
        return {"mode": "safe"}

    def full_reset(self):
        """完全重置：清空除备份目录外的全部数据。

        SQLite 化之后无法逐文件 ``os.remove``，改成对每个键写入空容器
        （list / dict）。``settings`` 也归零。"""
        default_for_key = getattr(self.data_store, "_default_for_key", lambda _k: None)
        for key in self.data_store.paths:
            if key == self.BACKUP_ROOT_NAME:
                continue
            try:
                self.data_store.save_json(key, default_for_key(key))
            except Exception:
                logger.debug("full_reset: 重置 %s 失败", key, exc_info=True)
        return {"mode": "full"}
