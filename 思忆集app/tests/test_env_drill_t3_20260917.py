"""T3 磁盘故障注入演练（env-drill-2，2026-09-17；**2026-09-18 同批反转**）。

用 monkeypatch 让 open/os.replace/sqlite3.connect/write_text 抛 OSError，
验证写失败时的数据完整性与错误上报。覆盖：
- knowledge_graph.save() 失败：atomic_write_json 抛 → 调用方行为
  - _flush_graph：logger.exception 吞（PD-1 设计：内存不落盘，重启即丢）
  - _kg_cascade_sync（级联删除）：捕获后返回 `deleted:N (save failed:...)`
- 笔记 md / store.json 写失败：**已原子化**（P1-1），失败时目标文件不变、
  无半截残留；id_map 映射回滚（P1-3）；store.json 失败如实返回 False（P2-4）
- 级联删除返回值不再被丢弃（P1-4）：6 处调用点全部消费并上报
- 只读数据目录：启动期 mkdir 失败的行为

反转说明（AGENTS.md 守则 #7「修复侧优先把结论固化为机器断言」+ 诚实性纪律）：
本文件原先把**缺陷行为写成期望断言**（xfail strict 的撕裂/孤儿映射/静默吞），
修复后旧断言会把"修好"报成失败。故 P1-1/P1-3/P1-4/P2-4 落地时**同批反转**为
正向契约——不允许把缺陷锁成期望留在仓库里。

历史事实（反转前的勘察结论，保留供追溯）：
- DeskNotesNoteAdapter._write_md 曾用 `md_path.write_text` ——**非原子**。
- add_note 曾先 get_or_create_int 再 _write_md，md 写失败即孤儿映射。
- _save_store_state 曾 `store_path.write_text` 非原子 + 失败仅 logger.exception。
- notes_delete（_notes.py:505/512/662）曾 await cascade_delete_note(...) **丢弃返回值**。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.env_drill_t3

ENOSPC = OSError(28, "No space left on device")
EACCES = PermissionError(13, "Permission denied")


# ─────────────────────────────────────────────────────────────────────────────
# 1. 知识图谱 save() 失败
# ─────────────────────────────────────────────────────────────────────────────


def test_graph_save_failure_propagates(tmp_path):
    """graph_store.save() 底层 atomic_write_json 失败 → 抛异常（不吞），原文件保持不变。"""
    from knowledge.graph_store import KnowledgeGraphStore

    gp = tmp_path / "kg.json"
    store = KnowledgeGraphStore(str(gp))
    store.add_entity("e1", "实体A", entity_type="concept")
    store.save()
    original = gp.read_bytes()

    with mock.patch("data._atomic_json.os.replace", side_effect=ENOSPC):
        with pytest.raises(OSError):
            store.save()
    # 原子写失败：原文件不被撕裂（tmp+replace 语义，替换未发生）
    assert gp.read_bytes() == original


def test_flush_graph_swallows_save_failure(caplog):
    """_flush_graph 对 save 失败 logger.exception 吞掉（PD-1 有意为之，但 RPC 层无感知）。"""
    from backend.handlers._external_content import _flush_graph

    class Boom:
        def save(self):
            raise ENOSPC

    with caplog.at_level("ERROR"):
        # 不抛出 = 调用方以为成功，实体仅在内存、重启即丢
        _flush_graph(Boom(), "import_file")
    assert any("save 失败" in r.message or "图谱 save" in r.message for r in caplog.records)


def test_cascade_kg_save_failure_reports_via_return_string():
    """级联删除：save 失败 → _kg_cascade_sync 返回 `deleted:N (save failed:...)`（不抛出）。

    PD-2b 修复到位：内存删了、落盘失败时至少如实返回可解析字符串，而非静默。
    """
    from backend.handlers._cascade_cleanup import _kg_cascade_sync

    class KG:
        def __init__(self):
            self._entities = {"e1": {"name": "x"}}
            self._relations = []

        def remove_entity(self, eid):
            return self._entities.pop(eid, None) is not None

        def save(self):
            raise ENOSPC

    out = _kg_cascade_sync(KG(), "e1", "note")
    assert out.startswith("deleted:1") and "save failed" in out, "级联删除落盘失败必须在返回值里可见，否则上层无从知晓图谱未持久化"


def test_notes_delete_reports_cascade_failures():
    """[反转 P1-4] notes.delete 必须**消费** cascade 返回值并如实上报失败项。

    反转前（xfail strict）：断言 `notes_delete` 从不接收/检查返回值——把
    "save failed 被静默"固化成期望。P1-4 落地后同批反转。

    现行契约（两段，缺一不可）：
      1. 静态：``_notes.py`` / ``_tasks.py`` 全部 6 处 ``cascade_delete_*`` 调用点
         都经 :func:`report_cascade_failures` 消费返回值（机器断言，防复发）；
      2. 行为：失败项确实进入台账（``system.integrity`` 可查）并发出 WARNING。

    注意**返回仍为 True**：``notes.delete`` 的 True 语义是"笔记已删除"，级联是
    best-effort 附加清理；改判 False 会把"删除失败"错误地告诉用户。故本用例
    断言"可观测"而非"返回 False"——这是本次修复刻意的语义选择。
    """
    import re
    from pathlib import Path as _Path

    # 源码路径基于 __file__ 反推**绝对路径**，不用相对路径：相对路径会让测试对 cwd
    # 产生隐含依赖（从仓库根跑就挂、从 `思忆集app/` 跑才过）。2026-09-18 独立复核
    # 已判定本处为"需修"。测试必须与 cwd 无关。
    _APP_ROOT = _Path(__file__).resolve().parents[1]  # tests/ -> 思忆集app/

    from core import integrity

    # ── 1. 静态：6 处调用点全部消费返回值 ──
    sites = {
        "backend/handlers/_notes.py": 4,  # delete×2 + permanent_delete + batch_delete
        "backend/handlers/_tasks.py": 3,  # delete×2 + permanent_delete
    }
    for rel, expected in sites.items():
        src = (_APP_ROOT / rel).read_text(encoding="utf-8")
        consumed = len(re.findall(r"report_cascade_failures\(", src))
        bare = len(re.findall(r"await cascade_delete_\w+\(context,", src)) - consumed
        assert consumed == expected, f"{rel} 应消费 {expected} 处级联返回值，实际 {consumed}"
        assert bare == 0, f"{rel} 仍有 {bare} 处裸 await cascade_delete_*（返回值被丢弃）"

    # ── 2. 行为：失败项进台账 + WARNING ──
    from backend.handlers._cascade_cleanup import report_cascade_failures

    integrity.reset()
    failures = report_cascade_failures(
        "notes.delete",
        "note_42",
        {"knowledge_graph": "deleted:1 (save failed: [Errno 28] No space left on device)"},
    )
    assert failures and "save failed" in failures[0]
    snap = integrity.snapshot()
    assert snap["counts"].get("cascade") == 1
    assert "notes.delete" in snap["events"][-1]["detail"]
    assert snap["events"][-1]["target"] == "note_42"


def test_cascade_failures_extraction_shapes():
    """cascade_failures 只挑失败项：deleted:N / no-op 是正常终态，不算失败。"""
    from backend.handlers._cascade_cleanup import cascade_failures

    assert cascade_failures(None) == []
    assert cascade_failures({}) == []
    assert cascade_failures({"knowledge_graph": "deleted:3"}) == []
    assert cascade_failures({"memory_index": "no-op"}) == []
    got = cascade_failures(
        {
            "knowledge_graph": "deleted:1 (save failed: disk full)",
            "memory_index": "deleted:2",
            "structured_memory": "failed: boom",
        }
    )
    assert len(got) == 2
    assert any("save failed" in g for g in got) and any("failed: boom" in g for g in got)


# ─────────────────────────────────────────────────────────────────────────────
# 2. 笔记 md / store.json 写失败
# ─────────────────────────────────────────────────────────────────────────────


def test_add_note_write_md_is_atomic_no_torn_file(tmp_path):
    """[反转 P1-1] md 写已原子化：磁盘满时**不留半截 md**（目标文件保持原样）。

    反转前：断言 `_write_md` 用 ``Path.write_text`` 直写目标，注入"写一半后 ENOSPC"
    必留下缺尾的半截 md（撕裂实证）。P1-1 落地后同批反转为正向契约。

    现行契约：:func:`data._atomic_json.atomic_write_text` 先写同目录 tmp 再
    ``os.replace``；写失败时目标路径**要么不存在、要么是旧完整内容**，绝无中间态。
    """
    from data.desknotes_id_map import IDMapper
    from data.repositories.desknotes_note_adapter import DeskNotesNoteAdapter

    d = tmp_path / "notes"
    d.mkdir()
    (d / "notes").mkdir()
    idm = IDMapper(d)
    idm._load_or_init()
    adapter = DeskNotesNoteAdapter(d, idm)

    marker = "<<<TAIL-MARKER-ZZZ>>>"

    def fail_md_write(path, text, **kw):
        if str(path).endswith(".md"):
            raise ENOSPC
        from data._atomic_json import atomic_write_text as _real

        return _real(path, text, **kw)

    with mock.patch(
        "data.repositories.desknotes_note_adapter.atomic_write_text",
        side_effect=fail_md_write,
    ):
        with pytest.raises(OSError):
            adapter.add_note({"title": "撕裂测试", "content": ("正文内容" * 100) + marker})

    mds = list((d / "notes").glob("*.md"))
    assert mds == [], f"原子写失败不得留下半截 md，实际残留 {[m.name for m in mds]}"
    # 目录里也不该有未清理的临时文件（_atomic_write 的 finally 负责）
    assert list((d / "notes").glob("*.tmp")) == [], "失败路径必须清理临时文件"


def test_add_note_idmap_orphan_rolled_back_on_md_failure(tmp_path):
    """[反转 P1-3] md 写失败 → 刚建的 id_map 映射必须回滚，不留孤儿映射。

    反转前（xfail strict）：断言孤儿映射必然残留（缺陷固化）。P1-3 落地后反转。

    孤儿映射的用户可见后果：``get_note(int)`` 解析到无文件的 uuid → None，
    ``delete`` 走映射却无文件可删 —— 表现为"笔记存在但打不开"。
    """
    from data.desknotes_id_map import IDMapper
    from data.repositories.desknotes_note_adapter import DeskNotesNoteAdapter

    d = tmp_path / "notes"
    d.mkdir()
    (d / "notes").mkdir()
    idm = IDMapper(d)
    idm._load_or_init()
    adapter = DeskNotesNoteAdapter(d, idm)

    def fail_md_only(path, text, **kw):
        if str(path).endswith(".md"):
            raise ENOSPC
        from data._atomic_json import atomic_write_text as _real

        return _real(path, text, **kw)

    with mock.patch(
        "data.repositories.desknotes_note_adapter.atomic_write_text",
        side_effect=fail_md_only,
    ):
        with pytest.raises(OSError):
            adapter.add_note({"title": "孤儿", "content": "x"})

    idmap = json.loads((d / "desknotes_id_map.json").read_text(encoding="utf-8"))
    orphans = [u for u, e in idmap["note_map"].items() if e.get("path") and not (d / e["path"]).exists()]
    assert orphans == [], f"md 写失败必须回滚 id_map，仍发现孤儿映射: {orphans}"


def test_add_note_keeps_preexisting_mapping_on_failure(tmp_path):
    """回滚必须**只回滚本次新建**的映射：既有合法映射不得被误删（P1-3 边界）。"""
    from data.desknotes_id_map import IDMapper
    from data.repositories.desknotes_note_adapter import DeskNotesNoteAdapter

    d = tmp_path / "notes"
    d.mkdir()
    (d / "notes").mkdir()
    idm = IDMapper(d)
    idm._load_or_init()
    adapter = DeskNotesNoteAdapter(d, idm)

    uuid = "siyi_preexisting_1"
    allocated = idm.get_or_create_int(uuid)

    def fail_md_only(path, text, **kw):
        if str(path).endswith(".md"):
            raise ENOSPC
        from data._atomic_json import atomic_write_text as _real

        return _real(path, text, **kw)

    with mock.patch(
        "data.repositories.desknotes_note_adapter.atomic_write_text",
        side_effect=fail_md_only,
    ):
        with pytest.raises(OSError):
            adapter.add_note({"remote_id": uuid, "title": "既有映射", "content": "x"})

    assert idm.to_int(uuid) == allocated, "既有映射在回滚中被误删 —— 回滚范围越界"


def test_save_store_state_reports_failure(tmp_path, caplog):
    """[反转 P2-4] _save_store_state 写失败 → 返回 False + 台账留痕 + 旧文件不变。

    反转前：断言"不抛异常即静默丢失"，且把 store.json 不存在/为空当作期望
    （非原子写失败会留下 0 字节截断文件）。P2-4 落地后反转。
    """
    import logging as _logging

    from core import integrity
    from data.desknotes_id_map import IDMapper
    from data.repositories.desknotes_note_adapter import DeskNotesNoteAdapter

    d = tmp_path / "notes"
    d.mkdir()
    (d / "notes").mkdir()
    idm = IDMapper(d)
    idm._load_or_init()
    adapter = DeskNotesNoteAdapter(d, idm)
    assert adapter._save_store_state({"folders": [{"id": "f0", "name": "原"}]}) is True
    before = (d / "store.json").read_bytes()

    integrity.reset()
    with caplog.at_level(_logging.WARNING):
        with mock.patch(
            "data.repositories.desknotes_note_adapter.atomic_write_text",
            side_effect=ENOSPC,
        ):
            ok = adapter._save_store_state({"folders": [{"id": "f1", "name": "测试夹"}]})

    assert ok is False, "写失败必须如实返回 False，不得伪装成成功"
    assert (d / "store.json").read_bytes() == before, "写失败不得破坏既有 store.json"
    assert integrity.snapshot()["counts"].get("note_store") == 1, "失败必须进台账（system.integrity 可查）"
    assert any("store.json" in r.getMessage() for r in caplog.records), "失败必须有 WARNING 日志"


# ─────────────────────────────────────────────────────────────────────────────
# 3. 只读目录 / 启动 mkdir 失败
# ─────────────────────────────────────────────────────────────────────────────


def test_local_storage_mkdir_failure_raises(tmp_path):
    """data_store 建目录失败 → atomic_write_json 的 os.makedirs 抛出（fail-fast，非静默）。"""
    from data._atomic_json import atomic_write_json

    target = tmp_path / "deep" / "nested" / "x.json"
    with mock.patch("data._atomic_json.os.makedirs", side_effect=EACCES):
        with pytest.raises(PermissionError):
            atomic_write_json(str(target), {"a": 1})


def test_graph_load_corrupt_quarantines_then_empty(tmp_path):
    """图谱文件损坏 → load 先隔离改名保全物证，再置空图（不静默覆盖）。"""
    from knowledge.graph_store import KnowledgeGraphStore

    gp = tmp_path / "kg.json"
    gp.write_text("{ this is not valid json", encoding="utf-8")
    store = KnowledgeGraphStore(str(gp))
    store.load()  # 不抛
    assert store._entities == {}
    # 原损坏文件被隔离为 *.corrupted.*，未被 save 覆盖丢失
    assert gp.exists() is False or not _json_parses(gp)
    corrupted = list(tmp_path.glob("kg.json.corrupted.*"))
    assert corrupted, f"应隔离保全损坏现场，实际 glob={list(tmp_path.iterdir())}"


def _json_parses(p: Path) -> bool:
    try:
        json.loads(p.read_text(encoding="utf-8"))
        return True
    except (json.JSONDecodeError, OSError):
        return False
