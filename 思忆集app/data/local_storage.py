"""本地数据存储（P1-6 / P2-18：JSON+.lock+.bak 三件套 → SQLite WAL）。

历史背景
========
最初版每个键一个 JSON 文件 + 同目录 ``.lock`` 文件锁 + ``.bak`` / ``.bak.1`` 备份
轮转 + post-write JSON 解析校验。这套机制可靠但 IO 重：单次 ``save_json``
要 mkstemp / rename / copy2 多次系统调用，并发场景下文件锁还会成为瓶颈。

新实现把 80+ 个键统一搬进单个 SQLite 数据库 ``local_storage.db``（``kv_store``
单表），借 WAL + ``BEGIN IMMEDIATE`` 拿到比旧版更强的原子性 + 更低延迟。
公共 API 完全不变：100+ 个 ``load_json / save_json / update_json`` 调用方
一行不动。

兼容性
======
* ``paths[key]`` 仍返回 ``<base_dir>/<key>.json`` 路径字符串。文件实际上
  不再被创建/读取（首启迁移后），但路径计算保留——目前唯一的真实消费者是
  ``repositories/_chat_session.py:48``，它取 ``paths["chat_sessions"]`` 的
  父目录作为分片目录根。
* ``_storage_meta.json`` 文件仍写一份精简版，因为 ``memory/migration.py``
  会直读其中的 ``_memory_migration_done_v1`` 标志。
* 首启时把现有的 ``<key>.json`` 内容一次性灌进 SQLite，并把旧文件改名为
  ``<key>.json.legacy.<ts>`` 留 30 天回滚保险。
* ``settings`` 这个键额外镜像写一份明文 ``settings.json``，供
  ``claudejiaoben/diag/*.py`` 等诊断脚本继续直读。
"""

from __future__ import annotations

import contextlib
import copy
import glob
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

try:
    from PyQt6.QtCore import QDateTime, Qt
except Exception:
    QDateTime = None  # type: ignore[assignment]
    Qt = None  # type: ignore[assignment]

from data._atomic_json import atomic_write_json, quarantine_corrupt_file, sweep_orphan_tmp
from data.sqlite_store import SqliteKVStore


logger = logging.getLogger("siyi.local_storage")


