"""M2.1 SEC-8：desktop_screenshot 写边界收敛到 <data_dir>/screenshots/。

出口断言（任务书 M2.1-5）：外部 target_dir（如 C:/Windows/Temp 类路径）
→ 返回文件最终路径落在 <data_dir>/screenshots/ 白名单下。

隔离：SIYI_DATA_DIR → tmp_path；ImageGrab.grab monkeypatch（不依赖真桌面）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from mcp_server import desktop


class _FakeImage:
    def save(self, path: str) -> None:
        Path(path).write_bytes(b"PNG-FAKE")


@pytest.fixture
def fake_grab(monkeypatch, tmp_path):
    pytest.importorskip("PIL.ImageGrab")
    import PIL.ImageGrab

    monkeypatch.setattr(PIL.ImageGrab, "grab", lambda: _FakeImage())
    monkeypatch.setenv("SIYI_DATA_DIR", str(tmp_path))
    return tmp_path


def test_external_target_dir_rewritten_to_whitelist(fake_grab, monkeypatch) -> None:
    external = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp")
    out = desktop.desktop_screenshot(external)
    assert out["status"] == "ok"
    allowed = os.path.normcase(str(fake_grab / "screenshots"))
    assert os.path.normcase(out["path"]).startswith(allowed)
    assert Path(out["path"]).exists()


def test_whitelist_target_dir_kept(fake_grab) -> None:
    allowed_dir = str(fake_grab / "screenshots")
    out = desktop.desktop_screenshot(allowed_dir)
    assert out["status"] == "ok"
    assert os.path.normcase(out["path"]) == os.path.normcase(os.path.join(allowed_dir, "screenshot.png"))
