from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore
from services.subagent_service import SubagentManager


class _SettingsRepo:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get_setting(self, key, default=None):
        return self._values.get(key, default)


def test_subagent_weekly_notes_digest_writes_summary(tmp_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "a.md").write_text("# Alpha\n\n讨论了MCP 接入方案", encoding="utf-8")
    (notes_dir / "b.md").write_text("# Beta\n\n整理了本周任务和日报要点。", encoding="utf-8")

    manager = SubagentManager(data_store=LocalDataStore(base_dir=str(tmp_path)))
    run = manager.spawn(
        "weekly_notes_digest",
        {"root": str(notes_dir), "output_path": str(tmp_path / "digest.md"), "days": 7},
        label="本周笔记整理",
    )

    deadline = time.time() + 5
    latest = run
    while time.time() < deadline:
        latest = manager.get_run(run["run_id"])
        if latest and latest.get("status") in {"completed", "failed"}:
            break
        time.sleep(0.05)

    assert latest is not None
    assert latest["status"] == "completed"
    summary_path = Path(latest["result"]["summary_path"])
    assert summary_path.exists()
    content = summary_path.read_text(encoding="utf-8")
    assert "本周笔记整理" in content


def test_subagent_weekly_tech_digest_uses_focus_topics_and_persists_outputs(tmp_path):
    events = []
    settings = _SettingsRepo({"assistant_focus_topics": ["AI Agent", "PyQt"]})
    data_store = LocalDataStore(base_dir=str(tmp_path))

    class _NoteRepo:
        def __init__(self):
            self.rows = []

        def add_note(self, payload):
            self.rows.append(payload)
            return len(self.rows)

    class _InboxRepo:
        def __init__(self):
            self.rows = []

        def add_entry(self, payload):
            self.rows.append(payload)
            return len(self.rows)

    def _executor(steps, parallel):
        step = steps[0]
        events.append(step["action_id"])
        if step["action_id"] == "memory.recall":
            return {
                "status": "completed",
                "outcomes": [{"status": "ok", "payload": {"items": [{"source_type": "note", "title": "旧笔记", "snippet": "与AI Agent 工具相关"}]}}],
            }
        if step["action_id"] == "web.search":
            return {
                "status": "completed",
                "outcomes": [{"status": "ok", "payload": {"results": [{"title": "AI Agent 新进展", "url": "https://example.com/agent"}]}}],
            }
        if step["action_id"] == "news.briefing":
            topic = step["payload"]["topic"]
            return {"status": "completed", "outcomes": [{"status": "ok", "payload": {"briefing": f"{topic} 本周值得关注的发布与实践。"}}]}
        return {"status": "completed", "outcomes": [{"status": "failed", "error": "unexpected"}]}

    note_repo = _NoteRepo()
    inbox_repo = _InboxRepo()
    manager = SubagentManager(
        data_store=data_store,
        settings_repository=settings,
        capability_executor=_executor,
        note_repository=note_repo,
        inbox_repository=inbox_repo,
    )

    result = manager._run_weekly_tech_digest({"output_path": str(tmp_path / "tech_digest.md")})

    assert result["status"] == "completed"
    assert Path(result["summary_path"]).exists()
    content = Path(result["summary_path"]).read_text(encoding="utf-8")
    assert "AI Agent" in content
    assert "PyQt" in content
    assert events.count("memory.recall") == 2
    assert note_repo.rows
    assert inbox_repo.rows


def test_subagent_weekly_tech_digest_skips_without_topics(tmp_path):
    manager = SubagentManager(
        data_store=LocalDataStore(base_dir=str(tmp_path)),
        settings_repository=_SettingsRepo({"assistant_focus_topics": []}),
    )

    result = manager._run_weekly_tech_digest({"output_path": str(tmp_path / "tech_digest.md")})

    assert result["status"] == "skipped"
    assert result["reason"] == "no_topics"


def test_subagent_run_now_executes_weekly_tech_digest_and_persists_run(tmp_path):
    settings = _SettingsRepo({"assistant_focus_topics": ["AI Agent"]})
    data_store = LocalDataStore(base_dir=str(tmp_path))

    class _NoteRepo:
        def add_note(self, payload):
            return 1

    class _InboxRepo:
        def add_entry(self, payload):
            return 1

    def _executor(steps, parallel):
        step = steps[0]
        if step["action_id"] == "memory.recall":
            return {"status": "completed", "outcomes": [{"status": "ok", "payload": {"items": []}}]}
        if step["action_id"] == "web.search":
            return {"status": "completed", "outcomes": [{"status": "ok", "payload": {"results": []}}]}
        if step["action_id"] == "news.briefing":
            return {"status": "completed", "outcomes": [{"status": "ok", "payload": {"briefing": "本周摘要"}}]}
        return {"status": "completed", "outcomes": []}

    manager = SubagentManager(
        data_store=data_store,
        settings_repository=settings,
        capability_executor=_executor,
        note_repository=_NoteRepo(),
        inbox_repository=_InboxRepo(),
    )

    run = manager.run_now("weekly_tech_digest", {"output_path": str(tmp_path / "manual_digest.md")}, label="手动跑本周摘要")

    assert run["status"] == "completed"
    assert Path(run["result"]["summary_path"]).exists()
    persisted = data_store.load_json("subagent_runs", [])
    assert any(item.get("run_id") == run["run_id"] and item.get("status") == "completed" for item in persisted)


def test_subagent_weekly_tech_digest_blocks_output_outside_data_root(tmp_path):
    manager = SubagentManager(
        data_store=LocalDataStore(base_dir=str(tmp_path)),
        settings_repository=_SettingsRepo({"assistant_focus_topics": ["AI Agent"]}),
        capability_executor=lambda steps, parallel: {"status": "completed", "outcomes": [{"status": "ok", "payload": {}}]},
    )

    with pytest.raises(ValueError, match="output_path_outside_data_root"):
        manager._run_weekly_tech_digest({"output_path": str(tmp_path.parent / "escape.md")})


def test_subagent_capability_timeout_returns_failed_payload(tmp_path):
    manager = SubagentManager(
        data_store=LocalDataStore(base_dir=str(tmp_path)),
        settings_repository=_SettingsRepo({"assistant_focus_topics": ["AI Agent"]}),
        # 执行耗时 0.5s，超时阈值 0.05s：间隔足够大，高负载全量测试下
        # 也不会因线程池调度抖动误判为正常完成（旧值 0.05/0.01 太贴近）
        capability_executor=lambda steps, parallel: time.sleep(0.5) or {"status": "completed", "outcomes": []},
    )
    manager.CAPABILITY_TIMEOUT_SECONDS = 0.05

    result = manager._execute_single_capability("memory.recall", {"query": "AI Agent"})

    assert result["success"] is False
    assert result["reason"] == "capability_timeout"
