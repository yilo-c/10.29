from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.capability_router import AssistantCapabilityRouter
from data.local_storage import LocalDataStore


def _build_router(tmp_path):
    runtime_root = tmp_path / "runtime"
    workspace_root = tmp_path / "workspace"
    runtime_root.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)
    store = LocalDataStore(base_dir=str(runtime_root))
    router = AssistantCapabilityRouter(
        data_store=store,
        settings_repository=None,
        observability_service=None,
        note_repository=None,
        task_repository=None,
        news_repository=None,
        inbox_repository=None,
    )
    router.shell_execution.workspace_root = workspace_root
    return router


def test_web_download_defaults_to_sandboxed_download_root(tmp_path, monkeypatch):
    router = _build_router(tmp_path)
    captured = {}

    def _fake_urlretrieve(url, save_path):
        captured["url"] = url
        captured["save_path"] = save_path
        Path(save_path).write_text("ok", encoding="utf-8")
        return save_path, None

    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)

    result = router.file_domain.web_download({"url": "https://example.com/files/demo report.pdf"})

    assert result["success"] is True
    assert Path(result["saved_to"]).is_relative_to(Path(tmp_path) / "runtime" / "assistant_downloads")
    assert Path(result["saved_to"]).exists()


def test_web_download_blocks_path_outside_allowed_root(tmp_path):
    router = _build_router(tmp_path)

    result = router.file_domain.web_download(
        {
            "url": "https://example.com/file.txt",
            "save_path": str((Path(tmp_path).parent / "escape.txt").resolve()),
        }
    )

    assert result["success"] is False
    assert result["reason"] == "path_outside_allowed_root"


def test_web_download_blocks_remote_http_urls(tmp_path):
    router = _build_router(tmp_path)

    result = router.file_domain.web_download({"url": "http://example.com/file.txt"})

    assert result["success"] is False
    assert result["reason"] == "http_download_not_allowed"


def test_file_read_allows_relative_path_inside_workspace(tmp_path):
    router = _build_router(tmp_path)
    workspace_root = tmp_path / "workspace"
    (workspace_root / "nested").mkdir()
    (workspace_root / "nested" / "demo.txt").write_text("hello", encoding="utf-8")
    router.shell_execution.workspace_root = workspace_root

    result = router.file_domain.file_read({"path": "nested/demo.txt"})

    assert result["success"] is True
    assert result["content"] == "hello"


def test_file_write_defaults_to_data_root(tmp_path):
    router = _build_router(tmp_path)

    result = router.file_domain.file_write({"path": "reports/demo.txt", "content": "ok"})

    assert result["success"] is True
    written_path = Path(result["path"])
    assert written_path == (Path(tmp_path) / "runtime" / "reports" / "demo.txt")
    assert written_path.read_text(encoding="utf-8") == "ok"


def test_file_write_blocks_workspace_root_path(tmp_path):
    router = _build_router(tmp_path)
    workspace_root = tmp_path / "runtime"
    router.shell_execution.workspace_root = workspace_root

    result = router.file_domain.file_write({"path": str(workspace_root / "escape.txt"), "content": "blocked"})

    assert result["success"] is False
    assert result["reason"] == "workspace_write_blocked"


