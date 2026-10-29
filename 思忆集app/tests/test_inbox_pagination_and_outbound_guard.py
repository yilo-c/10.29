"""inbox.list 分页原语 + WebSocket 出站体积守卫。

背景（P0-2026-09-16 实测）：
- ``inbox.list`` 旧签名 ``(include_archived=False, **kwargs)``，客户端传的
  ``limit`` 落进 ``**kwargs`` 被静默吞掉 → 689 条真实数据单帧返回 ≈2.2MB，
  打爆客户端帧上限，同连接后续调用全灭（表现为"断联"）。
- ``ws_server`` 只有**入站**上限 ``SIYI_WS_MAX_MSG_SIZE``（aiohttp
  WebSocketResponse(max_msg_size=...)），出站 ``send_str`` 毫无约束 —— 超限
  照发不误且日志一片安静。

本文件锁死三件事：
1. 仓储层 limit/offset/order 真实下推到 SQL（不是内存里全量再切片）；
2. handler 层 limit 默认 50、上限 200、越界夹取（不再被 **kwargs 吞掉）；
3. 出站超限不下发，改回 -32007 可读错误（request/response 与流式两条通道）。
"""
from __future__ import annotations

import json
import tempfile

import pytest

from backend import ws_server
from backend.handlers._inbox_crud import INBOX_LIST_DEFAULT_LIMIT, INBOX_LIST_MAX_LIMIT
from core.errors import ErrorCode
from data._hot_tables import ensure_hot_tables
from data.local_storage import LocalDataStore
from repositories._inbox import LocalPersonalInboxRepository

N_ENTRIES = 120


def _new_store() -> LocalDataStore:
    return LocalDataStore(base_dir=tempfile.mkdtemp())


def _with_table() -> LocalDataStore:
    store = _new_store()
    ensure_hot_tables(store._sqlite._connect())
    return store


def _seed(repo: LocalPersonalInboxRepository, n: int = N_ENTRIES) -> None:
    for i in range(n):
        repo.add_entry({"title": f"条目{i}", "content": f"正文{i}", "status": "new"})


# ────────────────────────────────────────────────────────────────
# 一、仓储层分页原语
# ────────────────────────────────────────────────────────────────

def test_table_path_pagination_limit_offset_order():
    repo = LocalPersonalInboxRepository(_with_table())
    _seed(repo)
    assert repo.count_entries() == N_ENTRIES

    page1 = repo.list_entries(False, 50, 0, "asc")
    assert [e["id"] for e in page1] == list(range(1, 51))
    page2 = repo.list_entries(False, 50, 50, "asc")
    assert [e["id"] for e in page2] == list(range(51, 101))
    assert not ({e["id"] for e in page1} & {e["id"] for e in page2}), "翻页不得重叠"

    desc = repo.list_entries(False, 5, 0, "desc")
    assert [e["id"] for e in desc] == [N_ENTRIES, N_ENTRIES - 1, N_ENTRIES - 2,
                                       N_ENTRIES - 3, N_ENTRIES - 4]

    # limit=None 保持历史全量语义（内部路径）
    assert len(repo.list_entries(False, None, 0, "asc")) == N_ENTRIES


def test_table_path_pagination_under_limit_clause():
    """分页必须下推到 SQL：只回 LIMIT 行，不是全量读入再切片。"""
    store = _with_table()
    repo = LocalPersonalInboxRepository(store)
    _seed(repo)
    conn = repo._table_conn()
    assert conn is not None
    rows = conn.execute(
        "SELECT raw_json FROM inbox_entries_tb ORDER BY entry_id ASC LIMIT ? OFFSET ?", (10, 20)
    ).fetchall()
    assert len(rows) == 10, "SQL LIMIT/OFFSET 未生效"
    got = repo.list_entries(False, 10, 20, "asc")
    assert [json.loads(r[0])["id"] for r in rows] == [e["id"] for e in got]


def test_kv_fallback_pagination():
    """未建表的库（kv 回退路径）分页语义与表路径一致。"""
    repo = LocalPersonalInboxRepository(_new_store())
    _seed(repo)
    assert len(repo.list_entries(False, 50, 0, "asc")) == 50
    assert [e["id"] for e in repo.list_entries(False, 3, 0, "desc")] == [
        N_ENTRIES, N_ENTRIES - 1, N_ENTRIES - 2,
    ]
    assert repo.count_entries() == N_ENTRIES


def test_pagination_params_clamped_to_safe_range():
    repo = LocalPersonalInboxRepository(_with_table())
    _seed(repo, n=250)  # 多于上限才能观察"夹取"而非"数据本来就不够"
    # 越界/非法值一律夹取，绝不因参数异常而返回全量
    assert len(repo.list_entries(False, 999999, 0, "asc")) == repo.MAX_LIST_LIMIT
    assert len(repo.list_entries(False, -1, 0, "asc")) == repo.DEFAULT_LIST_LIMIT
    assert len(repo.list_entries(False, "abc", 0, "asc")) == repo.DEFAULT_LIST_LIMIT
    assert len(repo.list_entries(False, 10, -5, "asc")) == 10
    assert repo.list_entries(False, 10, 0, "倒序")[0]["id"] == 1, "非法 order 回落 asc"


