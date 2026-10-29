"""Sprint 1C — vocab ReAct tools (vocab.quiz_in_chat / vocab.review_status).

执行体已收敛（审计波次② C-1 步骤 3）：这些工具的真现在 A 套路由，
本文件仍然从 ``make_react_executor`` 入口断言，作为收敛后的回归护栏。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.react_tool_catalog import REACT_TOOL_CATALOG, SAFE_TOOL_IDS_BASE  # noqa: E402
from services.react_executor import make_react_executor  # noqa: E402


class _FakeRepo:
    def __init__(self, words: list[dict]) -> None:
        self._words = words

    def list_words(self):
        return list(self._words)

    def get_words_by_wordbook(self, wb_id: int):
        return [w for w in self._words if w.get("wordbook_id") == wb_id]


class _FakeContext:
    """C-1 步骤 3 重指向：vocab.* 实现已从 B 套内联分支迁入 A 套
    ``assistant/capability/_local_resource_tools.py``，故 context 须挂
    ``capability_router``（词库仓储由 router 持有）；断言一字不改。"""

    def __init__(self, words: list[dict]) -> None:
        import tempfile
        from pathlib import Path

        from assistant.capability_router import AssistantCapabilityRouter
        from data.local_storage import LocalDataStore

        self.vocabulary_repository = _FakeRepo(words)
        self.capability_router = AssistantCapabilityRouter(
            data_store=LocalDataStore(base_dir=tempfile.mkdtemp(prefix="siyi-c1-vocab-")),
            settings_repository=None,
            observability_service=None,
            note_repository=None,
            task_repository=None,
            news_repository=None,
            inbox_repository=None,
        )
        self.capability_router.vocabulary_repository = self.vocabulary_repository
        self.capability_router.shell_execution.workspace_root = Path(
            tempfile.mkdtemp(prefix="siyi-c1-vocab-ws-")
        )


def test_catalog_contains_vocab_tools():
    ids = {t["action_id"] for t in REACT_TOOL_CATALOG}
    assert "vocab.quiz_in_chat" in ids
    assert "vocab.review_status" in ids
    assert "vocab.quiz_in_chat" in SAFE_TOOL_IDS_BASE
    assert "vocab.review_status" in SAFE_TOOL_IDS_BASE


def test_quiz_in_chat_picks_due_word():
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    ctx = _FakeContext([
        {"id": 1, "word": "abandon", "meaning": "放弃", "next_review": past, "wordbook_id": 1},
        {"id": 2, "word": "future", "meaning": "未来", "next_review": future, "wordbook_id": 1},
    ])
    exec_ = make_react_executor(ctx)
    out = exec_("vocab.quiz_in_chat", {"prefer_due": True})
    assert "result" in out
    assert out["result"]["word_id"] == 1
    assert out["result"]["due"] is True


def test_quiz_in_chat_handles_empty_lib():
    ctx = _FakeContext([])
    exec_ = make_react_executor(ctx)
    out = exec_("vocab.quiz_in_chat", {})
    assert out["result"]["empty"] is True


def test_quiz_in_chat_falls_back_to_any_word_when_none_due():
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    ctx = _FakeContext([
        {"id": 5, "word": "future", "meaning": "未来", "next_review": future, "wordbook_id": 1},
    ])
    exec_ = make_react_executor(ctx)
    out = exec_("vocab.quiz_in_chat", {"prefer_due": True})
    assert out["result"]["word_id"] == 5
    assert out["result"]["due"] is False


def test_review_status_counts_correctly():
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    ctx = _FakeContext([
        {"id": 1, "word": "a", "next_review": past, "last_review": today + "T10:00:00"},
        {"id": 2, "word": "b", "next_review": past},
        {"id": 3, "word": "c", "next_review": future},
    ])
    exec_ = make_react_executor(ctx)
    out = exec_("vocab.review_status", {})
    assert out["result"]["total"] == 3
    assert out["result"]["due"] == 2
    assert out["result"]["reviewed_today"] == 1


def test_quiz_filters_by_wordbook():
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    ctx = _FakeContext([
        {"id": 1, "word": "a", "next_review": past, "wordbook_id": 1},
        {"id": 2, "word": "b", "next_review": past, "wordbook_id": 2},
    ])
    exec_ = make_react_executor(ctx)
    out = exec_("vocab.quiz_in_chat", {"wordbook_id": 2})
    assert out["result"]["word_id"] == 2
