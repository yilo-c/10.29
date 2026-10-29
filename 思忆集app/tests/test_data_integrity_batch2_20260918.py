"""批2 数据完整性契约测试（P1-1/P1-2/P1-3/P1-4/P2-3/P2-4 + core.integrity 基元）。

与 ``test_env_drill_t1/t3_20260917.py`` 的分工：
- 那两份是**演练现场**（注入故障复现崩溃语义），本文件是**契约固化**——
  把本批每处修复的行为约定写成机器断言，防止后续会话把修复改回去
  （AGENTS.md 守则 #7：修复侧优先把结论固化为机器断言）。

覆盖：
1. ``core.integrity`` 基元：入账 + 打日志不可分割、有界、快照可序列化、不抛
2. ``list_orphan_tmp`` / ``sweep_orphan_tmp``：**点开头文件必须命中**（glob 陷阱）、
   年龄门槛、非递归、只认已知命名
3. ``system.integrity`` RPC：注册存在 + 返回形状（盘面 + 台账）
4. 反复发静态断言：笔记两文件不得再出现裸 ``write_text`` 直写目标
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest import mock

import pytest

# 读取源码一律基于 __file__ 反推**绝对路径**，**不用相对路径**：
# 相对路径会让测试对 cwd 产生隐含依赖（从仓库根跑就挂、从 `思忆集app/` 跑才过），
# 2026-09-18 独立复核（`deliverables/clean-review-batch2-2026-09-18.md`）已捕获本文件
# 的 --repo 两处写法并判定为"需修"。测试必须与 cwd 无关。
APP_ROOT = Path(__file__).resolve().parents[1]  # tests/ -> 思忆集app/

# ─────────────────────────────────────────────────────────────────────────────
# 1. core.integrity 基元
# ─────────────────────────────────────────────────────────────────────────────


def test_integrity_record_and_snapshot(caplog):
    """record 必须同时入台账 + 打 WARNING（"入账"与"打日志"不可分裂）。"""
    import logging

    from core import integrity

    integrity.reset()
    with caplog.at_level(logging.WARNING):
        integrity.record("demo_kind", "出事了", target="obj-1")

    snap = integrity.snapshot()
    assert snap["counts"] == {"demo_kind": 1}
    assert len(snap["events"]) == 1
    ev = snap["events"][0]
    assert ev["kind"] == "demo_kind" and ev["target"] == "obj-1" and ev["detail"] == "出事了"
    assert isinstance(ev["at"], float) and ev["at"] > 0
    # JSON 可序列化（RPC 直接回传）
    assert json.loads(json.dumps(snap))["counts"] == {"demo_kind": 1}
    assert any("obj-1" in r.getMessage() for r in caplog.records), "台账入账必须同时发出 WARNING"


def test_integrity_is_bounded_and_counts_drops():
    """台账有界（MAX_EVENTS），溢出计数不丢——无界会随长期运行吃内存。"""
    from core import integrity

    integrity.reset()
    for i in range(integrity.MAX_EVENTS + 7):
        integrity.record("flood", f"e{i}")

    snap = integrity.snapshot()
    assert len(snap["events"]) == integrity.MAX_EVENTS
    assert snap["counts"]["flood"] == integrity.MAX_EVENTS + 7
    assert snap["dropped"] == 7


def test_integrity_record_never_raises():
    """观测通道自身不得成为故障源：任何入参都不抛。"""
    from core import integrity

    integrity.reset()
    integrity.record("", "")  # 空入参
    integrity.record("k", "d", target=None)  # type: ignore[arg-type]
    assert integrity.snapshot()["counts"].get("k") == 1


# ─────────────────────────────────────────────────────────────────────────────
# 2. 孤儿 tmp 探测 / 清扫契约
# ─────────────────────────────────────────────────────────────────────────────


def _age(p: Path, days: float) -> None:
    t = time.time() - days * 86400
    os.utime(p, (t, t))


def test_list_orphan_tmp_matches_dot_prefixed(tmp_path):
    """**点开头的孤儿必须命中**——glob.glob("*.tmp") 不匹配点开头文件，
    而真实孤儿 43/45 是 ``.ai_cache_*.tmp``。此断言就是那次先红后绿的固化。
    """
    from data._atomic_json import list_orphan_tmp

    dot = tmp_path / ".ai_cache_ab12cd34.tmp"
    dot.write_text("x", encoding="utf-8")
    _age(dot, 3)

    got = list_orphan_tmp(str(tmp_path))
    assert got == [".ai_cache_ab12cd34.tmp"], f"点开头的已知命名孤儿必须被探测到，实际 {got}"


def test_orphan_tmp_known_shapes_only(tmp_path):
    """三类已知命名命中；任意 *.tmp 一律不碰（宁可漏清不可误删）。"""
    from data._atomic_json import list_orphan_tmp

    cases = {
        "report.json.1234.deadbeefdeadbeefdeadbeefdeadbeef.tmp": True,  # 本模块命名
        ".ai_cache_zzz999.tmp": True,  # ai cache mkstemp
        "siyi-abc.json.tmp": True,  # 旧 LocalDataStore 遗留
        "backup.tmp": False,  # 未知形状
        "notes.md.tmp": False,  # 未知形状
        "siyi-abc.tmp": False,  # 少 .json 段
        ".ai_cache_.tmp": False,  # 空随机段
    }
    for name, expected in cases.items():
        p = tmp_path / name
        p.write_text("x", encoding="utf-8")
        _age(p, 3)
        got = list_orphan_tmp(str(tmp_path))
        assert (name in got) is expected, f"{name}: 期望命中={expected}，实际列表={got}"


def test_orphan_tmp_age_threshold_and_nonrecursive(tmp_path):
    """年龄门槛内不删（可能是在途写）；子目录不递归（边界清晰）。"""
    from data._atomic_json import list_orphan_tmp

    fresh = tmp_path / "siyi-fresh.json.tmp"
    fresh.write_text("x", encoding="utf-8")

    sub = tmp_path / "sub"
    sub.mkdir()
    nested = sub / "siyi-nested.json.tmp"
    nested.write_text("x", encoding="utf-8")
    _age(nested, 30)

    got = list_orphan_tmp(str(tmp_path))
    assert got == [], f"门槛内的与子目录内的一律不得命中，实际 {got}"
    assert list_orphan_tmp(str(sub)) == ["siyi-nested.json.tmp"], "对子目录单独调用时才应命中"


def test_sweep_reports_removed_even_if_platform_shim_raises(tmp_path):
    """平台"安全删除 shim"在文件已移走后仍抛错时，不得谎报清理失败。

    实测（2026-09-18）：WorkBuddy CLI 的 safe-delete shim 把删除改道回收站，
    Windows 下会抛 ``SHFileOperationW 失败: 0x2``，但文件**确实已消失**。
    判据以盘面为准（文件是否还在），不以调用是否抛错为准。
    """
    from data import _atomic_json

    stale = tmp_path / "siyi-x.json.tmp"
    stale.write_text("{}", encoding="utf-8")
    _age(stale, 30)

    real_remove = os.remove

    def spurious(self_path):
        real_remove(self_path)
        raise OSError("SHFileOperationW 失败: 0x2")

    with mock.patch("data._atomic_json.os.remove", side_effect=spurious):
        removed = _atomic_json.sweep_orphan_tmp(str(tmp_path))

    assert removed == ["siyi-x.json.tmp"], "文件已消失就必须计入 removed（否则谎报失败）"
    assert not stale.exists()


def test_sweep_keeps_file_when_removal_truly_fails(tmp_path):
    """真删不掉（权限/占用）时必须保留文件 + 记 warning + 不计入 removed。"""
    from data import _atomic_json

    stale = tmp_path / "siyi-y.json.tmp"
    stale.write_text("{}", encoding="utf-8")
    _age(stale, 30)

    with mock.patch("data._atomic_json.os.remove", side_effect=PermissionError(13, "denied")):
        removed = _atomic_json.sweep_orphan_tmp(str(tmp_path))

    assert removed == []
    assert stale.exists(), "真失败不得移除文件"


def test_ai_cache_sweeps_own_dir_on_init(tmp_path):
    """ai/client/_cache.py 构造时清扫自己目录（写入者清理自己的目录）。"""
    from ai.client._cache import AiResponseCache

    cache_dir = tmp_path / "ai_cache"
    cache_dir.mkdir()
    orphan = cache_dir / ".ai_cache_oldoldold.tmp"
    orphan.write_text("x", encoding="utf-8")
    _age(orphan, 5)

    AiResponseCache(str(cache_dir / "responses.json"))
    assert not orphan.exists(), "AI 缓存构造时必须清掉本目录的过期孤儿 tmp"


# ─────────────────────────────────────────────────────────────────────────────
# 3. system.integrity RPC
# ─────────────────────────────────────────────────────────────────────────────


class _FakeRepo:
    def integrity_report(self):
        return {
            "notes_dir": "/tmp/notes",
            "md_total": 3,
            "corrupted": [{"file": "bad.md", "problem": "unclosed_frontmatter"}],
            "corrupted_count": 1,
            "orphan_tmp": [],
            "orphan_tmp_data_dir": [],
            "ledger": {},
        }


def _register_system(registry, context):
    from backend.handlers._system_settings import register_system_settings

    register_system_settings(registry, context, 18765, mock.Mock())


class _Registry:
    def __init__(self):
        self.handlers: dict = {}

    def register(self, name, fn, **kw):
        self.handlers[name] = fn

    def register_with_schema(self, name, fn, **kw):
        self.handlers[name] = fn

    def list_methods(self):
        return sorted(self.handlers)


def test_system_integrity_registered_and_shape():
    """system.integrity 存在，且同时返回**实时盘面**与**进程台账**两段。"""
    import asyncio

    from core import integrity

    integrity.reset()
    integrity.record("cascade", "notes.delete 级联清理部分失败：kg: save failed", target="n1")

    ctx = mock.Mock()
    ctx.personal_note_repository = _FakeRepo()
    reg = _Registry()
    _register_system(reg, ctx)

    assert "system.integrity" in reg.handlers, "system.integrity 未注册"
    out = asyncio.run(reg.handlers["system.integrity"]())

    assert set(out) >= {"ledger", "notes"}
    assert out["notes"]["corrupted_count"] == 1, "盘面必须如实反映损坏文件"
    assert out["ledger"]["counts"]["cascade"] == 1, "台账必须带上进程内已发生的事件"


def test_system_integrity_without_desknotes_repo():
    """非 desk-notes 数据源（本地 JSON 存储）时不得崩，如实说明原因。"""
    import asyncio

    ctx = mock.Mock()
    ctx.personal_note_repository = object()  # 无 integrity_report
    reg = _Registry()
    _register_system(reg, ctx)

    out = asyncio.run(reg.handlers["system.integrity"]())
    assert out["notes"] is None
    assert "notes_unavailable_reason" in out


# ─────────────────────────────────────────────────────────────────────────────
# 4. 反复发静态断言（机器断言替代"记得别改回去"）
# ─────────────────────────────────────────────────────────────────────────────


def test_note_files_have_no_bare_write_text():
    """笔记两文件（md / store.json）不得再出现裸 ``write_text`` 直写目标。

    P1-1 治本点：全仓只剩这两个文件用裸写直写目标（graph / id_map / sidecar
    早已原子化）。本断言防"后续会话顺手改回裸写"。
    """
    import re

    src = (APP_ROOT / "data/repositories/desknotes_note_adapter.py").read_text(encoding="utf-8")
    # 排除 atomic_write_text（那正是应走的路径）；裸 write_text 应为 0 处
    hits = [m.group(0) for m in re.finditer(r"(?<!atomic_)write_text\s*\(", src)]
    assert hits == [], f"desknotes_note_adapter 又出现裸 write_text 直写（应走 atomic_write_text）：{hits}"


def test_cascade_call_sites_all_consume_return_value():
    """6 处级联调用点必须全部消费返回值（P1-4 反复发）。"""
    import re

    for rel, expected in (("backend/handlers/_notes.py", 4), ("backend/handlers/_tasks.py", 3)):
        src = (APP_ROOT / rel).read_text(encoding="utf-8")
        consumed = len(re.findall(r"report_cascade_failures\(", src))
        assert consumed == expected, f"{rel}: 应消费 {expected} 处，实际 {consumed}"


def test_md_problem_detects_unclosed_frontmatter(tmp_path):
    """_md_problem 与 _parse_md 判据同源：未闭合 frontmatter 必须被点名。"""
    from data.repositories.desknotes_note_adapter import DeskNotesNoteAdapter
    from data.desknotes_id_map import IDMapper

    d = tmp_path / "dn"
    (d / "notes").mkdir(parents=True)
    idm = IDMapper(d)
    idm._load_or_init()
    adapter = DeskNotesNoteAdapter(d, idm)

    good = d / "notes" / "good.md"
    good.write_text("---\nid: good\ntitle: 好\n---\n\n正文", encoding="utf-8")
    assert adapter._md_problem(good) is None

    bad = d / "notes" / "bad.md"
    bad.write_text("---\nid: bad\ntitle: 坏", encoding="utf-8")
    assert adapter._md_problem(bad) == "unclosed_frontmatter"

    # 无 frontmatter 的普通 md 不算损坏（可能是外部导入的纯正文）
    plain = d / "notes" / "plain.md"
    plain.write_text("就是一段正文", encoding="utf-8")
    assert adapter._md_problem(plain) is None


@pytest.mark.parametrize("kind", ["note_store", "note_read", "cascade", "tmp_sweep"])
def test_integrity_kind_constants_exist(kind):
    """kind 常量集中定义，避免各处写裸字符串漂移。"""
    from core import integrity

    assert getattr(integrity, f"KIND_{kind.upper()}") == kind
