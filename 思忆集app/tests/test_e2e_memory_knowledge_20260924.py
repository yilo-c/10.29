# -*- coding: utf-8 -*-
"""E2E 记忆与知识库模块（2026-09-24 战役）—— 真调 WS 面 + 离线进程内闭环 + 波次③取证.

只测不改源码。矩阵覆盖：

- 矩阵② 真实 WS RPC 面（conftest AUTOSTART 隔离临时后端实调）：
  * memory.* 注册方法冒烟（memory.list / recent / search / related /
    related_text / surface_for_input / save_conversation_anchor / delete /
    restore + tags.list + memory.narrative.types）—— 按注册表实际，
    **不存在** memory.extract RPC，抽取是事件驱动（memory.auto_extract）
    + knowledge.ingest_conversation（见矩阵④）。
  * 知识图谱查询面：knowledge.graph / knowledge.search / knowledge.relations
    / knowledge.traverse 可用（返回真结构，非 -32601 Method not found）。
  * 向量搜索：真模型（sentence-transformers / bge-small-zh）首次下载不可行，
    以既有测试（knowledge/retrieval/tests/test_rag_integration.py）的
    **离线 embedding** 口径为准 —— 见 E2E-V，用 EmbeddingService(offline_mode)
    + ChromaVectorStore + RAGPipeline 走真实向量检索闭环，不联网、不伪造绿。

- 矩阵③ 结构化存储链路（离线进程内）：
  * StructuredMemoryStore 写入→检索→落盘重载闭环。
  * 波次③「存储收口」取证 C-2（``memory/structured_store.py`` 启动快照
    整表覆盖写竞态 / 三键未建表 local_storage kv 载体）：
    - 复现证据（默认绿）：双 store 实例丢更新、外部裸写被 store 快照覆写吞掉。
    - 理想合并语义（xfail strict=False）：两写并存 —— 修复后自动转正。
    本波**只登记不修**。

- 矩阵④ 协作链（chat 记忆抽取 → memory 索引 → search 可召回）：
  * 离线：memory.save_conversation_anchor → memory.auto_extract 生产订阅者
    → memory/index.py index_memory → memory.list / search / surface_for_input
    / related 召回（引用 test_journey_chat_memory 的沉淀链口径，独立实调）。
  * 真机 WS：同链在 AUTOSTART 隔离后端实调 save_conversation_anchor →
    surface_for_input 可召回。
  * LLM 抽取 → 图谱 回灌（mock AI）已由 test_journey_knowledge 覆盖，本文件
    以矩阵② 实调其注册面（knowledge.search/graph/unified_query）作补充。

已知坑（本文件实测口径）：
  - conftest AUTOSTART：SIYI_INTEGRATION_AUTOSTART=1 自启随机端口临时后端，
    运行时从 os.environ["SIYI_WS_URL"] / ["SIYI_DIAG_TOKEN"] 取值与 ws_session
    同连一后端；SIYI_INTEGRATION_REQUIRED=1 让后端起不来时 fail-not-skip。
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore  # noqa: E402
from memory.structured_store import StructuredMemoryStore  # noqa: E402

_STORE_KEY = "structured_memory_entries"
METHOD_NOT_FOUND = -32601


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _registered(resp: dict[str, Any]) -> bool:
    """WS 回执是否「方法已注册」：只要不是 -32601 Method not found 即视为已注册
    （域级 error / 空结果不算未注册；本冒烟只验注册面 + 基本形状）。"""
    err = resp.get("error")
    if isinstance(err, dict) and err.get("code") == METHOD_NOT_FOUND:
        return False
    return True


# ══════════════════════════════════════════════════════════════════════
# 矩阵② — 真实 WS RPC 面冒烟（AUTOSTART 隔离临时后端实调）
# ══════════════════════════════════════════════════════════════════════

# 注册表实际（backend/handlers/_chat_memory.py + _narrative.py + _inbox_knowledge.py）
MEMORY_SURFACE = (
    "memory.list",
    "memory.recent",
    "memory.search",
    "memory.related",
    "memory.related_text",
    "memory.surface_for_input",
    "memory.save_conversation_anchor",
    "tags.list",
    "memory.narrative.types",
)


@pytest.mark.integration
@pytest.mark.parametrize("method,params", [
    ("memory.list", {"limit": 5, "offset": 0}),
    ("memory.recent", {"limit": 5}),
    ("memory.search", {"query": "思忆集", "limit": 5}),
    ("memory.related", {"text": "思忆集", "limit": 5}),
    ("memory.related_text", {"text": "思忆集", "limit": 5}),
    ("memory.surface_for_input", {"text": "思忆集"}),
    ("tags.list", {}),
    ("memory.narrative.types", {}),
])
async def test_e2e_ws_memory_surface_registered(rpc, method: str, params: dict[str, Any]) -> None:
    """memory.* 注册方法逐个真调：不得 -32601（未注册）。"""
    resp = await rpc(method, params)
    assert _registered(resp), f"{method} 未在 WS 面注册：{resp.get('error')}"


@pytest.mark.integration
async def test_e2e_ws_memory_search_and_recent_shape(rpc) -> None:
    """memory.search / memory.recent 返回真列表；limit 生效上界。"""
    r = await rpc("memory.search", {"query": "编程", "limit": 3})
    assert _registered(r), r.get("error")
    assert isinstance(r.get("result"), list), f"memory.search 应返回 list: {r!r}"
    assert len(r["result"]) <= 3, f"limit=3 上界应生效: {r['result']!r}"

    rr = await rpc("memory.recent", {"limit": 2})
    assert _registered(rr), rr.get("error")
    assert isinstance(rr.get("result"), list)
    assert len(rr["result"]) <= 2


@pytest.mark.integration
async def test_e2e_ws_memory_list_paged_shape(rpc) -> None:
    """memory.list 面板契约：{success, entries/total, ...} 真分页信封。"""
    r = await rpc("memory.list", {"limit": 5, "offset": 0})
    assert _registered(r), r.get("error")
    result = r.get("result")
    assert isinstance(result, dict) and result.get("success") is True, f"memory.list 信封异常: {result!r}"


@pytest.mark.integration
async def test_e2e_ws_memory_extract_not_a_registered_rpc(rpc) -> None:
    """诚实边界：注册表**没有** memory.extract RPC —— 记忆抽取是事件驱动
    （memory.auto_extract 生产订阅者）+ knowledge.ingest_conversation。
    本用例锁定：memory.extract 真调应 -32601（若将来有人补挂则本断言转红提醒对齐文档）。"""
    resp = await rpc("memory.extract", {"text": "测试"})
    assert not _registered(resp), (
        f"memory.extract 意外已注册（{resp!r}）：矩阵口径（无该 RPC）需与注册表同步更新"
    )


# 知识图谱查询面注册表实际
GRAPH_SURFACE: tuple[tuple[str, dict[str, str]], ...] = (
    ("knowledge.graph", {}),
    ("knowledge.search", {"query": "思忆集"}),
    ("knowledge.relations", {"entity_id": ""}),
    ("knowledge.traverse", {"start_id": ""}),
)


@pytest.mark.integration
@pytest.mark.parametrize("method,params", GRAPH_SURFACE)
async def test_e2e_ws_knowledge_graph_query_registered(rpc, method: str, params: dict[str, Any]) -> None:
    """知识图谱查询方法在 WS 面可用（非 -32601）。"""
    resp = await rpc(method, params)
    assert _registered(resp), f"{method} 未在 WS 面注册：{resp.get('error')}"


@pytest.mark.integration
async def test_e2e_ws_knowledge_graph_full_shape(rpc) -> None:
    """knowledge.graph 返回 {entities, relations, total_entities, truncated} 真结构。"""
    resp = await rpc("knowledge.graph", {"max_entities": 50})
    assert _registered(resp), resp.get("error")
    result = resp.get("result")
    assert isinstance(result, dict), f"knowledge.graph 应返回 dict: {result!r}"
    for key in ("entities", "relations", "total_entities", "truncated"):
        assert key in result, f"knowledge.graph 缺字段 {key}: {result!r}"
    assert isinstance(result["entities"], list) and isinstance(result["relations"], list)


@pytest.mark.integration
@pytest.mark.xfail(
    strict=False,
    reason=(
        "波次③取证 · 知识图谱查询面缺口：backend/handlers/_inbox_knowledge.py "
        "定义了 knowledge_graph_for_viz handler 并登记 S3-3 非退化契约，但"
        "register_inbox_knowledge 从未 registry.register('knowledge.graph_for_viz', ...)"
        "（对照既有红测 tests/test_graph_viz_contract_s33_20260916.py 同因 KeyError）。"
        "真机 WS 面 → -32601 Method not found。本波只登记不修，登记行补上后本用例转正。"
    ),
)
async def test_e2e_ws_knowledge_graph_for_viz_should_be_available(rpc) -> None:
    """理想：图谱可视化查询面（Cytoscape 节点/边）在 WS 可用。当前未注册（缺口）。"""
    resp = await rpc("knowledge.graph_for_viz", {"query": "思忆集"})
    assert _registered(resp), f"knowledge.graph_for_viz 未注册：{resp.get('error')}"


# ══════════════════════════════════════════════════════════════════════
# 矩阵② — 向量搜索离线 embedding 闭环（不下载真模型）
# ══════════════════════════════════════════════════════════════════════

def test_e2e_vector_search_offline_closure(tmp_path: Path) -> None:
    """真实向量检索闭环（ChromaDB + 离线确定性 embedding）：写入 → 相似召回。

    诚实边界：sentence-transformers 真模型（bge-small-zh）首次下载在隔离测试
    环境不可行 → 采用既有测试 test_rag_integration.py 的 EmbeddingService
    (offline_mode=True) 口径，向量索引/检索链路为真实实现，仅 embedding 值为
    确定性伪随机（L2 归一），足以证明「写入即可被向量检索召回」的链路完整性。
    """
    from knowledge.retrieval.document_parser import DocumentParser
    from knowledge.retrieval.embedding_service import EmbeddingService
    from knowledge.retrieval.rag_pipeline import RAGPipeline
    from knowledge.retrieval.vector_store import ChromaVectorStore

    persist_dir = tmp_path / "chroma_db"
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir(parents=True, exist_ok=True)

    embedding = EmbeddingService(offline_mode=True)
    assert embedding.embedding_dim == 512, f"离线 embedding 维度应为 512: {embedding.embedding_dim}"
    store = ChromaVectorStore(persist_directory=persist_dir, collection_name="e2e_mem_kb")
    pipeline = RAGPipeline(
        vector_store=store,
        document_parser=DocumentParser(),
        embedding_service=embedding,
        knowledge_base_dir=kb_dir,
    )

    marker = f"量子纠缠态 ZEBRA-{uuid.uuid4().hex[:8]}"

    async def journey() -> dict[str, Any]:
        await pipeline.add_document(
            content=f"{marker} 是本次写入知识库的唯一指纹段落，用于验证向量检索闭环。",
            metadata={"source": "e2e_mem_kb", "file_name": "zebra.md"},
        )
        return await pipeline.query_knowledge_base(marker, n_results=5)

    results = _run(journey())
    docs = results.get("documents") or []
    assert docs, f"向量检索应召回刚写入的段落（离线 embedding 链路）: {results!r}"
    assert any(marker in str(d) for d in docs), f"召回应命中唯一指纹: {docs!r}"
    store.close()


# ══════════════════════════════════════════════════════════════════════
# 矩阵③ — StructuredMemoryStore 写入→检索→重载闭环（离线进程内）
# ══════════════════════════════════════════════════════════════════════

def test_e2e_structured_store_write_retrieve_closure(tmp_path: Path) -> None:
    ds = LocalDataStore(base_dir=str(tmp_path))
    store = StructuredMemoryStore(ds)

    e_task = store.create_entry(
        supertag_type="@任务",
        entity_type="task",
        field_values={"title": "写周报", "priority": "high"},
        tags=["work", "e2e"],
    )
    e_person = store.create_entry(
        supertag_type="@人物",
        entity_type="person",
        field_values={"name": "张三"},
        relations=[{"type": "part_of", "target_id": e_task["id"], "label": "参与"}],
        tags=["e2e"],
    )
    assert e_task["id"] and e_person["id"] and e_task["id"] != e_person["id"]

    # 读回单条
    got = store.get_entry(e_task["id"])
    assert got and got["field_values"]["title"] == "写周报"

    # 计数 / 类型过滤
    assert store.count_entries(supertag_type="@任务") == 1
    assert store.count_entries(entity_type="person") == 1

    # 字段检索
    assert any(x["id"] == e_person["id"] for x in store.search_by_field("name", "张三"))
    # 标签检索（match_any）
    tag_hits = store.search_by_tags(["work"])
    assert any(x["id"] == e_task["id"] for x in tag_hits)
    # 关系检索（结构化 link）
    rel_hits = store.search_by_relation(target_id=e_task["id"])
    assert any(x["id"] == e_person["id"] for x in rel_hits), f"应经关系召回 e_person: {rel_hits!r}"

    # 更新闭环
    upd = store.update_entry(e_task["id"], field_values={"status": "done"})
    assert upd and upd["field_values"]["status"] == "done"
    assert upd["field_values"]["title"] == "写周报", "update 应合并字段而非整体覆盖"

    # 落盘重载闭环（检索链跨实例一致）
    reloaded = StructuredMemoryStore(LocalDataStore(base_dir=str(tmp_path)))
    ids = {e["id"] for e in reloaded.list_entries(limit=100)}
    assert ids == {e_task["id"], e_person["id"]}, f"重载应见两实体: {ids!r}"
    got_task = reloaded.get_entry(e_task["id"])
    assert got_task is not None
    assert got_task["field_values"]["status"] == "done"

    # 删除闭环
    assert reloaded.delete_entry(e_person["id"]) is True
    assert reloaded.get_entry(e_person["id"]) is None


def test_e2e_structured_store_cleanup_relations_for_target(tmp_path: Path) -> None:
    """P1.5 悬空引用清理：目标删除后指向它的 relation 被摘除（检索链闭环）。"""
    ds = LocalDataStore(base_dir=str(tmp_path))
    store = StructuredMemoryStore(ds)
    e_task = store.create_entry(supertag_type="@任务", entity_type="task", field_values={"title": "T"})
    store.create_entry(
        supertag_type="@人物",
        entity_type="person",
        field_values={"name": "李四"},
        relations=[{"type": "part_of", "target_id": e_task["id"], "label": "参与"}],
    )
    assert store.search_by_relation(target_id=e_task["id"]), "前置：应先有 1 条指向 e_task 的关系"
    removed = store.cleanup_relations_for_target(e_task["id"])
    assert removed == 1, f"应清理 1 条 relation，实际 {removed}"
    assert store.search_by_relation(target_id=e_task["id"]) == []


# ── 波次③ 取证 C-2：结构化记忆整表覆盖写竞态 / 三键未建表（只登记不修）──


def test_e2e_structured_store_kv_carrier_not_table(tmp_path: Path) -> None:
    """M-4 三键未建表复现（证据，默认绿）：结构化记忆落 local_storage kv，
    非独立 SQLite 表/repository。整表值即结构化条目全量。"""
    ds = LocalDataStore(base_dir=str(tmp_path))
    store = StructuredMemoryStore(ds)
    store.create_entry(supertag_type="@任务", field_values={"title": "kv 载体取证"})

    conn = ds._sqlite._connect()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "structured_memory_entries" not in tables, (
        f"结构化记忆已升为专表（{tables}）→ 波次③ M-4 已修，请摘除本取证并更新登记"
    )
    # 键值以整表 JSON blob 存于通用 kv（_LIST_KEYS 注册），非专表行
    blob = ds._sqlite.get(_STORE_KEY, None)
    assert blob is not None, "structured_memory_entries 应作为 kv 键存在"
    parsed = json.loads(blob)
    assert isinstance(parsed, list) and len(parsed) == 1


def test_e2e_c2_two_store_instances_lost_update(tmp_path: Path) -> None:
    """C-2 复现（证据，默认绿）：两个 StructuredMemoryStore 实例（模拟 cron /
    knowledge_synthesis / handler 各自持 store）各自 __init__ 载入启动快照，
    后写者以 self._entries 整表覆盖写吞掉先写者 → 丢更新。"""
    ds = LocalDataStore(base_dir=str(tmp_path))
    store_x = StructuredMemoryStore(ds)  # 启动快照 []
    store_y = StructuredMemoryStore(ds)  # 启动快照 []（未见 X 后续写）

    e_x = store_x.create_entry(supertag_type="@任务", field_values={"owner": "X"})
    e_y = store_y.create_entry(supertag_type="@任务", field_values={"owner": "Y"})

    persisted = ds.load_json(_STORE_KEY, [])
    ids = {e["id"] for e in persisted}
    # 当前（缺陷）行为：仅最后写者快照存活 → X 被吞
    assert e_y["id"] in ids
    assert e_x["id"] not in ids, (
        f"C-2 症状已消失（X 未被吞）→ 整表覆盖竞态疑已修复，"
        f"应摘除本取证并转正 test_e2e_c2_ideal_* ：persisted={persisted!r}"
    )


def test_e2e_c2_external_bare_write_clobbered(tmp_path: Path) -> None:
    """C-2 复现（证据，默认绿）：store 持启动快照期间，外部经裸 load_json/save_json
    直写同键（对照 services/knowledge_synthesis.py、cron_actions.py、
    _inbox_knowledge.py:197 的裸写路径）；随后 store 任一 CRUD → _save() 用旧
    快照整表覆盖，外部写被静默吞掉。"""
    ds = LocalDataStore(base_dir=str(tmp_path))
    store = StructuredMemoryStore(ds)
    store.create_entry(supertag_type="@任务", field_values={"owner": "store"})

    # 外部裸写方追加一条（读-改-写：先见 store 的条目，再并入 EXT）
    current = ds.load_json(_STORE_KEY, [])
    ext = {"id": "EXT_bare_writer", "supertag_type": "@资讯", "entity_type": "info",
           "source": "system", "created_at": "2026-09-24T00:00:00+00:00",
           "updated_at": "2026-09-24T00:00:00+00:00", "relations": [], "tags": [], "field_values": {}}
    ds.save_json(_STORE_KEY, list(current) + [ext])
    assert any(e["id"] == "EXT_bare_writer" for e in ds.load_json(_STORE_KEY, [])), "前置：外部写应落盘"

    # store 后续一次 CRUD → 用其内存快照（无 EXT）整表覆盖
    store.create_entry(supertag_type="@任务", field_values={"owner": "store2"})

    persisted_ids = {e["id"] for e in ds.load_json(_STORE_KEY, [])}
    assert "EXT_bare_writer" not in persisted_ids, (
        f"C-2 症状已消失（外部裸写未被覆写）→ 收口疑已修复，请摘除本取证："
        f"persisted={persisted_ids!r}"
    )


@pytest.mark.xfail(
    strict=False,
    reason=(
        "波次③「存储收口」取证 C-2 · 已知缺口（登记不修）：StructuredMemoryStore "
        "__init__ 一次性 _load 快照，_save 以 self._entries 整表覆盖写 structured_memory_entries"
        " 键（memory/structured_store.py:57,378-383），该键又被 cron/知识归纳/handler 裸 load_json/"
        "save_json 直写。理想语义=写前 reload-merge 收口（或升 SQLite 表），使多写方并存。"
        "本用例断言理想行为 → 现状失败=红（=缺口）；修复后自动转正。"
    ),
)
def test_e2e_c2_ideal_both_writes_survive(tmp_path: Path) -> None:
    """理想（当前缺口 → xfail）：双 store 实例并存写 + 外部裸写均不应被整表覆盖吞掉。"""
    ds = LocalDataStore(base_dir=str(tmp_path))
    store_x = StructuredMemoryStore(ds)
    store_y = StructuredMemoryStore(ds)
    e_x = store_x.create_entry(supertag_type="@任务", field_values={"owner": "X"})
    ext = {"id": "EXT", "supertag_type": "@资讯", "entity_type": "info", "source": "system",
           "created_at": "2026-09-24T00:00:00+00:00", "updated_at": "2026-09-24T00:00:00+00:00",
           "relations": [], "tags": [], "field_values": {}}
    ds.save_json(_STORE_KEY, list(ds.load_json(_STORE_KEY, [])) + [ext])
    e_y = store_y.create_entry(supertag_type="@任务", field_values={"owner": "Y"})

    ids = {e["id"] for e in ds.load_json(_STORE_KEY, [])}
    assert {e_x["id"], e_y["id"], "EXT"} <= ids, f"三写方应并存（理想合并语义）：ids={ids!r}"


# ══════════════════════════════════════════════════════════════════════
# 矩阵④ — 协作链：chat 记忆抽取 → memory 索引 → search 可召回
# ══════════════════════════════════════════════════════════════════════

class _MemCtx:
    """最小 DI 上下文（引用 test_journey_chat_memory 口径）：未知槽位一律 None。"""

    def __init__(self, memory_index):
        self.memory_index_service = memory_index
        self.chat_session_repository = None
        self.settings_repository = None
        self.memory_linker = None
        self.personal_note_repository = None
        self.personal_task_repository = None

    def __getattr__(self, name):  # noqa: ANN401
        return None


def test_e2e_memory_chain_anchor_index_recall_offline(tmp_path: Path) -> None:
    """离线沉淀链实调：save_conversation_anchor → memory.auto_extract 生产订阅者
    → memory/index.py index_memory → memory.list / search / surface_for_input /
    related 可召回（引用 journey 测试，独立于其断言面额外验 memory.search）。"""
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry
    from memory.index import AssistantMemoryIndexService

    store = LocalDataStore(base_dir=str(tmp_path))
    memory_index = AssistantMemoryIndexService(data_store=store)
    ctx = _MemCtx(memory_index)
    registry = MethodRegistry()
    register_chat_memory(registry, ctx)

    marker = f"ZEBRA-{uuid.uuid4().hex[:8]}"
    anchor = f"思忆集记忆链路 {marker}：用户偏好用 Python 3.11 与 pytest 跑测试"

    ok = _run(registry.dispatch(
        "memory.save_conversation_anchor",
        {"session_id": "9100", "messages": [{"role": "user", "content": anchor}]},
    ))
    assert ok is True, "锚点保存应返回 True（emit auto_extract）"

    listed = _run(registry.dispatch("memory.list", {"limit": 100}))
    listed_rows = listed.get("items") or listed.get("entries") or []
    assert any(marker in json.dumps(item, ensure_ascii=False) for item in listed_rows), (
        f"沉淀链断点：memory.list 未召回锚点（auto_extract 订阅者未写入索引？）: {listed!r}"
    )

    search = _run(registry.dispatch("memory.search", {"query": marker, "limit": 5}))
    assert any(marker in json.dumps(item, ensure_ascii=False) for item in (search or [])), (
        f"memory.search 应召回锚点: {search!r}"
    )

    surfaced = _run(registry.dispatch("memory.surface_for_input", {"text": marker}))
    assert any(marker in json.dumps(item, ensure_ascii=False) for item in (surfaced or [])), (
        f"memory.surface_for_input 应召回锚点: {surfaced!r}"
    )

    related = _run(registry.dispatch("memory.related", {"text": marker}))
    assert related and any(marker in json.dumps(item, ensure_ascii=False) for item in related), (
        f"memory.related 应召回锚点: {related!r}"
    )


@pytest.mark.integration
async def test_e2e_memory_chain_anchor_recall_live_ws(rpc) -> None:
    """真机 WS 面沉淀链：save_conversation_anchor → surface_for_input 可召回
    （AUTOSTART 隔离后端，生产 register_chat_memory 挂载的订阅者生效）。"""
    marker = f"ZEBRA-WS-{uuid.uuid4().hex[:8]}"
    anchor = f"思忆集WS记忆链路 {marker}：临时后端上锚点应经 auto_extract 落到 assistant_memory_index"

    saved = await rpc("memory.save_conversation_anchor", {
        "session_id": "e2e-mem-live",
        "messages": [{"role": "user", "content": anchor}],
    })
    assert _registered(saved), saved.get("error")
    assert saved.get("result") is True, f"锚点保存回执应为 True: {saved!r}"

    surfaced = await rpc("memory.surface_for_input", {"text": marker})
    assert _registered(surfaced), surfaced.get("error")
    blob = json.dumps(surfaced.get("result"), ensure_ascii=False)
    assert marker in blob, f"真机 WS 沉淀链断点：surface_for_input 未召回锚点: {surfaced!r}"