def test_shell_run_uses_parameterized_execution(tmp_path, monkeypatch):
    router = _build_router(tmp_path)
    captured = {}

    def _fake_execute(command, args=None, timeout=15, working_dir=None, env=None):
        captured["command"] = command
        captured["timeout"] = timeout
        captured["working_dir"] = working_dir
        return {"success": True, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(router.shell_execution, "execute", _fake_execute)

    result = router.shell_domain.shell_run({"command": "python -V", "timeout": 5})

    assert result["success"] is True
    assert captured["command"] == "python -V"
    assert captured["timeout"] == 5


def test_shell_run_blocks_unsafe_shell_syntax(tmp_path):
    router = _build_router(tmp_path)

    result = router.shell_domain.shell_run({"command": "echo ok && whoami", "timeout": 5})

    assert result["success"] is False
    assert result["reason"] == "unsafe_shell_syntax_blocked"


def test_shell_run_blocks_single_ampersand_separator(tmp_path):
    router = _build_router(tmp_path)

    result = router.shell_domain.shell_run({"command": "echo ok & whoami", "timeout": 5})

    assert result["success"] is False
    assert result["reason"] == "unsafe_shell_syntax_blocked"


def test_shell_run_wraps_cmd_builtin_without_shell_flag(tmp_path, monkeypatch):
    router = _build_router(tmp_path)
    captured = {}

    def _fake_execute(command, args=None, timeout=15, working_dir=None, env=None):
        captured["command"] = command
        return {"success": True, "stdout": "hello\n", "stderr": ""}

    monkeypatch.setattr(router.shell_execution, "execute", _fake_execute)

    result = router.shell_domain.shell_run({"command": "echo hello", "timeout": 5})

    assert result["success"] is True
    # shell_execution.execute() uses build_command internally which wraps cmd builtins
    assert captured["command"] == "echo hello"


# 鈹€鈹€ Phase 7: execute() 画像集成 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
from assistant.models import PlannedStep


def _build_router_with_profile(tmp_path):
    """构建带"user_profile_service 鍜"inbox_service 鐨"router。"""
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    store = LocalDataStore(base_dir=str(runtime_root))

    observed = []
    profile_svc = SimpleNamespace(
        observe_action=lambda action_id, goal="": observed.append(action_id),
    )

    delivered = []
    inbox_svc = SimpleNamespace(
        deliver_task_result=lambda **kw: delivered.append(kw) or {"id": "fake"},
    )

    router = AssistantCapabilityRouter(
        data_store=store,
        settings_repository=None,
        observability_service=None,
        note_repository=None,
        task_repository=None,
        news_repository=None,
        inbox_repository=None,
        user_profile_service=profile_svc,
        inbox_service=inbox_svc,
    )
    return router, observed, delivered


def test_execute_calls_observe_action_on_success(tmp_path):
    """execute() 成功后应调用 user_profile_service.observe_action。"""
    router, observed, _ = _build_router_with_profile(tmp_path)
    # 注册涓€涓畝鍗"handler
    router._handlers["test.echo"] = lambda payload: {"echo": payload.get("msg", "")}

    step = PlannedStep(step_id="s1", action_id="test.echo", payload={"msg": "hi"})
    result = router.execute(step)

    assert result["echo"] == "hi"
    assert "test.echo" in observed


def test_execute_no_longer_delivers_inbox_directly(tmp_path):
    """execute() 不再直接交付收件绠扁€斺€旂敱鎵褑层统涓€处理。"""
    router, _, delivered = _build_router_with_profile(tmp_path)
    router._handlers["test.echo"] = lambda payload: {"echo": "ok"}

    step = PlannedStep(step_id="s1", action_id="test.echo", payload={})
    result = router.execute(step)

    assert result["echo"] == "ok"
    assert len(delivered) == 0  # 交付鐢"execute_plan() 统一处理


def test_execute_does_not_crash_without_profile_service(tmp_path):
    """鏃"user_profile_service 鏃"execute() 搴旀常工浣溿€"""
    router = _build_router(tmp_path)
    router._handlers["test.noop"] = lambda payload: {"ok": True}

    step = PlannedStep(step_id="s1", action_id="test.noop", payload={})
    result = router.execute(step)

    assert result["ok"] is True


def test_shell_run_blocks_windows_cmd_wrappers(tmp_path, monkeypatch):
    router = _build_router(tmp_path)
    monkeypatch.setattr("shutil.which", lambda executable: str(Path(tmp_path) / "tool.cmd"))

    result = router.shell_domain.shell_run({"command": "tool --help", "timeout": 5})

    assert result["success"] is False
    assert result["reason"] == "windows_cmd_wrapper_blocked"


def test_shell_run_blocks_working_dir_outside_workspace(tmp_path, monkeypatch):
    router = _build_router(tmp_path)
    monkeypatch.setattr("shutil.which", lambda executable: str(Path(tmp_path) / "python.exe"))
    outside_workspace = (Path(tmp_path) / "outside").resolve()

    result = router.shell_domain.shell_run(
        {
            "command": "python -V",
            "timeout": 5,
            "working_dir": str(outside_workspace),
        }
    )

    assert result["success"] is False
    assert result["reason"] == "working_dir_outside_workspace"


def test_news_briefing_uses_create_fallback_client(tmp_path, monkeypatch):
    router = _build_router(tmp_path)
    router.settings_repository = object()
    captured = {}

    def _fake_news_lookup(payload):
        return {
            "items": [
                {
                    "title": "测试新闻",
                    "source": "测试婧",
                    "category": "科技",
                    "published_at": "2026-04-18 14:05",
                    "summary": "鎽樿鍐呭",
                }
            ]
        }

    class _FakeClient:
        def complete_text(self, messages, temperature=0.2, max_tokens=None):
            captured["messages"] = messages
            captured["temperature"] = temperature
            captured["max_tokens"] = max_tokens
            return "生成的简鎶"

    def _fake_create_fallback_client(*, settings_repository=None, **kwargs):
        captured["settings_repository"] = settings_repository
        return _FakeClient()

    monkeypatch.setattr(router.knowledge_domain, "news_lookup", _fake_news_lookup)
    monkeypatch.setattr("services.ai_service.create_fallback_client", _fake_create_fallback_client)

    result = router.knowledge_domain.news_briefing({"topic": "AI", "max_items": 3})

    assert result["briefing"] == "生成的简鎶"
    assert captured["settings_repository"] is router.settings_repository
    assert captured["messages"][0]["role"] == "system"
    assert captured["max_tokens"] == 800


# 鈹€鈹€ 能力鐩綍导出 & 注册表同姝"鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_get_supported_actions_returns_all_handler_keys(tmp_path):
    """get_supported_actions() 应返回所有已注册鐨"action_id。"""
    router = _build_router(tmp_path)
    actions = router.get_supported_actions()

    assert isinstance(actions, frozenset)
    assert "news.lookup" in actions
    assert "shell.run" in actions
    assert "subagent.spawn" in actions
    assert len(actions) >= 24


def test_router_actions_match_planner_catalog(tmp_path):
    """璺敱器的 _handlers 搴旇鐩"CAPABILITY_CATALOG 涓殑鎵€鏈"action_id。"""
    from assistant.agents.planner import AssistantPlannerLayer

    router = _build_router(tmp_path)
    router_actions = router.get_supported_actions()
    catalog_actions = {cap["action_id"] for cap in AssistantPlannerLayer.CAPABILITY_CATALOG}

    missing_in_router = catalog_actions - router_actions
    assert not missing_in_router, f"CATALOG 声明了但璺敱器未实现: {missing_in_router}"


def test_tool_registry_ingests_router_catalog(tmp_path):
    """AssistantToolRegistry.register_capability_catalog 能消璐"CAPABILITY_CATALOG。"""
    from assistant.character_runtime import AssistantToolRegistry
    from assistant.agents.planner import AssistantPlannerLayer

    registry = AssistantToolRegistry()
    registry.register_capability_catalog(AssistantPlannerLayer.CAPABILITY_CATALOG)

    tools = registry.list_tools()
    tool_ids = {t.tool_id for t in tools}
    assert "news.lookup" in tool_ids
    assert "shell.run" in tool_ids
    assert len(tool_ids) >= 24


# ─── 本地资源工具接线（ReAct → butler 迁移断线修复）─────────────────────────


def test_local_resource_actions_all_registered(tmp_path):
    """notes/tasks/inbox/vocab 等 22 个断线工具必须全部出现在 _handlers。"""
    from assistant.capability._local_resource_tools import LOCAL_RESOURCE_ACTIONS

    router = _build_router(tmp_path)
    supported = router.get_supported_actions()
    missing = [aid for aid in LOCAL_RESOURCE_ACTIONS if aid not in supported]
    assert not missing, f"本地资源工具未接线: {missing}"


def test_butler_declared_catalog_fully_wired(tmp_path):
    """回归守卫：butler 按 catalog∩白名单声明给模型的工具必须全部可执行。

    断线事故形态 = 声明了但 execute() 抛 unsupported action。
    """
    from services.react_tool_catalog import REACT_TOOL_CATALOG
    from assistant.agents.butler_agent import is_tool_allowed

    router = _build_router(tmp_path)
    supported = router.get_supported_actions()
    unwired = [
        entry["action_id"]
        for entry in REACT_TOOL_CATALOG
        if is_tool_allowed(entry["action_id"])
        and entry["action_id"] != "confirmation.status"  # butler 内部特判，不经 router
        and entry["action_id"] not in supported
    ]
    assert not unwired, f"butler 声明了但未接线: {unwired}"


def test_notes_list_end_to_end_via_wired_handler(tmp_path):
    router = _build_router(tmp_path)
    router.note_repository = SimpleNamespace(
        list_notes=lambda: [
            {"id": 1, "title": "周报", "content": "hello", "tags": [], "created_at": "", "updated_at": ""}
        ]
    )

    result = router.execute(PlannedStep(step_id="s1", action_id="notes.list", payload={}))

    assert result["count"] == 1
    assert result["result"][0]["title"] == "周报"


def test_notes_list_without_repo_returns_structured_error(tmp_path):
    router = _build_router(tmp_path)

    result = router.execute(PlannedStep(step_id="s1", action_id="notes.list", payload={}))

    assert result == {"error": "笔记仓储不可用"}


def test_vocab_review_status_via_post_init_injection(tmp_path):
    """适配层懒读取：构建后注入 vocabulary_repository 必须生效。"""
    router = _build_router(tmp_path)
    router.vocabulary_repository = SimpleNamespace(
        list_words=lambda: [{"word": "alpha", "next_review": None, "last_review": None}]
    )

    result = router.execute(PlannedStep(step_id="s1", action_id="vocab.review_status", payload={}))

    assert result["result"]["total"] == 1
    assert result["result"]["due"] == 0


def test_notes_delete_requires_sandbox_approval_by_default(tmp_path, monkeypatch):
    """接线不得绕过破坏性操作沙箱审批：默认路径必须转审批而非直接删除。"""
    router = _build_router(tmp_path)
    deleted = []
    def _fake_delete(nid):
        deleted.append(nid)
        return True

    router.note_repository = SimpleNamespace(delete_note=_fake_delete)
    fake_sandbox = SimpleNamespace(add=lambda action_id, args, preview: "pending-123")
    monkeypatch.setattr("services.sandbox_manager.get_sandbox_manager", lambda: fake_sandbox)

    result = router.execute(PlannedStep(step_id="s1", action_id="notes.delete", payload={"note_id": 7}))

    assert result["requires_approval"] is True
    assert result["pending_id"] == "pending-123"
    assert deleted == []


def test_notes_delete_executes_inside_approval_context(tmp_path):
    """审批通过后（_in_approval_var=True）应真正执行删除。"""
    from services.sandbox_manager import _in_approval_var

    router = _build_router(tmp_path)
    deleted = []
    def _fake_delete(nid):
        deleted.append(nid)
        return True

    router.note_repository = SimpleNamespace(delete_note=_fake_delete)

    token = _in_approval_var.set(True)
    try:
        result = router.execute(PlannedStep(step_id="s1", action_id="notes.delete", payload={"note_id": 7}))
    finally:
        _in_approval_var.reset(token)

    assert result == {"result": {"note_id": 7, "deleted": True}}
    assert deleted == [7]
