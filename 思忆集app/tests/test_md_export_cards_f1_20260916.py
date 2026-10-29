# -*- coding: utf-8 -*-
"""F1 卡片孤儿修复断言（2026-09-16 知识管线审计批 2）。

背景：export_daily 此前 include_cards=False，91 张 knowledge.card 只存在
kv_store，主题轴 MD 永不出盘；而 include 全禁的顾虑是每日任务冲掉用户
编辑。修复后语义 = **新卡片照常落盘、磁盘已有同名主题文件不覆盖**。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore
from knowledge.md_exporter import export_daily
from knowledge.md_store import MDKnowledgeStore


class _Ctx:
    def __init__(self, store):
        self.local_data_store = store


def _card(idx: str, topic: str = "测试主题") -> dict:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "id": f"card-{idx}", "entity_type": "knowledge.card", "supertag_type": "@知识",
        "title": f"卡片{idx}", "content": f"结论{idx}", "card_topic": topic,
        "tags": ["知识卡片"], "source": "deepseek", "entities": ["实体A"],
        "created_at": now, "updated_at": now, "relations": [],
    }


def test_export_daily_lands_new_cards(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("structured_memory_entries", [_card("1"), _card("2")])
    result = export_daily(_Ctx(store), days=1)
    assert result["success"] is True
    md = MDKnowledgeStore(store.base_dir)
    cards = [r for r in md.list_files() if "/卡片-" in r]
    assert len(cards) == 2, f"卡片应落盘主题轴: {cards}"
    assert result.get("cards", 0) >= 2


def test_export_daily_never_overwrites_existing_topic_files(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    md = MDKnowledgeStore(store.base_dir)
    # 用户手工编辑过的卡片（同主题同标题 → 同落盘路径）
    edited_rel = "主题/测试主题/卡片-卡片1.md"
    md.write(edited_rel, "---\ntitle: 卡片1\ntopic: 测试主题\ntype: 知识卡片\n---\n我的改写内容\n")
    before = md.read(edited_rel)
    store.save_json("structured_memory_entries", [_card("1"), _card("2")])
    result = export_daily(_Ctx(store), days=1)
    assert result["success"] is True
    assert md.read(edited_rel) == before, "已存在的主题文件不得被每日任务覆盖（F1 边界）"
    # 新卡片（卡片2）仍要落盘
    assert any("/卡片-" in r and "卡片2" in r for r in md.list_files())
    assert result.get("cards_skipped_exists", 0) >= 1


def test_export_daily_timeline_unchanged_for_old_dates(tmp_path):
    """增量语义回归：早于窗口期的时间轴文件不重写（与修复前行为一致）。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    md = MDKnowledgeStore(store.base_dir)
    old_rel = "2026/2026-01/2026-01-01_记忆.md"
    md.write(old_rel, "---\ndate: 2026-01-01\ntype: 记忆\n---\n历史手写记忆\n")
    store.save_json("structured_memory_entries", [_card("1")])
    before = md.read(old_rel)
    export_daily(_Ctx(store), days=1)
    assert md.read(old_rel) == before
