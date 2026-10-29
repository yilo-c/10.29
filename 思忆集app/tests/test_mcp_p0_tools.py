"""M-mcp 契约测试（第一批：任务 / 笔记 / 收件箱 P0 工具，全部走 SiyiStore 门面）。

验证：
- 7 个 P0 工具（siyi_tasks_toggle / siyi_tasks_update / siyi_tasks_search /
  siyi_notes_get / siyi_notes_update / siyi_notes_tag / siyi_inbox_update）已注册。
- 经 ToolSpec.handler 调用能真正落库（toggle / tag / update 生效）。
- 命名空间不抢 siyi_ 之外前缀、函数名不含 kernel。
"""

from __future__ import annotations

import json

import pytest

from mcp_server.store import SiyiStore
from mcp_server.tools import build_tool_specs

P0_NAMES = {
    "siyi_tasks_toggle",
    "siyi_tasks_update",
    "siyi_tasks_search",
    "siyi_notes_get",
    "siyi_notes_update",
    "siyi_notes_tag",
    "siyi_inbox_update",
}


@pytest.fixture
def store(tmp_path):
    return SiyiStore(data_dir=str(tmp_path))


@pytest.fixture
def specs(store):
    # 七维-安C3(c) 后 policy=None（缺省）为 fail-close（RW/DANGER require_approval）；
    # 本文件测 store 落库管道，显式 allow_all 保持直调语义。
    return build_tool_specs(store, policy={"mode": "allow_all"})


def _call(specs, name, **params):
    spec = next(s for s in specs if s.name == name)
    return json.loads(spec.handler(params))


def test_p0_tool_names_registered(specs):
    names = {s.name for s in specs}
    assert P0_NAMES <= names


def test_no_kernel_in_tool_names(specs):
    # 限制：MCP 工具命名空间不出现 kernel
    assert not any("kernel" in s.name for s in specs)


def test_tasks_toggle_and_search(store, specs):
    created = store.tasks_create("buy milk")
    tid = created["id"]
    res = _call(specs, "siyi_tasks_toggle", task_id=tid, is_completed=True)
    assert res["completed"] is True
    search = _call(specs, "siyi_tasks_search", query="")
    hit = next(t for t in search if t["id"] == tid)
    assert hit["completed"] is True


def test_tasks_update(store, specs):
    created = store.tasks_create("old title")
    tid = created["id"]
    res = _call(specs, "siyi_tasks_update", task_id=tid, priority="high")
    assert res["updated"] is True
    search = _call(specs, "siyi_tasks_search", query="")
    hit = next(t for t in search if t["id"] == tid)
    assert hit["priority"] == "high"


def test_notes_get_and_tag(store, specs):
    created = store.notes_create("hello", content="world")
    nid = created["id"]
    got = _call(specs, "siyi_notes_get", note_id=nid)
    assert got.get("error") != "not_found"
    tag = _call(specs, "siyi_notes_tag", note_id=nid, tags=["a", "b"])
    assert tag["tags"] == ["a", "b"]


def test_inbox_update(store, specs):
    created = store.inbox_add("content", title="t")
    eid = created["id"]
    res = _call(specs, "siyi_inbox_update", entry_id=eid, status="done")
    assert res["updated"] is True
