"""M2.1 SEC-2：cmd.exe /k 白名单摘除 + app_launch 审批参数摘要。

出口断言（任务书 M2.1-3）：
①mcp_server/desktop.py 源码不再含 cmd.exe 字面量；
②审批 preview 含命令串且 detail 长度 ≤200。

隔离：ConfirmationStore 直连 tmp_path。
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from mcp_server import desktop
from mcp_server.desktop import APP_LAUNCH_ALLOWLIST, _require_approved, app_launch, approval_preview
from services.confirmation_store import ConfirmationStore


def test_desktop_source_free_of_cmd_shell_carrier() -> None:
    src = (APP_ROOT / "mcp_server" / "desktop.py").read_text(encoding="utf-8")
    assert "cmd.exe" not in src
    assert "cmd" not in APP_LAUNCH_ALLOWLIST


def test_app_launch_refuses_removed_cmd_entry() -> None:
    assert app_launch("cmd")["status"] == "refused"


def test_approval_preview_carries_command_summary(tmp_path) -> None:
    store = ConfirmationStore(data_dir=str(tmp_path))
    gate = _require_approved(store, "siyi_app_launch", {"app_name": "notepad"})
    assert gate["status"] == "requires_approval"
    entry = store.get(gate["confirmation_id"])
    assert entry["title"] == "notepad"
    assert "notepad.exe" in entry["detail"]
    assert len(entry["detail"]) <= 200


def test_approval_preview_truncated_to_200() -> None:
    title, detail = approval_preview("siyi_app_launch", {"app_name": "x" * 300})
    assert len(detail) <= 200
    other_title, other_detail = approval_preview("siyi_clipboard_read", {})
    assert other_title == "siyi_clipboard_read" and other_detail == ""
