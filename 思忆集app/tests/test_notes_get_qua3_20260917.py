"""N-B（2026-09-17 夜间批）：notes.get 的 QUA-3 int/str 类型归一契约测试。

背景：RPC schema 允许 note_id 为 ["string","integer"]（_notes.py notes.get
注册处），但 LocalPersonalNoteRepository.get_note 用严格 ==（repositories/
_note.py:61），存储 id 为 int —— 前端/调用方以 str 传 id（JSON 往返、
uuid、URL 参数等常见路径）时恒不命中 → notes.get 静默返回 None，
调用方误判「笔记不存在」。与已修的 update/delete/batch_delete/
permanent_delete（QUA-3，2026-09-11/16 批）同款病，本轮收口。

修法（backend/handlers/_notes.py notes_get）：
① fast path 原样 repo.get_note(note_id) —— 合法 int 传参不增加任何开销；
② 未命中时用 _resolve_note_id 做 int/str 归一探活，命中真实 id 再读；
③ 仍未命中如实 None；软删笔记经 get_note 幽灵读防护仍 None，语义不变。

契约（本文件锁死）：
- C1 str(nid) 必须命中 int 记录（修复前 None = 假「不存在」）；
- C2 int(nid) fast path 行为不变（合法读不误伤，且不触发归一扫描）；
- C3 不存在的 id 两种传型都如实 None（不得 ghost 读到别的笔记）；
- C4 软删笔记 str 传参仍 None（幽灵读防护不因归一失效）；
- C5 str-id 存储（uuid 型适配器同款协议）int 传参归一命中。

隔离：真实 LocalDataStore + LocalPersonalNoteRepository 全在 tmp_path
（与 test_qua3_notes_delete.py 同款），绝不触碰真实数据目录。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._notes import register_notes  # noqa: E402
from data.local_storage import LocalDataStore  # noqa: E402
from repositories._note import LocalPersonalNoteRepository  # noqa: E402


class _Registry:
    def __init__(self):
        self.registered: dict[str, object] = {}

    def register(self, name, fn, risk=None, risk_resolver=None) -> None:
        self.registered[name] = fn

    def register_with_schema(self, name, fn, schema=None, risk=None, risk_resolver=None) -> None:
        self.registered[name] = fn


def _make_env(tmp_path: Path):
    store = LocalDataStore(base_dir=str(tmp_path / "data"))
    repo = LocalPersonalNoteRepository(store)
    ctx = SimpleNamespace(
        personal_note_repository=repo,
        write_guard=None,
        memory_index_service=None,
        settings_repository=None,
        ai_client=None,
    )
    reg = _Registry()
    register_notes(reg, ctx)
    return store, repo, reg


def _seed(repo) -> int:
    nid = repo.add_note({"title": "t", "content": "c"})
    assert isinstance(nid, int), "真实仓储 next_id 应生成 int id（QUA-3 前提）"
    return nid


# ───────────────────────── C1: str-id 命中（主案） ─────────────────────────
def test_notes_get_string_id_hits_real_note(tmp_path):
    """C1 主案：str(nid) 必须读到 int id 笔记（修复前恒 None = 假「不存在」）。"""
    store, repo, reg = _make_env(tmp_path)
    try:
        nid = _seed(repo)
        note = asyncio.run(reg.registered["notes.get"](note_id=str(nid)))
        assert note is not None, "str-id 不得再返回 None（N-B 主案）"
        assert note.get("id") == nid and note.get("content") == "c"
    finally:
        store.close()


# ───────────────────────── C2: int fast path 不误伤 ─────────────────────────
def test_notes_get_int_id_fast_path_unchanged(tmp_path):
    """C2：int 传参走原 get_note fast path；不得因归一改造引入额外全量扫描。"""
    store, repo, reg = _make_env(tmp_path)
    try:
        nid = _seed(repo)
        note = asyncio.run(reg.registered["notes.get"](note_id=nid))
        assert note is not None and note.get("id") == nid

        # fast path 命中时 _resolve_note_id 不得被调用（O(n) 扫描仅在 miss 后兜底）
        calls: list = []
        original = repo.get_note

        def counting_get(note_id):
            calls.append(note_id)
            return original(note_id)

        repo.get_note = counting_get  # type: ignore[method-assign]
        try:
            hit = asyncio.run(reg.registered["notes.get"](note_id=nid))
            assert hit is not None
            assert len(calls) == 1, f"int fast path 只应查一次，实际 {len(calls)} 次"
        finally:
            repo.get_note = original  # type: ignore[method-assign]
    finally:
        store.close()


# ───────────────────────── C3: 不存在 id 如实 None ─────────────────────────
def test_notes_get_missing_id_returns_false_none(tmp_path):
    """C3：ghost id 两种传型都 None——归一不得让不存在读到别的笔记。"""
    store, repo, reg = _make_env(tmp_path)
    try:
        _seed(repo)
        assert asyncio.run(reg.registered["notes.get"](note_id=99999999)) is None
        assert asyncio.run(reg.registered["notes.get"](note_id="99999999")) is None
    finally:
        store.close()


# ───────────────────────── C4: 软删幽灵读防护保留 ─────────────────────────
def test_notes_get_soft_deleted_string_id_still_none(tmp_path):
    """C4：回收站笔记 str 传参仍 None——get_note 幽灵读语义不得被归一绕过。

    _resolve_note_id 探活本身走 get_note（默认不含软删），因此软删行
    探不到 → notes.get 维持 None。permanent_delete 需要命中软删行时
    显式 include_deleted=True，是另一条路径，不影响本契约。
    """
    store, repo, reg = _make_env(tmp_path)
    try:
        nid = _seed(repo)
        asyncio.run(reg.registered["notes.delete"](note_id=nid))
        assert repo.get_note(nid) is None, "前置：软删后 get_note 返回 None"
        assert asyncio.run(reg.registered["notes.get"](note_id=str(nid))) is None, "str-id 归一不得复活幽灵读"
        assert asyncio.run(reg.registered["notes.get"](note_id=nid)) is None
    finally:
        store.close()


# ───────────────────────── C5: str-id 存储反向归一 ─────────────────────────
def test_notes_get_int_id_on_str_store_resolves(tmp_path):
    """C5：uuid 型仓储（存储 id 为 str，desknotes_note_adapter 同款协议）
    以 int 传参——经 str(id) 候选归一命中。"""
    store = LocalDataStore(base_dir=str(tmp_path / "data"))
    repo = LocalPersonalNoteRepository(store)
    uuid_id = "abc-123"
    repo.add_note({"id": uuid_id, "title": "t", "content": "u"})
    ctx = SimpleNamespace(
        personal_note_repository=repo,
        write_guard=None,
        memory_index_service=None,
        settings_repository=None,
        ai_client=None,
    )
    reg = _Registry()
    register_notes(reg, ctx)
    try:
        # 直接 str 命中 fast path
        assert asyncio.run(reg.registered["notes.get"](note_id=uuid_id)) is not None
        # int 传参（如 123）无法归一到 "abc-123"，如实 None，不得乱命中
        assert asyncio.run(reg.registered["notes.get"](note_id=123)) is None
    finally:
        store.close()


# ───────────────────────── 回归面：repo 缺失兜底 ─────────────────────────
def test_notes_get_repo_unavailable_returns_none(tmp_path):
    ctx = SimpleNamespace(
        personal_note_repository=None,
        write_guard=None,
        memory_index_service=None,
        settings_repository=None,
        ai_client=None,
    )
    reg = _Registry()
    register_notes(reg, ctx)
    assert asyncio.run(reg.registered["notes.get"](note_id="1")) is None
