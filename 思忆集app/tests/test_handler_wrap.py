"""复C1 静默失败包装测试 — _wrap 统一降级包装器 + 三 handler 接线。

覆盖：
1. error_body 结构契约 {code, message, degraded: True}
2. wrap_degraded 装饰器：异常 → ERR 日志（logger.exception）+ 降级返回体
3. degrade 内联助手：既有 except 点升级后 ERR 落码
4. 三 handler 接线例：_chat_context_anchor / _inbox_knowledge / _notes
5. AST 结构断言：三文件 broad-except-DEBUG 计数归零（防回归机器断言）
"""

from __future__ import annotations

import ast
import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _wrap():
    import backend.handlers._wrap as m

    return m


def _error_records(caplog, code: str):
    return [r for r in caplog.records if r.levelno >= logging.ERROR and code in r.getMessage()]


# ── 1. 错误体契约 ──


def test_error_body_structure():
    w = _wrap()
    body = w.error_body(w.CODE_CHAT_INT, "锚点构建失败")
    assert body == {"code": w.CODE_CHAT_INT, "message": "锚点构建失败", "degraded": True}
    # 错误码符合 SIYI-<域>-<类别> 草案
    assert w.CODE_MEM_DATA == "SIYI-MEM-DATA"
    assert w.CODE_STO_DATA == "SIYI-STO-DATA"


# ── 2. wrap_degraded 装饰器 ──


def test_wrap_degraded_async_dict_fallback(caplog):
    w = _wrap()

    @w.wrap_degraded(w.CODE_STO_DATA, fallback={"items": []}, message="notes.list failed")
    async def boom() -> dict:
        raise RuntimeError("repo exploded")

    with caplog.at_level(logging.ERROR):
        res = asyncio.run(boom())

    assert res["items"] == []
    assert res["code"] == w.CODE_STO_DATA
    assert res["degraded"] is True
    assert "notes.list failed" in res["message"]
    recs = _error_records(caplog, w.CODE_STO_DATA)
    assert recs, "异常必须以 ERR 级落日志且含 code"
    assert recs[0].exc_info is not None, "ERR 日志必须带 traceback（logger.exception）"


def test_wrap_degraded_sync_list_fallback(caplog):
    w = _wrap()

    @w.wrap_degraded(w.CODE_MEM_DATA, fallback=[], message="memory.search failed")
    def boom() -> list:
        raise ValueError("chroma down")

    with caplog.at_level(logging.ERROR):
        res = boom()

    assert res == []  # list 无法携带错误体，仅 ERR 日志可见 code
    assert _error_records(caplog, w.CODE_MEM_DATA)


def test_wrap_degraded_success_passthrough(caplog):
    w = _wrap()

    @w.wrap_degraded(w.CODE_STO_DATA, fallback={"items": []}, message="should not fire")
    async def ok() -> dict:
        return {"items": [1, 2]}

    with caplog.at_level(logging.ERROR):
        res = asyncio.run(ok())

    assert res == {"items": [1, 2]}, "成功路径不得混入降级键"
    assert not _error_records(caplog, w.CODE_STO_DATA)


# ── 3. degrade 内联助手 ──


def test_degrade_inside_except_logs_error_and_merges(caplog):
    w = _wrap()
    log = logging.getLogger("siyi.test.degrade")

    def run() -> dict:
        try:
            raise ValueError("boom")
        except Exception:
            return w.degrade(log, w.CODE_STO_DATA, "demo failed", fallback={"a": 1})

    with caplog.at_level(logging.ERROR):
        res = run()

    assert res == {"a": 1, "code": w.CODE_STO_DATA, "message": "demo failed", "degraded": True}
    recs = _error_records(caplog, w.CODE_STO_DATA)
    assert recs and recs[0].exc_info is not None


# ── 4a. _chat_context_anchor 接线 ──


class _BoomMemorySvc:
    def find_related(self, *a, **kw):
        raise RuntimeError("memory index broken")


def test_anchor_find_related_failure_logs_error_code(caplog):
    from backend.handlers._chat_context_anchor import build_chat_context_anchor

    w = _wrap()
    ctx = SimpleNamespace(memory_index_service=_BoomMemorySvc())
    with caplog.at_level(logging.ERROR):
        anchor = build_chat_context_anchor(ctx, "随便聊聊")

    assert "相关记忆" not in anchor, "记忆服务故障必须安静降级（不注入该段）"
    recs = _error_records(caplog, w.CODE_CHAT_INT)
    assert recs, "find_related 失败必须 ERR 级落日志且含 SIYI-CHAT-INT"


