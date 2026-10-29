# -*- coding: utf-8 -*-
"""P3 工具升级测试：file.grep / file.edit / desktop.screenshot / 审批闭环。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.capability._file_ops import FileCapabilityDomain  # noqa: E402
from assistant.capability import _desktop  # noqa: E402
from assistant.capability._desktop import DesktopAutomationCapabilityDomain  # noqa: E402
from assistant import butler_agent  # noqa: E402
from core.events import event_bus  # noqa: E402


class FakeShellExecution:
    def __init__(self, root: Path) -> None:
        self.workspace_root = str(root)


class FakeRouter:
    """满足 FileCapabilityDomain / DesktopAutomationCapabilityDomain 最小依赖。"""

    _DEFAULT_DOWNLOAD_DIRNAME = "assistant_downloads"

    def __init__(self, tmp_path: Path) -> None:
        # workspace 与 data 平级：真实环境中写入目录不在 workspace 内
        # （_resolve_write_path 会拒绝 workspace 内写入，保护项目源码）
        self.shell_execution = FakeShellExecution(tmp_path / "workspace")
        self.data_store = SimpleNamespace(base_dir=str(tmp_path / "data"))
        self.write_guard = None
        self.ai_client = None
        Path(self.data_store.base_dir).mkdir(parents=True, exist_ok=True)


@pytest.fixture
def domain(tmp_path):
    return FileCapabilityDomain(FakeRouter(tmp_path)), FakeRouter(tmp_path)


# ── file.grep ─────────────────────────────────────────────


def test_file_grep_finds_match(tmp_path):
    router = FakeRouter(tmp_path)
    dom = FileCapabilityDomain(router)
    data_dir = Path(router.data_store.base_dir)
    (data_dir / "a.txt").write_text("hello world\nfoo bar\n", encoding="utf-8")
    (data_dir / "b.txt").write_text("nothing here\n", encoding="utf-8")

    result = dom.file_grep({"pattern": r"hello \w+", "path": str(data_dir)})
    assert result["success"] is True
    assert result["count"] == 1
    assert result["matches"][0]["line"] == 1
    assert "hello world" in result["matches"][0]["text"]


def test_file_grep_invalid_regex(tmp_path):
    dom = FileCapabilityDomain(FakeRouter(tmp_path))
    result = dom.file_grep({"pattern": "([bad"})
    assert result["success"] is False
    assert "invalid_regex" in result["reason"]


def test_file_grep_respects_max_results(tmp_path):
    router = FakeRouter(tmp_path)
    dom = FileCapabilityDomain(router)
    data_dir = Path(router.data_store.base_dir)
    (data_dir / "many.txt").write_text("\n".join(f"hit-{i}" for i in range(20)), encoding="utf-8")
    result = dom.file_grep({"pattern": "hit-", "path": str(data_dir), "max_results": 5})
    assert result["count"] == 5
    assert result["truncated"] is True


# ── file.edit ─────────────────────────────────────────────


def _write_data_file(router, name: str, content: str) -> Path:
    p = Path(router.data_store.base_dir) / name
    p.write_text(content, encoding="utf-8")
    return p


def test_file_edit_unique_replace(tmp_path):
    router = FakeRouter(tmp_path)
    dom = FileCapabilityDomain(router)
    p = _write_data_file(router, "t.txt", "aaa bbb aaa\n")

    result = dom.file_edit({"path": str(p), "old_text": "bbb", "new_text": "ccc"})
    assert result["success"] is True and result["replaced"] == 1
    assert p.read_text(encoding="utf-8") == "aaa ccc aaa\n"


def test_file_edit_ambiguous_without_replace_all(tmp_path):
    router = FakeRouter(tmp_path)
    dom = FileCapabilityDomain(router)
    p = _write_data_file(router, "t.txt", "aaa bbb aaa\n")

    result = dom.file_edit({"path": str(p), "old_text": "aaa", "new_text": "x"})
    assert result["success"] is False
    assert "ambiguous" in result["reason"]
    assert p.read_text(encoding="utf-8") == "aaa bbb aaa\n"  # 未被改动


def test_file_edit_replace_all(tmp_path):
    router = FakeRouter(tmp_path)
    dom = FileCapabilityDomain(router)
    p = _write_data_file(router, "t.txt", "aaa bbb aaa\n")

    result = dom.file_edit({"path": str(p), "old_text": "aaa", "new_text": "x", "replace_all": True})
    assert result["success"] is True and result["replaced"] == 2
    assert p.read_text(encoding="utf-8") == "x bbb x\n"


def test_file_edit_old_text_not_found(tmp_path):
    router = FakeRouter(tmp_path)
    dom = FileCapabilityDomain(router)
    p = _write_data_file(router, "t.txt", "aaa\n")

    result = dom.file_edit({"path": str(p), "old_text": "zzz", "new_text": "x"})
    assert result["success"] is False
    assert result["reason"] == "old_text_not_found"


# ── desktop.screenshot ────────────────────────────────────


def test_desktop_screenshot_saves_and_builds_artifact(tmp_path, monkeypatch):
    from PIL import Image

    fake_img = Image.new("RGB", (800, 600), "red")
    monkeypatch.setattr(_desktop, "ImageGrab", SimpleNamespace(grab=lambda **kw: fake_img))

    router = FakeRouter(tmp_path)
    dom = DesktopAutomationCapabilityDomain(router)
    result = dom.desktop_screenshot({"describe": False})

    assert result["success"] is True
    assert Path(result["path"]).exists()
    arts = result.get("artifacts")
    assert arts and arts[0]["type"] == "image"
    assert arts[0]["src"].startswith("data:image/jpeg;base64,")


def test_desktop_screenshot_vision_note_without_ai(tmp_path, monkeypatch):
    from PIL import Image

    fake_img = Image.new("RGB", (100, 100), "blue")
    monkeypatch.setattr(_desktop, "ImageGrab", SimpleNamespace(grab=lambda **kw: fake_img))

    router = FakeRouter(tmp_path)
    dom = DesktopAutomationCapabilityDomain(router)
    result = dom.desktop_screenshot({})  # describe 默认 true，但无 ai_client
    assert result["success"] is True
    assert "vision_note" in result


# ── 审批闭环（CONFIRM → pending_confirmation → resolve）──


class FakeCapRouter:
    def __init__(self):
        self.calls = []

    async def execute_direct(self, action_id, args, approved=False):
        self.calls.append((action_id, dict(args or {})))
        return {"ok": True, "action": action_id}


class FakeCtx:
    pass


@pytest.fixture(autouse=True)
def _clear_confirmations(tmp_path):
    import os
    import services.confirmation_store as _cs_mod
    os.environ["SIYI_DATA_DIR"] = str(tmp_path)
    _cs_mod._store_singleton = None
    _cs_mod.get_confirmation_store()
    butler_agent._pending_contexts.clear()
    yield
    butler_agent._pending_contexts.clear()
    _cs_mod._store_singleton = None


async def test_confirm_tool_returns_pending_and_emits_event():
    ctx = FakeCtx()
    ctx.capability_router = FakeCapRouter()

    events = []
    handler = lambda ev: events.append(ev)  # noqa: E731
    event_bus.on("action.confirm_required", handler)
    try:
        out = json.loads(await butler_agent.execute_tool_action(ctx, "shell.run", {"command": "dir"}))
    finally:
        event_bus.off("action.confirm_required", handler)

    assert out["pending_confirmation"] is True
    cid = out["confirmation_id"]
    assert events and events[0].payload["confirmation_id"] == cid
    # 未批准前不应执行
    assert ctx.capability_router.calls == []


async def test_resolve_confirmation_approved_executes():
    ctx = FakeCtx()
    cap = FakeCapRouter()
    ctx.capability_router = cap

    out = json.loads(await butler_agent.execute_tool_action(ctx, "shell.run", {"command": "dir"}))
    cid = out["confirmation_id"]

    resolved_events = []
    handler = lambda ev: resolved_events.append(ev)  # noqa: E731
    event_bus.on("action.confirm_resolved", handler)
    try:
        res = await butler_agent.resolve_confirmation(cid, approved=True)
    finally:
        event_bus.off("action.confirm_resolved", handler)

    assert res["success"] is True and res["approved"] is True
    assert cap.calls == [("shell.run", {"command": "dir"})]
    assert resolved_events and resolved_events[0].payload["approved"] is True

    status = butler_agent.get_confirmation_status(cid)
    assert status["status"] == "approved"
    assert status["result"]["ok"] is True


async def test_resolve_confirmation_rejected_does_not_execute():
    ctx = FakeCtx()
    cap = FakeCapRouter()
    ctx.capability_router = cap

    out = json.loads(await butler_agent.execute_tool_action(ctx, "app.launch", {"command": "notepad"}))
    cid = out["confirmation_id"]

    res = await butler_agent.resolve_confirmation(cid, approved=False)
    assert res["success"] is True and res["approved"] is False
    assert cap.calls == []
    assert butler_agent.get_confirmation_status(cid)["status"] == "rejected"


async def test_confirmation_status_tool_and_unknown_id():
    # confirmation.status 作为工具调用
    ctx = FakeCtx()
    ctx.capability_router = FakeCapRouter()
    out = json.loads(await butler_agent.execute_tool_action(ctx, "shell.run", {"command": "dir"}))
    cid = out["confirmation_id"]

    status = json.loads(await butler_agent.execute_tool_action(
        ctx, "confirmation.status", {"confirmation_id": cid}))
    assert status["status"] == "pending"

    unknown = butler_agent.get_confirmation_status("nonexistent")
    assert unknown["status"] == "expired_or_not_found"


async def test_artifacts_stripped_from_llm_text_and_emitted():
    """工具结果中的 artifacts（base64）不进 LLM 文本，改经 chat.artifact 事件下发。"""
    ctx = FakeCtx()

    class ArtifactCap:
        async def execute_direct(self, action_id, args, approved=False):
            return {"success": True, "artifacts": [{"type": "image", "src": "data:image/jpeg;base64,AAAA"}]}

    ctx.capability_router = ArtifactCap()

    events = []
    handler = lambda ev: events.append(ev)  # noqa: E731
    event_bus.on("chat.artifact", handler)
    try:
        text = await butler_agent.execute_tool_action(ctx, "desktop.screenshot", {})
    finally:
        event_bus.off("chat.artifact", handler)

    assert "base64,AAAA" not in text  # LLM 上下文不含图片数据
    assert events and events[0].payload["artifacts"][0]["src"].endswith("AAAA")


# ── file.delete（P3: 走文件能力域 + 白名单路径，不再依赖 shell.run）─────────


def test_file_delete_removes_file_within_roots(tmp_path):
    dom = FileCapabilityDomain(FakeRouter(tmp_path))
    data_dir = Path(tmp_path) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    f = data_dir / "empty.txt"
    f.write_text("", encoding="utf-8")

    result = dom.file_delete({"path": str(f)})
    assert result["success"] is True
    assert result["deleted"] is True
    assert not f.exists()


def test_file_delete_rejects_outside_roots(tmp_path):
    dom = FileCapabilityDomain(FakeRouter(tmp_path))
    outside = tmp_path / "outside" / "secret.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("secret", encoding="utf-8")

    result = dom.file_delete({"path": str(outside)})
    assert result["success"] is False
    assert "path_outside_allowed_root" in result.get("reason", "") or "outside" in str(result.get("reason", ""))


def test_file_delete_rejects_missing_and_nonempty_dir(tmp_path):
    dom = FileCapabilityDomain(FakeRouter(tmp_path))
    data_dir = Path(tmp_path) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    missing = dom.file_delete({"path": str(data_dir / "nope.txt")})
    assert missing["success"] is False
    assert missing["reason"] == "path_not_found"

    sub = data_dir / "sub"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "keep.txt").write_text("keep", encoding="utf-8")
    nonempty = dom.file_delete({"path": str(sub)})
    assert nonempty["success"] is False
    assert nonempty["reason"] == "directory_not_empty"
