"""T5-J5 · L2 旅程 E2E：会话入库知识图谱 → 图谱召回与落库。

旅程（AI 一律 mock，无真实 AI 调用 [pending-live-AI]）：
  真实仓储预置会话消息 → knowledge.ingest_conversation（mock 抽取 AI 的
  chat_completion 返回固定候选 JSON，complete_text 返回固定实体 JSON）
  → 真实 KnowledgeGraphExtractor **LLM 通道**抽取写入真实 KnowledgeGraphStore
  （tmp_path）→ knowledge.search / knowledge.graph / knowledge.unified_query
  召回入库实体 → 断言图谱落库（JSON 持久化 + 重开可读）。

Mock 边界：仅 mock 抽取 LLM 的回复内容；图谱抽取为 **LLM 通道**（KG-CH，
2026-09-21 起为默认且唯一可用于聊天文本的通道），记忆索引/图存储均为
tmp_path 上的真实实现。
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore  # noqa: E402
from knowledge.graph_extractor import KnowledgeGraphExtractor  # noqa: E402
from knowledge.graph_store import KnowledgeGraphStore  # noqa: E402
from memory.extractor import MemoryExtractor  # noqa: E402
from memory.index import AssistantMemoryIndexService  # noqa: E402
from repositories._chat_session import LocalChatSessionRepository  # noqa: E402

# 实体边界用「」隔离；LLM 通道下实体由 mock 回复直接给定，
# 不再依赖正则切词（旧注释描述的 find_by_name 折叠仅对正则通道成立）。
CANDIDATE_CONTENT = "张三说：「思忆集重构项目启动会开得很顺利」"
CANDIDATES = [
    {
        "type": "project",
        "content": CANDIDATE_CONTENT,
        "confidence": 0.9,
        "source": "user",
        "tags": ["J5"],
        "reason": "J5 journey e2e 固定候选",
    }
]
# register_all 内联注册前会先调用各域模块的 register_*；旅程测试只关心
# knowledge 域，域模块注册一律 no-op，避免无关 eager 副作用（cron/工具/同步等）。
_NOOP_MODULES = [
    "_agent_tasks",
    "_ai",
    "_ai_chat",
    "_api_keys",
    "_audit",
    "_capabilities",
    "_chat_memory",
    "_confirmations",
    "_cron",
    "_dictation",
    "_evolution",
    "_external_content",
    "_feedback",
    "_inbox_data",
    "_info_proxy",
    "_info_items",
    "_info_sources",
    "_job_hunter",
    "_kernel",
    "_maintenance",
    "_narrative",
    "_notes",
    "_notifications",
    "_ocr",
    "_personal",
    "_process",
    "_provider_health",
    "_reminder",
    "_reading",
    "_remote",
    "_rss",
    "_sandbox",
    "_skin",
    "_sync",
    "_system_settings",
    "_tasks",
    "_task_queue",
    "_timeline",
    "_tools",
    "_trace",
    "_trae",
    "_transfer",
    "_transcription",
    "_url_learning",
    "_weather",
    "_word_extract",
    "_wordbook",
]


class _FakeSettingsRepo:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get_setting(self, key, default=None):
        return self._data.get(key, default)

    def set_setting(self, key, value):
        self._data[key] = value


class _FakeContext:
    """最小 DI 上下文：未知属性一律 None（模拟未注入的服务槽位）。"""

    def __init__(self):
        self.settings_repository = _FakeSettingsRepo()
        self.local_data_store = None
        self.chat_session_repository = None
        self.memory_index_service = None
        self.memory_extractor = None
        self.knowledge_graph_store = None
        self.vocabulary_repository = None
        self.info_item_repository = None
        self.personal_note_repository = None
        self.personal_task_repository = None
        self.personal_inbox_repository = None
        self.memory_linker = None

    def __getattr__(self, name):
        return None


class _MockExtractAI:
    """mock 抽取 AI：chat_completion 返回候选 JSON（OpenAI 风格 dict），
    complete_text 返回实体/关系 JSON 字符串（知识图谱 LLM 通道契约）。

    与生产同形：MemoryContext 把同一个 ai_client 同时交给 MemoryExtractor 与
    KnowledgeGraphExtractor（memory/context.py:158-161 + memory_manager.py:129-135）。
    """

    def __init__(self, candidates):
        self._payload = json.dumps(candidates, ensure_ascii=False)
        self._kg_payload = json.dumps(
            {
                "entities": [
                    {"name": "张三", "type": "person", "aliases": []},
                    {"name": "思忆集重构项目", "type": "project", "aliases": []},
                ],
                "relations": [{"source": "张三", "target": "思忆集重构项目", "relation": "part_of"}],
            },
            ensure_ascii=False,
        )
        self.calls: list[dict] = []
        self.graph_calls: list[dict] = []

    def chat_completion(self, messages, temperature=0.1, max_tokens=800, **kwargs):
        self.calls.append({"messages": messages, "temperature": temperature})
        return {"choices": [{"message": {"content": self._payload}}]}

    def complete_text(self, messages, temperature=0.1, max_tokens=1000, **kwargs):
        self.graph_calls.append({"messages": messages, "temperature": temperature})
        return self._kg_payload


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _noop_domain_registers(monkeypatch) -> None:
    """把 register_all 调用的域模块 register_* 全部替换为 no-op。

    被 await 的 async register 以 async no-op 替换，其余以同步 no-op 替换。
    knowledge 域内联 handler（ingest/unified_query/organize）与
    register_inbox_knowledge（search/graph/relations）不受影响，仍为真实实现。
    """
    import backend.handlers as handlers_pkg  # noqa: F401

    for name in _NOOP_MODULES:
        try:
            mod = importlib.import_module("backend.handlers." + name)
        except Exception:  # noqa: BLE001
            continue
        for attr in list(vars(mod)):
            if not attr.startswith("register"):
                continue
            orig = getattr(mod, attr)
            if inspect.iscoroutinefunction(orig):

                async def _async_noop(*args, **kwargs):
                    return None

                monkeypatch.setattr(mod, attr, _async_noop, raising=False)
            else:
                monkeypatch.setattr(mod, attr, lambda *args, **kwargs: None, raising=False)


def _build(tmp_path, monkeypatch):
    """tmp_path 真实仓储/图存储 + mock 抽取 AI + knowledge 域方法注册。"""
    _noop_domain_registers(monkeypatch)

    store = LocalDataStore(base_dir=str(tmp_path))
    kg_path = tmp_path / "knowledge_graph.json"
    kg_store = KnowledgeGraphStore(path=str(kg_path))
    chat_repo = LocalChatSessionRepository(store)
    mem_index = AssistantMemoryIndexService(data_store=store)
    ai = _MockExtractAI(CANDIDATES)
    extractor = MemoryExtractor(
        ai_client=ai,
        data_store=store,
        memory_index_service=mem_index,
        knowledge_graph_extractor=KnowledgeGraphExtractor(kg_store, ai_client=ai),
    )

    ctx = _FakeContext()
    ctx.local_data_store = SimpleNamespace(base_dir=str(tmp_path))
    ctx.chat_session_repository = chat_repo
    ctx.memory_index_service = mem_index
    ctx.memory_extractor = extractor
    ctx.knowledge_graph_store = kg_store
    ctx.ai_client = ai

    from backend.handlers import register_all
    from backend.handlers._inbox_knowledge import register_inbox_knowledge
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_inbox_knowledge(registry, ctx)  # knowledge.search/graph/relations
    _run(register_all(registry, ctx))  # knowledge.ingest_conversation/unified_query
    return ctx, registry, ai, kg_path


def _seed_session(chat_repo) -> int:
    sid = chat_repo.create_session("J5旅程会话")
    chat_repo.add_message(sid, "user", CANDIDATE_CONTENT)
    chat_repo.add_message(sid, "助手", "好的，已记录到知识图谱。")
    return sid


def _ingest(registry, sid):
    return _run(registry.dispatch("knowledge.ingest_conversation", {"session_id": sid}))


def test_j5_ingest_conversation_writes_graph(tmp_path, monkeypatch):
    """knowledge.ingest_conversation：mock 抽取 → 候选自动写入 + 图谱 LLM 通道抽取。"""
    ctx, registry, ai, _kg_path = _build(tmp_path, monkeypatch)
    sid = _seed_session(ctx.chat_session_repository)

    resp = _ingest(registry, sid)
    assert "error" not in resp, f"ingest 不应报错: {resp}"
    assert resp["message_count"] == 2, f"应归一化 2 条消息: {resp}"
    assert resp["extracted"] >= 1, f"mock AI 候选应被解析: {resp}"
    assert resp["auto_write"] >= 1, f"confidence 0.9 >= 0.8 应自动写入: {resp}"
    assert resp["stored"] >= 1, f"候选应写入记忆索引: {resp}"
    assert ai.calls, "mock 抽取 AI chat_completion 应被调用"
    # KG-CH（2026-09-21）：回灌必须走 LLM 通道（complete_text），不得回到正则
    assert ai.graph_calls, "图谱回灌应调用 complete_text（LLM 通道），实为正则静默降级"


def test_j5_knowledge_search_finds_entity(tmp_path, monkeypatch):
    """knowledge.search 应召回入库实体（人名 张三）。"""
    ctx, registry, _ai, _kg_path = _build(tmp_path, monkeypatch)
    sid = _seed_session(ctx.chat_session_repository)
    resp = _ingest(registry, sid)
    assert "error" not in resp, f"前置 ingest 失败: {resp}"

    hits = _run(registry.dispatch("knowledge.search", {"query": "张三"}))
    assert hits, "knowledge.search 应回召到入库实体 张三"
    assert any("张三" in str(h.get("name", "")) for h in hits), f"命中应含 张三: {hits!r}"


def test_j5_knowledge_graph_contains_entities(tmp_path, monkeypatch):
    """knowledge.graph 全图应包含 LLM 通道抽取的实体（人名 + 项目名，含类型断言）。"""
    ctx, registry, _ai, _kg_path = _build(tmp_path, monkeypatch)
    sid = _seed_session(ctx.chat_session_repository)
    resp = _ingest(registry, sid)
    assert "error" not in resp, f"前置 ingest 失败: {resp}"

    graph = _run(registry.dispatch("knowledge.graph", {}))
    entities = graph.get("entities", [])
    for name, etype in (("张三", "person"), ("思忆集重构项目", "project")):
        hit = [e for e in entities if name in str(e.get("name", ""))]
        assert hit, f"图谱应含实体 {name}，实际: {[e.get('name') for e in entities]}"
        assert any(e.get("type") == etype for e in hit), f"实体 {name} 类型应为 {etype}: {hit!r}"


def test_j5_unified_query_surfaces_entity(tmp_path, monkeypatch):
    """knowledge.unified_query 应经知识图谱源召回入库实体。"""
    ctx, registry, _ai, _kg_path = _build(tmp_path, monkeypatch)
    sid = _seed_session(ctx.chat_session_repository)
    resp = _ingest(registry, sid)
    assert "error" not in resp, f"前置 ingest 失败: {resp}"

    unified = _run(registry.dispatch("knowledge.unified_query", {"text": "张三"}))
    kg_items = unified.get("sources", {}).get("knowledge", [])
    fused = unified.get("results", [])
    blob = json.dumps(kg_items + fused, ensure_ascii=False)
    assert "张三" in blob, f"unified_query 应回召知识图谱实体 张三: sources={kg_items!r} results={fused!r}"


def test_j5_graph_persists_and_reloads(tmp_path, monkeypatch):
    """图谱 JSON 应落盘（tmp_path）：重开 store 可召回同一实体（落库断言）。"""
    ctx, registry, _ai, kg_path = _build(tmp_path, monkeypatch)
    sid = _seed_session(ctx.chat_session_repository)
    resp = _ingest(registry, sid)
    assert "error" not in resp, f"前置 ingest 失败: {resp}"

    assert kg_path.exists(), f"图谱 JSON 应已写入磁盘: {kg_path}"
    reloaded = KnowledgeGraphStore(path=str(kg_path))
    hits = reloaded.find_by_name("张三")
    assert hits, "重开的 KnowledgeGraphStore 应能召回落盘实体 张三"
