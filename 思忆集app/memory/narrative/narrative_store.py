"""NarrativeStore — 叙事存储和检索层。

基于现有 memory/索引基础设施，提供叙事粒度的存取能力。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("siyi.memory.narrative.store")


class NarrativeStore:
    """叙事存储：将生成的叙事持久化到 JSON 文件，支持按时间范围检索。

    每个叙事条目包含：
    - narrative_id: 唯一 ID
    - narrative_type: weekly | milestone | emotion | monthly
    - title: 叙事标题
    - content: 叙事正文
    - start_date: 覆盖的时间范围起点
    - end_date: 覆盖的时间范围终点
    - created_at: 生成时间
    - source_memory_ids: 引用的记忆片段 ID 列表（确保可追溯）
    """

    def __init__(self, data_dir: str | Path):
        self._data_dir = Path(data_dir) / "narratives"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._data_dir / "index.json"
        self._narratives: dict[str, dict[str, Any]] = {}
        self._load_index()

    def _load_index(self) -> None:
        """加载叙事索引到内存。"""
        if self._index_path.exists():
            try:
                with open(self._index_path, encoding="utf-8") as f:
                    self._narratives = json.load(f)
            except Exception as exc:
                logger.warning("Failed to load narrative index: %s", exc)
                self._narratives = {}

    def _save_index(self) -> None:
        """持久化叙事索引。"""
        try:
            # 原子写：先写临时文件再 os.replace，防止崩溃损坏叙事索引
            tmp_path = f"{self._index_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._narratives, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._index_path)
        except Exception as exc:
            logger.warning("Failed to save narrative index: %s", exc)

    def save(self, narrative: dict[str, Any]) -> str:
        """保存一条叙事。

        Args:
            narrative: 叙事条目（至少包含 title, content, narrative_type）

        Returns:
            叙事 ID
        """
        nid = narrative.get("narrative_id") or _generate_nid()
        narrative["narrative_id"] = nid
        narrative["created_at"] = narrative.get("created_at", datetime.now(timezone.utc).isoformat())
        self._narratives[nid] = narrative
        self._save_index()
        logger.info("Narrative saved: %s — %s", nid, narrative.get("title", ""))
        return nid

    def get(self, narrative_id: str) -> dict[str, Any] | None:
        """获取单条叙事。"""
        return self._narratives.get(narrative_id)

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """列出最近的叙事，按时间倒序。"""
        sorted_items = sorted(
            self._narratives.values(),
            key=lambda x: x.get("created_at", ""),
            reverse=True,
        )
        return sorted_items[:limit]

    def list_by_type(self, narrative_type: str, limit: int = 20) -> list[dict[str, Any]]:
        """按叙事类型筛选。"""
        matched = [n for n in self._narratives.values() if n.get("narrative_type") == narrative_type]
        matched.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return matched[:limit]

    def list_by_date_range(self, start: str, end: str) -> list[dict[str, Any]]:
        """按时间范围筛选叙事。"""
        matched = []
        for n in self._narratives.values():
            n_start = n.get("start_date", "")
            if n_start and start <= n_start <= end:
                matched.append(n)
        matched.sort(key=lambda x: x.get("start_date", ""), reverse=True)
        return matched

    def get_latest_of_type(self, narrative_type: str) -> dict[str, Any] | None:
        """获取指定类型的最新叙事。"""
        items = self.list_by_type(narrative_type, limit=1)
        return items[0] if items else None

    def delete(self, narrative_id: str) -> bool:
        """删除叙事。"""
        if narrative_id in self._narratives:
            del self._narratives[narrative_id]
            self._save_index()
            return True
        return False

    def count(self) -> int:
        """叙事总数。"""
        return len(self._narratives)


def _generate_nid() -> str:
    """生成唯一叙事 ID。"""
    import uuid

    return f"nar_{uuid.uuid4().hex[:12]}"
