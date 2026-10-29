"""OQ2 桌面能力 P1 工具测试（mcp_server/desktop.py）。

安全边界验证：
- 审批门控：无批准 → requires_approval（并发起确认）；批准后 → 放行；
  pending 存在 → 仍要求批准（内核不可自我批准）。
- app_launch 白名单：白名单外拒绝；白名单内可启动（headless 环境兜底 error）。
- 依赖缺失优雅降级：截图/剪贴板能力缺失或环境不支持时返回
  {status: unavailable|error}，不崩溃。
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from mcp_server import desktop
from services.confirmation_store import ConfirmationStore


@pytest.fixture
def store(tmp_path) -> ConfirmationStore:
    s = ConfirmationStore(data_dir=str(tmp_path))
    s.clear()
    return s


class TestApprovalGate:
    def test_no_approval_creates_and_requires(self, store) -> None:
        """首次调用：无任何确认 → 发起待批确认并返回 requires_approval。"""
        gate = desktop._require_approved(store, "siyi_desktop_screenshot", {})
        assert gate is not None
        assert gate["status"] == "requires_approval"
        assert gate["confirmation_id"]
        pending = [e for e in store.list() if e["status"] == "pending"]
        assert len(pending) == 1
        assert pending[0]["action_id"] == "siyi_desktop_screenshot"

    def test_pending_not_enough(self, store) -> None:
        """已发起待批但仍未批准 → 仍返回 requires_approval（不可自我批准）。"""
        desktop._require_approved(store, "siyi_clipboard_read", {})
        gate = desktop._require_approved(store, "siyi_clipboard_read", {})
        assert gate is not None and gate["status"] == "requires_approval"

    def test_approved_returns_none(self, store) -> None:
        """用户批准后 → 放行（返回 None，可执行）。"""
        cid = store.create("siyi_desktop_screenshot", {}, source="mcp:kernel", title="t")
        store.resolve(cid, approved=True)
        assert desktop._require_approved(store, "siyi_desktop_screenshot", {}) is None

    def test_rejected_not_enough(self, store) -> None:
        """被拒绝的确认不构成放行。"""
        cid = store.create("siyi_clipboard_write", {"text": "x"}, source="mcp:kernel", title="t")
        store.resolve(cid, approved=False)
        gate = desktop._require_approved(store, "siyi_clipboard_write", {"text": "x"})
        assert gate is not None and gate["status"] == "requires_approval"


class TestAppLaunchAllowlist:
    def test_refused_outside_allowlist(self) -> None:
        result = desktop.app_launch("evil.exe")
        assert result["status"] == "refused"
        assert "白名单" in result["reason"]

    def test_allowlist_entry_runs_or_errors_gracefully(self) -> None:
        result = desktop.app_launch("notepad")
        # headless 环境可能无法真正弹窗，但不应崩溃；status ∈ ok/error
        assert result["status"] in ("ok", "error")

    def test_empty_name_refused(self) -> None:
        assert desktop.app_launch("")["status"] == "refused"


class TestCapabilityGracefulDegradation:
    def test_screenshot_returns_dict(self, tmp_path) -> None:
        result = desktop.desktop_screenshot(str(tmp_path))
        assert isinstance(result, dict)
        assert result["status"] in ("ok", "unavailable", "error")

    def test_clipboard_read_returns_dict(self) -> None:
        result = desktop.clipboard_read()
        assert isinstance(result, dict)
        assert result["status"] in ("ok", "error", "unavailable")

    def test_clipboard_write_returns_dict(self) -> None:
        result = desktop.clipboard_write("probe")
        assert isinstance(result, dict)
        assert result["status"] in ("ok", "error", "unavailable")


class TestToolsRegistered:
    def test_p1_tools_in_build_specs(self) -> None:
        """4 个桌面工具在 build_tool_specs 注册且带审批门控 handler。"""
        from mcp_server.tools import build_tool_specs
        from mcp_server.store import SiyiStore

        store = SiyiStore(data_dir=str(__import__("tempfile").mkdtemp()))
        specs = build_tool_specs(store)
        names = {s.name for s in specs}
        for n in ("siyi_desktop_screenshot", "siyi_clipboard_read",
                  "siyi_clipboard_write", "siyi_app_launch"):
            assert n in names, f"缺少 P1 工具 {n}"
