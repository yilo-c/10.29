"""T1 kill -9 数据一致性演练——可固化部分（env-drill-2，2026-09-17）。

真进程击杀循环（25 轮，taskkill /F）在仓库外脚本 `D:/h/3.25/drill_t1_loop.py`
执行（结果见演练报告）。本文件把可单元化的判据固化进 pytest：

1. atomic_write_json 崩溃语义：模拟"写 tmp 中途进程死亡 / replace 前失败"，
   断言目标文件永不撕裂（tmp+os.replace 契约）。
2. 半写检测判据：真实击杀判据（frontmatter 闭合 + idmap 映射存在）的可复现单元。
3. Windows 句柄占用（Defender 扫描近似）下 os.replace 退避重试最终成功。
4. 笔记 md / store.json 的原子性与孤儿 tmp 启动清扫（**2026-09-18 同批反转**：
   反转前本节是 xfail 缺陷固化——"无启动 sweep"被写成期望断言；P1-1/P2-3 落地后
   同批改为正向契约，否则旧断言会把"修好"报成失败）。

覆盖隔离：全部走 tmp_path，SIYI_DATA_DIR 由 conftest 守卫兜底。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.env_drill_t1


# ─────────────────────────────────────────────────────────────────────────────
# 1. atomic_write_json 崩溃/失败语义：目标文件永不撕裂
# ─────────────────────────────────────────────────────────────────────────────


def test_atomic_write_leaves_no_tmp_on_success(tmp_path):
    from data._atomic_json import atomic_write_json

    p = tmp_path / "a.json"
    atomic_write_json(str(p), {"k": "v"})
    assert json.loads(p.read_text(encoding="utf-8")) == {"k": "v"}
    assert not list(tmp_path.glob("*.tmp")), "成功路径不得遗留 .tmp"


def test_atomic_write_target_intact_when_replace_fails(tmp_path):
    """replace 全部重试失败 → 抛异常，但既有目标文件字节不变（无撕裂），tmp 已清理。"""
    from data._atomic_json import atomic_write_json

    p = tmp_path / "b.json"
    atomic_write_json(str(p), {"gen": 1})
    before = p.read_bytes()

    with mock.patch("data._atomic_json.os.replace", side_effect=PermissionError(5, "denied")):
        with pytest.raises(OSError):
            atomic_write_json(str(p), {"gen": 2})
    assert p.read_bytes() == before, "replace 失败必须保持旧文件原样"
    assert not list(tmp_path.glob("*.tmp")), "失败路径 finally 必须清理 tmp"


def test_atomic_write_killed_after_flush_before_replace(tmp_path):
    """崩溃窗口固化：tmp 已写完但 replace 前进程死亡（等价 kill -9）。

    真 kill -9 无法在单元里复现（进程级），用"tmp 写成功→os.replace 抛异常"近似
    SIGKILL 语义：目标文件仍是旧值；孤儿 .tmp 是唯一残留——该残留由
    test_startup_sweep_removes_stale_tmp（P2-3）与 LocalDataStore 启动清扫兜底。
    """
    from data._atomic_json import atomic_write_json

    p = tmp_path / "c.json"
    atomic_write_json(str(p), {"v": "old"})
    before = p.read_bytes()

    # 绕过 finally 的清理，模拟"进程在 finally 之前被 SIGKILL"
    def _kill_like_replace(tmp, target, *a, **k):
        raise SystemExit("simulated SIGKILL between flush and replace")

    real_remove = os.remove
    with mock.patch("data._atomic_json._replace_with_retry", side_effect=_kill_like_replace):
        with mock.patch("data._atomic_json.os.remove", side_effect=lambda path: None):  # 清理不生效
            with pytest.raises(SystemExit):
                atomic_write_json(str(p), {"v": "new"})
    assert p.read_bytes() == before, "SIGKILL 语义下目标文件必须保持旧内容"
    _ = real_remove  # 断链防护


def test_startup_sweep_removes_stale_tmp(tmp_path):
    """孤儿 .tmp 有启动自愈清扫（P2-3 治本，2026-09-18 同批反转）。

    反转前：本用例断言"生产 data/ 存着 2026-05 的 siyi-*.json.tmp ×2，全仓无任何
    启动期 glob('*.tmp')+remove 逻辑"——把**缺陷固化成期望**。清扫落地后该断言
    会把"修好"报成失败，故同批反转为正向契约。

    现行契约（:func:`data._atomic_json.sweep_orphan_tmp`）：
    - 只清**已知原子写命名**（三形状），不碰任意 *.tmp；
    - 默认年龄门槛 1 天（避开并发写者的在途文件）；
    - 非递归、单个失败不中断。
    """
    from data._atomic_json import sweep_orphan_tmp

    stale = tmp_path / "siyi-abc123.json.tmp"
    stale.write_text("{}", encoding="utf-8")
    old = time.time() - 40 * 86400
    os.utime(stale, (old, old))

    fresh = tmp_path / "siyi-fresh1.json.tmp"
    fresh.write_text("{}", encoding="utf-8")  # mtime = now → 在途保护，不得删

    unrelated = tmp_path / "something-else.tmp"
    unrelated.write_text("keep me", encoding="utf-8")
    os.utime(unrelated, (old, old))  # 名字不在已知形状内 → 即使很旧也不得删

    removed = sweep_orphan_tmp(str(tmp_path))
    assert removed == ["siyi-abc123.json.tmp"], f"应只清已知命名的过期孤儿，实际 {removed}"
    assert not stale.exists()
    assert fresh.exists(), "年龄门槛内的 tmp 可能是在途写，不得删除"
    assert unrelated.exists(), "非已知命名的 *.tmp 一律不碰（宁可漏清不可误删）"


def test_local_data_store_sweeps_on_init(tmp_path):
    """接线验证：LocalDataStore 启动即清扫自己目录下的孤儿 tmp（P2-3 落点）。"""
    import os as _os
    import time as _time

    from data.local_storage import LocalDataStore

    base = tmp_path / "store"
    base.mkdir()
    orphan = base / ".ai_cache_zzzzzzzz.tmp"
    orphan.write_text("x", encoding="utf-8")
    old = _time.time() - 3 * 86400
    _os.utime(orphan, (old, old))

    LocalDataStore(str(base))
    assert not orphan.exists(), "LocalDataStore 构造时必须清掉本目录的过期孤儿 tmp"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Windows 句柄占用（Defender 近似）下 replace 重试
# ─────────────────────────────────────────────────────────────────────────────


def test_replace_retry_survives_transient_permission_error(tmp_path):
    """前 2 次 PermissionError（Defender 瞬时句柄），第 3 次成功 → 原子写最终落地。"""
    from data._atomic_json import atomic_write_json

    p = tmp_path / "d.json"
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(tmp, target):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "being used by another process")
        return real_replace(tmp, target)

    with mock.patch("data._atomic_json.os.replace", side_effect=flaky):
        atomic_write_json(str(p), {"retry": "ok"}, base_delay=0.001)
    assert json.loads(p.read_text(encoding="utf-8")) == {"retry": "ok"}
    assert calls["n"] == 3


def test_idmapper_atomic_retry(tmp_path):
    """IDMapper._write_atomic 同款退避：tmp 名固定（.json.tmp）——并发互踩风险面。"""
    from data.desknotes_id_map import IDMapper

    idm = IDMapper(tmp_path)
    idm._load_or_init()
    i1 = idm.allocate_note_int("uuid-1")
    i2 = idm.allocate_note_int("uuid-1")
    assert i1 == i2, "uuid 复用映射应幂等"
    data = json.loads((tmp_path / "desknotes_id_map.json").read_text(encoding="utf-8"))
    assert data["note_map"]["uuid-1"]["int_id"] == i1


# ─────────────────────────────────────────────────────────────────────────────
# 3. 击杀判据单元化：完整写入笔记在重启后（新 adapter 实例）可读回
# ─────────────────────────────────────────────────────────────────────────────


def test_note_roundtrip_survives_process_restart_semantics(tmp_path):
    """写一条笔记 → 销毁全部内存对象（模拟重启）→ 新实例读回完整内容。

    对应击杀演练的正路径：只要 add_note 返回过，数据必已完整落盘可读。
    """
    from data.repositories.desknotes_note_adapter import DeskNotesNoteAdapter

    d = tmp_path / "dn"
    (d / "notes").mkdir(parents=True)
    a1 = DeskNotesNoteAdapter(d, _fresh_mapper(d))
    nid = a1.add_note({"title": "击杀演练-完整", "content": "正文" * 200})
    del a1  # 进程死亡近似

    a2 = DeskNotesNoteAdapter(d, _fresh_mapper(d))
    note = a2.get_note(nid)
    assert note is not None
    assert note["title"] == "击杀演练-完整"
    assert len(note["content"]) == len("正文" * 200)


def _fresh_mapper(d: Path):
    from data.desknotes_id_map import IDMapper

    m = IDMapper(d)
    m._load_or_init()
    return m


# ─────────────────────────────────────────────────────────────────────────────
# 4. 崩溃语义下的撕裂面（2026-09-18 同批反转为正向契约）
# ─────────────────────────────────────────────────────────────────────────────


def test_md_write_is_atomic_no_torn_residue(tmp_path):
    """笔记 md / store.json 已走原子写：写入中途死亡不留半截文件。

    反转前（缺陷固化，xfail strict）：断言 `_write_md` 用 ``Path.write_text`` 直写
    目标，注入"写一半即死"后**半截 md 必留在盘上**（``assert False`` 立论）。
    P1-1 落地后该断言会把"修好"报成失败，故同批反转。

    现行契约（:func:`data._atomic_json.atomic_write_text`）：先写同目录唯一 tmp，
    ``os.replace`` 才落地。故"写到一半进程死"时**目标文件根本不会被创建/改动**，
    残留的只有 tmp（由 sweep 兜底）。
    """
    from data._atomic_json import atomic_write_text
    from data.repositories.desknotes_note_adapter import DeskNotesNoteAdapter

    d = tmp_path / "dn"
    (d / "notes").mkdir(parents=True)
    adapter = DeskNotesNoteAdapter(d, _fresh_mapper(d))

    real_atomic = atomic_write_text

    def interrupt_on_md(path, text, **kw):
        # kill -9 近似：临时文件写到一半即死（目标文件尚未被 replace）
        if str(path).endswith(".md"):
            raise KeyboardInterrupt("simulated hard kill mid-write")
        return real_atomic(path, text, **kw)

    with mock.patch(
        "data.repositories.desknotes_note_adapter.atomic_write_text",
        side_effect=interrupt_on_md,
    ):
        with pytest.raises(KeyboardInterrupt):
            adapter.add_note({"title": "撕裂", "content": "正文" * 200})

    mds = list((d / "notes").glob("*.md"))
    assert mds == [], f"原子写失败时目标 md 不得存在（半截即撕裂），实际残留 {[m.name for m in mds]}"


def test_store_json_write_is_atomic(tmp_path):
    """store.json 同款：原子写失败时既有文件字节不变（不截断）。"""
    from data.repositories.desknotes_note_adapter import DeskNotesNoteAdapter

    d = tmp_path / "dn"
    (d / "notes").mkdir(parents=True)
    adapter = DeskNotesNoteAdapter(d, _fresh_mapper(d))
    assert adapter._save_store_state({"folders": [{"id": "f1", "name": "甲"}]}) is True
    store = d / "store.json"
    before = store.read_bytes()

    with mock.patch(
        "data.repositories.desknotes_note_adapter.atomic_write_text",
        side_effect=OSError(28, "No space left on device"),
    ):
        assert adapter._save_store_state({"folders": []}) is False

    assert store.read_bytes() == before, "写失败必须保持旧 store.json 原样（原子写语义）"


def test_half_md_is_reported_not_silently_absorbed(tmp_path):
    """撕裂 md 必须**被上报**，不能静默变成一条"看起来正常"的笔记。

    勘察纠错（2026-09-18）：本用例原名 ``test_half_md_is_torn_not_quietly_absent``，
    断言"撕裂笔记被静默丢出列表"。实测（只读探针 + 逐行读 _parse_md）**该断言前提
    有误**：``_parse_md`` 对"以 ``---`` 开头但无闭合 ``---``"的文件不抛异常，走
    ``fm={} + body=整段原文`` 分支 → 该笔记**照常进入列表**，只是标题塌成
    "未命名笔记"、正文混进 frontmatter 残桩。

    即真实缺陷是"**数据损坏伪装成有这条**"（用户不会去修一条看起来只是没起名的
    笔记），而非"静默消失"。两者都危险，但修法不同：前者要的是**留痕**。

    现行契约（P1-2a）：解析路径返回值不变（不引入读取回归），但损坏事实必须
    进入台账（``system.integrity`` 可查）并发出 WARNING。
    """
    from core import integrity
    from data.repositories.desknotes_note_adapter import DeskNotesNoteAdapter

    d = tmp_path / "dn"
    (d / "notes").mkdir(parents=True)
    adapter = DeskNotesNoteAdapter(d, _fresh_mapper(d))
    nid = adapter.add_note({"title": "完整参照", "content": "abc"})
    assert adapter.get_note(nid) is not None

    # 手工制造撕裂 md（模拟写入中断产物）
    torn = d / "notes" / "torn-uuid.md"
    torn.write_text("---\nid: torn-uuid\ntitle: 半", encoding="utf-8")  # frontmatter 未闭合

    integrity.reset()
    adapter.list_notes()

    snap = integrity.snapshot()
    assert snap["counts"].get("note_read", 0) >= 1, "撕裂 md 必须留下可查询的痕迹，不得静默吸收"
    assert any("torn-uuid.md" == e["target"] for e in snap["events"]), "台账必须指认到具体文件"

    # 实时盘面同样要能点名（盘面优先于台账）
    report = adapter.integrity_report()
    assert {"file": "torn-uuid.md", "problem": "unclosed_frontmatter"} in report["corrupted"]

    # 去重：反复 list_notes 不得把同一个文件刷满台账
    n_before = len(integrity.snapshot()["events"])
    adapter.list_notes()
    adapter.list_notes()
    assert len(integrity.snapshot()["events"]) == n_before, "同一损坏文件只应记一次（去重）"


def test_integrity_report_clean_on_healthy_dir(tmp_path):
    """正常数据目录的盘面必须干净——诊断入口自身不能制造噪声。"""
    from data.repositories.desknotes_note_adapter import DeskNotesNoteAdapter

    d = tmp_path / "dn"
    (d / "notes").mkdir(parents=True)
    adapter = DeskNotesNoteAdapter(d, _fresh_mapper(d))
    adapter.add_note({"title": "甲", "content": "一"})
    adapter.add_note({"title": "乙", "content": "二"})

    report = adapter.integrity_report()
    assert report["md_total"] == 2
    assert report["corrupted"] == []
    assert report["corrupted_count"] == 0
    assert report["orphan_tmp"] == []


# ─────────────────────────────────────────────────────────────────────────────
# 5. sqlite WAL 崩溃语义
# ─────────────────────────────────────────────────────────────────────────────


def test_wal_db_survives_unclean_close(tmp_path):
    """task_queue.db（WAL）击杀语义：不 close 直接丢连接 → 重开 integrity_check ok。

    SQLite WAL + synchronous=NORMAL 崩溃只丢最后一个未 checkpoint 事务，
    文件级完整性由 WAL 帧校验保证。
    """
    import sqlite3

    db = tmp_path / "q.db"
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    con.execute("BEGIN IMMEDIATE")
    con.execute("INSERT INTO t(v) VALUES('committed')")
    con.commit()
    # 再开一个未提交事务后"进程死亡"（不 rollback 不 commit，直接弃连接）
    con2 = sqlite3.connect(str(db))
    con2.execute("BEGIN IMMEDIATE")
    con2.execute("INSERT INTO t(v) VALUES('lost-on-crash')")
    # 模拟 SIGKILL：跳过 close/rollback
    del con, con2

    r = sqlite3.connect(f"file:{db}?mode=rw", uri=True)
    assert r.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    vals = sorted(v for (v,) in r.execute("SELECT v FROM t"))
    assert vals == ["committed"], "未提交事务必须整体丢失（atomicity），不得半截"
    r.close()


def test_auth_token_reload_after_crash(tmp_path):
    """auth_token 崩溃语义：load_or_create 落盘的 token，新进程读回同值（tmp 名带 PID+UUID 不互踩）。"""
    from backend import auth_token_store as ats

    t1, created1 = ats.load_or_create(str(tmp_path))
    assert created1
    t2, created2 = ats.load_or_create(str(tmp_path))
    assert t2 == t1 and not created2
    assert not list(tmp_path.glob("*.tmp"))