# ────────────────────────────────────────────────────────────────
# 二、handler 层：limit 不再被 **kwargs 吞掉
# ────────────────────────────────────────────────────────────────

class _FakeRepo:
    """比 MAX_LIST_LIMIT 多，才能区分"夹取到 200"与"数据本来只有 120 条"。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.entries = [{"id": i, "status": "new"} for i in range(1, 251)]

    def list_entries(self, include_archived=False, limit=None, offset=0, order="asc"):
        self.calls.append((include_archived, limit, offset, order))
        items = list(self.entries)
        if order == "desc":
            items.reverse()
        if limit is None:
            return items[offset:]
        return items[offset: offset + limit]


class _Ctx:
    def __init__(self, repo) -> None:
        self.personal_inbox_repository = repo


class _Registry:
    def __init__(self) -> None:
        self.methods: dict = {}

    def register(self, name, handler, **_kw) -> None:
        self.methods[name] = handler


@pytest.fixture()
def inbox_list_handler():
    from backend.handlers import _inbox_crud

    repo = _FakeRepo()
    reg = _Registry()
    captured: dict = {}

    # 只截取 inbox.list 的注册，其余 register_* 依赖 context 的属性在本用例中用不到
    real_register = reg.register

    def _spy(name, handler, **kw):
        if name == "inbox.list":
            captured["handler"] = handler
        real_register(name, handler, **kw)

    reg.register = _spy  # type: ignore[method-assign]
    _inbox_crud.register_inbox_crud(reg, _Ctx(repo))
    return captured["handler"], repo


@pytest.mark.asyncio
async def test_handler_accepts_limit_offset_order(inbox_list_handler):
    handler, repo = inbox_list_handler
    out = await handler(limit=10, offset=5)
    assert len(out) == 10
    assert repo.calls[-1] == (False, 10, 5, "asc"), "limit/offset 未透传到仓储层"


@pytest.mark.asyncio
async def test_handler_default_limit_is_safe(inbox_list_handler):
    """不传 limit 时行为从"全量"变为"默认 50 条"——这是本次 P0 修复本身。"""
    handler, repo = inbox_list_handler
    out = await handler()
    assert len(out) == INBOX_LIST_DEFAULT_LIMIT == 50
    assert repo.calls[-1][1] == INBOX_LIST_DEFAULT_LIMIT


@pytest.mark.asyncio
async def test_handler_clamps_over_max_limit(inbox_list_handler):
    handler, repo = inbox_list_handler
    out = await handler(limit=10_000)
    assert len(out) == INBOX_LIST_MAX_LIMIT == 200
    assert repo.calls[-1][1] == INBOX_LIST_MAX_LIMIT


@pytest.mark.asyncio
async def test_handler_rejects_negative_and_junk_limit(inbox_list_handler):
    handler, repo = inbox_list_handler
    assert len(await handler(limit=-5)) == INBOX_LIST_DEFAULT_LIMIT
    assert len(await handler(limit="not-a-number")) == INBOX_LIST_DEFAULT_LIMIT
    assert repo.calls[-1][1] == INBOX_LIST_DEFAULT_LIMIT


# ────────────────────────────────────────────────────────────────
# 三、出站体积守卫
# ────────────────────────────────────────────────────────────────

def test_guard_default_threshold_configured():
    """守卫默认阈值存在且可通过环境变量调（0 = 关闭）。"""
    assert ws_server.DEFAULT_OUTBOUND_MAX_BYTES == 4 * 1024 * 1024
    assert ws_server.OUTBOUND_MAX_BYTES > 0


def test_guard_passes_normal_payload(monkeypatch):
    monkeypatch.setattr(ws_server, "OUTBOUND_MAX_BYTES", 1024)
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": ["x" * 10]})
    assert ws_server.enforce_outbound_limit(payload, method="inbox.list", request_id=1) is payload


def test_guard_blocks_oversize_payload_with_readable_error(monkeypatch):
    monkeypatch.setattr(ws_server, "OUTBOUND_MAX_BYTES", 1024)
    payload = json.dumps({"jsonrpc": "2.0", "id": 7, "result": ["x" * 5000]})
    guarded = ws_server.enforce_outbound_limit(payload, method="inbox.list", request_id=7)
    assert guarded is not payload, "超限帧必须被替换，绝不静默下发"

    body = json.loads(guarded)
    assert body["id"] == 7, "错误帧必须带回原 request_id，否则客户端无法配对"
    assert body["error"]["code"] == ErrorCode.RESPONSE_TOO_LARGE == -32007
    assert "inbox.list" in body["error"]["message"], "错误信息必须带方法名"
    assert body["error"]["data"]["bytes"] == len(payload)
    assert body["error"]["data"]["limit"] == 1024
    assert len(guarded.encode("utf-8")) < 1024, "替换帧本身不得再超限"


def test_guard_can_be_disabled(monkeypatch):
    monkeypatch.setattr(ws_server, "OUTBOUND_MAX_BYTES", 0)
    payload = json.dumps({"result": ["x" * 100000]})
    assert ws_server.enforce_outbound_limit(payload, method="m", request_id=1) is payload


def test_guard_stream_chunk_degrades_to_error_chunk(monkeypatch):
    """流通道帧形状不能变（客户端只认 result），超限降级为 type=error。"""
    monkeypatch.setattr(ws_server, "OUTBOUND_MAX_BYTES", 1024)
    raw = json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"type": "chunk", "data": "y" * 5000}})
    out = ws_server.enforce_outbound_limit_stream(
        raw, method="chat.send", request_id=3, chunk_type="chunk"
    )
    body = json.loads(out)
    assert "error" not in body, "流式帧不得回 JSON-RPC error（会被客户端静默丢弃）"
    assert body["result"]["type"] == "error"
    assert "chat.send" in body["result"]["data"]


def test_guard_stream_keeps_existing_error_chunk(monkeypatch):
    monkeypatch.setattr(ws_server, "OUTBOUND_MAX_BYTES", 4)
    raw = json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"type": "error", "data": "boom"}})
    assert ws_server.enforce_outbound_limit_stream(
        raw, method="chat.send", request_id=3, chunk_type="error"
    ) is raw, "error 帧不得被守卫二次改写（会吞掉真实错误）"


# ────────────────────────────────────────────────────────────────
# 四、agent 侧（react_executor）无界加载收口
# ────────────────────────────────────────────────────────────────

class _ExecRepo:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.entries = [{"id": i} for i in range(1, 251)]

    def list_entries(self, include_archived=False, limit=None, offset=0, order="asc"):
        self.calls.append({"include_archived": include_archived, "limit": limit,
                           "offset": offset, "order": order})
        items = self.entries[offset:]
        return items if limit is None else items[:limit]


def _executor_with(repo):
    """C-1 步骤 3 重指向：inbox.list 实现已迁入 A 套 _local_resource_tools，
    context 必须挂 capability_router（分页/收口断言一字不改）。"""
    import tempfile
    from pathlib import Path

    from assistant.capability_router import AssistantCapabilityRouter
    from data.local_storage import LocalDataStore
    from services.react_executor import make_react_executor

    router = AssistantCapabilityRouter(
        data_store=LocalDataStore(base_dir=tempfile.mkdtemp(prefix="siyi-c1-inbox-")),
        settings_repository=None,
        observability_service=None,
        note_repository=None,
        task_repository=None,
        news_repository=None,
        inbox_repository=repo,
    )
    router.shell_execution.workspace_root = Path(tempfile.mkdtemp(prefix="siyi-c1-inbox-ws-"))
    return make_react_executor(type("_Ctx", (), {"capability_router": router, "personal_inbox_repository": repo})())


def test_react_executor_inbox_list_defaults_to_bounded_page():
    repo = _ExecRepo()
    execute = _executor_with(repo)
    out = execute("inbox.list", {})
    assert repo.calls[-1]["limit"] == 50, "agent 侧不得再走 limit=None 全量路径"
    assert len(out["result"]) == 50
    assert out["count"] == 50


def test_react_executor_inbox_list_clamps_limit_and_offset():
    repo = _ExecRepo()
    execute = _executor_with(repo)
    assert len(execute("inbox.list", {"limit": 99999})["result"]) == 200
    assert repo.calls[-1]["limit"] == 200
    assert repo.calls[-1]["offset"] == 0
    execute("inbox.list", {"limit": "10", "offset": 20})
    assert repo.calls[-1]["limit"] == 10 and repo.calls[-1]["offset"] == 20
    execute("inbox.list", {"limit": 0, "offset": -5})
    assert repo.calls[-1]["limit"] == 1, "limit 下限 1"
    assert repo.calls[-1]["offset"] == 0, "offset 不得为负"


# ────────────────────────────────────────────────────────────────
# 五、验收脚本的定时炸弹（新条目必须落在显式取的那一页里）
# ────────────────────────────────────────────────────────────────

def test_new_entry_only_reachable_via_desc_page_beyond_50():
    """复现原缺陷：库 >50 条时，默认页（asc 最旧 50）永远看不到刚写入的条目。"""
    repo = LocalPersonalInboxRepository(_with_table())
    _seed(repo, n=120)
    new_id = repo.add_entry({"title": "刚捕获", "content": "x", "status": "new"})

    asc_page = repo.list_entries(False, 50, 0, "asc")
    desc_page = repo.list_entries(False, 50, 0, "desc")
    assert new_id not in [e["id"] for e in asc_page], "默认页（asc）本就取不到新条目——这正是假 RED 成因"
    assert new_id in [e["id"] for e in desc_page], "desc 首页必须包含最新写入条目（v1_acceptance 断言依据）"


def test_acceptance_check_capture_reads_newest_page():
    """锁死 v1_acceptance.check_capture 的取页口径，防止定时炸弹被改回来。"""
    import inspect

    from scripts import v1_acceptance

    src = inspect.getsource(v1_acceptance.check_capture)
    assert 'order": "desc"' in src or "order='desc'" in src, \
        "check_capture 必须以 desc 取最新页，否则库 >50 条后必然假 RED"
    assert "limit" in src