# ── 4b. _inbox_knowledge 接线 ──


class _Registry:
    def __init__(self):
        self.registered = {}

    def register(self, name, fn, risk=None, risk_resolver=None):
        self.registered[name] = fn

    def register_with_schema(self, name, fn, schema=None, risk=None, risk_resolver=None):
        self.registered[name] = fn


class _BoomKG:
    def get_relations(self, entity_id):
        raise RuntimeError("graph store broken")

    def get_all_entities(self):
        raise RuntimeError("graph store broken")

    def get_all_relations(self):
        raise RuntimeError("graph store broken")


def test_knowledge_relations_failure_degrades_to_empty(caplog):
    from backend.handlers._inbox_knowledge import register_inbox_knowledge

    w = _wrap()
    reg = _Registry()
    ctx = SimpleNamespace(knowledge_graph_store=_BoomKG())
    register_inbox_knowledge(reg, ctx)
    with caplog.at_level(logging.ERROR):
        res = asyncio.run(reg.registered["knowledge.relations"](entity_id="e1"))

    assert res == []
    assert _error_records(caplog, w.CODE_MEM_DATA), "knowledge.relations 失败必须 ERR 落码"


def test_knowledge_graph_dict_fallback_carries_error_body(caplog):
    from backend.handlers._inbox_knowledge import register_inbox_knowledge

    w = _wrap()
    reg = _Registry()
    ctx = SimpleNamespace(knowledge_graph_store=_BoomKG())
    register_inbox_knowledge(reg, ctx)
    with caplog.at_level(logging.ERROR):
        res = asyncio.run(reg.registered["knowledge.graph"]())

    # dict 降级必须携带结构化错误体（前端向后兼容：额外键）
    assert res["entities"] == [] and res["relations"] == []
    assert res["code"] == w.CODE_MEM_DATA
    assert res["degraded"] is True


# ── 4c. _notes 接线 ──


def test_notes_event_emit_failure_logs_error_code(caplog, monkeypatch):
    import backend.handlers._notes as notes_mod
    from backend.handlers._notes import register_notes

    w = _wrap()

    class _FakeNoteRepo:
        def add_note(self, info):
            return 1

        def list_notes(self, include_deleted=False):
            return []

    class _BoomBus:
        def emit(self, ev):
            raise RuntimeError("bus down")

    monkeypatch.setattr(notes_mod, "event_bus", _BoomBus())
    reg = _Registry()
    ctx = SimpleNamespace(
        personal_note_repository=_FakeNoteRepo(),
        memory_index_service=None,
        settings_repository=None,
        write_guard=None,
    )
    register_notes(reg, ctx)
    with caplog.at_level(logging.ERROR):
        res = asyncio.run(reg.registered["notes.create"](title="t", content="c"))

    assert res["id"] == 1, "事件总线故障不得阻塞保存主流程"
    assert _error_records(caplog, w.CODE_EVT_INT), "notes.changed 发射失败必须 ERR 落码"


# ── 5. AST 结构断言：broad-except-DEBUG 归零（防回归机器断言）──


_WIRED_FILES = (
    "backend/handlers/_chat_context_anchor.py",
    "backend/handlers/_inbox_knowledge.py",
    "backend/handlers/_notes.py",
)


def _broad_except_debug_sites(path: Path) -> list[int]:
    """返回 broad except（Exception/BaseException）体内含 logger.debug 调用的行号。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    sites: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        t = node.type
        broad = t is None or (isinstance(t, ast.Name) and t.id in {"Exception", "BaseException"})
        if not broad:
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "debug"
                and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id == "logger"
            ):
                sites.append(node.lineno)
                break
    return sites


@pytest.mark.parametrize("rel", _WIRED_FILES)
def test_no_broad_except_debug_in_wired_handlers(rel):
    sites = _broad_except_debug_sites(APP_ROOT / rel)
    assert sites == [], f"{rel} 仍有 broad-except+logger.debug 静默点: 行 {sites}"
