"""七维-数C3：graph_store「损坏→空图→save 覆盖」链回归测试。

背景（七维报告 §2 / 干净会话复核属实）：load() 对 JSONDecodeError 仅 warning
并置空图且不保护原文件；此后任何一次 save() 会用内存空图 os.replace 掉
磁盘上那个可能只是损坏而非丢失的原文件——损坏窗口被放大为确定性数据丢失。

修复语义：load 失败时先把原文件隔离改名 *.corrupted.<ts>（保留物证），
再置空图；save 使用 data/_atomic_json 原子写（PID+UUID 临时名）。
"""

from __future__ import annotations

import json
from pathlib import Path

from knowledge.graph_store import KnowledgeGraphStore


class TestCorruptionQuarantine:
    def test_corrupted_file_quarantined_and_survives_save(self, tmp_path: Path):
        p = tmp_path / "knowledge_graph.json"
        p.write_text('{"entities": {"e1": {"id": "e1"', encoding="utf-8")  # 截断损坏
        store = KnowledgeGraphStore(path=str(p))
        store.load()
        quarantine = list(tmp_path.glob("knowledge_graph.json.corrupted.*"))
        assert quarantine, "损坏文件未被隔离保留物证"
        assert quarantine[0].read_text(encoding="utf-8").startswith('{"entities"')
        store.save()  # 空图落盘——不得触碰物证
        assert p.exists(), "save 后应生成新图文件"
        assert list(tmp_path.glob("*.corrupted.*")) == quarantine, "物证被后续 save 破坏"
        assert json.loads(p.read_text(encoding="utf-8"))["entities"] == {}

    def test_valid_file_loads_normally_no_quarantine(self, tmp_path: Path):
        p = tmp_path / "kg.json"
        p.write_text(
            json.dumps({"entities": {"e1": {"id": "e1"}}, "relations": []}, ensure_ascii=False),
            encoding="utf-8",
        )
        store = KnowledgeGraphStore(path=str(p))
        store.load()
        assert "e1" in store._entities
        assert not list(tmp_path.glob("*.corrupted.*")), "健康文件不应被隔离"