class LocalDataStore:
    """本地存储门面：对外仍是 load_json / save_json / update_json + LRU 缓存，
    对内由 :class:`SqliteKVStore` 承载所有持久化职责。"""

    STORAGE_SCHEMA_VERSION = 1
    STORAGE_META_FILENAME = "_storage_meta.json"
    DB_FILENAME = "local_storage.db"

    _DICT_KEYS = {
        # 载体权威方向（波次④包B 文档化 · 蓝本 §1b，只加注释不改逻辑）：
        # - "settings"：载体⑤（B 栈第⑤事实载体）——SyncBridge 游标存在本键的
        #   cloud_sync_* 子键内（sync/cloud_sync_client.py::_save_cursor/_load_cursor），
        #   是 B 端到云栈游标的唯一真源。
        # - "integration_sync_cursors"：载体③——A 编排栈游标真源（读写口
        #   api/integration_hub.py get/upsert_sync_cursor，被 api/sync_orchestrator.py 消费）。
        # 两栈游标类型/语义不互通（B=秒 float，A=ISO 串/next_cursor），禁跨栈直接比较。
        "settings",
        "news_cache",
        "web_search_cache",
        "integration_oauth_tokens",
        "integration_sync_cursors",
        "assistant_memory",
        "assistant_memory_index",
        "assistant_memory_schedules",
        "assistant_memory_schedules_settings",
        "assistant_memory_co_access",
        "supertag_groups",
        "assistant_rules",
        "permission_quotas",
        "assistant_user_profile",
        "cross_device_sessions",
        "supertag_structured_data",
        "behavior_memory_checkpoint",
    }
    _LIST_KEYS = {
        # 载体权威方向（波次④包B 文档化 · 蓝本 §1b 载体④）：下列 6 个
        # remote_snapshot_* 键 = 上次拉取的远端整表**派生缓存**，非真源；
        # 生产写方仅未接线的 sync/services.py（蓝本实测），而读方
        # find_remote_snapshot_* 在 orchestrator 合并链路消费——读写闭合性
        # 存疑是已知现状（蓝本 §1b 尾注），丢失可重建，不得当真源依赖。
        "tasks",
        "cloud_tasks",
        "remote_snapshot_tasks",
        "notes",
        "folders",
        "cloud_notes",
        "remote_snapshot_notes",
        "translations",
        "cloud_translations",
        "remote_snapshot_translations",
        "words",
        "cloud_words",
        "remote_snapshot_words",
        "wordbooks",
        "cloud_wordbooks",
        "remote_snapshot_wordbooks",
        "news",
        "cloud_news",
        "remote_snapshot_news",
        "news_history",
        "reminders",
        "inbox_entries",
        "improvement_proposals",
        "integration_conflicts",
        "integration_audit_logs",
        "assistant_decisions",
        "assistant_events",
        "assistant_agent_tasks",
        "assistant_usage_stats",
        "assistant_execution_runs",
        "assistant_memory_pending",
        "assistant_memory_conflicts",
        "assistant_memory_extraction_log",
        "assistant_memory_links",
        "assistant_decision_memory_history",
        "assistant_imported_memory_md",
        "assistant_session_summaries",
        "supertag_schemas",
        "supertag_hierarchy",
        "structured_memory_entries",
        "cron_jobs",
        "mcp_servers",
        "mcp_tool_catalog",
        "subagent_runs",
        "permission_matrix",
        "audit_trail",
        "rule_center_rules",
        "assistant_codex_runs",
        "assistant_inbox_items",
        "assistant_checkpoints",
        "assistant_execution_plans",
        "assistant_budget_usage",
        "assistant_execution_history",
        "assistant_audit_log",
        "cost_tracker_records",
        "workflows",
        "device_registry",
        "supertag_auto_tag_rules",
        "chat_sessions",
    }

    # ``save_json`` 时额外镜像写明文 JSON 的键：保留供诊断脚本直读
    _MIRROR_TO_FILE = {"settings"}

    def __init__(self, base_dir: str | None = None, event_bus: Any = None):
        # V2.1 批次1#4：与 conf/credential 等子服务一致，honor SIYI_DATA_DIR，
        # 使集成测试 AUTOSTART 临时数据目录真正隔离（token 也会落在临时目录）。
        data_dir_env = os.environ.get("SIYI_DATA_DIR", "").strip()
        resolved_base_dir = base_dir or data_dir_env or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(resolved_base_dir, exist_ok=True)
        self.base_dir = resolved_base_dir
        # P2-3（2026-09-18）：启动期清扫本目录下的孤儿原子写临时文件。
        # 动机：2026-09-18 实测 data/ 残留 45 个（43 个 .ai_cache_*.tmp 各约 213 KB
        # + 2 个 5 月的 siyi-*.json.tmp），全是硬杀 / 清理被拒留下的垃圾，且
        # **没有任何启动自愈机制**——旧测试 test_no_startup_sweep_of_stale_tmp
        # 正是把"无清扫"这一缺陷固化成了期望（本批同批反转）。
        # 边界：只清已知原子写命名（见 data/_atomic_json 的「命名与清扫必须同处」），
        # 年龄门槛 1 天，故不会碰到同目录并发写者的在途文件。
        # 失败不阻塞启动：清扫是卫生动作，不是启动前置条件。
        try:
            sweep_orphan_tmp(resolved_base_dir)
        except Exception:  # noqa: BLE001 —— 清扫异常绝不能让后端起不来
            logger.warning("启动期孤儿 tmp 清扫失败（忽略，不影响启动）", exc_info=True)
        self.meta_path = os.path.join(resolved_base_dir, self.STORAGE_META_FILENAME)
        self.db_path = os.path.join(resolved_base_dir, self.DB_FILENAME)
        self.paths = self._build_paths(resolved_base_dir)

        # 容错三件套·③：meta 损坏且隔离失败时为 True，封锁 _save_storage_meta，
        # 禁止覆写损坏现场（禁 warning-only 继续）。
        self._meta_write_blocked = False

        # 底层 SQLite 存储（#29: 透传 event_bus，使锁等待告警可达通知管道）
        self._sqlite = SqliteKVStore(self.db_path, event_bus=event_bus)

        # 首启迁移：把已存在的 <key>.json 文件搬进 SQLite，旧文件改名 .legacy.<ts>
        self._legacy_migrate_once()

        # storage_meta：保留极简版（兼容 memory/migration.py 的直读）
        self._ensure_storage_schema()

        # LRU 缓存：与旧实现完全一致的语义（包括 128 条上限）。
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._CACHE_MAX_SIZE = 128
        # M11（2026-08-29）：缓存会被事件循环协程与 to_thread 工作线程并发访问
        # （如 settings 的 to_thread 读），OrderedDict 无锁会在 move_to_end /
        # popitem 时抛 "mutated during iteration" 或返回脏数据。用可重入锁保护
        # 结构操作；返回给调用方的仍是同一对象，保持既有的对象身份语义。
        self._cache_lock = threading.RLock()

    # ── 路径表（虚拟）─────────────────────────────────────────────────

    @staticmethod
    def _build_paths(base_dir: str) -> dict[str, str]:
        """返回 ``key -> <base_dir>/<filename>.json`` 的映射。

        新实现下这些文件实际并不被读写，但保留是为了：
        1. ``repositories/_chat_session.py`` 用 ``paths["chat_sessions"]`` 取目录名；
        2. 测试和诊断脚本仍能拿到合法路径字符串；
        3. 首启迁移要按这个表去扫描遗留 JSON 文件。
        """
        filename_overrides = {"news": "news_records.json"}
        keys = (
            "tasks",
            "cloud_tasks",
            "remote_snapshot_tasks",
            "settings",
            "notes",
            "folders",
            "cloud_notes",
            "remote_snapshot_notes",
            "translations",
            "cloud_translations",
            "remote_snapshot_translations",
            "words",
            "cloud_words",
            "remote_snapshot_words",
            "wordbooks",
            "cloud_wordbooks",
            "remote_snapshot_wordbooks",
            "news",
            "cloud_news",
            "remote_snapshot_news",
            "news_cache",
            "web_search_cache",
            "news_history",
            "info_cache",
            "reminders",
            "inbox_entries",
            "improvement_proposals",
            "integration_oauth_tokens",
            "integration_sync_cursors",
            "integration_conflicts",
            "integration_audit_logs",
            "assistant_memory",
            "assistant_memory_index",
            "assistant_decisions",
            "assistant_rules",
            "assistant_events",
            "assistant_agent_tasks",
            "assistant_usage_stats",
            "assistant_execution_runs",
            "cron_jobs",
            "mcp_servers",
            "mcp_tool_catalog",
            "subagent_runs",
            "info_items",
            "permission_matrix",
            "permission_quotas",
            "audit_trail",
            "rule_center_rules",
            "assistant_codex_runs",
            "assistant_user_profile",
            "assistant_inbox_items",
            "chat_conversations",
            "cost_tracker_records",
            "cross_device_sessions",
            "assistant_checkpoints",
            "assistant_execution_plans",
            "assistant_budget_usage",
            "assistant_execution_history",
            "assistant_audit_log",
            "workflows",
            "device_registry",
            "supertag_auto_tag_rules",
            "assistant_memory_pending",
            "assistant_memory_conflicts",
            "assistant_memory_extraction_log",
            "assistant_memory_schedules",
            "assistant_memory_schedules_settings",
            "assistant_memory_links",
            "assistant_memory_co_access",
            "assistant_decision_memory_history",
            "assistant_imported_memory_md",
            "assistant_session_summaries",
            "supertag_schemas",
            "supertag_hierarchy",
            "supertag_groups",
            "structured_memory_entries",
            "supertag_structured_data",
            "chat_sessions",
            "behavior_memory_checkpoint",
            "extension_registry",
        )
        return {k: os.path.join(base_dir, filename_overrides.get(k, f"{k}.json")) for k in keys}

    def _default_for_key(self, key: str):
        if key in self._DICT_KEYS:
            return {}
        if key in self._LIST_KEYS:
            return []
        return None

    # ── 首启迁移：JSON → SQLite ────────────────────────────────────────

    def _legacy_migrate_once(self) -> None:
        """对每个 ``paths[key]`` 如果文件存在且 SQLite 里没记录，则导入。

        导入成功后旧 ``.json`` 文件改名为 ``<key>.json.legacy.<ts>``。
        损坏或非 JSON 的文件改名为 ``<key>.json.corrupted.<ts>``，绝不静默丢弃。
        """
        timestamp = int(time.time())
        imported: dict[str, str] = {}
        for key, path in self.paths.items():
            if not os.path.isfile(path):
                continue
            if self._sqlite.has(key):
                # 已经迁移过：旧文件若还在，再改名一次防止下次重复尝试
                self._rename_legacy(path, timestamp)
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    payload = json.load(fh)
            except Exception:
                # 损坏文件：保留物证，不进 SQLite
                corrupted = f"{path}.corrupted.{timestamp}"
                try:
                    os.replace(path, corrupted)
                    logger.error(
                        "首启迁移：%s 解析失败，已保留至 %s 等待手工处理",
                        key,
                        corrupted,
                    )
                except OSError as exc:
                    logger.error("首启迁移：%s 解析失败且改名失败：%s", key, exc)
                continue

            # 形状强制（与旧 _migrate_key 行为对齐）：若顶层结构不匹配
            # _DICT_KEYS / _LIST_KEYS 的期望，跳过导入 → SQLite 维持空白，
            # 下次 load_json 自然返回 default。旧文件仍改名为 .legacy。
            expected_default = self._default_for_key(key)
            if isinstance(expected_default, list) and not isinstance(payload, list):
                logger.warning("首启迁移：%s 顶层不是 list，已重置", key)
                self._rename_legacy(path, timestamp)
                continue
            if isinstance(expected_default, dict) and not isinstance(payload, dict):
                logger.warning("首启迁移：%s 顶层不是 dict，已重置", key)
                self._rename_legacy(path, timestamp)
                continue

            try:
                imported[key] = json.dumps(payload, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                logger.error("首启迁移：%s 序列化失败：%s", key, exc)

        if imported:
            try:
                self._sqlite.set_many(imported)
                logger.info("首启迁移：已导入 %d 个键到 SQLite", len(imported))
            except Exception as exc:
                logger.exception("首启迁移：批量写入 SQLite 失败：%s", exc)
                # 失败时不删旧文件——让下次启动重试
                return
            # 全部成功落库后再改名旧文件，保证迁移半路崩溃时下次能重试
            for key in imported:
                self._rename_legacy(self.paths[key], timestamp)

    @staticmethod
    def _rename_legacy(path: str, timestamp: int) -> None:
        legacy = f"{path}.legacy.{timestamp}"
        try:
            os.replace(path, legacy)
        except OSError as exc:
            logger.debug("无法改名旧 JSON 文件 %s：%s", path, exc)

    # ── meta（兼容 memory/migration.py）────────────────────────────────

    def _load_storage_meta(self) -> dict[str, Any]:
        default_meta: dict[str, Any] = {
            "storage_schema_version": self.STORAGE_SCHEMA_VERSION,
            "versions": {},
            "updated_at": None,
        }
        if not os.path.exists(self.meta_path):
            return default_meta
        try:
            with open(self.meta_path, encoding="utf-8") as file_handle:
                payload = json.load(file_handle)
            if not isinstance(payload, dict):
                raise ValueError(f"storage meta 顶层不是 dict（{type(payload).__name__}）")
        except Exception as exc:
            # 容错三件套：损坏 meta 先 os.rename 为 <name>.corrupt-<ts> 保全现场（①）；
            # 改名成功以空默认值续用、功能不瘫（②）；改名失败则封锁 meta 回写
            # 并 error 告警（③），禁止 warning-only 覆写损坏现场。
            quarantined = quarantine_corrupt_file(self.meta_path)
            if quarantined:
                logger.error("_storage_meta.json 损坏，已隔离保全至 %s：%s", quarantined, exc)
                self._meta_write_blocked = False
            else:
                logger.error("_storage_meta.json 损坏且隔离失败，已封锁 meta 回写以保留现场：%s", exc)
                self._meta_write_blocked = True
            return default_meta
        payload.setdefault("storage_schema_version", self.STORAGE_SCHEMA_VERSION)
        payload.setdefault("versions", {})
        payload.setdefault("updated_at", None)
        self._meta_write_blocked = False
        return payload

    def _save_storage_meta(self, payload: dict[str, Any]) -> None:
        if self._meta_write_blocked:
            msg = "storage meta 损坏现场未能隔离：拒绝覆写 _storage_meta.json（待人工处理）"
            logger.error(msg)
            raise RuntimeError(msg)
        payload = dict(payload or {})
        payload["storage_schema_version"] = self.STORAGE_SCHEMA_VERSION
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_plain_json(self.meta_path, payload)

    def _ensure_storage_schema(self) -> None:
        meta = self._load_storage_meta()
        versions = meta.setdefault("versions", {})
        changed = False
        for key in self.paths:
            current_version = int(versions.get(key, 0) or 0)
            if current_version >= self.STORAGE_SCHEMA_VERSION:
                continue
            if self._migrate_key(key, current_version, self.STORAGE_SCHEMA_VERSION):
                changed = True
            versions[key] = self.STORAGE_SCHEMA_VERSION
            changed = True
        if meta.get("storage_schema_version") != self.STORAGE_SCHEMA_VERSION:
            changed = True
        if changed:
            try:
                self._save_storage_meta(meta)
            except RuntimeError:
                # ③ 拒写已告警；跳过 meta 落盘、不阻断启动（损坏现场原地保留）
                logger.error("storage meta 持久化被拒绝：损坏现场隔离失败，本次启动跳过 meta 写入")

    def _migrate_key(self, key: str, from_version: int, to_version: int) -> bool:
        """保留 ``settings`` 这个键的 v0→v1 业务级迁移钩子；其它键不再走形状强制。"""
        if key == "settings":

            class _SettingsRepositoryShim:
                def __init__(self, data_store: "LocalDataStore") -> None:
                    self.data_store = data_store

            try:
                from core.settings_schema import migrate_settings_repository

                migrate_settings_repository(_SettingsRepositoryShim(self))
                return True
            except Exception:
                return False
        return False

    # ── 公共 API ─────────────────────────────────────────────────────────

    def load_json(self, key: str, default: Any) -> Any:
        # LRU 命中：与旧实现一致——直接返回缓存对象（包含同对象身份语义）
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            # 缓存未命中：释放锁后再走 SQLite（IO 不该持锁），命中检查与写入
            # 之间允许并发，但写入时重新加锁并二次确认，避免覆盖他人已填的条目。

        text = self._sqlite.get(key, None)
        if text is None:
            data: Any = copy.deepcopy(default)
        else:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # 容错三件套·①（DB 变体）：损坏值落盘保全现场，读路径再以
                # 空默认值续用（②）。保全失败会 error 告警，且 update_json
                # 在覆写损坏值前会再次尝试保全、失败即拒写。
                preserved = self._preserve_corrupt_blob(key, text)
                if preserved:
                    logger.error("SQLite 中 %s 反序列化失败，现场已保全至 %s，返回默认值", key, preserved, exc_info=True)
                else:
                    logger.error("SQLite 中 %s 反序列化失败且现场保全失败，返回默认值", key, exc_info=True)
                data = copy.deepcopy(default)

        # 已注册键的缺省形状兜底：显式 default=None 时，若键缺失或存的是
        # JSON null，则按注册默认值返回（list 键→[]、dict 键→{}）。保证
        # "从未写入" 与 "SQLite 存 null" 读到一致的稳定形状，事务回滚等
        # 场景因此能把缺失键恢复到注册默认值而非 None。
        if data is None and default is None:
            registered_default = self._default_for_key(key)
            if registered_default is not None:
                data = copy.deepcopy(registered_default)

        with self._cache_lock:
            # 二次确认：并发下可能已被其他线程填入，保留先到者以免对象身份跳变
            existing = self._cache.get(key)
            if existing is not None:
                self._cache.move_to_end(key)
                return existing
            self._cache[key] = data
            if len(self._cache) > self._CACHE_MAX_SIZE:
                self._cache.popitem(last=False)
        return data

    def health_check(self) -> dict[str, Any]:
        """轻量健康探针（每日 cron `system.memory_health` 用，2026-09-13 补齐）。

        cron_actions 的巡检 handler 调用本方法——此前缺失导致每日巡检必失败。
        只做廉价检查：线程共享 SQLite 连接可查、PRAGMA quick_check、表计数、
        kv 写读删一步到位（探测键即删）。注意 `_connect()` 返回**线程共享**连接，
        绝不 close。返回 {status, checks, errors}，不抛异常。
        """
        checks: dict[str, Any] = {}
        errors: list[str] = []
        try:
            conn = self._sqlite._connect()  # 线程共享连接：只查不关
            conn.execute("SELECT 1").fetchone()
            checks["sqlite_queryable"] = True
            quick = conn.execute("PRAGMA quick_check").fetchone()
            checks["integrity"] = bool(quick) and quick[0] == "ok"
            if not checks["integrity"]:
                errors.append("quick_check failed")
            checks["tables"] = len(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall())
        except Exception as exc:  # noqa: BLE001 —— 巡检只报告不抛
            checks["sqlite_queryable"] = False
            errors.append(f"sqlite: {exc}")
        try:
            probe = ".health_probe_kv"
            self.save_json(probe, {"ts": 1})
            self.load_json(probe, None)
            self._sqlite.delete(probe)
            checks["kv_roundtrip"] = True
        except Exception as exc:  # noqa: BLE001
            checks["kv_roundtrip"] = False
            errors.append(f"kv_roundtrip: {exc}")
        return {"status": "ok" if not errors else "degraded", "checks": checks, "errors": errors[:5]}

    def save_json(self, key: str, data: Any) -> None:
        try:
            text = json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError):
            logger.error("save_json: %s 序列化失败", key, exc_info=True)
            raise
        self._sqlite.set(key, text)

        # 镜像写：仅 settings（供诊断脚本直读）
        if key in self._MIRROR_TO_FILE:
            try:
                self._write_plain_json(self.paths.get(key, os.path.join(self.base_dir, f"{key}.json")), data)
            except Exception as exc:
                logger.debug("mirror write for %s failed: %s", key, exc)

        # 写后失效缓存（与旧实现保持一致：相同 path 的所有别名都失效）
        path = self.paths.get(key)
        with self._cache_lock:
            if path is None:
                self._cache.pop(key, None)
                return
            for k, p in self.paths.items():
                if p == path:
                    self._cache.pop(k, None)

    def update_json(self, key: str, default: Any, updater: Callable[[Any], Any]) -> Any:
        """原子 read-modify-write。

        与旧版差异：现在用 SQLite ``BEGIN IMMEDIATE`` 事务包裹整个流程，
        updater 抛异常时事务回滚，磁盘值绝不会处于中间态。
        """
        seed_text = json.dumps(copy.deepcopy(default), ensure_ascii=False)

        def _wrap(current_text: str) -> str:
            if not current_text:
                current = copy.deepcopy(default)
            else:
                try:
                    current = json.loads(current_text)
                except json.JSONDecodeError as exc:
                    # 容错三件套：覆写损坏值之前必须先落盘保全现场（①）；
                    # 保全成功 → 以空默认值继续（②）；保全失败 → 抛异常触发
                    # 事务回滚，拒绝对损坏值的覆盖写并 error 告警（③）。
                    preserved = self._preserve_corrupt_blob(key, current_text)
                    if preserved is None:
                        logger.error("update_json: %s SQLite 值损坏且现场保全失败，拒绝覆写（事务回滚）", key, exc_info=True)
                        raise RuntimeError(f"update_json 拒绝覆写损坏键 {key}：现场保全失败") from exc
                    logger.error("update_json: %s SQLite 值损坏，现场已保全至 %s，以空默认值继续", key, preserved)
                    current = copy.deepcopy(default)
            updated = updater(current)
            return json.dumps(updated, ensure_ascii=False)

        new_text = self._sqlite.update(key, seed_text, _wrap)

        # 失效缓存
        path = self.paths.get(key)
        if path is not None:
            for k, p in self.paths.items():
                if p == path:
                    self._cache.pop(k, None)
        else:
            self._cache.pop(key, None)

        try:
            return json.loads(new_text)
        except json.JSONDecodeError:
            logger.error("update_json: %s 写回结果反序列化失败（存储层异常，需排查）", key, exc_info=True)
            return copy.deepcopy(default)

    def invalidate_cache(self, key: str) -> None:
        self._cache.pop(key, None)

    def append_to_list(
        self,
        key: str,
        item: Any,
        cap: int | None = None,
    ) -> int:
        """增量追加到列表型键（T2.2 写放大治理，核心 API）。

        与 ``update_json(key, [], lambda cur: (cur + [item])[:cap])`` 语义等价，
        但**不在 Python 端整体反序列化/再序列化整个列表**——追加在 SQLite 层
        经 ``json_insert`` 原地完成，把单次写放大从 O(列表长度) 降到 O(1) 级。

        参数：
            key: 列表型键名（须存 JSON 数组）。
            item: 要追加的单个元素（任意可 json 序列化对象）。
            cap: 上限，超长丢弃最早若干条（在 SQL 层完成，无 Python 整体 dump）。

        返回：追加后数组的最终长度。

        缓存一致性：内存 LRU 缓存中若有该 key 的列表，则同步原地 append（并
        按需截断），保证 read-after-write 立即可见；缓存未命中则不预填（下次
        load_json 会从 SQLite 读到最新值）。
        """
        try:
            item_text = json.dumps(item, ensure_ascii=False)
        except (TypeError, ValueError):
            logger.error("append_to_list: %s 元素序列化失败", key, exc_info=True)
            raise
        final_len = self._sqlite.append_to_list(key, item_text, cap)
        # 缓存同步：命中则原地追加，保持 read-after-write 一致
        cached = self._cache.get(key)
        if isinstance(cached, list):
            cached.append(copy.deepcopy(item))
            if cap is not None and cap > 0 and len(cached) > cap:
                del cached[: len(cached) - cap]
        return final_len

    def clear_cache(self) -> None:
        self._cache.clear()

    def close(self) -> None:
        """关闭底层 SQLite 连接（释放 Windows 文件句柄）。"""
        sqlite = getattr(self, "_sqlite", None)
        if sqlite is not None:
            sqlite.close_all()

    def __del__(self) -> None:  # pragma: no cover - GC 兜底
        try:
            self.close()
        except Exception as exc:
            # 不在 GC 路径做重逻辑，但记录异常以便诊断 Windows 文件句柄泄漏
            logging.getLogger("siyi.data.local_storage").debug("LocalDataStore close failed during GC: %s", exc, exc_info=True)

    # ── 静态工具（旧版保留）────────────────────────────────────────────

    def next_id(self, items: list[dict[str, Any]]) -> int:
        if not items:
            return 1
        max_id = 0
        for item in items:
            raw = item.get("id", 0)
            try:
                max_id = max(max_id, int(raw))
            except (TypeError, ValueError):
                # 跳过非数字 id（如 "_daily_2026-06-06"）
                pass
        return max_id + 1

    def serialize_reminder_time(self, value):
        if value is None:
            return None
        if QDateTime and isinstance(value, QDateTime):
            if not value.isValid():
                return None
            return value.toString("yyyy-MM-dd HH:mm")
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M")
        if isinstance(value, str):
            return value
        return str(value)

    def deserialize_reminder_time(self, value):
        if value is None:
            return None
        if QDateTime and isinstance(value, QDateTime):
            return value if value.isValid() else None
        if isinstance(value, str) and QDateTime is not None:
            dt = QDateTime.fromString(value, "yyyy-MM-dd HH:mm")
            if not dt.isValid():
                dt = QDateTime.fromString(value, "yyyy-MM-ddTHH:mm:ss")
            if not dt.isValid() and Qt is not None:
                dt = QDateTime.fromString(value, Qt.DateFormat.ISODate)
            return dt if dt.isValid() else value
        return value

    @staticmethod
    def fts_search_items(items, query, fields, *, max_results=50, index_engine=None, index_threshold=500):
        """对 JSON 列表做简单多字段全文搜索，返回按相关度排序的结果。

        Args:
            items: 数据项列表（每项为 dict）。
            query: 用户搜索词，支持空格/中文逗号分词。
            fields: 参与搜索的字段名列表；title/name 字段命中时权重更高。
            max_results: 最多返回多少条，默认 50。
            index_engine: 可选的 LocalIndexEngine 实例。当 items 数量超过
                index_threshold 时，委托给 index_engine 做基于 FTS5 的 BM25
                搜索，避免线性扫描在大数据集上性能急剧下降。
            index_threshold: 触发委托的数据量阈值，默认 500。
        """
        if not query or not items:
            return []

        if index_engine is not None and len(items) >= index_threshold:
            try:
                hits = index_engine.search(query, limit=max_results)
                if hits:
                    id_set = {h[0] for h in hits}
                    return [it for it in items if it.get("id") in id_set][:max_results]
            except Exception as exc:
                logger.debug("FTS5 委托失败，回退到线性扫描: %s", exc)

        terms = [t.lower().strip() for t in re.split(r"[\s，,]+", query) if t.strip()]
        if not terms:
            return []
        priority_fields = {"title", "name"}
        scored = []
        for item in items:
            score = 0
            for field in fields:
                text = str(item.get(field) or "").lower()
                for term in terms:
                    if term in text:
                        score += 3 if field in priority_fields else 1
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda p: p[0], reverse=True)
        return [p[1] for p in scored[:max_results]]

    # ── 内部工具 ─────────────────────────────────────────────────────────

    def _preserve_corrupt_blob(self, key: str, text: str) -> str | None:
        """把 SQLite 中无法解析的损坏值落盘保全现场（容错三件套·①的 DB 变体）。

        SQLite 行无法 ``os.rename``，改写一份取证 wrapper 文件
        ``<base_dir>/<key>.json.corrupt-<ts>``（原始损坏文本存于 ``raw_text``）。
        同键已有隔离文件时直接复用，避免每次读都刷一个新现场。失败返回
        ``None``——调用方必须拒绝覆盖式写入并告警（③）。
        """
        safe_key = re.sub(r"[^\w.-]", "_", key)
        try:
            existing = glob.glob(os.path.join(self.base_dir, f"{safe_key}.json.corrupt-*"))
            if existing:
                return existing[0]
            target = os.path.join(self.base_dir, f"{safe_key}.json.corrupt-{int(time.time())}")
            atomic_write_json(
                target,
                {
                    "_quarantine": "local_storage.sqlite_blob",
                    "key": key,
                    "quarantined_at": datetime.now(timezone.utc).isoformat(),
                    "raw_text": text,
                },
            )
        except OSError:
            logger.error("SQLite 损坏值落盘保全失败（key=%s）", key, exc_info=True)
            return None
        return target

    @staticmethod
    def _write_plain_json(path: str, data: Any) -> None:
        """简单的明文 JSON 落盘，仅用于 ``_storage_meta.json`` 与
        ``settings`` 镜像。生产数据走 SQLite。

        原子写（临时文件 + fsync + os.replace 退避重试）由
        :func:`data._atomic_json.atomic_write_json` 统一保证，不再手写
        易撕裂的 ``open(path, "w")``。
        """
        atomic_write_json(path, data)

    # ── 测试便利（保留向后兼容字段读取）─────────────────────────────────

    @contextlib.contextmanager
    def _path_lock(self, path: str):
        """旧版的文件锁上下文管理器：保留为空操作，避免遗留调用方崩溃。"""
        yield
