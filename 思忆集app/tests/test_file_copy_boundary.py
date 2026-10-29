"""M2.1 SEC-3：file_copy dest 改走 _resolve_write_path 写边界。

出口断言（任务书 M2.1-4）：
①dest 越界（写边界外绝对路径）→ {"success": False, ...} 且目标不落盘；
②边界内 dest → success。

隔离：FakeRouter data_root/workspace 全在 tmp_path。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.capability._file_ops import FileCapabilityDomain


class FakeRouter:
    _DEFAULT_DOWNLOAD_DIRNAME = "assistant_downloads"

    def __init__(self, tmp_path: Path) -> None:
        self.shell_execution = SimpleNamespace(workspace_root=str(tmp_path / "workspace"))
        self.data_store = SimpleNamespace(base_dir=str(tmp_path / "data"))
        self.write_guard = None
        Path(self.data_store.base_dir).mkdir(parents=True, exist_ok=True)


@pytest.fixture
def router(tmp_path):
    return FakeRouter(tmp_path)


def _src_file(router) -> Path:
    p = Path(router.data_store.base_dir) / "src.txt"
    p.write_text("payload", encoding="utf-8")
    return p


def test_copy_dest_outside_write_boundary_refused(router, tmp_path) -> None:
    dom = FileCapabilityDomain(router)
    src = _src_file(router)
    outside = tmp_path / "outside" / "leak.txt"
    result = dom.file_copy({"source": str(src), "dest": str(outside)})
    assert result["success"] is False
    assert not outside.exists()


def test_copy_dest_within_boundary_ok(router) -> None:
    dom = FileCapabilityDomain(router)
    src = _src_file(router)
    dst = Path(router.data_store.base_dir) / "sub" / "copied.txt"
    result = dom.file_copy({"source": str(src), "dest": str(dst)})
    assert result["success"] is True
    assert dst.read_text(encoding="utf-8") == "payload"


def test_copy_workspace_dest_still_blocked(router) -> None:
    """写边界附带语义：workspace 内 dest 亦拒（与 file_write 一致，护源码）。"""
    dom = FileCapabilityDomain(router)
    src = _src_file(router)
    ws_dest = Path(router.shell_execution.workspace_root) / "evil.txt"
    result = dom.file_copy({"source": str(src), "dest": str(ws_dest)})
    assert result["success"] is False
    assert not ws_dest.exists()
