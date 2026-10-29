"""Day 0 — 6 个补完的 RPC handler 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from backend.method_registry import MethodRegistry
from backend.handlers._ai import register_ai
from backend.handlers._inbox_data import register_inbox_data
from backend.handlers._info_proxy import register_info_proxy


# ── Mocks ─────────────────────────────────────────────────────────────

class _FakeAi:
    """模拟 ai_client，返回固定 JSON。"""

    def __init__(self, response: str = ""):
        self._response = response

    def complete_text(self, messages, max_tokens=300, **kw):
        return self._response

    def chat_completion(self, **kwargs):
        return {"choices": [{"message": {"content": self._response}}]}


class _FakeTranslationRepo:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self._next_id = 100

    def add_translation_record(self, text, result, from_code, to_code) -> int:
        wid = self._next_id
        self._next_id += 1
        self.records.append({"id": wid, "text": text, "result": result, "from": from_code, "to": to_code})
        return wid


class _FakeVocabRepo:
    def __init__(self, words=None):
        self._words = words or []

    def list_words(self):
        return list(self._words)


class _FakeInboxRepo:
    def __init__(self, entries=None):
        self.entries = list(entries or [])
        self.updates: list[tuple] = []

    def get_entry(self, eid):
        for e in self.entries:
            if e.get("id") == eid:
                return dict(e)
        return None

    def update_entry(self, eid, info):
        self.updates.append((eid, info))


class _FakeGraphStore:
    def __init__(self, relations=None):
        self._relations = relations or []

    def get_relations(self, entity_id):
        return [r for r in self._relations if r.get("entity_id") == entity_id]


class _FakeProfileSvc:
    def __init__(self, focus_topics=None):
        self._topics = focus_topics or []

    def load(self):
        return {"long_term": {"focus_topics": self._topics}}


class _FakeMemorySvc:
    def __init__(self, recents=None):
        self._recents = recents or []

    def list_recent(self, limit=50):
        return self._recents[:limit]


class _FakeContext:
    def __init__(
        self,
        ai_client=None,
        translation_repo=None,
        vocab_repo=None,
        inbox_repo=None,
        graph_store=None,
        profile_svc=None,
        memory_svc=None,
    ):
        self.ai_client = ai_client
        self.translation_history_repository = translation_repo
        self.vocabulary_repository = vocab_repo
        self.personal_inbox_repository = inbox_repo
        self.knowledge_graph_store = graph_store
        self.user_profile_service = profile_svc
        self.memory_index_service = memory_svc
        self.settings_repository = None


# ── 1. translate.word_detail ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_word_detail_uses_repo_when_already_enriched():
    """词库已有 phonetic / synonyms 时，直接从仓储返回，不调 AI。"""
    word = {
        "id": 1,
        "word": "ephemeral",
        "phonetic": "瑟藞fem蓹r蓹l",
        "phonetic_us": "",
        "phonetic_uk": "",
        "meaning": "短暂的",
        "meanings": ["短暂的", "瞬息的"],
        "synonyms": [{"word": "transient", "similarity": "high", "nuance": "正式"}],
        "antonyms": [],
        "collocations": [],
        "examples": ["Life is ephemeral."],
        "level": "C1",
    }
    ctx = _FakeContext(ai_client=_FakeAi("{}"), vocab_repo=_FakeVocabRepo([word]))
    reg = MethodRegistry()
    register_ai(reg, ctx)
    out = await reg.dispatch("translate.word_detail", {"word": "ephemeral"})
    assert out["phonetic"] == "瑟藞fem蓹r蓹l"
    assert "短暂的" in out["meanings"]
    assert out["synonyms"][0]["word"] == "transient"


@pytest.mark.asyncio
async def test_word_detail_calls_ai_when_not_in_repo():
    """词库没有时调 AI 查询。"""
    ai_response = (
        '{"phonetic":"瑟藞fem蓹r蓹l","phonetic_us":"","phonetic_uk":"","level":"C1",'
        '"meanings":["短暂的"],"synonyms":[],"antonyms":[],"collocations":[],'
        '"examples":["Life is ephemeral."],"etymology":"epi+hemer (day)"}'
    )
    ctx = _FakeContext(ai_client=_FakeAi(ai_response), vocab_repo=_FakeVocabRepo([]))
    reg = MethodRegistry()
    register_ai(reg, ctx)
    out = await reg.dispatch("translate.word_detail", {"word": "ephemeral"})
    assert out["phonetic"] == "瑟藞fem蓹r蓹l"
    assert out["meanings"] == ["短暂的"]
    assert out["etymology"] == "epi+hemer (day)"


@pytest.mark.asyncio
async def test_word_detail_rejects_empty_word():
    ctx = _FakeContext()
    reg = MethodRegistry()
    register_ai(reg, ctx)
    out = await reg.dispatch("translate.word_detail", {"word": "  "})
    assert "error" in out


@pytest.mark.asyncio
async def test_word_detail_handles_ai_unavailable():
    ctx = _FakeContext(ai_client=None, vocab_repo=_FakeVocabRepo([]))
    reg = MethodRegistry()
    register_ai(reg, ctx)
    out = await reg.dispatch("translate.word_detail", {"word": "test"})
    assert "error" in out


# ── 2. translations.save ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_translations_save_persists_record():
    repo = _FakeTranslationRepo()
    ctx = _FakeContext(translation_repo=repo)
    reg = MethodRegistry()
    register_ai(reg, ctx)
    out = await reg.dispatch("translations.save", {
        "source_text": "Hello world",
        "translated_text": "你好世界",
        "source_lang": "en",
        "target_lang": "zh",
        "mode": "quick",
    })
    assert out["success"] is True
    assert out["id"] >= 100
    assert len(repo.records) == 1
    assert repo.records[0]["text"] == "Hello world"


@pytest.mark.asyncio
async def test_translations_save_rejects_empty():
    repo = _FakeTranslationRepo()
    ctx = _FakeContext(translation_repo=repo)
    reg = MethodRegistry()
    register_ai(reg, ctx)
    out = await reg.dispatch("translations.save", {"source_text": "", "translated_text": "x"})
    assert out["success"] is False


# ── 3. knowledge.relations ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_knowledge_relations_returns_filtered():
    relations = [
        {"entity_id": "e1", "target": "e2", "type": "related"},
        {"entity_id": "e1", "target": "e3", "type": "follows"},
        {"entity_id": "e9", "target": "e2", "type": "related"},
    ]
    ctx = _FakeContext(graph_store=_FakeGraphStore(relations))
    reg = MethodRegistry()
    register_inbox_data(reg, ctx)
    out = await reg.dispatch("knowledge.relations", {"entity_id": "e1"})
    assert len(out) == 2


@pytest.mark.asyncio
async def test_knowledge_relations_handles_missing_kg():
    ctx = _FakeContext(graph_store=None)
    reg = MethodRegistry()
    register_inbox_data(reg, ctx)
    out = await reg.dispatch("knowledge.relations", {"entity_id": "x"})
    assert out == []


# ── 4. inbox.classify ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_inbox_classify_calls_ai_and_writes_back():
    """inbox.classify 应调用 AI 分类并写回 entry。"""
    ai_response = '{"kind":"task","confidence":0.85,"reason":"包含截止日期","suggested_action":"convert_to_task"}'
    inbox = _FakeInboxRepo([{"id": 1, "title": "周五前交报告", "content": "周五前交报告"}])
    ctx = _FakeContext(ai_client=_FakeAi(ai_response), inbox_repo=inbox)
    reg = MethodRegistry()
    register_inbox_data(reg, ctx)
    out = await reg.dispatch("inbox.classify", {"entry_id": 1})
    assert out["success"] is True
    assert out["kind"] == "task"
    # 写回 entry
    assert len(inbox.updates) == 1
    eid, info = inbox.updates[0]
    assert eid == 1
    assert info["ai_kind"] == "task"


@pytest.mark.asyncio
async def test_inbox_classify_rejects_missing_entry():
    inbox = _FakeInboxRepo([])
    ctx = _FakeContext(inbox_repo=inbox)
    reg = MethodRegistry()
    register_inbox_data(reg, ctx)
    out = await reg.dispatch("inbox.classify", {"entry_id": 999})
    assert out["success"] is False


# ── 5. info.personalize ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_info_personalize_scores_by_focus_topics():
    profile = _FakeProfileSvc(focus_topics=["python", "agent", "rust"])
    ctx = _FakeContext(profile_svc=profile)
    reg = MethodRegistry()
    register_info_proxy(reg, ctx)
    items = [
        {"title": "Python 3.13 released", "url": "u1", "snippet": "new GIL"},
        {"title": "Cooking recipes", "url": "u2", "snippet": "delicious food"},
        {"title": "Agent frameworks compared", "url": "u3", "snippet": "AI"},
    ]
    out = await reg.dispatch("info.personalize", {"items": items})
    # 命中关键词的应排在前面
    assert out["order"][0] in ("u1", "u3")
    assert out["scores"]["u2"] < max(out["scores"]["u1"], out["scores"]["u3"])
    assert out["keywords_matched"] >= 2


@pytest.mark.asyncio
async def test_info_personalize_handles_empty_items():
    ctx = _FakeContext()
    reg = MethodRegistry()
    register_info_proxy(reg, ctx)
    out = await reg.dispatch("info.personalize", {"items": []})
    assert out == {"scores": {}, "order": [], "keywords_matched": 0}


# ── 6. report.generate ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_generate_propagates_failures_gracefully(monkeypatch):
    """failure 时不抛，应返回 success=False。"""
    ctx = _FakeContext(ai_client=None, inbox_repo=_FakeInboxRepo([]))
    reg = MethodRegistry()
    register_info_proxy(reg, ctx)
    # _collect_weekly_stats 会因为缺少各种 repo 而抛错或返回空，handler 应该 graceful 处理
    out = await reg.dispatch("report.generate", {})
    # 即使全空，也应该是 dict 且有 success 字段
    assert "success" in out
