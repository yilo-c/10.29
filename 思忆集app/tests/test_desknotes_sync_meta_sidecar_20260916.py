"""S1-1 / AL-1 回归：DeskNotesNoteAdapter 的 sync 元数据 sidecar 真值化。

修复前：``_note_to_dict`` 硬编码 ``sync_state="local_only"``/``versions=[]``，
``update_note`` 白名单静默丢弃一切 sync 字段 → 冲突检测/裁决全部永不生效（幻影字段）。

修复后（方案 A：sidecar ``.sync-meta.json``）：sync 字段读写走独立文件，md 内容
文件保持干净。本测试锁死这五个契约（对应任务书 ①-⑤）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.desknotes_id_map import IDMapper  # noqa: E402
from data.desknotes_sync_meta import SIDECAR_FILENAME  # noqa: E402
from data.repositories.desknotes_note_adapter import DeskNotesNoteAdapter  # noqa: E402


def _make_adapter(tmp_path):
    ddir = tmp_path / "desknotes-data"
    ddir.mkdir(parents=True, exist_ok=True)
    mapper = IDMapper(ddir)
    return DeskNotesNoteAdapter(ddir, mapper), ddir


# ── ① 注入 sync_state="conflict" 后读回恒 conflict（修复前恒 local_only）──────
def test_injected_conflict_state_persists(tmp_path):
    adapter, _ddir = _make_adapter(tmp_path)
    nid = adapter.add_note({"remote_id": "conflict-uuid-1", "title": "t", "content": "c"})

    # 基线：新建无 sidecar 记录 → 默认 local_only（不是异常态）
    assert adapter.get_note(nid)["sync_state"] == "local_only"

    hit = adapter.update_note(nid, {"sync_state": "conflict"})
    assert hit is True
    # 关键断言：读回恒 conflict（幻影字段修复前会被洗回 local_only）
    assert adapter.get_note(nid)["sync_state"] == "conflict"
    # 跨实例（重开适配器，新 SyncMetaStore 从盘上 sidecar 读回）仍是 conflict
    # → 证明真落盘非内存假象。复用同一 mapper（int→uuid 映射加载非本用例重点）。
    adapter2 = DeskNotesNoteAdapter(_ddir, adapter._id_mapper)
    assert adapter2.get_note(nid)["sync_state"] == "conflict"


# ── ② sidecar 真实落盘，且 md frontmatter 不含 sync 字段 ───────────────────────
def test_sidecar_on_disk_and_md_stays_clean(tmp_path):
    adapter, ddir = _make_adapter(tmp_path)
    nid = adapter.add_note({"remote_id": "clean-uuid-2", "title": "t", "content": "c"})
    adapter.update_note(nid, {"sync_state": "conflict", "versions": [{"title": "old", "content": "oc"}], "dirty": False})

    sidecar = ddir / SIDECAR_FILENAME
    assert sidecar.exists(), "sidecar 文件必须真实落盘"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    rec = data["clean-uuid-2"]
    assert rec["sync_state"] == "conflict"
    assert rec["versions"] and rec["versions"][0]["content"] == "oc"

    md = next((ddir / "notes").glob("clean-uuid-2.md"))
    frontmatter = md.read_text(encoding="utf-8").split("---")[1]
    for banned in ("sync_state", "versions", "dirty:", "conflict"):
        assert banned not in frontmatter, f"md frontmatter 不得含 sync 字段 {banned!r}"


# ── ③ versions 环形上限 5 ─────────────────────────────────────────────────────
def test_versions_ring_caps_at_five(tmp_path):
    adapter, _ddir = _make_adapter(tmp_path)
    nid = adapter.add_note({"remote_id": "ring-uuid-3", "title": "t", "content": "v0"})
    # 连续 6 次内容改写 → 每次把上一版压进 versions；上限 5 应裁掉最旧
    for i in range(1, 7):
        adapter.update_note(nid, {"content": f"v{i}"})
    note = adapter.get_note(nid)
    assert len(note["versions"]) == 5, f"versions 环形上限应为 5，实得 {len(note['versions'])}"
    # 保留的是最近 5 个历史（v5..v1），最旧的 v0 已被裁掉
    contents = [v["content"] for v in note["versions"]]
    assert contents == ["v5", "v4", "v3", "v2", "v1"]


# ── ④ sidecar 缺失时默认 local_only 不炸 ──────────────────────────────────────
def test_missing_sidecar_defaults_local_only(tmp_path):
    adapter, ddir = _make_adapter(tmp_path)
    # 直接手写一个 md（绕开适配器），模拟"有笔记但无 sidecar 记录"的旧数据
    (ddir / "notes").mkdir(parents=True, exist_ok=True)
    (ddir / "notes" / "legacy-uuid-4.md").write_text(
        "---\nid: legacy-uuid-4\ntitle: legacy\n---\nbody\n",
        encoding="utf-8",
    )
    # 不存在 sidecar 文件
    assert not (ddir / SIDECAR_FILENAME).exists()
    note = adapter.get_note("legacy-uuid-4")
    assert note is not None
    assert note["sync_state"] == "local_only"
    assert note["versions"] == []
    # list 路径也不得因缺 sidecar 抛异常
    listed = adapter.list_notes()
    assert any(n["remote_id"] == "legacy-uuid-4" for n in listed)


# ── ⑤ update_note 未命中仍返 False（别破坏 S0-1 的 bool 契约）──────────────────
def test_update_miss_still_returns_false(tmp_path):
    adapter, _ddir = _make_adapter(tmp_path)
    nid = adapter.add_note({"remote_id": "bool-uuid-5", "title": "t", "content": "c"})
    assert adapter.update_note(nid, {"content": "x"}) is True
    assert adapter.update_note(99999999, {"content": "y"}) is False
    # sync 字段走同一命中判定，不得因新路径改变 bool 语义
    assert adapter.update_note(99999999, {"sync_state": "conflict"}) is False


# ── 附加：sidecar 写失败不得回滚 md（写事务配对语义）───────────────────────────
def test_sidecar_write_failure_does_not_break_md(tmp_path, monkeypatch):
    adapter, _ddir = _make_adapter(tmp_path)
    nid = adapter.add_note({"remote_id": "fail-uuid-6", "title": "t", "content": "c"})
    monkeypatch.setattr(adapter._sync_meta, "_save", lambda: False)  # 模拟 sidecar 落盘失败
    assert adapter.update_note(nid, {"content": "newbody", "sync_state": "conflict"}) is True
    # md 必须已写入新内容（不因 sidecar 失败回滚）
    note = adapter.get_note(nid)
    assert note["content"] == "newbody"
    # sidecar 失败 → 读回退默认（下次成功写自然 reconcile）
    assert note["sync_state"] == "local_only"


# ── 集成：AL-1 的终极目的——真冲突检测（_detect_conflict）在 desknotes 上生效 ────
def test_conflict_detection_end_to_end(tmp_path):
    """修复前恒 local_only → 冲突永不上报；修复后走真实 sidecar，_detect_conflict 命中。

    用生产代码 sync._cloud_sync_crypto._detect_conflict 本体（非复刻），验证：
    笔记同步过（synced + last_synced_at=T）后本地又有编辑（updated_at 前移到 now）
    且远端在 T 之后也有更新 → 判为真冲突。
    """
    from datetime import datetime, timedelta, timezone

    from sync._cloud_sync_crypto import _detect_conflict

    adapter, _ddir = _make_adapter(tmp_path)
    nid = adapter.add_note({"remote_id": "e2e-uuid-7", "title": "t", "content": "c"})
    t_sync = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    # 模拟一次成功同步（orchestrator.mark_note_as_synced 落到 update_note）
    adapter.update_note(nid, {"sync_state": "synced", "last_synced_at": t_sync})
    # 本地随后编辑 → md.updatedAt 前移到 now（> t_sync）
    adapter.update_note(nid, {"content": "local edit"})
    local = adapter.get_note(nid)
    assert local["sync_state"] == "dirty"  # 有未同步修改
    # 远端在同步点之后也有更新 → 双方都改过 = 真冲突
    remote_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).timestamp()
    assert _detect_conflict(local, remote_time) is True
    # 反例：远端更新早于上次同步点 → 非冲突（纯 LWW）
    assert _detect_conflict(local, (datetime.now(timezone.utc) - timedelta(hours=3)).timestamp()) is False
