"""Desk-Notes sync 元数据 sidecar — ``{data_dir}/.sync-meta.json``。

背景（S1-1 / AL-1）：``DeskNotesNoteAdapter`` 过去在 ``_note_to_dict`` 里硬编码
``sync_state="local_only"`` / ``versions=[]``，``update_note`` 白名单又静默丢弃一切
sync 字段 → 云同步冲突检测（``sync._cloud_sync_crypto._detect_conflict``）、
``sync.conflicts.list``、``sync.resolve_conflict`` 全部永不生效（幻影字段）。

设计取舍（方案 A：sidecar，竞品锚 = Obsidian/Bear 的干净内容文件）：
- ``notes/{uuid}.md`` 的 frontmatter 只保留 desk-notes 自有字段；sync 元数据
  （sync_state / versions / dirty / conflict / last_synced_at / version 等）写入
  本 sidecar，结构 ``{"<uuid>": {"sync_state": ..., "versions": [...],
  "dirty": ..., "conflict": {...}, "updated_at": ..., ...}}``。
- 读侧无记录时回退默认值 ``local_only`` / ``[]``——没同步过的笔记不是异常态。
- 写事务配对：**md 写成功、sidecar 写失败不回滚 md**（内容完整性 > 元数据时效），
  只 ``logger.warning``；内存缓存回滚到盘上真值，下次读取按"缺 key → 默认值"
  自然 reconcile，不引入 journal 复杂度。

``updated_at`` 键在记录里指"笔记的同步语义时间戳"（来自 update/merge 显式传入，
供 local 裁决恢复时间戳用）；记录自身的落盘时间用 ``meta_updated_at``。

载体权威方向（波次④包B 文档化 · 蓝本 §1b 载体②，仅定性注释、不改逻辑）：
- 本 sidecar 是 desk-notes 笔记 sync 元数据（含 ``sync_state``，值域见
  ``core.sync_protocol.SyncState``）的存放处，但定位为**可丢弃缓存**：
  主资产是 ``notes/{uuid}.md`` 内容文件，sidecar 整份丢失也不致数据损失，
  读侧按"缺 key → 默认值"自然 reconcile（见上方设计取舍）。
- 与其余载体的关系：载体①（仓储实体 dict ``sync_state``）、载体③（kv
  ``integration_sync_cursors``，A 栈游标真源）、载体④（kv ``remote_snapshot_*``，
  派生缓存）、载体⑤（settings 内 ``cloud_sync_*``，B 栈游标）各自独立，
  互不 reconcile；权威方向登记见 ``data/local_storage.py`` 键旁注释与蓝本 §1b。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("siyi.data.desknotes_sync_meta")

SIDECAR_FILENAME = ".sync-meta.json"
DEFAULT_SYNC_STATE = "local_only"
MAX_VERSIONS = 5

#: 路由到 sidecar 的 sync 语义字段（md frontmatter 不得出现）。
SYNC_META_KEYS = (
    "sync_state",
    "versions",
    "dirty",
    "conflict",
    "last_synced_at",
    "version",
    "device_id",
    "updated_at",
    "created_at",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_sync_patch(note_dict: dict[str, Any]) -> dict[str, Any]:
    """从传入 dict 中挑出 sync 语义字段，构造 sidecar 补丁（白名单外字段忽略）。"""
    return {key: note_dict[key] for key in SYNC_META_KEYS if key in note_dict}


class SyncMetaStore:
    """uuid → sync 元数据 的 JSON sidecar 存储。

    实例内缓存 + 全量原子重写（与 desk-notes 数据目录下其它小 JSON 同策略）。
    单进程写方；外部（desk-notes 应用）不会写这个私有文件。
    """

    def __init__(self, data_dir: str | os.PathLike):
        self._path = Path(data_dir) / SIDECAR_FILENAME
        self._data: dict[str, dict[str, Any]] | None = None

    # ── 读 ─────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._data is not None:
            return self._data
        self._data = {}
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = {k: v for k, v in raw.items() if isinstance(v, dict)}
            except (json.JSONDecodeError, OSError):
                # 读失败 → 按"无记录"工作（默认值兜底），下次成功写会重建文件。
                logger.warning("sync-meta sidecar unreadable (%s); using defaults", self._path, exc_info=True)
                self._data = {}
        return self._data

    def get(self, uuid: str) -> dict[str, Any] | None:
        """返回该 uuid 的元数据副本；无记录返回 None（读侧自行套默认值）。"""
        rec = self._load().get(uuid)
        return dict(rec) if rec else None

    # ── 写 ─────────────────────────────────────────────────────────────

    def update(self, uuid: str, patch: dict[str, Any], *, max_versions: int = MAX_VERSIONS) -> bool:
        """合并 patch 到该 uuid 的记录并原子落盘。

        ``patch`` 中值为 ``None`` 的键表示"清除该覆盖项"（回到默认语义）。
        ``versions`` 环形上限 ``max_versions``（照抄 kv 侧 max_versions=5）。
        落盘失败返回 False（内存回滚到盘上真值），调用方不得因此回滚已写的 md。
        """
        data = self._load()
        # 值域内只会存非空 dict（rec 为空即 pop 键），故 None 即可表达
        # "原先不存在"，无需 _MISSING 哨兵（object 会让 mypy 收窄失败）。
        prev: dict[str, Any] | None = data.get(uuid)
        rec: dict[str, Any] = dict(prev) if prev else {}
        for key, val in patch.items():
            if key == "versions" and isinstance(val, list):
                rec["versions"] = val[:max_versions]
            elif val is None:
                rec.pop(key, None)
            else:
                rec[key] = val
        if rec:
            rec["meta_updated_at"] = _now_iso()
            data[uuid] = rec
        else:
            data.pop(uuid, None)
        if not self._save():
            # 失败回滚内存——盘上真值优先；读侧按默认值兜底，即任务书所要求的
            # "sidecar 缺 key → 默认值"式 reconcile（不造 journal）。
            if prev:
                data[uuid] = prev
            else:
                data.pop(uuid, None)
            return False
        return True

    def pop(self, uuid: str) -> None:
        """硬删除笔记时清理其 sidecar 记录（防孤儿键泄漏）。"""
        data = self._load()
        if uuid in data:
            data.pop(uuid)
            self._save()

    # ── 落盘 ───────────────────────────────────────────────────────────

    def _save(self) -> bool:
        """临时文件 + os.replace 原子写（同 IDMapper._write_atomic 策略，
        规避 Windows Defender 瞬时句柄导致的 PermissionError）。"""
        payload = json.dumps(self._data or {}, ensure_ascii=False, indent=2)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        delay = 0.05
        for attempt in range(3):
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(payload, encoding="utf-8")
                os.replace(tmp, self._path)
                return True
            except PermissionError:
                if attempt < 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                logger.warning("sync-meta sidecar write failed (PermissionError): %s", self._path, exc_info=True)
                return False
            except OSError:
                logger.warning("sync-meta sidecar write failed: %s", self._path, exc_info=True)
                return False
        return False
