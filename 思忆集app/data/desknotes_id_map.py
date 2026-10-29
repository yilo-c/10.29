"""ID 映射 — 思忆集 int ID ↔ desk-notes UUID 双向转换。"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("siyi.data.desknotes_id_map")

_MAP_FILE = "desknotes_id_map.json"


class IDMapper:
    """持久化的 int ↔ UUID 双向映射。

    思忆集内部全部使用 int ID，desk-notes 使用 UUID 字符串。
    此映射写为 ``desknotes_id_map.json``，位于 desk-notes 数据目录下。
    """

    def __init__(self, desknotes_data_dir: str | os.PathLike):
        self._dir = Path(desknotes_data_dir)
        self._map_path = self._dir / _MAP_FILE
        self._note_map: dict[str, dict] = {}  # uuid → {int_id, path}
        self._int_to_uuid: dict[int, str] = {}  # int_id → uuid
        self._next_id: int = 1
        self._loaded = False
        self._suppress_save = False

    # ── Public API ─────────────────────────────────────────────────────

    def sync_from_stores(self):
        """扫描 desk-notes 的 stores.json，为所有已知 UUID 分配 int ID。"""
        if self._loaded:
            self._load()
        else:
            self._load_or_init()

        # 批量模式：扫描期间逐条 allocate_* 会各自落盘（大量笔记=大量瞬时写，
        # 命中 Defender 扫描窗口的概率骤增）。这里抑制中间落盘，末尾一次写。
        self._suppress_save = True
        try:
            self._sync_from_stores_scan()
        finally:
            self._suppress_save = False
        self._save()

    def _sync_from_stores_scan(self):
        store_path = self._dir / "store.json"
        if not store_path.exists():
            logger.info("desk-notes store.json not found at %s, skipping sync", store_path)
            return

        try:
            raw = json.loads(store_path.read_text(encoding="utf-8"))
            store_state = raw.get("state") or raw
            # 扫描 todos
            for todo in store_state.get("todos") or []:
                uuid = todo.get("id")
                if uuid and uuid not in self._note_map:
                    self.allocate_note_int(uuid)
            # 扫描 categories
            for cat in store_state.get("categories") or []:
                uuid = cat.get("id")
                if uuid and uuid not in self._note_map:
                    self.allocate_note_int(uuid)
            # 扫描 folders
            for folder in store_state.get("folders") or []:
                uuid = folder.get("id")
                if uuid and uuid not in self._note_map:
                    self.allocate_note_int(uuid)
        except Exception:
            logger.exception("failed to sync ID mapper from store.json")

        # 扫描 notes/ 目录
        notes_dir = self._dir / "notes"
        if notes_dir.is_dir():
            for md_file in notes_dir.glob("*.md"):
                uuid = md_file.stem
                if uuid and uuid not in self._note_map:
                    self.allocate_note_int(uuid)

        self._save()

    def allocate_note_int(self, uuid: str) -> int:
        """为笔记 UUID 分配新的 int ID（自动持久化）。"""
        if uuid in self._note_map:
            return self._note_map[uuid]["int_id"]
        int_id = self._next_id
        self._next_id += 1
        self._note_map[uuid] = {"int_id": int_id, "path": f"notes/{uuid}.md"}
        self._int_to_uuid[int_id] = uuid
        self._save()
        return int_id

    def allocate_task_int(self, uuid: str) -> int:
        """为待办 UUID 分配新的 int ID（自动持久化）。"""
        if uuid in self._note_map:
            return self._note_map[uuid]["int_id"]
        int_id = self._next_id
        self._next_id += 1
        self._note_map[uuid] = {"int_id": int_id, "path": None}
        self._int_to_uuid[int_id] = uuid
        if not self._suppress_save:
            self._save()
        return int_id

    def to_uuid(self, note_id: int | str) -> Optional[str]:
        """int ID 或 UUID 字符串 → UUID 转换。

        如果是 UUID 格式的字符串（含 '-'），直接返回。
        如果是整数或数字字符串，通过映射表查找。
        """
        if isinstance(note_id, str):
            if "-" in note_id and len(note_id) > 20:
                return note_id  # 已是 UUID
            try:
                note_id = int(note_id)
            except (ValueError, TypeError):
                return None
        if isinstance(note_id, int):
            return self._int_to_uuid.get(note_id)
        return None

    def to_int(self, uuid: str) -> Optional[int]:
        """UUID → int ID 转换。"""
        entry = self._note_map.get(uuid)
        return entry["int_id"] if entry else None

    def get_or_create_int(self, uuid: str) -> int:
        """获取或创建 int ID。"""
        existing = self.to_int(uuid)
        if existing is not None:
            return existing
        return self.allocate_note_int(uuid)

    def remove(self, uuid: str) -> bool:
        """摘除 uuid ↔ int 映射（S1-2/AN-2：todo 物理删除时清映射）。

        内容行已硬删而映射仍留存 = 八轮 §2.1 的 id_map 孤儿来源；
        permanent_delete 必须连同映射一起摘除。
        """
        entry = self._note_map.pop(uuid, None)
        if entry is None:
            return False
        int_id = entry.get("int_id")
        if int_id is not None:
            self._int_to_uuid.pop(int_id, None)
        self._save()
        return True

    def get_note_path(self, uuid: str) -> Optional[str]:
        """获取笔记文件的相对路径。"""
        entry = self._note_map.get(uuid)
        return entry.get("path") if entry else None

    def update_note_path(self, uuid: str, path: str):
        """更新笔记文件路径（文件系统迁移后）。"""
        if uuid in self._note_map:
            self._note_map[uuid]["path"] = path
            self._save()

    # ── Persistence ────────────────────────────────────────────────────

    def _load_or_init(self):
        if self._map_path.exists():
            try:
                data = json.loads(self._map_path.read_text(encoding="utf-8"))
                self._note_map = data.get("note_map", {})
                self._next_id = data.get("next_id", 1)
                self._rebuild_int_index()
            except Exception:
                logger.exception("failed to load ID map, starting fresh")
        self._loaded = True

    def _load(self):
        self._load_or_init()

    def _save(self):
        self._dir.mkdir(parents=True, exist_ok=True)
        data = {
            "note_map": self._note_map,
            "next_id": self._next_id,
        }
        self._write_atomic(
            self._map_path,
            json.dumps(data, ensure_ascii=False, indent=2),
        )

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        """原子写入：临时文件 + os.replace，不加锁消除瞬时 PermissionError。

        原理：不再对目标文件做截断式重写（这会撞上系统组件如 Windows Defender
        实时扫描的瞬时独占句柄），而是写一个全新的临时文件后 rename 覆盖。
        rename 只触碰新句柄，几乎不会与扫描窗口冲突；仅当目标被其它进程以
        禁止删除共享的方式打开时才可能失败，此时用极短指数退避重试兜底。
        """
        tmp = path.with_suffix(path.suffix + ".tmp")
        delay = 0.05
        for attempt in range(3):
            try:
                tmp.write_text(content, encoding="utf-8")
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt < 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

    def _rebuild_int_index(self):
        self._int_to_uuid = {}
        for uuid, entry in self._note_map.items():
            int_id = entry.get("int_id")
            if int_id is not None:
                self._int_to_uuid[int_id] = uuid
