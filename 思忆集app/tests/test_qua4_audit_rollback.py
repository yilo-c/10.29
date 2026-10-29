"""M2.3/M2.4②③ QUA-4：audit_rollback int/str id 漂移污染 + 回滚可信性。

审查档实测（comprehensive-code-review-2026-09-11 K5）：写审计时 id 被
str 化（_notes.py 的 protect("note", str(note_id))），回滚用 str 查真实
仓储（int id、严格 ==）不命中 → 走重建分支插入 str-id 副本与原 int-id
记录并存 = 数据污染。DR-3 演练注释自认此坑（靠字符串种子绕行）。

本批修复口径（SQ-23）：
① 回滚定位记录时 int/str 类型归一 + 含软删行，禁止盲插副本；
② 原条目回滚成功后置 rolled_back 终态，二次回滚拒绝（陈旧快照覆盖新改动）；
③ 当前 version != 条目 after.version → version_conflict 拒绝盲目覆盖；
④ 快照被截断（…[truncated]）→ 拒绝回滚而非写坏数据；
⑤ 不支持的 target_type 如实报 rolled_back=False（原为假 True）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest  # noqa: E402

from core.audit_rollback import AuditTrail, RollbackManager, WriteGuard  # noqa: E402
from data.local_storage import LocalDataStore  # noqa: E402
from repositories._note import LocalPersonalNoteRepository  # noqa: E402


@pytest.fixture
def env(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path / "data"))
    repo = LocalPersonalNoteRepository(store)
    trail = AuditTrail(data_dir=str(tmp_path / "audit"))
    manager = RollbackManager(store, trail, note_repository=repo)
    guard = WriteGuard(trail)
    yield {"store": store, "repo": repo, "trail": trail, "manager": manager, "guard": guard}
    store.close()


def _seed(env, title="原始标题", content="原始内容") -> int:
    return env["repo"].add_note({"title": title, "content": content})


def _drain_audit(trail) -> None:
    """阻塞至审计写线程队列清空（根除「写完立即读」的 flaky）。

    ``AuditTrail`` 的 intent/complete 只把条目投进 ``core.audit_writer`` 的单写线程
    队列（``submit_intent`` / ``submit_status`` 异步落库），而
    ``_submit_status`` 会先 ``fetch_entry`` 做存在性校验、``rollback`` /
    ``list_entries`` 同样直读 SQLite。测试里「写完立刻读」因此存在竞态窗口：
    intent 未落库 → 终态被静默丢弃（``record_write_complete`` 返回 False）；
    读早于写 → ``entry_not_found`` / ``IndexError``。``drain()`` 即 ``Queue.join()``，
    显式建立 happens-before。
    """
    trail.writer.drain()


def _latest_entry(manager) -> dict:
    """取最近一条审计（先 drain 写队列，再读）。"""
    _drain_audit(manager.audit_trail)
    return manager.audit_trail.list_entries(limit=1)[0]


def _guarded_update(env, nid: int, new_info: dict) -> str:
    """复刻 _notes.py 的写保护路径：target_id 被 str 化（漂移源头）。"""
    repo = env["repo"]
    before = repo.get_note(nid)
    entry_id = env["trail"].record_write_intent(
        actor="user", action_id="notes.update", target_type="note",
        target_id=str(nid), operation="update", before_snapshot=before,
    )
    _drain_audit(env["trail"])  # intent 必须先落库，否则下面的 complete 会被判「条目不存在」丢弃
    repo.update_note(nid, new_info)
    after = repo.get_note(nid)
    assert env["trail"].record_write_complete(entry_id, after_snapshot=after) is True
    _drain_audit(env["trail"])  # 终态同样异步落库，rollback 直读 SQLite
    return entry_id


def test_rollback_note_update_no_duplicate_str_id(env):
    """K5 主案：str target_id 回滚 int 笔记 → 恢复原记录，禁止 str-id 副本。"""
    nid = _seed(env)
    entry_id = _guarded_update(env, nid, {"title": "改后"})
    result = env["manager"].rollback(entry_id)
    assert result["rolled_back"] is True, result.get("reason")
    rows = env["repo"].list_notes(include_deleted=True)
    assert len(rows) == 1, f"回滚不得制造 id 漂移副本，实际 {rows}"
    assert rows[0]["id"] == nid
    assert env["repo"].get_note(nid)["title"] == "原始标题"


def test_rollback_soft_delete_restores_original_row(env):
    """软删回滚：恢复原 int-id 记录（修复前误走重建分支 → 双记录并存）。"""
    nid = _seed(env)
    before = env["repo"].get_note(nid)
    entry_id = env["trail"].record_write_intent(
        actor="user", action_id="notes.delete", target_type="note",
        target_id=str(nid), operation="delete", before_snapshot=before,
    )
    _drain_audit(env["trail"])
    env["repo"].delete_note(nid)
    assert env["trail"].record_write_complete(entry_id, after_snapshot=None) is True
    _drain_audit(env["trail"])
    assert env["repo"].get_note(nid) is None

    result = env["manager"].rollback(entry_id)
    assert result["rolled_back"] is True, result.get("reason")
    restored = env["repo"].get_note(nid)
    assert restored is not None, "软删回滚必须恢复原记录（含 id 类型）"
    assert len(env["repo"].list_notes(include_deleted=True)) == 1


def test_rollback_twice_refused_and_chain(env):
    """rollback→再改→再 rollback 链：重复回滚同一条目必须拒绝；回滚新条目成功。"""
    nid = _seed(env)
    e1 = _guarded_update(env, nid, {"title": "第一次改", "content": "B"})
    first = env["manager"].rollback(e1)
    assert first["rolled_back"] is True

    second = env["manager"].rollback(e1)
    assert second["rolled_back"] is False
    assert "rolled_back" in str(second.get("reason")), second

    e2 = _guarded_update(env, nid, {"title": "第二次改", "content": "C"})
    third = env["manager"].rollback(e2)
    assert third["rolled_back"] is True, third.get("reason")
    note = env["repo"].get_note(nid)
    assert note["title"] == "原始标题" and note["content"] == "原始内容"
    rows = env["repo"].list_notes(include_deleted=True)
    assert len(rows) == 1


def test_rollback_version_conflict_refused(env):
    """当前 version != after.version：有外部改动，拒绝盲目覆盖（SQ-23）。"""
    nid = _seed(env)
    entry_id = _guarded_update(env, nid, {"title": "审计内改"})
    env["repo"].update_note(nid, {"title": "外部旁路改"})  # 不经审计再推一版
    result = env["manager"].rollback(entry_id)
    assert result["rolled_back"] is False
    assert "version_conflict" in str(result.get("reason"))
    assert env["repo"].get_note(nid)["title"] == "外部旁路改"


def test_rollback_truncated_snapshot_refused(env):
    """before 快照 content 被 2000 字符截断：拒绝回滚而非把截断文写回。"""
    nid = _seed(env)
    long_text = "字" * (AuditTrail.MAX_SNAPSHOT_CHARS + 500)
    before = dict(env["repo"].get_note(nid))
    before["content"] = long_text
    entry_id = env["trail"].record_write_intent(
        actor="user", action_id="notes.update", target_type="note",
        target_id=str(nid), operation="update", before_snapshot=before,
    )
    _drain_audit(env["trail"])
    env["repo"].update_note(nid, {"title": "新标题"})
    assert env["trail"].record_write_complete(entry_id, after_snapshot=env["repo"].get_note(nid)) is True
    _drain_audit(env["trail"])

    result = env["manager"].rollback(entry_id)
    assert result["rolled_back"] is False
    assert "snapshot_truncated" in str(result.get("reason"))
    stored = env["repo"].get_note(nid)
    assert stored["content"] != long_text[: AuditTrail.MAX_SNAPSHOT_CHARS] + "…[truncated]"


def test_rollback_unsupported_target_type_is_honest_failure(env):
    """⑤：不支持的 target_type 必须如实失败（修复前 warning 后假报 True）。"""
    entry_id = env["trail"].record_write_intent(
        actor="user", action_id="weird.write", target_type="quantum_ledger",
        target_id="x", operation="update", before_snapshot={"a": 1},
    )
    _drain_audit(env["trail"])
    assert env["trail"].record_write_complete(entry_id, after_snapshot={"a": 2}) is True
    _drain_audit(env["trail"])
    result = env["manager"].rollback(entry_id)
    assert result["rolled_back"] is False
    assert "unsupported_target_type" in str(result.get("reason"))


# ── M2.4③：MCP 文件工具 WriteGuard 快照的「可回滚实效」 ──


class _FakeRouter:
    _DEFAULT_DOWNLOAD_DIRNAME = "assistant_downloads"

    def __init__(self, tmp_path: Path, guard) -> None:
        self.shell_execution = SimpleNamespace(workspace_root=str(tmp_path / "workspace"))
        self.data_store = SimpleNamespace(base_dir=str(tmp_path / "data"))
        self.write_guard = guard
        Path(self.data_store.base_dir).mkdir(parents=True, exist_ok=True)
        Path(self.shell_execution.workspace_root).mkdir(parents=True, exist_ok=True)


@pytest.fixture
def file_env(tmp_path):
    from assistant.capability._file_ops import FileCapabilityDomain
    from core.audit_rollback import AuditTrail as _AT

    trail = _AT(data_dir=str(tmp_path / "audit"))
    router = _FakeRouter(tmp_path, WriteGuard(trail))
    manager = RollbackManager(SimpleNamespace(save_json=lambda *a: None), trail)
    dom = FileCapabilityDomain(router)
    return {"dom": dom, "trail": trail, "manager": manager, "root": Path(router.data_store.base_dir)}


def test_file_ops_write_new_rollback_removes_file(file_env):
    """file_write 新建 → 回滚删除落盘文件（修复前 file 型假成功、文件残留）。"""
    dom, manager, root = file_env["dom"], file_env["manager"], file_env["root"]
    target = root / "a.txt"
    result = dom.file_write({"path": str(target), "content": "hello"})
    assert result["success"] is True
    entry = _latest_entry(manager)
    rb = manager.rollback(entry["entry_id"])
    assert rb["rolled_back"] is True, rb.get("reason")
    assert not target.exists(), "回滚必须移除 agent 新建文件"


def test_file_ops_write_overwrite_rollback_restores_content(file_env):
    """file_write 覆盖 → 回滚恢复旧内容（delete/overwrite 高危类）。"""
    dom, manager, root = file_env["dom"], file_env["manager"], file_env["root"]
    target = root / "b.txt"
    target.write_text("ORIG", encoding="utf-8")
    dom.file_write({"path": str(target), "content": "NEW"})
    entry = _latest_entry(manager)
    rb = manager.rollback(entry["entry_id"])
    assert rb["rolled_back"] is True, rb.get("reason")
    assert target.read_text(encoding="utf-8") == "ORIG"


def test_file_ops_delete_rollback_recreates_same_bytes(file_env):
    """file_delete → 回滚按快照原样重建，长文本不被 2000 截断毁掉。"""
    dom, manager, root = file_env["dom"], file_env["manager"], file_env["root"]
    target = root / "c.txt"
    payload = "内容很长" * 1000  # 4000 字符 > MAX_SNAPSHOT_CHARS(2000)
    target.write_text(payload, encoding="utf-8")
    result = dom.file_delete({"path": str(target)})
    assert result["success"] is True
    assert not target.exists()
    entry = _latest_entry(manager)
    rb = manager.rollback(entry["entry_id"])
    assert rb["rolled_back"] is True, rb.get("reason")
    assert target.read_text(encoding="utf-8") == payload, "恢复必须无损（禁止截断标记入库）"


def test_file_ops_copy_overwrite_rollback_restores_dest(file_env):
    """file_copy overwrite：必须快照 dest 旧内容，回滚恢复 dest（修复前只有 source 快照）。"""
    dom, manager, root = file_env["dom"], file_env["manager"], file_env["root"]
    src = root / "src.txt"
    dst = root / "dst.txt"
    src.write_text("PAYLOAD", encoding="utf-8")
    dst.write_text("DST-ORIG", encoding="utf-8")
    result = dom.file_copy({"source": str(src), "dest": str(dst), "overwrite": True})
    assert result["success"] is True
    assert dst.read_text(encoding="utf-8") == "PAYLOAD"
    entry = _latest_entry(manager)
    rb = manager.rollback(entry["entry_id"])
    assert rb["rolled_back"] is True, rb.get("reason")
    assert dst.read_text(encoding="utf-8") == "DST-ORIG"


def test_file_ops_oversized_snapshot_rollback_refused_not_corrupting(file_env):
    """>65536 快照不可完整恢复：如实拒绝，而不是写出半截/带标记的文件。"""
    dom, manager, root = file_env["dom"], file_env["manager"], file_env["root"]
    target = root / "big.txt"
    payload = "x" * 70000
    target.write_text(payload, encoding="utf-8")
    dom.file_delete({"path": str(target)})
    entry = _latest_entry(manager)
    rb = manager.rollback(entry["entry_id"])
    assert rb["rolled_back"] is False
    assert "truncated" in str(rb.get("reason")) or "not_restorable" in str(rb.get("reason"))


def test_file_ops_web_download_guarded(file_env, monkeypatch):
    """web_download 纳入写前快照：新建下载文件回滚即删除（原完全裸奔）。"""
    import urllib.request

    dom, manager, root = file_env["dom"], file_env["manager"], file_env["root"]
    download_root = root / "assistant_downloads"
    download_root.mkdir(parents=True, exist_ok=True)
    saved = download_root / "f.bin"

    def fake_urlretrieve(url, filename):
        Path(filename).write_text("downloaded", encoding="utf-8")
        return filename, {}

    monkeypatch.setattr(urllib.request, "urlretrieve", fake_urlretrieve)
    result = dom.web_download({"url": "http://127.0.0.1/f.bin", "save_path": str(saved)})
    assert result["success"] is True
    assert saved.exists()
    _drain_audit(manager.audit_trail)
    entries = [e for e in manager.audit_trail.list_entries(limit=5) if e.get("action_id") == "file.download"]
    assert entries, "web_download 必须落 WriteGuard 审计"
    rb = manager.rollback(entries[-1]["entry_id"])
    assert rb["rolled_back"] is True, rb.get("reason")
    assert not saved.exists()
