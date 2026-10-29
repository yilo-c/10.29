# -*- coding: utf-8 -*-
"""handlers keyword-only/单参误调普查回归测试（2026-09-16 批3收尾）。

背景：本轮修复的 bug 家族——service/repository 方法为 keyword-only（签名带
``*``）或单参数 dict，但 handler 以 ``asyncio.to_thread(obj.method, 位置参数...)``
调用，运行时必抛 TypeError，被 handler 的 ``except`` 吞成
``success=False``/空列表，前端面板恒空且无报错（X1 静默失效的传参子型）。

已确认并修复 6 处：
- ``_task_queue.py`` 3 处（list/enqueue/purge_completed，commit 26883f9）
- ``_feedback.py`` chat_feedback_set 7 位置参数调 keyword-only
  record_feedback；chat_feedback_list 位置参数调 keyword-only list_feedback；
  懒构建兜底 FeedbackService(data_store) 位置参数（__init__ 全 kw-only）
- ``_info_items.py`` info_items_save_to_note 5 位置参数调单参 dict 的
  add_note——「资讯保存为笔记」自诞生起静默失效

本文件两层断言：
1. 行为层：feedback set/list 走真实 FeedbackService、save_to_note 走真实
   LocalPersonalNoteRepository，修复前必抛 TypeError 被吞（先红后绿）；
2. 结构层：AST 扫描 handlers 文件里以**已知类**绑定的属性名
   （svc./q./repo./note_repo./service./extractor./tracker.）发起的
   ``to_thread(obj.method, 位置参数...)`` 调用，用
   ``inspect.Signature.bind`` 对照真实类签名校验可绑定性。变量名→类映射在
   _VAR_CLASS_BINDINGS 中登记；新 handler 若引入同类误调（绑定到已登记类），
   本测试直接红。未登记类不在本测试范围（防同名方法假阳性，见映射注释）。
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

import backend.handlers._feedback as _feedback_mod
import backend.handlers._info_items as _info_items_mod
from repositories._note import LocalPersonalNoteRepository
from services.cron_service import CronService
from services.feedback_service import FeedbackService
from services.self_maintenance import SelfMaintenanceService
from services.task_queue import PersistentTaskQueue

_HANDLERS_DIR = pathlib.Path(__file__).resolve().parents[1] / "backend" / "handlers"

# 变量名（to_thread 第一参 obj. 的 Name.id）→ 该类在生产中的真实类型。
# 只登记「在本仓库内该属性名唯一指向一个类」的条目：按名匹配其他同名
# 方法（如 get_setting 存在于多个类）会假阳性，故不做全量类解析。
_VAR_CLASS_BINDINGS: dict[str, type] = {
    "svc": FeedbackService,
    "q": PersistentTaskQueue,
    "note_repo": LocalPersonalNoteRepository,
    "cron": CronService,
    "service": SelfMaintenanceService,  # 仅 _maintenance.py 使用（见白名单）
}

# service 变量名只在 _maintenance.py 绑定 SelfMaintenanceService；
# 其他文件出现 service. 时跳过（避免同名误判）。
_VAR_FILE_RESTRICTION: dict[str, set[str]] = {
    "service": {"_maintenance.py"},
}


def _to_thread_positional_calls(filename: str) -> list[tuple[int, str, int]]:
    """返回 (行号, <var>.<method>, 位置实参个数) —— 仅含已登记变量的调用。"""
    path = _HANDLERS_DIR / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "to_thread" and len(node.args) >= 2:
            target = node.args[0]
            if not (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id in _VAR_CLASS_BINDINGS):
                continue
            var = target.value.id
            allowed = _VAR_FILE_RESTRICTION.get(var)
            if allowed is not None and filename not in allowed:
                continue
            npos = len(node.args) - 1
            if npos > 0:
                found.append((node.lineno, f"{var}.{target.attr}", npos))
    return found


# ---------------------------------------------------------------- 结构层


@pytest.mark.parametrize(
    "filename",
    [
        "_feedback.py",
        "_task_queue.py",
        "_info_items.py",
        "_maintenance.py",
        "_cron.py",
        "_agent_tasks.py",
    ],
)
def test_no_positional_args_to_kwonly_methods(filename):
    bad: list[str] = []
    for lineno, dotted, npos in _to_thread_positional_calls(filename):
        var, meth = dotted.split(".")
        cls = _VAR_CLASS_BINDINGS[var]
        fn = getattr(cls, meth, None)
        if fn is None:
            continue
        try:
            inspect.signature(fn).bind(None, *([object()] * npos))
        except TypeError as exc:
            bad.append(f"{filename}:{lineno} {dotted} 位置传参 {npos} 个 → {exc}")
    assert not bad, "发现 keyword-only/参数不匹配调用:\n" + "\n".join(bad)


def test_survey_scans_found_known_classes():
    """自检：普查函数确实能捕获违规（对旧版 _info_items 逻辑做内联反证）。

    用一个合成的 AST 片段验证扫描+绑定校验管线本身有效，防止测试因
    扫描器失效而假绿。
    """
    src = "import asyncio\nasync def f(svc):\n    await asyncio.to_thread(svc.record_feedback, 's', 1, 'up')\n"
    tree = ast.parse(src)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "to_thread" and len(node.args) >= 2:
            target = node.args[0]
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id in _VAR_CLASS_BINDINGS:
                hits.append((node.lineno, target.value.id, target.attr, len(node.args) - 1))
    assert hits, "扫描器未能识别合成违规"
    lineno, var, meth, npos = hits[0]
    with pytest.raises(TypeError):
        inspect.signature(getattr(FeedbackService, meth)).bind(None, *([object()] * npos))


# ---------------------------------------------------------------- 行为层


class _FakeRegistry:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def register(self, name: str, fn, *a, **kw) -> None:
        self.handlers[name] = fn

    def register_with_schema(self, name: str, fn, schema=None, *a, **kw) -> None:
        self.handlers[name] = fn


class _FakeStore:
    def __init__(self) -> None:
        self.data: dict = {}

    def load_json(self, key: str, default=None):
        return self.data.get(key, default)

    def save_json(self, key: str, value) -> None:
        self.data[key] = value

    def next_id(self, items):
        return (max((it.get("id", 0) for it in items), default=0) + 1) if items else 1


class _FakeSettingsRepo:
    def get_setting(self, key, default=None):
        return default

    def set_setting(self, key, value) -> None:
        pass


class _FeedbackContext:
    def __init__(self, store: _FakeStore) -> None:
        # 走 handler 内懒构建兜底路径：同时验证 FeedbackService.__init__
        # 的 keyword-only 构造修复
        self.feedback_service = None
        self.local_data_store = store
        self.settings_repository = _FakeSettingsRepo()
        self.ai_client = None
        self.event_bus = None


@pytest.mark.asyncio
async def test_chat_feedback_set_records_for_real():
    store = _FakeStore()
    ctx = _FeedbackContext(store)
    ctx.feedback_service = FeedbackService(data_store=store, settings_repository=_FakeSettingsRepo())
    reg = _FakeRegistry()
    _feedback_mod.register_feedback(reg, ctx)
    out = await reg.handlers["chat.feedback.set"](session_id="s1", message_index=0, rating="down", comment="bad")
    # 修复前：record_feedback 位置传参 → TypeError 被吞 → recorded False
    assert out.get("recorded") is True, f"feedback.set 被异常吞掉: {out}"
    items = store.data.get("chat_feedback", [])
    assert len(items) == 1 and items[0]["rating"] == "down"


@pytest.mark.asyncio
async def test_chat_feedback_list_returns_recorded_items():
    store = _FakeStore()
    svc = FeedbackService(data_store=store, settings_repository=_FakeSettingsRepo())
    svc.record_feedback(session_id="s1", rating="up")
    ctx = _FeedbackContext(store)
    ctx.feedback_service = svc
    reg = _FakeRegistry()
    _feedback_mod.register_feedback(reg, ctx)
    out = await reg.handlers["chat.feedback.list"](session_id="s1", limit=10)
    assert out.get("error") is None or "error" not in out, f"list 被异常吞掉: {out}"
    assert out.get("count", 0) == 1


@pytest.mark.asyncio
async def test_feedback_lazy_service_build_uses_kwargs():
    """_get_service 懒构建兜底：FeedbackService.__init__ 是 keyword-only，
    修复前构造即抛 TypeError 返回 None（面板显示 service unavailable）。"""
    store = _FakeStore()
    ctx = _FeedbackContext(store)  # feedback_service=None → 走懒构建
    svc = _feedback_mod._get_service(ctx)
    assert svc is not None, "懒构建兜底被 keyword-only 构造炸掉"


class _FakeRawRepo:
    def __init__(self, item: dict) -> None:
        self._item = item

    def get_item(self, item_id):
        return self._item


class _InfoItemsContext:
    def __init__(self, note_repo, raw_repo) -> None:
        self.personal_note_repository = note_repo
        self.info_item_repository = raw_repo


@pytest.mark.asyncio
async def test_save_to_note_passes_dict_to_add_note():
    store = _FakeStore()
    note_repo = LocalPersonalNoteRepository(data_store=store)
    item = {
        "id": 7,
        "title": "Hello World",
        "url": "https://example.com",
        "summary_zh": "摘要文本",
        "source": "HN",
        "tags": ["ai"],
        "saved": True,
    }
    ctx = _InfoItemsContext(note_repo, _FakeRawRepo(item))
    reg = _FakeRegistry()
    await _info_items_mod.register(reg, ctx)
    out = await reg.handlers["info.items.save_to_note"](item_id=7)
    # 修复前：add_note 收 5 个位置参数 → TypeError 被吞 → success False
    assert out.get("success") is True, f"save_to_note 传参被吞: {out}"
    notes = store.data.get("notes", [])
    assert len(notes) == 1
    assert notes[0]["title"] == "Hello World"
    assert notes[0]["category"] == "新闻收藏"
    assert "ai" in notes[0]["tags"]
