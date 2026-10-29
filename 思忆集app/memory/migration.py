"""统一记忆系统数据迁移模块。

将旧文件（MEMORY.md、session_summaries.json、user_preferences.json）
迁移到 AssistantMemoryIndexService / UserProfileService 的新 JSON store 格式。

策略："读旧写新"——首次从旧文件读，写入新 JSON store，之后只读写新格式。
旧文件保留不删（重命名 .bak），一周后手动确认再删。

迁移安全：
- 绝不删除旧文件——只重命名为 .bak
- 每次迁移前备份
- 每个文件独立迁移，部分失败不影响已完成的部分
- 回退路径：如果迁移失败会记录日志，不阻塞应用启动
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data._atomic_json import atomic_write_json, quarantine_corrupt_file

logger = logging.getLogger("siyi.memory.migration")

_MIGRATION_DONE_KEY = "_memory_migration_done_v1"

_OLD_FILE_NAMES = (
    "MEMORY.md",
    "session_summaries.json",
    "user_preferences.json",
)

_NEW_SESSION_KEY = "assistant_session_summaries"


def run_migration(
    *,
    data_store,
    user_profile_service,
    memory_index_service,
    data_dir: str,
) -> dict[str, Any]:
    """执行一次性数据迁移（幂等）。

    如果迁移已完成（storage meta 中有标记），直接返回空结果。
    否则逐个检查旧文件，读取 → 转换 → 写入新服务 → 重命名 .bak。

    Args:
        data_store: LocalDataStore 实例
        user_profile_service: UserProfileService 实例
        memory_index_service: AssistantMemoryIndexService 实例（用于 rebuild）
        data_dir: data/ 目录的绝对路径

    Returns:
        迁移结果字典 {"files_migrated": [...], "errors": [...]}
    """
    result: dict[str, Any] = {
        "files_migrated": [],
        "errors": [],
        "already_done": False,
    }

    # ── 检查是否已完成 ──
    # 注意：_load_storage_meta 返回 (meta, meta_writable) 二元组（QUA-0 容错三件套）。
    # 此前这里漏了解包，导致每次启动都抛 'tuple' object has no attribute 'get'，
    # 记忆迁移永久空转（non-blocking 告警掩盖）。
    meta, meta_writable = _load_storage_meta(data_store)
    if meta.get(_MIGRATION_DONE_KEY):
        logger.info("Memory migration already completed, skipping.")
        result["already_done"] = True
        return result

    data_path = Path(data_dir)
    if not data_path.is_dir():
        logger.warning("Data directory does not exist: %s", data_path)
        return result

    # ── 逐个文件迁移 ──
    _migrate_user_preferences(data_path, user_profile_service, result)
    _migrate_session_summaries(data_path, data_store, result)
    _migrate_memory_md(data_path, data_store, memory_index_service, result)

    # ── 标记完成 ──
    # meta_writable=False（损坏现场隔离失败）时按容错契约③拒绝回写标记
    if meta_writable:
        meta[_MIGRATION_DONE_KEY] = True
        meta["_migration_completed_at"] = datetime.now(timezone.utc).isoformat()
        _save_storage_meta(data_store, meta)
    else:
        result["errors"].append("storage meta 不可写，跳过迁移完成标记（保留损坏现场）")

    if result["files_migrated"]:
        logger.info(
            "Memory migration completed: %d files migrated, %d errors",
            len(result["files_migrated"]),
            len(result["errors"]),
        )
    else:
        logger.info("Memory migration: no old files found to migrate.")

    return result


# ── 迁移函数 ─────────────────────────────────────────────


def _migrate_user_preferences(
    data_path: Path,
    user_profile_service,
    result: dict[str, Any],
) -> None:
    """将 user_preferences.json 迁移到 UserProfileService 的画像层。"""
    old_path = data_path / "user_preferences.json"
    if not old_path.is_file():
        return
    try:
        with open(old_path, encoding="utf-8") as f:
            old_data = json.load(f)
        if not isinstance(old_data, dict):
            logger.warning("user_preferences.json is not a dict, skipping")
            return

        # 映射旧 key 到 UserProfileService 各层
        layer_updates: dict[str, dict[str, Any]] = {}
        for key, value in old_data.items():
            if key.startswith("_"):
                continue
            mapped = _map_preference_key(key, value)
            if mapped is not None:
                layer, patch = mapped
                layer_updates.setdefault(layer, {}).update(patch)

        # 逐层写入
        for layer, patch in layer_updates.items():
            try:
                user_profile_service.update_layer(layer, patch)
            except Exception as exc:
                logger.warning("Failed to update profile layer %s: %s", layer, exc)

        _rename_to_bak(old_path)
        result["files_migrated"].append(str(old_path.name))
        logger.info("Migrated user_preferences.json -> user_profile (%d keys)", len(old_data))
    except Exception as exc:
        logger.error("Failed to migrate user_preferences.json: %s", exc)
        result["errors"].append(f"user_preferences.json: {exc}")


def _migrate_session_summaries(
    data_path: Path,
    data_store,
    result: dict[str, Any],
) -> None:
    """将 session_summaries.json 迁移到 data_store 的新 key。"""
    old_path = data_path / "session_summaries.json"
    if not old_path.is_file():
        return
    try:
        with open(old_path, encoding="utf-8") as f:
            summaries = json.load(f)
        if not isinstance(summaries, list):
            logger.warning("session_summaries.json is not a list, skipping")
            return

        # 标准化字段并写入新 key
        normalized = []
        for s in summaries:
            if not isinstance(s, dict):
                continue
            normalized.append({
                "session_id": str(s.get("session_id", "")),
                "summary": str(s.get("summary", "")),
                "topics": list(s.get("topics") or []),
                "created_at": str(s.get("created_at", "")),
            })

        if normalized:
            data_store.save_json(_NEW_SESSION_KEY, normalized)

        _rename_to_bak(old_path)
        result["files_migrated"].append(str(old_path.name))
        logger.info(
            "Migrated session_summaries.json -> %s (%d entries)",
            _NEW_SESSION_KEY,
            len(normalized),
        )
    except Exception as exc:
        logger.error("Failed to migrate session_summaries.json: %s", exc)
        result["errors"].append(f"session_summaries.json: {exc}")


def _migrate_memory_md(
    data_path: Path,
    data_store,
    memory_index_service,
    result: dict[str, Any],
) -> None:
    """将 MEMORY.md 的内容迁移到记忆索引条目。"""
    old_path = data_path / "MEMORY.md"
    if not old_path.is_file():
        return
    try:
        content = old_path.read_text(encoding="utf-8")
        if not content.strip():
            _rename_to_bak(old_path)
            result["files_migrated"].append(str(old_path.name))
            return

        # 解析 MEMORY.md 分区（按 ## 标题拆分）
        entries = _parse_memory_md(content)

        # 如果 memory_index_service 可用，将条目写入索引
        if memory_index_service is not None and entries:
            _store_memory_md_entries(entries, data_store, memory_index_service)

        _rename_to_bak(old_path)
        result["files_migrated"].append(str(old_path.name))
        logger.info(
            "Migrated MEMORY.md -> assistant_memory_index (%d entries)",
            len(entries),
        )
    except Exception as exc:
        logger.error("Failed to migrate MEMORY.md: %s", exc)
        result["errors"].append(f"MEMORY.md: {exc}")


# ── 辅助函数 ─────────────────────────────────────────────


def _map_preference_key(key: str, value: Any):
    """将旧 user_preferences key 映射到 UserProfileService 的层和字段。

    Returns:
        (layer_name, patch_dict) 或 None（跳过）
    """
    mapping = {
        "require_confirm_high_risk": ("permission", {"require_confirm_high_risk": bool(value)}),
        "allow_proactive_push": ("permission", {"allow_proactive_push": bool(value)}),
        "auto_execute_level": ("permission", {"auto_execute_level": str(value)}),
        "quiet_hours": ("permission", {"quiet_hours": list(value) if isinstance(value, list) else []}),
        "summary_style": ("output", {"summary_style": str(value)}),
        "language": ("output", {"language": str(value)}),
        "max_items_per_digest": ("output", {"max_items_per_digest": int(value) if isinstance(value, (int, float)) else 10}),
        "preferred_delivery": ("output", {"preferred_delivery": str(value)}),
        "focus_topics": ("long_term", {"focus_topics": list(value) if isinstance(value, list) else [str(value)]}),
        "common_sources": ("long_term", {"common_sources": list(value) if isinstance(value, list) else [str(value)]}),
        "current_focus": ("context", {"current_focus": str(value)}),
        "active_project": ("context", {"active_project": str(value)}),
    }
    return mapping.get(key)


def _parse_memory_md(content: str) -> list[dict[str, Any]]:
    """解析 MEMORY.md 格式，提取日期分区和条目。

    MEMORY.md 格式:
        ## 2024-01-01 12:00
        - preference text
        - another preference
    """
    entries: list[dict[str, Any]] = []
    current_date = ""
    for line in content.splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("## "):
            current_date = line_stripped[3:].strip()
        elif line_stripped.startswith("- ") and current_date:
            entries.append({
                "source_type": "memory_md",
                "title": f"长期记忆 {current_date}",
                "content": line_stripped[2:].strip(),
                "updated_at": current_date,
            })
    return entries


def _store_memory_md_entries(
    entries: list[dict[str, Any]],
    data_store,
    memory_index_service,
) -> None:
    """将 MEMORY.md 解析出的条目写入记忆索引和 data_store。"""
    # 写入 data_store 作为原始备份
    data_store.save_json("assistant_imported_memory_md", entries)

    # 通过 memory_index_service 重建索引以使条目可检索
    try:
        memory_index_service.rebuild_index()
    except Exception as exc:
        logger.warning("Memory index rebuild failed after migration: %s", exc)

    # 也尝试将条目直接追加到索引状态中
    try:
        DEFAULT_STATE: dict[str, Any] = {"entries": [], "built_at": None, "source_counts": {}}

        state = data_store.load_json("assistant_memory_index", DEFAULT_STATE)
        if not isinstance(state, dict):
            state = dict(DEFAULT_STATE)
        existing = list(state.get("entries") or [])
        existing_ids = {e.get("entry_id") for e in existing if e.get("entry_id")}
        new_count = 0
        for entry in entries:
            content_hash = hashlib.sha256(
                (entry.get("content", "") + entry.get("title", "")).encode("utf-8")
            ).hexdigest()[:16]
            eid = f"memory_md:{content_hash}"
            if eid in existing_ids:
                continue
            existing.append({
                "entry_id": eid,
                "source_type": "memory_md",
                "source_id": eid,
                "title": entry.get("title", "长期记忆"),
                "content": entry.get("content", ""),
                "summary": "长期记忆导入",
                "tags_text": "",
                "updated_at": entry.get("updated_at", ""),
                "metadata": {},
            })
            new_count += 1
        if new_count:
            state["entries"] = existing
            src = dict(state.get("source_counts") or {})
            src["memory_md"] = src.get("memory_md", 0) + new_count
            state["source_counts"] = src
            state["built_at"] = datetime.now(timezone.utc).isoformat()
            data_store.save_json("assistant_memory_index", state)
            logger.info("Appended %d memory_md entries to memory index", new_count)
    except Exception as exc:
        logger.warning("Failed to append entries to memory index: %s", exc, exc_info=True)


def _rename_to_bak(path: Path) -> None:
    """将文件重命名为 .bak（安全）；如果 .bak 已存在则追加时间戳。"""
    if not path.is_file():
        return
    bak_path = path.with_suffix(path.suffix + ".bak")
    if bak_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak_path = path.with_suffix(f".bak.{timestamp}")
    try:
        # 先复制再删除（比直接 rename 更安全，尤其在跨设备时）
        shutil.copy2(str(path), str(bak_path))
        path.unlink()
        logger.debug("Renamed %s -> %s", path.name, bak_path.name)
    except Exception as exc:
        logger.warning("Failed to rename %s to .bak: %s", path.name, exc)
        raise


def _load_storage_meta(data_store) -> tuple[dict[str, Any], bool]:
    """加载存储元信息（迁移标记存放于此）。

    返回 ``(meta, meta_writable)``。容错三件套：``_storage_meta.json`` 损坏先
    os.rename 为 ``<name>.corrupt-<ts>`` 保全现场（①），改名成功以空 dict 续用
    （②）；改名失败则 ``meta_writable=False``，调用方必须拒绝回写并告警（③），
    禁止 warning-only 覆写损坏现场。
    """
    meta_path = getattr(data_store, "meta_path", None)
    try:
        if meta_path and os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                raise ValueError(f"storage meta 顶层不是 dict: {type(payload).__name__}")
            return payload, True
        return {}, True
    except Exception as exc:
        if not meta_path:
            logger.error("data_store 无 meta_path，存储元信息不可用：%s", exc)
            return {}, True
        quarantined = quarantine_corrupt_file(str(meta_path))
        if quarantined:
            logger.error("存储元信息损坏，已隔离保全至 %s：%s", quarantined, exc)
            return {}, True
        logger.error("存储元信息损坏且隔离失败，调用方须拒绝回写以保留现场：%s", exc)
        return {}, False


def _save_storage_meta(data_store, meta: dict[str, Any]) -> None:
    """原子持久化存储元信息；写失败向上抛出（禁止 warning-only 假装成功）。"""
    meta_path = getattr(data_store, "meta_path", None)
    if meta_path:
        atomic_write_json(meta_path, meta)
