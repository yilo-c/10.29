"""思忆集 MCP Server（M3，数据边界）测试。

覆盖：数据门面（store）→ 工具注册（tools）→ 协议分派（protocol）→ stdio 端到端。
全部使用临时数据目录，不触碰生产数据。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_server import store as store_mod
from mcp_server.protocol import McpStdioServer
from mcp_server.store import KEY_NOTIFICATIONS, SiyiStore
from mcp_server.tools import build_tool_specs

SERVER_PATH = Path(__file__).resolve().parent.parent / "mcp_server" / "server.py"


@pytest.fixture()
def store(tmp_path):
    return SiyiStore(data_dir=str(tmp_path))


@pytest.fixture()
def server(store):
    return McpStdioServer(name="siyi", tools=build_tool_specs(store))


# ── store：数据门面 ────────────────────────────────────────────


def test_health_empty(store):
    health = store.health()
    assert health["server"] == "siyi-mcp"
    assert health["counts"] == {"notes": 0, "tasks": 0, "inbox": 0, "news": 0}


def test_notes_create_and_search(store):
    created = store.notes_create("DSH 接入笔记", "DeepSeek Harness 作为执行内核", tags=["kernel"])
    assert created["id"]
    found = store.notes_search("Harness")
    assert len(found) == 1
    assert found[0]["title"] == "DSH 接入笔记"
    assert "执行内核" in found[0]["content_preview"]


def test_tasks_create_and_list(store):
    t1 = store.tasks_create("验证分层", priority="high")
    store.tasks_create("写文档")
    assert t1["id"]
    tasks = store.tasks_list()
    assert len(tasks) == 2
    assert {t["title"] for t in tasks} == {"验证分层", "写文档"}
    assert next(t for t in tasks if t["title"] == "验证分层")["priority"] == "high"


def test_tasks_list_excludes_completed(store):
    tid = store.tasks_create("已完成任务")["id"]
    store.tasks.toggle_task_completion(tid, True)
    assert store.tasks_list() == []
    assert len(store.tasks_list(include_completed=True)) == 1


def test_inbox_add_and_list(store):
    created = store.inbox_add("一个想法：内核可替换", title="想法", tags=["idea"])
    assert created["id"]
    entries = store.inbox_list()
    assert len(entries) == 1
    assert entries[0]["title"] == "想法"
    assert entries[0]["tags"] == ["idea"]
    assert entries[0]["content_preview"].startswith("一个想法")


def test_news_save_and_list(store):
    store.news_save(
        url="https://example.com/a",
        title="Example Release",
        summary="something new",
        source="mcp",
        title_zh="示例发布",
    )
    result = store.news_list(limit=5)
    assert result["total"] == 1
    item = result["items"][0]
    assert item["title"] == "示例发布"  # 中文标题优先
    assert item["url"] == "https://example.com/a"
    # upsert 幂等：同 url 再存不新增
    store.news_save(url="https://example.com/a", title="Example Release v2")
    assert store.news_list()["total"] == 1


def test_news_list_unread_only(store):
    store.news_save(url="https://example.com/b", title="B")
    items = store.store.load_json(store.info.STORAGE_KEY, [])
    items[0]["read"] = True
    store.store.save_json(store.info.STORAGE_KEY, items)
    assert store.news_list(unread_only=True)["total"] == 0
    assert store.news_list()["total"] == 1


def test_notify_record_shape(store):
    result = store.notify("任务完成", "新闻简报已生成", priority="high")
    assert result["id"]
    records = store.store.load_json(KEY_NOTIFICATIONS, [])
    assert len(records) == 1
    rec = records[0]
    # 与 NotificationDispatcher（P2.9）记录形状一致，桌面端可见
    assert rec["event_type"] == "mcp.agent"
    assert rec["title"] == "任务完成"
    assert rec["body"] == "新闻简报已生成"
    assert rec["priority"] == "high"
    assert rec["read"] is False
    assert rec["created_at"]


def test_notify_lru_cap(store, monkeypatch):
    monkeypatch.setattr(store_mod, "_MAX_NOTIFICATIONS", 3)
    for i in range(5):
        store.notify(f"n{i}", "body")
    records = store.store.load_json(KEY_NOTIFICATIONS, [])
    assert len(records) == 3
    assert [r["title"] for r in records] == ["n2", "n3", "n4"]


# ── protocol：协议分派（无需 stdio）────────────────────────


def test_protocol_initialize(server):
    resp = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "siyi"
    assert "tools" in resp["result"]["capabilities"]


def test_protocol_tools_list_contains_siyi_tools(server):
    resp = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    expected = {
        "siyi_health",
        "siyi_notes_search",
        "siyi_notes_create",
        "siyi_tasks_list",
        "siyi_tasks_create",
        "siyi_inbox_list",
        "siyi_inbox_add",
        "siyi_news_list",
        "siyi_news_save",
        "siyi_notify",
    }
    assert expected <= names
    for tool in resp["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object"


def test_protocol_tools_call_health(server):
    resp = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "siyi_health", "arguments": {}},
        }
    )
    content = resp["result"]["content"][0]
    assert content["type"] == "text"
    payload = json.loads(content["text"])
    assert payload["server"] == "siyi-mcp"


def test_protocol_unknown_tool_returns_error(server):
    resp = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "siyi_nope", "arguments": {}},
        }
    )
    assert resp["error"]["code"] == -32602


def test_protocol_notifications_return_none(server):
    assert server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_protocol_tool_exception_becomes_is_error(store, server):
    def boom(_args):
        raise RuntimeError("boom")

    server._tools["siyi_health"] = server._tools["siyi_health"].__class__(
        name="siyi_health",
        description="x",
        input_schema={"type": "object", "properties": {}},
        handler=boom,
    )
    resp = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "siyi_health", "arguments": {}},
        }
    )
    assert resp["result"]["isError"] is True
    assert "boom" in resp["result"]["content"][0]["text"]


# ── stdio 端到端（真实子进程，复刻 V2 验证路径）────────────


def test_stdio_end_to_end(tmp_path):
    # 七维-安C3(c)：policy 缺省 fail-close —— 预置一条已批准确认供 siyi_notes_create 放行
    from services.confirmation_store import ConfirmationStore as _CS

    _cs = _CS(data_dir=str(tmp_path))
    # SEC-1：审批绑定 args_sha256——预批必须用与实际调用一致的参数，否则指纹不匹配仍拦
    _cid = _cs.create("siyi_notes_create", {"title": "e2e", "content": "stdio 端到端"}, source="mcp:kernel", title="e2e")
    _cs.resolve(_cid, approved=True)
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_PATH), "--data-dir", str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test"}},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "siyi_notes_create", "arguments": {"title": "e2e", "content": "stdio 端到端"}},
            },
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "siyi_notes_search", "arguments": {"query": "端到端"}}},
        ]
        proc.stdin.write("\n".join(json.dumps(m, ensure_ascii=False) for m in messages) + "\n")
        proc.stdin.flush()
        proc.stdin.close()

        responses = {}
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if "id" in msg:
                responses[msg["id"]] = msg
            if len(responses) == 4:
                break

        assert responses[1]["result"]["serverInfo"]["name"] == "siyi"
        names = {t["name"] for t in responses[2]["result"]["tools"]}
        assert "siyi_notes_create" in names
        created = json.loads(responses[3]["result"]["content"][0]["text"])
        assert created["title"] == "e2e"
        found = json.loads(responses[4]["result"]["content"][0]["text"])
        assert len(found) == 1 and found[0]["title"] == "e2e"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
    assert proc.returncode is not None


# ── §5.4.2 siyi_tool_run（ToolRunner）──────────────────────────────
from mcp_server.tool_runner import ToolRunner


@pytest.fixture()
def runner(store):
    return ToolRunner(data_dir=store.data_dir)


class TestToolRunner:
    def test_list_tools_empty_registry(self, runner):
        # 临时目录无 tool_specs.json，注册表为空，list 返回空列表不崩
        assert runner.list_tools() == []

    def test_run_missing_tool_id(self, runner):
        res = runner.run("", "x")
        assert res["success"] is False
        assert "missing tool_id" in res["error"]

    def test_run_unknown_tool(self, runner):
        res = runner.run("nope", "go")
        assert res["success"] is False
        assert "unknown tool" in res["error"]

    def test_run_high_risk_refused_without_approval(self, runner, monkeypatch):
        # 注入一个 risk=high 的伪工具，验证内核结构性拒绝（不自我批准）
        from services.tooling.tool_spec import ToolSpec, ToolCommand

        fake = ToolSpec(
            tool_id="evil",
            display_name="evil",
            enabled=True,
            commands={"boom": ToolCommand(name="boom", description="danger", template="rm -rf /", risk="high")},
        )
        monkeypatch.setattr(runner._registry, "get", lambda tid: fake)
        monkeypatch.setattr(runner._registry, "find_by_action_id", lambda aid: (fake, fake.commands["boom"]))
        res = runner.run("evil", "boom")
        assert res["success"] is False
        assert res.get("requires_shell_approval") is True
        assert "内核不可自我批准" in res["error"]

    def test_list_tools_with_registered_spec(self, tmp_path):
        # 有 tool_specs.json 时 list_tools 返回注册表条目（含 available 状态，不崩）
        (tmp_path / "tool_specs.json").write_text(
            json.dumps(
                {
                    "tools": [
                        {"tool_id": "fake", "display_name": "Fake", "cli_path": "definitely-not-a-real-cli-xyz", "enabled": True, "commands": {}},
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        r = ToolRunner(data_dir=str(tmp_path))
        assert r.list_tools() == [
            {"tool_id": "fake", "display_name": "Fake", "enabled": True, "available": False},
        ]

    def test_run_low_risk_echo_succeeds(self, tmp_path):
        # 临时注册 risk=low 的 echo 命令 -> 调用 -> success: true 且有 output（§5.4.2 DoD）
        import sys as _sys

        (tmp_path / "tool_specs.json").write_text(
            json.dumps(
                {
                    "tools": [
                        {
                            "tool_id": "echo",
                            "display_name": "Echo",
                            "cli_path": _sys.executable,
                            "enabled": True,
                            "commands": {
                                "ping": {"description": "echo tool", "template": "{cli} -c print('tool-echo-ok')", "risk": "low", "timeout": 30}
                            },
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        r = ToolRunner(data_dir=str(tmp_path))
        res = r.run("echo", "ping")
        assert res["success"] is True, res
        assert "tool-echo-ok" in res["output"]

    def test_audit_records_on_low_risk_success(self, tmp_path):
        # §5.4.4 工具执行审计：低风险执行成功 -> audit_mcp.db 落 source=mcp_tool 成功条
        import sqlite3 as _sql, sys as _sys
        from pathlib import Path as _P

        (tmp_path / "tool_specs.json").write_text(
            json.dumps(
                {
                    "tools": [
                        {
                            "tool_id": "echo",
                            "display_name": "Echo",
                            "cli_path": _sys.executable,
                            "enabled": True,
                            "commands": {
                                "ping": {"description": "echo tool", "template": "{cli} -c print('tool-echo-ok')", "risk": "low", "timeout": 30}
                            },
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        r = ToolRunner(data_dir=str(tmp_path))
        res = r.run("echo", "ping")
        assert res["success"] is True, res
        db = _sql.connect(str(_P(tmp_path) / "audit_mcp.db"))
        rows = db.execute("SELECT source, tool_id, command, success FROM mcp_tool_audit").fetchall()
        db.close()
        assert rows, "审计记录未写入 audit_mcp.db"
        assert rows[-1] == ("mcp_tool", "echo", "ping", 1)

    def test_audit_records_on_high_risk_refusal(self, runner, monkeypatch):
        # §5.4.4：高风险被内核结构性拒绝 -> 同样审计落 source=mcp_tool 失败条（不自我批准）
        import sqlite3 as _sql
        from services.tooling.tool_spec import ToolSpec, ToolCommand

        fake = ToolSpec(
            tool_id="evil",
            display_name="evil",
            enabled=True,
            commands={"boom": ToolCommand(name="boom", description="danger", template="rm -rf /", risk="high")},
        )
        monkeypatch.setattr(runner._registry, "get", lambda tid: fake)
        monkeypatch.setattr(runner._registry, "find_by_action_id", lambda aid: (fake, fake.commands["boom"]))
        res = runner.run("evil", "boom")
        assert res["success"] is False
        assert res.get("requires_shell_approval") is True
        db = _sql.connect(str(runner._audit._db_path))
        rows = db.execute("SELECT source, tool_id, command, success FROM mcp_tool_audit WHERE command='boom'").fetchall()
        db.close()
        assert ("mcp_tool", "evil", "boom", 0) in rows


# ── §5.4.2 siyi_tool_list（runner 注入时注册）──────────────────────────


def test_protocol_tools_list_includes_run_and_list_when_runner(store, runner):
    srv = McpStdioServer(name="siyi", tools=build_tool_specs(store, runner))
    resp = srv.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert {"siyi_tool_run", "siyi_tool_list"} <= names


def test_protocol_call_siyi_tool_list(store, runner):
    srv = McpStdioServer(name="siyi", tools=build_tool_specs(store, runner))
    resp = srv.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "siyi_tool_list", "arguments": {}},
        }
    )
    content = resp["result"]["content"][0]
    assert content["type"] == "text"
    payload = json.loads(content["text"])
    assert isinstance(payload, list)  # 临时目录注册表为空 -> []
    assert payload == []
