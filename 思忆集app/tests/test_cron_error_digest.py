"""V3-08 验收测试：system.error_digest 解析临时日志并按异常签名聚合投递。"""
from __future__ import annotations

import types
from datetime import datetime, timedelta
from typing import Any

from services.cron_actions import execute_cron_action, list_cron_actions


class _CapturingInbox:
    def __init__(self) -> None:
        self.deliveries: list[dict[str, Any]] = []

    def deliver_suggestion(self, *, text, action="", reason="", **kwargs):
        self.deliveries.append({"text": text, "action": action})
        return {"delivered_to": "inbox_card", "item": {"title": text}}


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _old_date(days_ago: int) -> str:
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _ctx(tmp_path) -> Any:
    inbox = _CapturingInbox()
    settings = types.SimpleNamespace(
        get_setting=lambda key, default="": (
            str(tmp_path) if key == "error_digest_log_dir" else default
        )
    )
    return types.SimpleNamespace(settings_repository=settings, assistant_inbox_service=inbox), inbox


def _write_log(tmp_path, name: str, lines: list[str]) -> None:
    (tmp_path / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_error_digest_registered() -> None:
    ids = {a["action_id"] for a in list_cron_actions()}
    assert "system.error_digest" in ids


def test_error_digest_completed_and_delivered(tmp_path) -> None:
    _write_log(tmp_path, "backend.dev.log", [
        f"{_today()} [ERROR] siyi.svc: Crash boom one",
        f"{_today()} [ERROR] siyi.svc: Crash boom one",
        f"{_today()} [CRITICAL] siyi.svc: second kind",
    ])
    _write_log(tmp_path, "backend.dev.err.log", [])
    ctx, inbox = _ctx(tmp_path)
    res = execute_cron_action(ctx, "system.error_digest", {})
    assert res is not None
    assert res.get("status") == "completed"
    assert res.get("digest_text")
    assert inbox.deliveries
    assert inbox.deliveries[0]["action"] == "trace.list"
    assert "每周错误摘要" in res["digest_text"]


def test_error_digest_filters_outdated_entries(tmp_path) -> None:
    _write_log(tmp_path, "backend.dev.log", [
        f"{_old_date(8)} [ERROR] siyi.svc: stale one",
        f"{_today()} [ERROR] siyi.svc: fresh two",
    ])
    ctx, inbox = _ctx(tmp_path)
    res = execute_cron_action(ctx, "system.error_digest", {})
    assert res is not None
    assert res.get("status") == "completed"
    text = res.get("digest_text", "")
    assert "fresh" in text
    assert "stale" not in text


def test_error_digest_aggregates_traceback_exception_type(tmp_path) -> None:
    _write_log(tmp_path, "backend.dev.log", [
        f"{_today()} [ERROR] siyi.knowledge: Traceback (most recent call last):",
        '  File "x.py", line 1, in foo',
        "    bar()",
        "AttributeError: 'list' object has no attribute 'get'",
    ])
    _write_log(tmp_path, "backend.dev.err.log", [])
    ctx, inbox = _ctx(tmp_path)
    res = execute_cron_action(ctx, "system.error_digest", {})
    assert res is not None
    assert res.get("status") == "completed"
    assert "AttributeError" in res.get("digest_text", "")