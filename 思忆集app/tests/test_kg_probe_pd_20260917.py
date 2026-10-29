# -*- coding: utf-8 -*-
"""P-D 知识图谱写入侧动态探针（2026-09-17，kg-probe）。

隔离实例（SIYI_INTEGRATION_AUTOSTART=1 → 随机端口 + 临时 data 目录，测后随
pytest 会话目录整删）。生产 data/ 零接触、零 git、不改生产代码。

覆盖（对应任务书 ①-④）：
  A 注册表写入面清单（system.status methods，找图谱实体 delete RPC）
  B external_content.import_kindle → 图谱节点/边真增加？磁盘落？MD 导出含卡片？
  C 去重：同文件二次导入 / 标题微变（大小写）
  D 删除/回滚面：文章删除后图谱悬空；cascade_delete_note KG 侧空转（单元级）
  E knowledge.ingest_conversation：UUID/数字 id 空间接合 + 离线降级
  F knowledge.brief 无内核降级（RPC 级 + 单元级）
  G LLM 提取通道（FakeLLM 单元级）：去重/出处/谓词准入/包含折叠/噪声门
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

# 生产数据泄漏守卫放行（2026-09-17）：本模块的 _guard_prod_knowledge_graph 是
# 红线保障 fixture —— 它快照生产 knowledge_graph.json 原始字节并在会话末
# **字节级回滚**，因此会触碰生产路径（净效果为零，回滚后自身断字节一致）。
# 这是有意为之的观察/回滚设计，故显式声明 allow_production_write。
pytestmark = pytest.mark.allow_production_write

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore  # noqa: E402
from knowledge.graph_extractor import KnowledgeGraphExtractor  # noqa: E402
from knowledge.graph_store import KnowledgeGraphStore  # noqa: E402

# 模块级共享状态（同一隔离后端，测试按定义顺序执行）
_STATE: dict = {}

# ── 生产图谱守护（红线保障 fixture） ────────────────────────────
# 实测发现：隔离实例(headless)构造 AppContext 时未传 data_base_dir，
# 导致 KnowledgeGraphStore 路径退化为**相对后端 CWD(=APP_ROOT)** 的
# data/knowledge_graph.json —— 与 local_data_store/md_store 的 SIYI_DATA_DIR
# 临时目录**不同形**。故任何走 AUTOSTART 后端的图谱 save() 都会写进**生产仓库
# 图谱权威文件**（PD-ISO 隔离泄漏，本报告头号缺陷）。
# 为守住"生产 data/ 一字节不碰"红线，本 fixture 在会话开始快照该文件原始字节、
# 会话结束字节级回滚（文件原本不存在则删除）。这样动态探针仍能**观察**泄漏，
# 但净效果对生产文件为零。
_PROD_KG = APP_ROOT / "data" / "knowledge_graph.json"


@pytest.fixture(scope="session", autouse=True)
def _guard_prod_knowledge_graph():
    pre = _PROD_KG.read_bytes() if _PROD_KG.is_file() else None
    print(f"\n[PD守护] 快照生产图谱: {'%d bytes' % len(pre) if pre else '不存在'}")
    try:
        yield
    finally:
        if pre is not None:
            _PROD_KG.write_bytes(pre)
        elif _PROD_KG.is_file():
            _PROD_KG.unlink()
        after = _PROD_KG.read_bytes() if _PROD_KG.is_file() else None
        restored = pre == after
        print(f"[PD守护] 回滚生产图谱: {'一致' if restored else '不一致!'} ({len(after) if after else 0} bytes)")
        assert restored, "红线违反：生产 knowledge_graph.json 未能字节回滚"


def _ok(resp: dict) -> dict:
    assert "error" not in resp, f"RPC 层错误: {resp.get('error')}"
    return resp.get("result")


# ── A 注册表写入面 ──────────────────────────────────────────────


async def test_pd_a_registry_write_surface(rpc):
    methods = _ok(await rpc("system.status"))["methods"]
    kg_ns = sorted(m for m in methods if m.startswith("knowledge."))
    ex_ns = sorted(m for m in methods if m.startswith("external_content."))
    _STATE["methods"] = methods
    print("\nknowledge.* =", kg_ns)
    print("external_content.* =", ex_ns)
    for m in (
        "knowledge.ingest_conversation",
        "knowledge.md_save",
        "knowledge.md_export",
        "knowledge.brief",
        "external_content.import_kindle",
    ):
        assert m in methods, f"写路径 RPC 缺失: {m}"
    # PD-NODEL 契约反转（2026-09-18 批4）：
    # 反转说明：旧注释"图谱实体删除 RPC：预期**不存在**（探针固化缺陷面）"把
    # "删除实体 RPC 缺失"当作期望锁死（且只 print 不断言，等于零约束）。批4
    # 补齐 knowledge.entity.delete 后，契约反转为：该 RPC 必须存在且过 R4 审批。
    # 历史事实（反转前勘察结论）：全仓**没有任何**删除图谱实体的 RPC ——
    # graph_store.remove_entity() 早已存在，但只被 _cascade_cleanup 与
    # reading.delete_article 内部调用；用户与 AI 都拿不到"删掉这个实体"的入口，
    # 图谱里提错的实体只能靠删来源笔记或重建全图来收拾。
    delete_rpcs = [m for m in methods if ("graph" in m and "delete" in m) or ("entity" in m and "delete" in m)]
    _STATE["graph_delete_rpcs"] = delete_rpcs
    print("图谱删除类 RPC =", delete_rpcs)
    assert "knowledge.entity.delete" in delete_rpcs, f"PD-NODEL 已修：删除实体 RPC 必须存在（R4 级），实际={delete_rpcs}"


# ── B Kindle 导入 → 图谱/磁盘/MD ────────────────────────────────

# 注意：标准 Kindle 元数据行是 "Location: 10-12"，而 _parse_kindle_clippings
# 的正则是 r"Location\\s+..." —— 冒号挡路，location 恒提取失败（B-旁证）
_CLIPPINGS = (
    "测试笔记·P-D量子计算入门 (张三)\r\n"
    "- Location: 10-12 | Added on 2026年9月17日 上午10:00:00\r\n\r\n"
    "量子计算利用叠加态进行并行计算，上海市今天举办研讨会。\r\n"
    "==========\r\n"
)


def _kg_path_from_md_list(rpc_res: dict) -> str:
    base = os.path.dirname(rpc_res["root"])  # root = <base_dir>/knowledge
    _STATE["base_dir"] = base
    return os.path.join(base, "knowledge_graph.json")


def _read_graph_file(path: str) -> dict:
    if not os.path.isfile(path):
        return {"entities": {}, "relations": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def test_pd_b_kindle_import_graph_and_disk(rpc, tmp_path):
    lst = _ok(await rpc("knowledge.md_list"))
    assert lst.get("success"), lst
    before = _ok(await rpc("knowledge.graph"))
    n0 = len(before["entities"])

    clip = tmp_path / "clippings_pd_b.txt"
    clip.write_text(_CLIPPINGS, encoding="utf-8")
    res = _ok(await rpc("external_content.import_kindle", {"file_path": str(clip)}))
    print("\nimport_kindle =", res)
    assert res.get("success") and res.get("imported") == 1

    after = _ok(await rpc("knowledge.graph"))
    ext = [e for e in after["entities"] if str(e.get("id", "")).startswith("external_content_")]
    src = [e for e in after["entities"] if str(e.get("id", "")).startswith("external_source_")]
    edges = [
        rel
        for rel in after["relations"]
        if str(rel.get("source_id", "")).startswith("external_content_") and rel.get("relation_type") == "sourced_from"
    ]
    print(f"图谱内存态: 实体 {n0}→{len(after['entities'])}, 文章实体 {len(ext)}, 来源枢纽 {len(src)}, sourced_from 边 {len(edges)}")
    assert len(ext) >= 1 and len(src) == 1 and len(edges) >= 1, "导入后图谱（内存）应有文章实体+来源枢纽+sourced_from 边"
    _STATE["pd_b_ext_ids"] = [e["id"] for e in ext]

    # 磁盘权威文件落点核查（PD-1 + PD-ISO 修复后契约反转，2026-09-17 主线程批）：
    #   旧现状：import 全程无 save() → 磁盘恒无 external 实体（重启即丢）。
    #   新契约：_flush_graph 随导入 save 落盘。落点 = 隔离临时目录（PD-ISO 修复后
    #   graph_store honor SIYI_DATA_DIR，不再泄漏生产 _PROD_KG —— 生产文件应恒无
    #   external 实体，本会话守护 fixture 仍字节回滚兜底）。
    kg_path = _kg_path_from_md_list(lst)  # <隔离 data 目录>/knowledge_graph.json
    disk = _read_graph_file(kg_path)
    disk_ext = [eid for eid in disk.get("entities", {}) if str(eid).startswith("external_content_")]
    prod = _read_graph_file(str(_PROD_KG))
    prod_ext = [eid for eid in prod.get("entities", {}) if str(eid).startswith("external_content_")]
    _STATE["disk_leak_check"] = {
        "import_just_now_disk_ext": len(disk_ext),  # PD-1 已修：save 收口 → 隔离目录应 >=1
        "prod_ext": len(prod_ext),  # PD-ISO 已修：生产文件不得再收到 external 实体
    }
    print("磁盘落点核查(import):", _STATE["disk_leak_check"])
    assert len(disk_ext) >= 1, "PD-1 已修：import_kindle 的 _flush_graph 应使 external 实体随导入落盘（重启不丢）"
    assert len(prod_ext) == 0, "PD-ISO 已修：图谱 save 应落隔离目录，生产文件不该再被外部实体污染"

    hits = _ok(await rpc("knowledge.search", {"query": "P-D量子计算入门"}))
    _STATE["pd_b_search_hits_after_import"] = len(hits)
    print("knowledge.search 命中(内存态) =", len(hits))


async def test_pd_b2_md_save_card_export(rpc):
    """卡片经 knowledge.md_save 回灌 → kv_store；md_export 主题轴是否包含（F1 复验）。"""
    card_md = (
        "---\ntitle: P-D卡片探针\ntopic: PD探针主题\ntype: 知识卡片\ndate: 2026-09-17\n---\n\n"
        "**结论**：探针写入的原子卡片应当在导出中落盘。\n\n**关键实体**：探针、知识图谱\n"
    )
    rel = "主题/PD探针主题/卡片-P-D卡片探针.md"
    saved = _ok(await rpc("knowledge.md_save", {"rel": rel, "content": card_md}))
    sync = saved.get("sync") or {}
    print("\nmd_save.sync =", sync)
    assert saved.get("success") is True
    assert sync.get("success") is True, f"卡片回灌失败: {sync}"

    cards = _ok(await rpc("knowledge.cards", {"days": 0}))
    assert any(str(c.get("title")) == "P-D卡片探针" for c in cards), "卡片应入 structured_memory_entries"

    exp = _ok(await rpc("knowledge.md_export", {"max_conversations": 0, "max_graph_entities": 100}))
    assert exp.get("success"), exp
    files = _ok(await rpc("knowledge.md_list"))["files"]
    topic_cards = [f for f in files if f.startswith("主题/") and "/卡片-" in f]
    print("导出后主题轴卡片 =", topic_cards, "| export.cards =", exp.get("cards"))
    assert any("P-D卡片探针" in f for f in topic_cards), "include_cards 病灶复现：卡片未出主题轴"

    # 读真实磁盘落点（=生产文件 _PROD_KG，PD-ISO 泄漏点）：
    # 本隔离后端 ai_client=None，md_save 的 _sync_entities_to_graph 跳过 → 不调 store.save()，
    # 故 external 实体（仅存于内存）不该出现在磁盘。若出现=图谱 flush 命中生产文件。
    kg = _read_graph_file(str(_PROD_KG))
    disk_ext = [eid for eid in kg.get("entities", {}) if str(eid).startswith("external_content_")]
    _STATE["disk_ext_after_export"] = disk_ext
    _STATE["disk_prod_size_after_b2"] = _PROD_KG.stat().st_size if _PROD_KG.is_file() else 0
    print("md_save/export 后【生产文件】external_content 实体 =", disk_ext or "（仍无 → 隔离后端 ai_client=None 未触发图谱 save）")


# ── C 去重 ─────────────────────────────────────────────────────


async def test_pd_c_dedupe_import(rpc, tmp_path):
    clip = tmp_path / "clippings_pd_c.txt"
    clip.write_text(_CLIPPINGS, encoding="utf-8")
    r1 = _ok(await rpc("external_content.import_kindle", {"file_path": str(clip)}))
    g1 = _ok(await rpc("knowledge.graph"))
    r2 = _ok(await rpc("external_content.import_kindle", {"file_path": str(clip)}))
    g2 = _ok(await rpc("knowledge.graph"))
    print("\n同文件: 第一次 imported=", r1.get("imported"), "第二次 imported=", r2.get("imported"), "skipped=", r2.get("skipped"))
    assert r2.get("imported") == 0 and r2.get("skipped") == 1, "同 external_id 二次导入必须去重"
    assert len(g2["entities"]) == len(g1["entities"]), "二次导入不得新增节点"

    # 标题微变（纯大小写）
    # 勘察纠偏（2026-09-18 批4）：原变体把 "P-D" 换成 "pd"，同时**删掉了连字符**，
    # 与注释所称"只剩大小写差异"不符——那样测的是"分隔符折叠"，属另一类语义变更。
    # 这里改成纯大小写变体，与 PD-5a 的"大小写异体"描述对齐。
    variant = _CLIPPINGS.replace("测试笔记·P-D量子计算入门", "测试笔记·p-d量子计算入门")
    clip_v = tmp_path / "clippings_pd_c_variant.txt"
    clip_v.write_text(variant, encoding="utf-8")
    rv = _ok(await rpc("external_content.import_kindle", {"file_path": str(clip_v)}))
    gv = _ok(await rpc("knowledge.graph"))
    names = [e.get("name", "") for e in gv["entities"] if str(e.get("id", "")).startswith("external_content_")]
    lowered = [n.lower() for n in names]
    dupe = sorted({n for n in lowered if lowered.count(n) > 1})
    new_nodes = len(gv["entities"]) - len(g2["entities"])
    print("标题微变: imported=", rv.get("imported"), "新增图谱节点=", new_nodes, "忽略大小写重复 =", dupe)
    _STATE["variant_dup"] = {"imported": rv.get("imported"), "new_nodes": new_nodes, "dupe_lower": dupe}
    # PD-5a 契约反转（2026-09-18 批4）：
    # 反转说明：旧注释"external_content 通道无名称级去重 → 预期 imported=1 造重复
    # 节点（大小写异体）"是缺陷固化，且断言写成 `in (0, 1)` 等于没有断言。
    # 治本后契约：导入前按规范化名称（NFKC + casefold + 空白折叠）去重，
    # 大小写异体必须 imported=0 / skipped=1 / 不新增节点 / 无重复节点。
    # 历史事实（反转前勘察结论）：kindle 的 external_id = "标题_位置"，标识本身
    # 就是名称；大小写一变即新 id → _dedupe_existing 放行 → 新文章 + 新
    # external_content_* 节点，同一本书被拆成两个图谱节点。
    assert rv.get("imported") == 0 and rv.get("skipped") == 1, f"PD-5a 已修：大小写异体应去重, got {rv}"
    assert new_nodes == 0, f"PD-5a 已修：不得新增图谱节点, got {new_nodes}"
    assert dupe == [], f"PD-5a 已修：不应出现忽略大小写的重复节点, got {dupe}"


# ── D 删除 / 回滚面 ────────────────────────────────────────────


async def test_pd_d_article_delete_leaves_dangling(rpc, rpc_approved):
    arts = _ok(await rpc("reading.list_articles"))
    items = arts if isinstance(arts, list) else (arts.get("articles") or arts.get("items") or [])
    kindle = [a for a in items if str(a.get("id", "")).startswith("kindle_")]
    if not kindle:
        pytest.skip(f"无 kindle 文章可删: {type(arts)}")
    aid = kindle[0]["id"]
    ent_id = f"external_content_{aid}"
    # reading.delete_article 是 R4 破坏性方法（pending→approve）→ 必须走 rpc_approved，
    # 否则只拿到 pending 回执、真删从未发生（探针首轮踩坑记录）。
    del_res = _ok(await rpc_approved("reading.delete_article", {"article_id": aid}))
    print("\ndelete_article(approved) =", del_res, "目标实体 =", ent_id)
    graph = _ok(await rpc("knowledge.graph"))
    ent_alive = any(e.get("id") == ent_id for e in graph["entities"])
    dangling = [rel for rel in graph["relations"] if ent_id in (rel.get("source_id"), rel.get("target_id"))]
    title = kindle[0].get("title") or ""
    hits = _ok(await rpc("knowledge.search", {"query": str(title)}))
    print(f"删文章后: 图谱实体仍存={ent_alive}, 触及该实体的边={len(dangling)}, search 同名命中={len(hits)}")
    # PD-3 修复后契约反转（2026-09-17 主线程批）：旧现状=只删 saved_articles、
    # 图谱实体/边滞留成悬空引用（本探针固化）；新契约=删除文章级联清理实体与边。
    assert not ent_alive, "PD-3 已修：delete_article 应级联删除图谱实体（不再悬空）"
    assert len(dangling) == 0, "PD-3 已修：触及该实体的边应随实体一并清理"
    # rpc_approved 回的是审批信封 {success,pending_id,action_id,result:{...}}，
    # 真删除回执在信封的 result 层。
    inner = (del_res or {}).get("result") or del_res or {}
    assert inner.get("graph_entities_removed", 0) >= 1, f"删除回执应携带级联清理计数, got {del_res}"
    _STATE["article_delete_dangling"] = {"entity_alive": ent_alive, "edges": len(dangling), "hits": len(hits)}


async def test_pd_d2_note_delete_cascade_kg_gap(tmp_path):
    """cascade_delete_note KG 分支（PD-2/PD-2b 修复后契约反转，2026-09-17 主线程批）。

    旧现状：delete_entity 方法名不存在→AttributeError 被吞、兜底 pop 错键空间、
    边字段名 source/target 与真实 source_id/target_id 不符 → no-op 谎报 deleted，
    且 remove_by_source 生效也不 save。
    新契约：真删除（remove_by_source 属性反查 + remove_entity id 直删）、
    有删除即 save() 落盘、结果携带计数、无命中如实 no-op。
    """
    from backend.handlers._cascade_cleanup import cascade_delete_note

    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")
    kg.add_entity("ent_note_a1", "笔记里的实体甲", "concept", properties={"source_id": "note:42"})
    kg.add_entity("ent_note_b2", "实体乙", "concept")
    kg.add_relation("ent_note_a1", "ent_note_b2", "related_to")
    kg.save()
    ctx = SimpleNamespace(knowledge_graph_store=kg, memory_index_service=None, structured_memory_store=None)
    results = await cascade_delete_note(ctx, 42)
    print("\ncascade_delete_note =", results)
    assert results.get("knowledge_graph") == "deleted:1", f"PD-2 已修：应真删 1 实体并携带计数，got {results}"
    assert kg.get_entity("ent_note_a1") is None, "PD-2 已修：source_id=note:42 实体应被级联删除"
    assert kg.get_entity("ent_note_b2") is not None, "无关实体不得误删"
    # PD-2b：级联删除必须落盘（重开 store 看不到该实体）
    disk = _read_graph_file(str(tmp_path / "kg.json"))
    assert "ent_note_a1" not in disk.get("entities", {}), "PD-2b 已修：删除后磁盘不应残留（save 收口）"
    disk_edges = [r for r in disk.get("relations", []) if "ent_note_a1" in (r.get("source_id"), r.get("target_id"))]
    assert not disk_edges, "触及被删实体的边必须一并落盘清理"
    # 无命中 → 如实 no-op（不再谎报 deleted）
    results2 = await cascade_delete_note(ctx, 999999)
    assert results2.get("knowledge_graph") == "no-op", f"无命中应报 no-op，got {results2}"
    _STATE["cascade_fixed"] = {"reported": results.get("knowledge_graph"), "noescape": results2.get("knowledge_graph")}


# ── E ingest_conversation ──────────────────────────────────────


async def test_pd_e_ingest_conversation(rpc):
    msgs = [
        {"role": "user", "content": "我在上海做一个叫 蓝鲸计划 的项目，负责人是王小明，用 DeepSeek 模型。"},
        {"role": "assistant", "content": "好的，已记录 蓝鲸计划 项目与王小明负责人的信息。"},
    ]
    sid_uuid = "pd-probe-0001-0002-0003"
    assert _ok(await rpc("chat.save_messages", {"session_id": sid_uuid, "title": "PD探针会话", "messages": msgs})) is True
    resp_uuid = await rpc("knowledge.ingest_conversation", {"session_id": sid_uuid})
    ing = resp_uuid.get("result") or resp_uuid.get("error") or {}
    print("\ningest(UUID串) =", ing)
    _STATE["ingest_uuid"] = ing
    # PD-4 修复后契约反转（2026-09-17 主线程批）：旧现状=chat 系接受 UUID
    # client_id、ingest 只认 int → 两 id 空间不接合恒 invalid session_id；
    # 新契约=复用 _resolve_session_id（sessions_index 反查 client_id→sid）。
    assert "invalid session_id" not in str(ing), f"PD-4 已修：UUID client_id 应可解析, got {ing}"
    assert ing.get("message_count") == 2, f"UUID 解析后应读到同一会话 2 条消息, got {ing}"

    sid_num = "987654"
    assert _ok(await rpc("chat.save_messages", {"session_id": sid_num, "title": "PD探针会话2", "messages": msgs})) is True
    t0 = time.monotonic()
    resp = await rpc("knowledge.ingest_conversation", {"session_id": sid_num})
    dt = time.monotonic() - t0
    body = resp.get("result") if "error" not in resp else resp.get("error")
    print(f"ingest(数字) {dt:.1f}s =", body)
    _STATE["ingest_num"] = {"seconds": round(dt, 1), "resp": body}
    if "error" in resp:
        assert dt < 90, f"RPC 挂死 {dt:.0f}s（崩溃面）"
    else:
        assert body.get("message_count") == 2, "应读到 2 条消息"
    # 连接未崩
    assert "error" not in await rpc("system.info"), "ingest 后 RPC 连接应仍可服务"


# ── F knowledge.brief 无内核降级 ───────────────────────────────


async def test_pd_f_brief_degrade(rpc):
    st = _ok(await rpc("system.status"))
    ai_on = bool((st.get("services") or {}).get("ai"))
    t0 = time.monotonic()
    resp = await rpc("knowledge.brief", {"days": 7})
    dt = time.monotonic() - t0
    body = resp.get("result") if "error" not in resp else resp.get("error")
    print(f"\nknowledge.brief(ai_on={ai_on}) {dt:.1f}s:", body if isinstance(body, (dict, str)) else "<large>")
    summary = body if isinstance(body, dict) else {"raw": str(body)[:200]}
    _STATE["brief_rpc"] = {"ai_on": ai_on, "seconds": round(dt, 1), "result": summary}
    if "error" in resp:
        assert dt < 90, f"brief RPC 挂死 {dt:.0f}s → 崩溃面（八轮 §D-1 未愈）"
    # 降级后连接健康
    ok_after = await rpc("system.info")
    assert "error" not in ok_after, "brief 后 RPC 连接应仍可服务"


def test_pd_f2_brief_unit_no_kernel(tmp_path):
    """单元级：cards>0 + gateway/ai_client 双缺 → 即时结构化降级。"""
    from services.knowledge_synthesis import generate_knowledge_brief

    store = LocalDataStore(base_dir=str(tmp_path))
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    store.save_json(
        "structured_memory_entries",
        [
            {
                "id": "c1",
                "entity_type": "knowledge.card",
                "supertag_type": "@知识",
                "relations": [],
                "source": "deepseek",
                "created_at": now,
                "updated_at": now,
                "tags": ["知识卡片"],
                "content": "探针结论",
                "title": "探针卡",
                "card_topic": "PD探针",
                "entities": [],
                "_conv": "x1",
            },
        ],
    )
    ctx = SimpleNamespace(local_data_store=store, agent_gateway=None, ai_client=None)
    t0 = time.monotonic()
    res = generate_knowledge_brief(ctx, days=7)
    dt = time.monotonic() - t0
    print(f"\nunit brief(无内核) {dt:.2f}s =", res)
    assert res.get("success") is False and "简报" in str(res.get("error"))
    assert dt < 5, f"无内核降级应即时（实测 {dt:.1f}s）"
    _STATE["brief_unit"] = {"seconds": round(dt, 2), "error": res.get("error")}


# ── G LLM 提取通道单元探针（FakeLLM） ──────────────────────────


class _FakeLLM:
    def __init__(self, replies: list[str]):
        self._replies = list(replies)
        self.calls = 0

    def complete_text(self, messages, temperature=0.2, max_tokens=None, **kw):
        self.calls += 1
        return self._replies.pop(0) if self._replies else '{"entities": [], "relations": []}'


def _llm(entities, relations=None):
    return json.dumps({"entities": entities, "relations": relations or []}, ensure_ascii=False)


def test_pd_g_llm_channel_dedupe_provenance(tmp_path):
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    llm = _FakeLLM(
        [
            _llm(
                [{"name": "蓝鲸计划", "type": "project"}, {"name": "王小明", "type": "person"}],
                [{"source": "王小明", "target": "蓝鲸计划", "relation": "负责"}],
            ),
            _llm([{"name": "蓝鲸计划", "type": "project"}, {"name": "王小明", "type": "person"}]),
            _llm([{"name": " 王小明", "type": "person"}, {"name": "DEEPSEEK", "type": "concept"}]),
            _llm([{"name": "DeepSeek", "type": "concept"}]),
        ]
    )
    ex = KnowledgeGraphExtractor(store, ai_client=llm)
    r1 = ex.extract_from_text("王小明负责蓝鲸计划", source_id="note:1", use_llm=True)
    n1 = len(store.get_all_entities())
    ex.extract_from_text("王小明负责蓝鲸计划", source_id="note:2", use_llm=True)
    n2 = len(store.get_all_entities())
    ex.extract_from_text("变体", source_id="note:3", use_llm=True)
    n3 = len(store.get_all_entities())
    ex.extract_from_text("大小写", source_id="note:3", use_llm=True)
    n4 = len(store.get_all_entities())
    names = sorted(e["name"] for e in store.get_all_entities())
    print("\nG1: n(1轮)=", n1, "n(重复)=", n2, "n(空格+DEEPSEEK)=", n3, "n(DeepSeek)=", n4, "names=", names)
    assert n1 == 2 and n2 == 2, "同文本二次提取不新增"
    assert n3 == 3, "' 王小明' strip 后复用；DEEPSEEK 新实体"
    assert n4 == 3, "DeepSeek 大小写异体应命中 name_index 复用"

    # 谓词准入：'负责' 不在 5 谓词表 → W2 丢弃，不伪造 related_to
    rels = [rel.get("relation_type") for rel in store.get_all_relations()]
    print("relations =", rels, "| relation_dropped =", ex.get_extraction_stats().get("relation_dropped"))
    assert rels == [] and ex.get_extraction_stats().get("relation_dropped") == 1, "未准入谓词必须丢弃留痕"

    # 出处契约：properties.sources 累积、去重
    ent = store.find_by_name("蓝鲸计划")[0]
    srcs = (ent.get("properties") or {}).get("sources") or []
    print("蓝鲸计划 sources =", srcs)
    assert srcs == ["note:1", "note:2"], "出处应累积两个不同来源且不重复"
    assert r1.get("via") == "llm"


def test_pd_g2_inclusion_folding(tmp_path):
    """包含折叠契约反转（2026-09-18 批4，PD-5b）。

    反转说明：旧断言 ``names == ["量子计算"]`` 把"新实体被折叠吞并"当期望锁死
    （注释原文即"缺陷固化"）。治本后 ``find_by_name`` 默认 = **规范化精确匹配**
    （身份解析语义），子串折叠降级为显式选项 ``allow_substring=True``（检索/
    召回调用方使用）。故"量子"必须真建出来；折叠能力本身保留，只是不再默认生效。

    历史事实（反转前勘察结论）：``find_by_name`` 在精确未命中后无条件做**双向
    子串包含**匹配，``_add_entity_if_new("量子")`` 因而命中已存在的「量子计算」
    并复用其 id → 新实体永远建不出来，图谱只剩 ``['量子计算']``。
    """
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    llm = _FakeLLM(
        [
            _llm([{"name": "量子计算", "type": "concept"}]),
            _llm([{"name": "量子", "type": "concept"}]),
        ]
    )
    ex = KnowledgeGraphExtractor(store, ai_client=llm)
    ex.extract_from_text("量子计算", use_llm=True)
    ex.extract_from_text("量子", use_llm=True)
    names = sorted(e["name"] for e in store.get_all_entities())
    print("\nG2 包含折叠: names =", names)
    _STATE["inclusion_folding"] = names
    assert names == ["量子", "量子计算"], "PD-5b 已修：新实体必须真建出来（不再被折叠吞并）"
    # 折叠能力未被删除：显式开启时仍可用于检索/召回场景（双向包含折叠）
    folded = [e["name"] for e in store.find_by_name("量子计", allow_substring=True)]
    assert "量子计算" in folded, f"折叠能力被误删（检索场景仍需要它），实际={folded}"


def test_pd_g3_pattern_noise_gate(tmp_path):
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    ex = KnowledgeGraphExtractor(store, ai_client=None)
    ex.extract_from_text("我觉得这个问题需要使用一些方法来解决", use_llm=False)
    noise = [e["name"] for e in store.get_all_entities()]
    ex.extract_from_text("李四说要去上海市出差", use_llm=False)
    names = sorted(e["name"] for e in store.get_all_entities())
    print("\nG3 patterns: 碎片句入库 =", noise, "; 正经句 =", names)
    assert noise == [], f"句子碎片应被噪声门拦截, 实际={noise}"
    assert "李四" in names, "人名模式应命中"
    # PD-5c 契约反转（2026-09-18 批4）：
    # 反转说明：旧断言要求"上海市"**不在** names、且存在一个长度 >3 的"…市"，
    # 即把"地名正则贪婪吞掉动词前缀"当期望锁死。治本后契约反转为：必须提取出
    # 干净的「上海市」，且不得存在带动词前缀的假地名。
    # 历史事实（反转前勘察结论）：`[一-鿿]{2,6}` 既无左边界约束、量词又贪婪，
    # 引擎从位置 1 起贪 6 个字恰好凑出「四说要去上海」+「市」→ 入库
    # "四说要去上海市"（实测 names = ['四说要去上海市', '李四']）。
    assert "上海市" in names, f"PD-5c 已修：应提取干净地名，实际={names}"
    assert not any(n.endswith("市") and len(n) > 3 for n in names), f"仍有动词前缀被吞: {names}"
    _STATE["pattern_location_pollution"] = names


def test_pd_g4_legacy_shard_migration(tmp_path):
    """L1 碎屑换代：遗留 mentioned_in 容器边遇到即迁移（增量），无全量清理调度。"""
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    store.add_entity("e1", "深度强化学习", "concept")
    store.add_entity("mem_c1", "记忆:c1", "concept", properties={"source_id": "memory:c1"})
    store.add_relation("e1", "mem_c1", "mentioned_in")
    store.save()
    llm = _FakeLLM([_llm([{"name": "深度强化学习", "type": "concept"}])])
    ex = KnowledgeGraphExtractor(store, ai_client=llm)
    ex.extract_from_text("再谈深度强化学习", source_id="memory:c1", use_llm=True)
    ents = [e["name"] for e in store.get_all_entities()]
    rels = [r.get("relation_type") for r in store.get_all_relations()]
    ent = store.find_by_name("深度强化学习")[0]
    print("\nG4: entities =", ents, "relations =", rels, "sources =", (ent.get("properties") or {}).get("sources"))
    assert "记忆:c1" not in ents, "增量迁移：容器节点应被摘除"
    assert "mentioned_in" not in rels
    assert (ent.get("properties") or {}).get("sources") == ["memory:c1"]
