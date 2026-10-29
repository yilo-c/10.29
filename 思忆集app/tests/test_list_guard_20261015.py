"""帧守卫（_list_guard）单元测试 + 9 个集合方法接线门禁（2026-10-15）。

背景：U1 大响应治理（audit.list/knowledge.search）之后，Explore 清点出
7 个硬命中 + 2 个疑似无界集合方法。本批统一套 cap_list/capped_payload：
当前数据量零行为变化，超 2000 截断 + 告警（+ dict 形态 truncated 标记）。
门禁部分用源码标记锁定接线，防止后续重构静默丢守卫。
"""

from __future__ import annotations

from pathlib import Path

from backend.handlers._list_guard import DEFAULT_LIST_CAP, cap_list, clamp_limit, capped_payload

HANDLERS = Path(__file__).resolve().parents[1] / "backend" / "handlers"


class TestCapList:
    def test_under_cap_returns_same_object(self):
        items = [{"i": n} for n in range(10)]
        out = cap_list(items, method="x.test")
        assert out is items

    def test_over_cap_truncates_to_prefix(self):
        items = [{"i": n} for n in range(DEFAULT_LIST_CAP + 50)]
        out = cap_list(items, method="x.test")
        assert len(out) == DEFAULT_LIST_CAP
        assert out[0] == {"i": 0}
        assert out[-1] == {"i": DEFAULT_LIST_CAP - 1}


class TestCappedPayload:
    def test_under_cap_no_truncated(self):
        items = [1, 2, 3]
        out, truncated = capped_payload(items, method="x.test")
        assert out is items and truncated is False

    def test_over_cap_marks_truncated(self):
        items = list(range(DEFAULT_LIST_CAP + 1))
        out, truncated = capped_payload(items, method="x.test")
        assert truncated is True and len(out) == DEFAULT_LIST_CAP


class TestWiringGate:
    """接线门禁：9 个治理点必须仍调用帧守卫（防重构静默丢失）。"""

    def _src(self, name: str) -> str:
        return (HANDLERS / name).read_text(encoding="utf-8")

    def test_notes_list_trashed(self):
        assert 'cap_list(trashed, method="notes.list_trashed")' in self._src("_notes.py")

    def test_tasks_list_trashed(self):
        assert 'cap_list(trashed, method="tasks.list_trashed")' in self._src("_tasks.py")

    def test_reminder_list(self):
        assert 'cap_list(due, method="reminder.list")' in self._src("_reminder.py")

    def test_word_list_all_four_paths(self):
        src = self._src("_wordbook.py")
        assert src.count('method="word.list"') == 4

    def test_reading_list(self):
        assert 'capped_payload(items, method="reading.list")' in self._src("_reading.py")

    def test_chat_list_conversations(self):
        assert 'cap_list(result, method="chat.list_conversations")' in self._src("_chat_memory.py")

    def test_transfer_files(self):
        assert 'cap_list(await asyncio.to_thread(_list_files), method="transfer.files")' in self._src("_transfer.py")

    def test_sync_conflicts_list(self):
        assert 'capped_payload(result, method="sync.conflicts.list")' in self._src("_sync.py")

    def test_maintenance_proposals_list(self):
        assert 'capped_payload(list(proposals), method="maintenance.proposals.list")' in self._src("_maintenance.py")

    def test_confirmation_list(self):
        assert 'capped_payload(list(confirmations), method="confirmation.list")' in self._src("_confirmations.py")

    def test_guard_module_importable(self):
        import backend.handlers._list_guard as g  # noqa: F401

        assert g.DEFAULT_LIST_CAP == 2000


class TestClampLimit:
    def test_normal_passthrough(self):
        assert clamp_limit(50, default=50) == 50

    def test_zero_and_negative_fall_to_default(self):
        # 前端全量核实从不传 limit<=0：0/负从"全量穿透"收紧为默认
        assert clamp_limit(0, default=50) == 50
        assert clamp_limit(-1, default=50) == 50

    def test_over_cap_clamped(self):
        assert clamp_limit(999_999, default=50) == DEFAULT_LIST_CAP

    def test_invalid_falls_to_default(self):
        assert clamp_limit(None, default=50) == 50
        assert clamp_limit("abc", default=50) == 50
        assert clamp_limit("37", default=50) == 37


class TestClampWiringGate:
    """第二梯队 15 点接线门禁（limit<=0 全量穿透收紧 + 上限 clamp）。"""

    def _src(self, name: str) -> str:
        return (HANDLERS / name).read_text(encoding="utf-8")

    def test_notes_list(self):
        assert "clamp_limit(limit, default=50)" in self._src("_notes.py")

    def test_tasks_list(self):
        assert "clamp_limit(limit, default=50)" in self._src("_tasks.py")

    def test_chat_history(self):
        assert "clamp_limit(limit, default=50)" in self._src("_chat_memory.py")

    def test_translations_list(self):
        assert "clamp_limit(limit, default=50)" in self._src("_ai_translate.py")

    def test_word_list_due(self):
        assert "clamp_limit(limit, default=200)" in self._src("_wordbook.py")

    def test_ai_usage_two_sites(self):
        assert self._src("_ai_usage.py").count("clamp_limit(") == 2

    def test_trace_list(self):
        assert "clamp_limit(limit, default=50)" in self._src("_trace.py")

    def test_task_queue_two_sites(self):
        assert self._src("_task_queue.py").count("clamp_limit(") == 2

    def test_job_hunter_two_sites(self):
        assert self._src("_job_hunter.py").count("clamp_limit(") == 2

    def test_personal_raw_items(self):
        assert "clamp_limit(limit, default=100)" in self._src("_personal.py")

    def test_cron_history(self):
        assert "clamp_limit(limit, default=20)" in self._src("_cron.py")

    def test_transfer_history(self):
        assert "clamp_limit(limit, default=20)" in self._src("_transfer.py")

    def test_inbox_list_confirmations(self):
        assert "clamp_limit(limit, default=50)" in self._src("_inbox_crud.py")
