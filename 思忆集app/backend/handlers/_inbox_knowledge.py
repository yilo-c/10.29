"""knowledge.* JSON-RPC handlers：知识图谱查询 + 归纳 + MD 内容层读写。

从原 _inbox_data.py 拆分而来。MD 子域统一经 ``context.local_data_store.base_dir``
构造 ``MDKnowledgeStore``，数据访问层保持统一，不在本模块另起路径。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from backend.handlers._async_rpc import poll_job_result as _poll_job_result
from backend.handlers._wrap import CODE_MEM_DATA, degrade

logger = logging.getLogger("siyi.backend.handlers.inbox_knowledge")


def _build_detail_resolvers(context: Any) -> tuple[dict[str, Any], Any]:
    """给节点详情归一层拼「反查器」（P4-KG-DETAIL）。

    为何需要它：图实体的出处只存了一串 id（`"note:42"` / 445 条还是裸 id），
    面板拿不到标题也不知道能不能跳。这里把各域仓储接成一个字典，
    幵等能力**逐个能力探测**：哪个仓储没注入就跳过哪个（只会少一条标题，
    不会抛、不会谎报）。全部是只读查询。
    """
    # 统一记忆条目索引：id → entry（记忆/MD 回灌的裸 id 大多落在这一族），
    # 同时拿它的 title/content 作为卡片正文——卡片正文不存在实体上。
    entries_by_id: dict[str, dict[str, Any]] = {}
    entries_by_title: dict[str, dict[str, Any]] = {}
    store = getattr(context, "local_data_store", None)
    if store is not None:
        try:
            raw_entries = store.load_json("structured_memory_entries", []) or []
        except Exception as exc:  # noqa: BLE001 — 读不到只是少一域，不炸整轮
            logger.warning("detail: structured_memory_entries 读取失败（来源标题会降级）: %s", exc)
            raw_entries = []
        for entry in raw_entries if isinstance(raw_entries, list) else []:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("id") or "").strip()
            if key:
                entries_by_id.setdefault(key, entry)
            title = str(entry.get("title") or "").strip()
            if title:
                entries_by_title.setdefault(title, entry)

    def _entry_title(ident: str) -> dict[str, Any] | None:
        entry = entries_by_id.get(str(ident).strip())
        if entry is None:
            return None
        return {"title": entry.get("title") or entry.get("card_topic") or "", "entity_type": entry.get("entity_type") or ""}

    resolvers: dict[str, Any] = {"memory": _entry_title, "md": _entry_title}

    notes = getattr(context, "personal_note_repository", None)
    get_note = getattr(notes, "get_note", None)
    if callable(get_note):

        def _note_title(ident: str) -> dict[str, Any] | None:
            try:
                note = get_note(ident)
            except Exception as exc:  # noqa: BLE001
                logger.warning("detail: note %s 反查失败: %s", ident, exc)
                return None
            if not isinstance(note, dict):
                return None
            return {"title": note.get("title") or ""}

        resolvers["note"] = _note_title

    inbox = getattr(context, "personal_inbox_repository", None)
    get_entry = getattr(inbox, "get_entry", None)
    if callable(get_entry):

        def _inbox_title(ident: str) -> dict[str, Any] | None:
            try:
                item = get_entry(int(ident)) if str(ident).isdigit() else get_entry(ident)
            except Exception as exc:  # noqa: BLE001
                logger.warning("detail: inbox %s 反查失败: %s", ident, exc)
                return None
            if not isinstance(item, dict):
                return None
            return {"title": item.get("title") or ""}

        resolvers["inbox"] = _inbox_title

    def _card_body(key: str) -> dict[str, Any] | None:
        """卡片/MOC 节点回查正文（实体只有 kind/title，正文在结构化记忆）。"""
        entry = entries_by_id.get(key) or entries_by_title.get(key)
        if not entry:
            return None
        if str(entry.get("entity_type") or "") not in ("knowledge.card", "knowledge.moc"):
            return None
        return {
            "entity_type": entry.get("entity_type"),
            "title": entry.get("title") or "",
            "content": entry.get("content") or "",
            "detail": entry.get("detail") or "",
            "expansion": entry.get("expansion") or "",
            "analysis": entry.get("analysis") or "",
        }

    return resolvers, _card_body


def _build_body_resolvers(context: Any) -> dict[str, Any]:
    """给 detail_backfill 拼「正文反查器」（P4-KG-DETAIL 批次 C，2026-10-15）。

    与 ``_build_detail_resolvers`` 的区别：那边只要 title（面板显示出处名），
    这边要 **content**（backfill 需要原文才能归纳详情）。同一批仓储、同一套
    能力探测纪律（没注入的域跳过，不抛不谎报）。全部只读。
    """
    resolvers: dict[str, Any] = {}

    store = getattr(context, "local_data_store", None)
    if store is not None:
        try:
            raw_entries = store.load_json("structured_memory_entries", []) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("backfill: structured_memory_entries 读取失败: %s", exc)
            raw_entries = []
        entries_by_id = {
            str(e.get("id") or "").strip(): e
            for e in (raw_entries if isinstance(raw_entries, list) else [])
            if isinstance(e, dict) and str(e.get("id") or "").strip()
        }

        def _memory_body(ident: str) -> dict[str, Any] | None:
            entry = entries_by_id.get(str(ident).strip())
            if entry is None:
                return None
            content = entry.get("content") or entry.get("detail") or entry.get("expansion") or ""
            return {"title": entry.get("title") or "", "content": str(content or "")}

        resolvers["memory"] = _memory_body

    notes = getattr(context, "personal_note_repository", None)
    get_note = getattr(notes, "get_note", None)
    if callable(get_note):

        def _note_body(ident: str) -> dict[str, Any] | None:
            try:
                note = get_note(ident)
            except Exception as exc:  # noqa: BLE001
                logger.warning("backfill: note %s 反查失败: %s", ident, exc)
                return None
            if not isinstance(note, dict):
                return None
            return {"title": note.get("title") or "", "content": str(note.get("content") or "")}

        resolvers["note"] = _note_body

    inbox = getattr(context, "personal_inbox_repository", None)
    get_entry = getattr(inbox, "get_entry", None)
    if callable(get_entry):

        def _inbox_body(ident: str) -> dict[str, Any] | None:
            try:
                item = get_entry(int(ident)) if str(ident).isdigit() else get_entry(ident)
            except Exception as exc:  # noqa: BLE001
                logger.warning("backfill: inbox %s 反查失败: %s", ident, exc)
                return None
            if not isinstance(item, dict):
                return None
            return {"title": item.get("title") or "", "content": str(item.get("content") or "")}

        resolvers["inbox"] = _inbox_body

    # deepseek 对话域（2026-10-15）：图谱 `deepseek:{uuid}` 出处是**平台会话 id**
    # （ai_chat_sync 写入侧口径，与本地 int id 不同），原文只在 chat.deepseek.com。
    # 有账户 token 时按 uuid 直取消息（只读）；带进程内缓存避免同一 uuid 重复拉。
    ds_token = ""
    try:
        from services.ai_chat_sync import get_account_token

        ds_token = get_account_token(context, "deepseek")
    except Exception as exc:  # noqa: BLE001
        logger.warning("backfill: deepseek token 读取失败（该域不可用）: %s", exc)
    if ds_token:
        try:
            from integrations.deepseek_client import DeepSeekAccountClient

            ds_client = DeepSeekAccountClient(ds_token)
            ds_cache: dict[str, dict[str, Any] | None] = {}

            def _deepseek_body(ident: str) -> dict[str, Any] | None:
                key = str(ident).strip()
                if key in ds_cache:
                    return ds_cache[key]
                result: dict[str, Any] | None = None
                # 2026-10-15（id 口径专项审计·读取侧分流）：出处 id **纯数字** =
                # 新口径（chat_importer 回填的**本地会话 id**）→ 查本地仓储；
                # uuid 形态 = 旧口径（平台会话 id，历史孤儿）→ 走平台 API。
                if key.isdigit():
                    local_repo = getattr(context, "chat_session_repository", None)
                    get_msgs = getattr(local_repo, "get_messages", None) if local_repo else None
                    if callable(get_msgs):
                        try:
                            local_msgs = get_msgs(int(key))
                            parts = [
                                f"[{m.get('speaker') or m.get('role')}] {m.get('message') or m.get('content')}"
                                for m in (local_msgs or [])
                                if str(m.get("message") or m.get("content") or "").strip()
                            ]
                            text = "\n".join(parts).strip()
                            if text:
                                result = {"title": f"DeepSeek 会话(本地 {key})", "content": text}
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("backfill: deepseek 本地会话 %s 反查失败: %s", key, exc)
                    ds_cache[key] = result
                    return result
                # 弱网/沙箱下该域 HTTPS 连接抖动（实测约 40% 失败率），重试可把
                # 单轮成功率提到 ~95%。生产环境通常一次即中。
                last_exc: Exception | None = None
                for attempt in range(3):
                    try:
                        msgs = ds_client.get_messages(key)
                        parts = [f"[{m.get('role')}] {m.get('content')}" for m in msgs if str(m.get("content") or "").strip()]
                        text = "\n".join(parts).strip()
                        if text:
                            result = {"title": f"DeepSeek 会话 {key[:8]}", "content": text}
                        last_exc = None
                        break
                    except Exception as exc:  # noqa: BLE001 — 单会话拉取失败只跳过
                        last_exc = exc
                        if attempt < 2:
                            time.sleep(1.0 + attempt)
                if last_exc is not None:
                    logger.warning("backfill: deepseek %s 拉取失败（3 次）: %s", key, last_exc)
                ds_cache[key] = result
                return result

            resolvers["deepseek"] = _deepseek_body
        except Exception as exc:  # noqa: BLE001
            logger.warning("backfill: deepseek client 构造失败（该域不可用）: %s", exc)

    return resolvers


async def run_knowledge_brief(context: Any, *, days: int = 7) -> dict[str, Any]:
    """生成最近 N 天知识简报的真实现 —— cron worker 与 RPC 降级共用（S3-2）。"""
    try:
        from services.knowledge_synthesis import generate_knowledge_brief

        return await asyncio.to_thread(generate_knowledge_brief, context, days)
    except Exception as exc:
        logger.warning("knowledge.brief failed: %s", exc)
        return {"success": False, "error": str(exc)}


def register_inbox_knowledge(registry, context) -> None:
    """注册 knowledge.* 命名空间下的 RPC 方法。"""

    # ── knowledge.search ──
    async def knowledge_search(
        query: str = "",
        entity_type: str = "",
        limit: int = 1000,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """检索实体。空参 = 列出（受 limit 保护）。

        2026-10-15（U1 实测）：空参原返回**全部**实体——实测 739 实体 → 323KB
        响应，逼近 WS 1MB 帧上限（超限会打死整条连接）；实体继续增长即会出事。
        引入保护性上限：默认 1000（> 现有 739，**不改变现状行为**）、
        上限 2000（防呆）。需要全量时应显式分页/调大 limit，而不是无限返回。
        """
        kg = getattr(context, "knowledge_graph_store", None)
        if not kg:
            return []
        try:
            lim = max(1, min(int(limit), 2000))
        except (TypeError, ValueError):
            lim = 1000
        if entity_type:
            rows = await asyncio.to_thread(kg.find_by_type, entity_type)
        elif query:
            # PD-5b（2026-09-18）：knowledge.search 是**检索**入口，用户输入
            # 「量子」期望看到「量子计算」→ 显式开启子串折叠（默认已收紧为
            # 规范化精确匹配，那是身份解析语义，不适用于搜索）。
            rows = await asyncio.to_thread(kg.find_by_name, query, allow_substring=True)
        else:
            rows = await asyncio.to_thread(kg.get_all_entities)
        return list(rows)[:lim]

    registry.register_with_schema(
        "knowledge.search",
        knowledge_search,
        schema={
            "properties": {
                "entity_type": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 1000, "minimum": 1, "maximum": 2000},
            },
            "type": "object",
        },
    )

    # ── knowledge.relations (查询实体的所有关系) ──
    async def knowledge_relations(entity_id: str = "", **kwargs: Any) -> list[dict[str, Any]]:
        """返回某个实体的所有关系（出边 + 入边）。"""
        if not entity_id:
            return []
        kg = getattr(context, "knowledge_graph_store", None)
        if not kg:
            return []
        try:
            return await asyncio.to_thread(kg.get_relations, entity_id)
        except Exception:
            return degrade(logger, CODE_MEM_DATA, "knowledge.relations failed", fallback=[])

    registry.register_with_schema(
        "knowledge.relations",
        knowledge_relations,
        schema={"properties": {"entity_id": {"type": "string"}}, "type": "object"},
    )

    # ── knowledge.entity.detail（节点详情归一层）──
    # P4-KG-DETAIL（2026-09-26）：图谱 6+ 条写入通道往 properties 各写各的键
    # （ent_*/card_*/moc_*/external_content_*），而面板只认 `source_id`——实测
    # 739 实体里 0 个带 source_id、658 个带 sources（数组被前端当对象丢弃）、
    # 0 个带正文。体感就是「节点点开没东西可看」。现在后端一次返回归一后的
    # 完整详情：实体自述 + 出处列表（带标题/可跳标记）+ 按谓词分组的邻居 +
    # 卡片正文。前端不再猜键名。见 knowledge/node_profile.py。
    async def knowledge_entity_detail(entity_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """返回单个实体的归一详情（不可在本地拼，需反查各域仓储）。"""
        eid = str(entity_id or "").strip()
        if not eid:
            return {"found": False, "error": "entity_id required"}
        kg = getattr(context, "knowledge_graph_store", None)
        if kg is None:
            return {"found": False, "entity_id": eid, "error": "knowledge_graph_store unavailable"}
        from knowledge.node_profile import build_entity_detail

        resolvers, card_loader = _build_detail_resolvers(context)
        try:
            return await asyncio.to_thread(
                build_entity_detail,
                kg,
                eid,
                resolvers=resolvers,
                card_loader=card_loader,
            )
        except Exception as exc:  # noqa: BLE001 — 降级为空详情但不谎报 found
            return degrade(
                logger,
                CODE_MEM_DATA,
                "knowledge.entity.detail failed",
                fallback={"found": False, "entity_id": eid, "error": str(exc)},
            )

    registry.register_with_schema(
        "knowledge.entity.detail",
        knowledge_entity_detail,
        schema={"properties": {"entity_id": {"type": "string"}}, "type": "object"},
    )

    # ── knowledge.detail_backfill（存量实体详情回填，P4-KG-DETAIL 批次 C）──
    # 2026-10-15：写侧历史上从未产 detail（批次 B 才补上 prompt），生产 739
    # 实体 0 个有详情。本方法把存量补上：出处反查原文 → AI 归纳/原文截断 →
    # 回写 properties.detail。**默认 dry_run=True 只预览不落盘**（它会改生产
    # 图谱文件）；实跑必须显式 dry_run=False。反查不到原文的实体跳过
    # （宁缺毋滥，绝不用实体名编造）。见 knowledge/detail_backfill.py。
    async def knowledge_detail_backfill(dry_run: bool = True, limit: int = 50, **kwargs: Any) -> dict[str, Any]:
        kg = getattr(context, "knowledge_graph_store", None)
        if kg is None:
            return {"error": "knowledge_graph_store unavailable"}
        from knowledge.detail_backfill import backfill_entity_details

        body_resolvers = _build_body_resolvers(context)
        ai_client = getattr(context, "ai_client", None)
        try:
            return await asyncio.to_thread(
                backfill_entity_details,
                kg,
                resolvers=body_resolvers,
                ai_client=ai_client,
                dry_run=bool(dry_run),
                limit=max(1, min(int(limit), 200)),
            )
        except Exception as exc:  # noqa: BLE001 — 降级为错误报告，不谎报成功
            return degrade(
                logger,
                CODE_MEM_DATA,
                "knowledge.detail_backfill failed",
                fallback={"error": str(exc)},
            )

    registry.register_with_schema(
        "knowledge.detail_backfill",
        knowledge_detail_backfill,
        schema={
            "properties": {
                "dry_run": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200},
            },
            "type": "object",
        },
    )

    # ── knowledge.entity.delete（删除单个图谱实体 + 级联其关系）──
    # P2-PD-NODEL（2026-09-18）：此前全仓**没有任何**删除图谱实体的 RPC ——
    # store 早有 remove_entity()，但只被级联清理（_cascade_cleanup）与
    # reading.delete_article 内部调用；用户与 AI 都拿不到"删掉这个实体"的入口，
    # 图谱里提错的实体只能靠删来源笔记或重建全图来收拾。
    async def knowledge_entity_delete(entity_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """删除一个图谱实体及其全部关系（出边 + 入边）。

        删除属不可逆操作 → 本方法登记在 backend.handlers.__init__ 的
        ``_R4_METHODS`` 集中清单内，dispatch 层强制走 sandbox 审批
        （未审批只拿到 pending，不执行）。范式与 notes.delete 等同级删除一致。

        行为契约（照抄同域既有教训）：
        * 有删除即 ``save()`` 落盘 —— 只改内存则重启复活（PD-2b 同型）；
        * 未命中如实 no-op 返回 ``success=False``，不谎报成功（P1-4 同型）；
        * ``save()`` 失败如实上报（``deleted=True`` 但 ``success=False``），
          不把"只在内存里删掉"说成删干净。
        """
        eid = str(entity_id or "").strip()
        if not eid:
            return {"success": False, "deleted": False, "error": "entity_id required"}
        kg = getattr(context, "knowledge_graph_store", None)
        if kg is None:
            return {"success": False, "deleted": False, "error": "knowledge_graph_store unavailable"}

        def _delete() -> dict[str, Any]:
            if not kg.remove_entity(eid):
                return {"success": False, "deleted": False, "entity_id": eid, "error": "not found"}
            try:
                kg.save()
            except Exception as exc:  # noqa: BLE001 — 落盘失败必须如实上报
                logger.warning("knowledge.entity.delete: 图谱 save 失败（删除仅在内存）: %s", exc)
                return {"success": False, "deleted": True, "entity_id": eid, "error": f"save failed: {exc}"}
            return {"success": True, "deleted": True, "entity_id": eid}

        return await asyncio.to_thread(_delete)

    registry.register_with_schema(
        "knowledge.entity.delete",
        knowledge_entity_delete,
        schema={"properties": {"entity_id": {"type": "string"}}, "type": "object"},
    )

    # ── knowledge.entity.merge（把一个实体并入另一个：边/别名/出处转指）──
    # P3-KG-MERGE（2026-09-21）：图谱里同一事物常多节点共存（正则/LLM 两代提取、
    # 单复数、异体字），而 find_by_name 默认是规范化**精确**匹配（PD-5b）不会自动
    # 折叠，此前全仓也无归并入口。合并不可逆（source 节点消失）→ 与 delete 同级 R4。
    async def knowledge_entity_merge(
        entity_id: str = "",
        into_entity_id: str = "",
        keep_alias: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """把 ``entity_id`` 并入 ``into_entity_id``（前者消失，后者名称/类型不变）。"""
        src = str(entity_id or "").strip()
        dst = str(into_entity_id or "").strip()
        if not src or not dst:
            return {"success": False, "merged": False, "error": "entity_id and into_entity_id required"}
        kg = getattr(context, "knowledge_graph_store", None)
        if kg is None:
            return {"success": False, "merged": False, "error": "knowledge_graph_store unavailable"}

        def _merge() -> dict[str, Any]:
            result = kg.merge_entities(src, dst, keep_alias=bool(keep_alias))
            if not result.get("merged"):
                return {"success": False, **result}
            try:
                kg.save()
            except Exception as exc:  # noqa: BLE001 — 落盘失败必须如实上报
                logger.warning("knowledge.entity.merge: 图谱 save 失败（合并仅在内存）: %s", exc)
                return {"success": False, "merged": True, "save_failed": True, "error": f"save failed: {exc}", **result}
            return {"success": True, **result}

        return await asyncio.to_thread(_merge)

    registry.register_with_schema(
        "knowledge.entity.merge",
        knowledge_entity_merge,
        schema={
            "properties": {
                "entity_id": {"type": "string"},
                "into_entity_id": {"type": "string"},
                "keep_alias": {"type": "boolean"},
            },
            "type": "object",
        },
    )

    # ── knowledge.backfill（关系回填：把已沉淀条目重跑一次 LLM 抽取）──
    # P1-2 KG-BACKFILL（2026-09-21）：历史上记忆回灌始终走正则通道（KG-CH 已修
    # 默认值），但存量图谱里的语义关系仍然缺失（实测 support/oppose = 0）。
    # **opt-in**：只由用户显式调用，不挂 cron、不在启动时自动跑（花真金白银的
    # LLM 调用不能隐式发生）；单次最多处理 limit 条，配合 offset 游标分批续跑。
    async def knowledge_backfill(
        days: int = 0,
        limit: int = 20,
        offset: int = 0,
        domains: Any = None,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """把各业务域的原文重跑一次 LLM 抽取，补写实体与语义关系。

        P4-KG-CAPTURE（2026-09-26）：采集面从「只有结构化记忆」扩到
        记忆/笔记/收件箱/任务/词库五域（统一由 `knowledge.domain_capture`
        转成同形条目）——之前笔记/收件箱/任务/词库**根本没有进图谱的通道**，
        这是图谱节点“说不清自己从哪来”的直接原因。`domains` 缺省 `memory`
        （保持旧行为），要一次扫全域传 `"all"`。

        分页契约（重要）：条目按 ``created_at`` 倒序排列，本次处理
        ``[offset, offset+limit)`` 区间，返回 ``next_offset`` 供下一次传入；
        不传 offset 反复调用会**重跑同一批**（幂等，不会重复建点/建边，
        但白烧 LLM）。要覆盖全库必须逐批推进 offset。
        """

        def _run() -> dict[str, Any]:
            import hashlib

            from knowledge.domain_capture import (
                CAPTURABLE_DOMAINS,
                capture_text,
                collect_capture_items,
                normalize_domains,
            )

            extractor = getattr(context, "knowledge_graph_extractor", None)
            if getattr(context, "knowledge_graph_store", None) is None:
                return {"success": False, "error": "knowledge_graph_store unavailable"}
            if extractor is None:
                return {"success": False, "error": "knowledge_graph_extractor unavailable"}
            if getattr(extractor, "ai_client", None) is None:
                # 无内核时正则通道不得充当替补（KG-CH 契约）：直接如实拒。
                return {"success": False, "error": "no_ai_client", "hint": "关系回填需要 LLM 内核，请先在设置里配置 AI"}
            graph = getattr(extractor, "store", None)

            picked = normalize_domains(domains)
            items = collect_capture_items(context, picked, days=int(days or 0))
            cap = max(1, min(int(limit or 20), 200))
            start = max(0, int(offset or 0))
            selected = items[start : start + cap]

            if dry_run:
                return {
                    "success": True,
                    "dry_run": True,
                    "domains": picked,
                    "available_domains": list(CAPTURABLE_DOMAINS),
                    "candidates": len(items),
                    "offset": start,
                    "would_process": len(selected),
                    "next_offset": start + len(selected),
                    "remaining": max(0, len(items) - start - len(selected)),
                    "preview": [
                        {
                            "kind": str(e.get("kind") or ""),
                            "id": str(e.get("id") or ""),
                            "title": str(e.get("title") or "")[:40],
                        }
                        for e in selected[:10]
                    ],
                }

            def _snapshot() -> tuple[set[str], set[tuple[str, str, str]]]:
                if graph is None:
                    return set(), set()
                ents = {str(e.get("id") or "") for e in graph.get_all_entities()}
                rels = {
                    (str(r.get("source_id") or ""), str(r.get("target_id") or ""), str(r.get("relation_type") or ""))
                    for r in graph.get_all_relations()
                }
                return ents, rels

            def _source_key(item: dict[str, Any]) -> str:
                raw = f"{item.get('kind') or 'unknown'}:{item.get('id') or item.get('title') or ''}"
                if len(raw) <= 96:
                    return raw
                return f"{raw[:64]}#{hashlib.md5(raw.encode('utf-8'), usedforsecurity=False).hexdigest()[:8]}"

            before_entities, before_relations = _snapshot()
            attempted = 0
            by_via: dict[str, int] = {}
            by_domain: dict[str, int] = {}
            errors: list[str] = []
            for item in selected:
                attempted += 1
                by_domain[str(item.get("kind") or "unknown")] = by_domain.get(str(item.get("kind") or "unknown"), 0) + 1
                text = capture_text(item)
                if not text:
                    by_via["empty"] = by_via.get("empty", 0) + 1
                    continue
                try:
                    # 统一走抽取器单点：出处带域（source_kind），不再产生裸 id 出处
                    result = extractor.extract_from_text(
                        text,
                        source_id=str(item.get("id") or ""),
                        use_llm=True,
                        source_kind=str(item.get("kind") or ""),
                    )
                except Exception as exc:  # noqa: BLE001 — 单条失败不终止整轮回填
                    by_via["exception"] = by_via.get("exception", 0) + 1
                    errors.append(f"{_source_key(item)}: {type(exc).__name__}: {exc}")
                    continue
                via = str(result.get("via") or "unknown")
                by_via[via] = by_via.get(via, 0) + 1
                if result.get("error"):
                    errors.append(f"{_source_key(item)}: {result['error']}")

            after_entities, after_relations = _snapshot()
            new_entities = len(after_entities - before_entities)
            new_relations = len(after_relations - before_relations)
            return {
                "success": True,
                "dry_run": False,
                "domains": picked,
                "candidates": len(items),
                "offset": start,
                "attempted": attempted,
                "by_domain": by_domain,
                "new_entities": new_entities,
                "new_relations": new_relations,
                "by_via": by_via,
                "next_offset": start + len(selected),
                "remaining": max(0, len(items) - start - len(selected)),
                "errors": errors[:10],
                "error_count": len(errors),
            }

        return await asyncio.to_thread(_run)

    registry.register_with_schema(
        "knowledge.backfill",
        knowledge_backfill,
        schema={
            "properties": {
                "days": {"type": "integer"},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
                "domains": {"type": ["string", "array"], "items": {"type": "string"}},
                "dry_run": {"type": "boolean"},
            },
            "type": "object",
        },
    )

    # ── knowledge.graph (返回全图：所有实体 + 所有关系) ──
    # 前端 graphService.ts 调用，期望返回 {entities: [...], relations: [...]}
    async def knowledge_graph(max_entities: int = 0, **kwargs: Any) -> dict[str, Any]:
        """返回知识图谱的全部实体和关系。

        Args:
            max_entities: P3-KG-CAP（2026-09-21）新增。>0 时只返回前 N 个实体
                （**优先返回有关系的节点**，孤立节点先被截），并置
                ``truncated=True``；0（默认）= 全量，行为与历史一致。
                背景：本方法此前无任何上限，图增长后单次 RPC 体量不可控
                （桌面渲染层已有 MAX_NODES=300 截断，但传输层没有）。

        Returns:
            {"entities": [...], "relations": [...], "total_entities": int, "truncated": bool}
        """
        kg = getattr(context, "knowledge_graph_store", None)
        if not kg:
            return {"entities": [], "relations": [], "total_entities": 0, "truncated": False}
        try:
            entities = await asyncio.to_thread(kg.get_all_entities)
            relations = await asyncio.to_thread(kg.get_all_relations)
            total = len(entities)
            cap = max(0, int(max_entities or 0))
            truncated = False
            if cap and total > cap:
                # 优先保留连通节点（孤立点被截掉不损图结构可读性）
                connected: set[str] = set()
                for rel in relations:
                    connected.add(str(rel.get("source_id") or ""))
                    connected.add(str(rel.get("target_id") or ""))
                ordered = sorted(
                    entities,
                    key=lambda e: 0 if str(e.get("id") or "") in connected else 1,
                )
                entities = ordered[:cap]
                truncated = True
                kept_ids = {str(e.get("id") or "") for e in entities}
                relations = [r for r in relations if str(r.get("source_id") or "") in kept_ids and str(r.get("target_id") or "") in kept_ids]
            return {
                "entities": entities,
                "relations": relations,
                "total_entities": total,
                "truncated": truncated,
            }
        except Exception:
            return degrade(
                logger,
                CODE_MEM_DATA,
                "knowledge.graph failed",
                fallback={"entities": [], "relations": [], "total_entities": 0, "truncated": False},
            )

    registry.register_with_schema(
        "knowledge.graph",
        knowledge_graph,
        schema={"properties": {"max_entities": {"type": "integer"}}, "type": "object"},
    )

    # ── knowledge.graph_for_viz (返回 Cytoscape.js 兼容的图数据) ──
    async def knowledge_graph_for_viz(
        query: str = "",
        entity_type: str = "",
        max_nodes: int = 500,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """返回适合 Cytoscape.js 渲染的图元素。

        Args:
            query: 可选，按名称过滤实体（模糊匹配）。
            entity_type: 可选，按实体类型过滤。
            max_nodes: 最多返回节点数，默认 500。

        Returns:
            {"nodes": [{"data": {"id", "label", "type", "color"}}],
             "edges": [{"data": {"id", "source", "target", "label"}}]}
        """
        kg = getattr(context, "knowledge_graph_store", None)
        if not kg:
            return {"nodes": [], "edges": []}

        # 实体类型 -> 颜色映射（与前端 KnowledgeGraphData 保持一致）
        _TYPE_COLORS: dict[str, str] = {
            "project": "#4fc3f7",
            "concept": "#81c784",
            "person": "#ff8a65",
            "location": "#ce93d8",
            "event": "#ffd54f",
            "organization": "#4db6ac",
            "date": "#90a4ae",
            "tag": "#a1887f",
            "unknown": "#78909c",
            # 中文类型兼容
            "项目": "#4fc3f7",
            "概念": "#81c784",
            "人物": "#ff8a65",
            "地点": "#ce93d8",
            "事件": "#ffd54f",
            "组织": "#4db6ac",
            "日期": "#90a4ae",
            "标签": "#a1887f",
            "其他": "#78909c",
        }

        def _build() -> dict[str, Any]:
            kg.load()
            # S3-3 置顶老伤（2026-09-16）：get_all_entities() 返回 list[dict]
            # （graph_store.py:420 `list(self._entities.values())`），而本函数下方
            # 按 dict 用（entities.items()/keys()/get(eid)）→ 原样必抛
            # AttributeError 被 except 吞成 degraded 空图（图谱可视化恒空根因）。
            # 转回 id→entity 映射（实体 id 字段见 graph_store.add_entity:258）。
            entities = {e["id"]: e for e in kg.get_all_entities() if isinstance(e, dict) and "id" in e}
            relations = kg.get_all_relations()

            # 过滤实体
            filtered_ids: set[str]
            if query:
                q = query.strip().lower()
                filtered_ids = {
                    eid
                    for eid, e in entities.items()
                    if q in (e.get("name") or "").lower() or any(q in (a or "").lower() for a in e.get("aliases", []))
                }
            elif entity_type:
                filtered_ids = {eid for eid, e in entities.items() if e.get("type") == entity_type}
            else:
                filtered_ids = set(entities.keys())

            # 限制节点数量：优先保留有关系的节点
            if len(filtered_ids) > max_nodes:
                connected = set()
                for rel in relations:
                    sid = rel.get("source_id", "")
                    tid = rel.get("target_id", "")
                    if sid in filtered_ids and tid in filtered_ids:
                        connected.add(sid)
                        connected.add(tid)
                if len(connected) >= max_nodes:
                    filtered_ids = set(list(connected)[:max_nodes])
                else:
                    remaining = [eid for eid in filtered_ids if eid not in connected]
                    filtered_ids = connected | set(remaining[: max_nodes - len(connected)])

            nodes: list[dict[str, Any]] = []
            for eid in filtered_ids:
                entity = entities.get(eid)
                if not entity:
                    continue
                etype = entity.get("type") or "unknown"
                nodes.append(
                    {
                        "data": {
                            "id": str(eid),
                            "label": entity.get("name") or str(eid),
                            "type": etype,
                            "color": _TYPE_COLORS.get(etype, "#78909c"),
                        }
                    }
                )

            node_ids = {n["data"]["id"] for n in nodes}
            edges: list[dict[str, Any]] = []
            seen_edge_ids: set[str] = set()
            for rel in relations:
                sid = rel.get("source_id", "")
                tid = rel.get("target_id", "")
                if sid not in node_ids or tid not in node_ids:
                    continue
                rel_type = rel.get("relation_type") or "related_to"
                edge_id = f"{sid}|{tid}|{rel_type}"
                if edge_id in seen_edge_ids:
                    continue
                seen_edge_ids.add(edge_id)
                edges.append(
                    {
                        "data": {
                            "id": edge_id,
                            "source": sid,
                            "target": tid,
                            "label": rel_type,
                        }
                    }
                )

            return {"nodes": nodes, "edges": edges}

        try:
            return await asyncio.to_thread(_build)
        except Exception:
            return degrade(
                logger,
                CODE_MEM_DATA,
                "knowledge.graph_for_viz failed",
                fallback={"nodes": [], "edges": []},
            )

    registry.register_with_schema(
        "knowledge.graph_for_viz",
        knowledge_graph_for_viz,
        schema={"properties": {"entity_type": {"type": "string"}, "max_nodes": {"type": "integer"}, "query": {"type": "string"}}, "type": "object"},
    )

    # ── knowledge.traverse (从起点实体 BFS 遍历，返回发现的实体+关系) ──
    # 前端 graphService.ts expandBackendNode 调用，期望返回 {entities: [...], relations: [...]}
    async def knowledge_traverse(
        start_id: str = "",
        max_hops: int = 1,
        relation_types: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """从起点实体进行 BFS 遍历，返回发现的实体和关系。

        Args:
            start_id: 起点实体 ID
            max_hops: 最大遍历深度，默认 1
            relation_types: 可选，只遍历指定类型的关系

        Returns:
            {"entities": [...], "relations": [...]}
        """
        if not start_id:
            return {"entities": [], "relations": []}
        kg = getattr(context, "knowledge_graph_store", None)
        if not kg:
            return {"entities": [], "relations": []}
        try:
            # graph_store.traverse 返回路径列表 [[entity, relation, entity, ...], ...]
            # 需展平为独立的 entities + relations 列表
            paths = await asyncio.to_thread(kg.traverse, start_id, max_hops, relation_types)
            entity_map: dict[str, dict[str, Any]] = {}
            relations: list[dict[str, Any]] = []
            seen_rel_keys: set[tuple] = set()
            for path in paths:
                for item in path:
                    if not isinstance(item, dict):
                        continue
                    # 实体：有 id 字段；关系：有 source_id/target_id 字段
                    if "source_id" in item and "target_id" in item:
                        key = (item.get("source_id", ""), item.get("target_id", ""), item.get("relation_type", ""))
                        if key not in seen_rel_keys:
                            seen_rel_keys.add(key)
                            relations.append(item)
                    elif "id" in item:
                        eid = str(item.get("id", ""))
                        if eid and eid not in entity_map:
                            entity_map[eid] = item

            # 也把起点实体包含进来
            def _get_start():
                kg.load()
                return getattr(kg, "_entities", {}).get(start_id)

            start_entity = await asyncio.to_thread(_get_start)
            if start_entity and str(start_entity.get("id", "")) not in entity_map:
                entity_map[str(start_entity.get("id", ""))] = start_entity
            return {"entities": list(entity_map.values()), "relations": relations}
        except Exception:
            return degrade(
                logger,
                CODE_MEM_DATA,
                "knowledge.traverse failed",
                fallback={"entities": [], "relations": []},
            )

    registry.register_with_schema(
        "knowledge.traverse",
        knowledge_traverse,
        schema={
            "properties": {
                "max_hops": {"type": "integer"},
                "relation_types": {"items": {"type": "string"}, "type": "array"},
                "start_id": {"type": "string"},
            },
            "type": "object",
        },
    )

    # ── knowledge.cards / knowledge.mocs / knowledge.synthesize / knowledge.brief ──
    # 知识归纳链路（knowledge_synthesis.py）：原子知识卡片 + 主题 MOC + 知识简报
    async def knowledge_cards(days: int = 0, **kwargs: Any) -> list[dict[str, Any]]:
        """返回知识卡片（可按最近 N 天过滤）。"""
        try:
            from services.knowledge_synthesis import list_knowledge_cards

            return await asyncio.to_thread(list_knowledge_cards, context, days)
        except Exception:
            return degrade(logger, CODE_MEM_DATA, "knowledge.cards failed", fallback=[])

    registry.register_with_schema(
        "knowledge.cards",
        knowledge_cards,
        schema={"properties": {"days": {"type": "integer"}}, "type": "object"},
    )

    async def knowledge_mocs(**kwargs: Any) -> list[dict[str, Any]]:
        """返回主题 MOC（知识地图），按卡片数降序。"""
        try:
            from services.knowledge_synthesis import list_topic_mocs

            return await asyncio.to_thread(list_topic_mocs, context)
        except Exception:
            return degrade(logger, CODE_MEM_DATA, "knowledge.mocs failed", fallback=[])

    registry.register_with_schema(
        "knowledge.mocs",
        knowledge_mocs,
        schema={"properties": {}, "type": "object"},
    )

    async def knowledge_synthesize(max_topics: int = 12, **kwargs: Any) -> dict[str, Any]:
        """触发知识归纳：把卡片聚类成主题 MOC 并回写图谱主题。"""
        try:
            from services.knowledge_synthesis import build_topic_mocs

            return await asyncio.to_thread(build_topic_mocs, context, max_topics)
        except Exception as exc:
            logger.warning("knowledge.synthesize failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register_with_schema(
        "knowledge.synthesize",
        knowledge_synthesize,
        schema={"properties": {"max_topics": {"type": "integer"}}, "type": "object"},
    )

    async def knowledge_brief(days: int = 7, **kwargs: Any) -> dict[str, Any]:
        """生成最近 N 天新增知识的可读 Markdown 简报（同步返回终局结果）。

        S3-2 回退（2026-09-16 架构复审，audit §1）：入队信封版本 RPC 秒回
        job_id，但 knowledge.brief 前端**零消费者**（简报走 proactive 事件推
        送，不经此 RPC 拉取），信封化属无消费方的死改动，且与其余五处同批引
        入契约风险 → 回退同步直调 run_knowledge_brief。异步骨架
        （worker/.status/completed 事件）保留待 S3-2b。
        """
        return await run_knowledge_brief(context, days=days)

    async def knowledge_brief_status(
        job_id: str = "",
        task_id: str = "",
        wait_seconds: float = 0.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """查询 knowledge.brief 异步任务的状态与结果。"""
        return await _poll_job_result(
            context,
            job_id=job_id,
            task_id=task_id,
            capability_id="knowledge.brief",
            wait_seconds=wait_seconds,
        )

    registry.register_with_schema(
        "knowledge.brief",
        knowledge_brief,
        schema={"properties": {"days": {"type": "integer"}}, "type": "object"},
    )
    registry.register_with_schema(
        "knowledge.brief.status",
        knowledge_brief_status,
        schema={
            "properties": {
                "job_id": {"type": "string"},
                "task_id": {"type": "string"},
                "wait_seconds": {"type": "number"},
            },
            "type": "object",
        },
    )

    # ── knowledge.md_* ：MD 内容层（知识库文件）读写 ──
    async def knowledge_md_export(max_conversations: int = 0, max_graph_entities: int = 400, **kwargs: Any) -> dict[str, Any]:
        """把当前沉淀数据全量导出为知识库 MD 文件（幂等重建）。"""
        try:
            from knowledge.md_exporter import export_all

            return await asyncio.to_thread(
                export_all,
                context,
                max_conversations=int(max_conversations or 0),
                max_graph_entities=int(max_graph_entities or 400),
            )
        except Exception as exc:
            logger.warning("knowledge.md_export failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register_with_schema(
        "knowledge.md_export",
        knowledge_md_export,
        schema={"properties": {"max_conversations": {"type": "integer"}, "max_graph_entities": {"type": "integer"}}, "type": "object"},
    )

    async def knowledge_md_list(**kwargs: Any) -> dict[str, Any]:
        """列出知识库文件树（时间轴 + 主题轴），含基础统计。"""
        try:
            from knowledge.md_store import MDKnowledgeStore

            store = getattr(context, "local_data_store", None)
            if store is None:
                return {"success": False, "error": "local_data_store not configured"}
            md = MDKnowledgeStore(store.base_dir)
            files = md.list_files()
            return {"success": True, "root": md.root, "files": files, "count": len(files)}
        except Exception as exc:
            logger.warning("knowledge.md_list failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register_with_schema(
        "knowledge.md_list",
        knowledge_md_list,
        schema={"properties": {}, "type": "object"},
    )

    async def knowledge_md_get(rel: str = "", **kwargs: Any) -> dict[str, Any]:
        """读取单个 MD 文件（含 frontmatter）。"""
        try:
            from knowledge.md_store import MDKnowledgeStore, parse_frontmatter

            store = getattr(context, "local_data_store", None)
            if store is None:
                return {"success": False, "error": "local_data_store not configured"}
            md = MDKnowledgeStore(store.base_dir)
            content = md.read(rel)
            if content is None:
                return {"success": False, "error": f"file not found: {rel}"}
            meta, body = parse_frontmatter(content)
            return {"success": True, "rel": rel, "meta": meta, "content": content}
        except Exception as exc:
            logger.warning("knowledge.md_get failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register_with_schema(
        "knowledge.md_get",
        knowledge_md_get,
        schema={"properties": {"rel": {"type": "string"}}, "type": "object"},
    )

    async def knowledge_md_save(rel: str = "", content: str = "", **kwargs: Any) -> dict[str, Any]:
        """保存用户改写后的 MD 文件，并触发反向解析回灌（记忆/图谱）。"""
        try:
            from knowledge.md_store import MDKnowledgeStore

            store = getattr(context, "local_data_store", None)
            if store is None:
                return {"success": False, "error": "local_data_store not configured"}
            md = MDKnowledgeStore(store.base_dir)
            path = md.write(rel, content or "")
            # 阶段三：改写后回灌记忆/图谱（md_parser 反向同步，幂等）
            sync: dict[str, Any] = {"success": False, "error": "md_parser unavailable", "skipped": True}
            try:
                from knowledge.md_parser import sync_md_to_backend

                sync = await asyncio.to_thread(sync_md_to_backend, context, rel, content or "")
            except Exception as exc:
                logger.warning("knowledge.md_save 回灌失败（已保存文件）: %s", exc)
                sync = {"success": False, "error": str(exc)}
            return {"success": True, "rel": rel, "path": path, "sync": sync}
        except Exception as exc:
            logger.warning("knowledge.md_save failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register_with_schema(
        "knowledge.md_save",
        knowledge_md_save,
        schema={"properties": {"content": {"type": "string"}, "rel": {"type": "string"}}, "type": "object"},
    )

    async def knowledge_md_sync(rel: str = "", **kwargs: Any) -> dict[str, Any]:
        """重新解析指定 MD 文件并回灌记忆/图谱（幂等；用于改写后重放）。"""
        try:
            from knowledge.md_store import MDKnowledgeStore

            store = getattr(context, "local_data_store", None)
            if store is None:
                return {"success": False, "error": "local_data_store not configured"}
            md = MDKnowledgeStore(store.base_dir)
            content = md.read(rel)
            if content is None:
                return {"success": False, "error": f"file not found: {rel}"}
            from knowledge.md_parser import sync_md_to_backend

            return await asyncio.to_thread(sync_md_to_backend, context, rel, content)
        except Exception as exc:
            logger.warning("knowledge.md_sync failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register_with_schema(
        "knowledge.md_sync",
        knowledge_md_sync,
        schema={"properties": {"rel": {"type": "string"}}, "type": "object"},
    )

    async def knowledge_md_stats(**kwargs: Any) -> dict[str, Any]:
        """知识库统计：文件数、按年/月/日分布、卡片/MOC 数。"""
        try:
            from knowledge.md_store import MDKnowledgeStore, extract_date

            store = getattr(context, "local_data_store", None)
            if store is None:
                return {"success": False, "error": "local_data_store not configured"}
            md = MDKnowledgeStore(store.base_dir)
            files = md.list_files()
            by_year: dict[str, int] = {}
            by_month: dict[str, int] = {}
            cards = mocs = 0
            for rel in files:
                if rel.startswith("主题/") and "/卡片-" in rel:
                    cards += 1
                elif rel.endswith("/MOC.md"):
                    mocs += 1
                d = extract_date(rel)
                if d is not None:
                    y, m = f"{d[0]:04d}", f"{d[0]:04d}-{d[1]:02d}"
                    by_year[y] = by_year.get(y, 0) + 1
                    by_month[m] = by_month.get(m, 0) + 1
            return {
                "success": True,
                "root": md.root,
                "total_files": len(files),
                "cards": cards,
                "mocs": mocs,
                "by_year": dict(sorted(by_year.items())),
                "by_month": dict(sorted(by_month.items())),
            }
        except Exception as exc:
            logger.warning("knowledge.md_stats failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register_with_schema(
        "knowledge.md_stats",
        knowledge_md_stats,
        schema={"properties": {}, "type": "object"},
    )
