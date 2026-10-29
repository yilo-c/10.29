"""M2.1 QUA-2：confirmation_flow 执行器异常须如实报 failed，不吞异常落 approved 兜底。

出口断言（任务书 M2.1-2）：monkeypatch 动作执行器抛 RuntimeError("boom")
→ 返回体含 error 且该动作不落 approved 兜底文案。

隔离：ConfirmationStore 直连 tmp_path，不触碰 data/data。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from services import confirmation_flow
from services.confirmation_store import ConfirmationStore


@pytest.fixture
def store(tmp_path, monkeypatch) -> ConfirmationStore:
    s = ConfirmationStore(data_dir=str(tmp_path))
    s.clear()
    monkeypatch.setattr(confirmation_flow, "get_confirmation_store", lambda: s)
    return s


def test_handler_exception_reports_error_not_approved_fallback(store, monkeypatch) -> None:
    def raiser(confirmation_id: str, approved: bool, reason) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(confirmation_flow, "_execution_handlers", [raiser])
    cid = store.create("notes.delete", {"id": 1}, source="butler")
    out = asyncio.run(confirmation_flow.resolve_confirmation(cid, True))
    assert out["result"]["error"] == "boom"
    assert out["result"].get("approved") is None
    assert "note" not in out["result"]
    assert store.get(cid)["result"]["error"] == "boom"


def test_no_handler_still_uses_default_branch(store, monkeypatch) -> None:
    """对照：无 handler（不抛异常）时仍走原兜底分支，行为不回退。"""
    monkeypatch.setattr(confirmation_flow, "_execution_handlers", [])
    cid = store.create("notes.delete", {"id": 2}, source="butler")
    out = asyncio.run(confirmation_flow.resolve_confirmation(cid, True))
    assert out["result"].get("approved") is True
