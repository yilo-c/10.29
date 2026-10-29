"""Handler registration — delegates to domain modules (notes, tasks, chat, ai, inbox, settings, wordbook, api_keys).

Usage::

    from backend.handlers import register_all
    register_all(registry, context, ws_port=18765)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.handlers import _info_items, _info_proxy
from backend.method_registry import is_r4_approved_replay
from app_runtime.app_context import AppContext

logger = logging.getLogger("siyi.backend.handlers")

# ── V2.1 批次2：R1-R4 风险分级集中标注 ─────────────────────────
# R4 清单（强制 sandbox 审批）统一在此维护：删除类 / 凭证写 / 工具注册 /
# provider 写 / 审计回滚 / 进程终止 / 同步冲突解决 / 学习数据回滚。
# 未列出的方法保持默认 R3（保守缺省）。见 test_p0_6_delete_r4_gating.py
# （test_no_delete_method_unclassified 防回退：源码任何删除类注册名须落于此清单）。
# 2026-08-26：由 register_all 内局部变量提升为模块级常量，便于回归测试直接引用。
_R4_METHODS = {
    # ── 删除类 ──
    "notes.delete",
    "notes.batch_delete",
    "notes.permanent_delete",
    "tasks.delete",
    "wordbook.delete",
    "word.delete",
    "transfer.delete",
    "reading.delete",
    "reading.delete_article",
    "reminder.delete",
    "chat.delete_conversation",
    "chat.feedback.delete",
    # 2026-09-17：memory.delete（U3 软删/revoke，_chat_memory.py 注册）命中
    # .delete 门禁正则但未入集中清单 → 防回退断言 FAIL 实锤。虽为可恢复软删，
    # 按项目规则「删除语义名须显式归类」纳入 R4，与 notes/word 等软删同级。
    "memory.delete",
    # 2026-09-18 批4（P2-PD-NODEL）：知识图谱实体删除 RPC 此前**根本不存在**
    # （store 有 remove_entity，但无任何 RPC 暴露）。新增即入 R4 清单——
    # 删除图谱实体不可逆，与 notes/word 同级必须过 sandbox 审批。
    "knowledge.entity.delete",
    # 2026-09-21（P3-KG-MERGE）：实体归并不在「.delete/.remove」正则口径内，但
    # 它同样不可逆（source 节点消失，名字降为别名）→ 与 delete 同级人工审批。
    # 守卫正则已同步收紧（\.merge$），防未来新增 merge 类 handler 漏网。
    "knowledge.entity.merge",
    "info.sections.delete",
    "info.sources.remove",
    "info.sources.reset",
    "notification.delete",
    # ── 调度 / 队列 / 用量 删除类（P0-6 补：原集中清单漏项，曾绕 sandbox）──
    "cron.remove",
    "task_queue.purge_completed",
    "usage.purge",
    # ── 审计回滚 / 进程终止 ──
    "audit.rollback",
    "audit.batch_rollback",
    "process.terminate",
    # ── 凭证（api_keys）写 ──
    "api_keys.create",
    "api_keys.update",
    "api_keys.delete",
    "api_keys.rotation.add_backup",
    "api_keys.rotation.remove_key",
    "api_keys.rotation.rotate",
    "api_keys.rotation.set_policy",
    # ── 工具注册 / 卸载 ──
    "tool.register",
    "tool.unregister",
    # ── provider 写 / 路由写 ──
    "provider.add",
    "provider.remove",
    "provider.update",
    "provider.routing.set",
    # ── 同步冲突解决（覆盖本地数据）──
    "sync.resolve_conflict",
    # ── 学习数据回滚 ──
    "vocab.rollback_fsrs",
    # ── 审批历史清空（AG-C1③；\.clear$ 删除类防回退守卫要求入清单）──
    "sandbox.history.clear",
    # 2026-09-17 R2-B6：file.write 纳入 R4 → 39→40（D6：此前不在集中清单，
    # 绕过 sandbox 审批通路；写面为破坏性操作，与 process.terminate 同级强制审批）。
    "file.write",
}


def _write_path_rejection(router, path: str) -> str | None:
    """D6（2026-09-17）：写路径合法性先行判定，返回拒绝原因；None 表示在白名单根内。

    file.write capability 的白名单解析器（FileCapabilityDomain._resolve_write_path）
    与真正落盘用的是同一份实现 —— 此处提前调用，使「越界路径」在**任何**执行/高危
    标签判定之前就给出明确原因（path_outside_allowed_root / workspace_write_blocked），
    不再被 broker 的 llm_source_high_risk 掩盖成「LLM 高危」。
    """
    domain = getattr(router, "file_domain", None)
    resolver = getattr(domain, "_resolve_write_path", None)
    if resolver is None:
        return None
    try:
        resolver(path)
    except ValueError as exc:
        return str(exc)
    except Exception:  # noqa: BLE001 — 解析器异常不阻断，交由执行层给结构化错误
        logger.debug("file.write 路径预检异常", exc_info=True)
        return None
    return None


async def register_all(registry, context: AppContext, ws_port: int = 18765) -> None:
    """Register all JSON-RPC method handlers from domain modules."""
    from services.api_key_service import ApiKeyService

    _data_dir = getattr(getattr(context, "local_data_store", None), "base_dir", None)
    ak_service = ApiKeyService(context.settings_repository, data_dir=_data_dir)

    from backend.handlers import (
        _agent_tasks,
        _ai,
        _ai_chat,
        _api_keys,
        _audit,
        _capabilities,
        _chat_memory,
        _clipboard,
        _confirmations,
        _cron,
        _dictation,
        _evolution,
        _event_client,
        _external_content,
        _feedback,
        _inbox_data,
        _info_sources,
        _job_hunter,
        _kernel,
        _maintenance,
        _modules_status,
        _notes,
        _notifications,
        _ocr,
        _personal,
        _process,
        _provider_health,
        _reminder,
        _reading,
        _remote,
        _rss,
        _sandbox,
        _skin,
        _sync,
        _system_settings,
        _tasks,
        _task_queue,
        _timeline,
        _tools,
        _trace,
        _trae,
        _transfer,
        _transcription,
        _url_learning,
        _weather,
        _word_extract,
        _wordbook,
    )

    _ai.register_ai(registry, context)
    _ai_chat.register_ai_chat(registry, context)
    _notes.register_notes(registry, context)
    _tasks.register_tasks(registry, context)
    _chat_memory.register_chat_memory(registry, context)
    _clipboard.register_clipboard(registry, context)  # 第二档 #4：复制即候选上下文
    _confirmations.register_confirmations(registry, context)
    _inbox_data.register_inbox_data(registry, context)
    _job_hunter.register_job_hunter(registry, context)
    _ocr.register_ocr(registry, context)
    _process.register_process(registry, context)
    _reading.register_reading(registry, context)
    _external_content.register_external_content(registry, context)
    _reminder.register_reminder(registry, context)
    _sandbox.register_sandbox(registry, context)
    _system_settings.register_system_settings(registry, context, ws_port, ak_service)
    _url_learning.register_url_learning(registry, context)
    _word_extract.register_word_extract(registry, context)
    _wordbook.register_wordbook(registry, context)
    _dictation.register_dictation(registry, context)
    _api_keys.register_api_keys(registry, context, ak_service)
    _transfer.register_transfer(registry, context)
    _event_client.register_event_client(registry, context)  # 第二档 #5：前端事件上报 RPC
    _transcription.register_transcription(registry, context)  # P2.1 本地音频转写
    _info_proxy.register_info_proxy(registry, context)
    await _info_items.register(registry, context)
    _capabilities.register_capabilities(registry, context)
    _weather.register_weather(registry, context)
    _sync.register_sync(registry, context)
    _agent_tasks.register_agent_tasks(registry, context)
    _cron.register_cron(registry, context)
    _task_queue.register_task_queue(registry, context)  # P1.2 持久任务队列
    _timeline.register_timeline(registry, context)  # P2.4 时间线聚合
    _trace.register_trace(registry, context)  # P1-1/P1-2 执行观测与断点续跑
    _maintenance.register_maintenance(registry, context)  # P2-7 自维护提案审批面
    _remote.register_remote(registry, context)
    _evolution.register_evolution(registry, context)
    _trae.register_trae(registry, context)
    _tools.register_tools(registry, context)
    _kernel.register_kernel(registry, context)  # Flip: 执行内核状态（徽章数据源）
    _audit.register_audit(registry, context)  # S15: 操作审计日志
    _feedback.register_feedback(registry, context)  # P2.8 用户反馈学习闭环
    _notifications.register_notifications(registry, context)  # P2.9 通知推送通道
    _provider_health.register_provider_health(registry, context)  # B2: Provider 健康监控
    await _info_sources.register(registry, context)  # C1: 资讯源配置管理
    await _rss.register(registry, context)  # C3: RSS/Atom feed 订阅管理
    _skin.register_skin(registry, context)  # 皮肤插件：skin.list / skin.active / skin.activate
    _modules_status.register_modules_status(registry, context)  # U4: modules.status 模块状态探针

    from backend.handlers import _narrative as _narrative_module

    _narrative_module.register_narrative(registry, context)
    _personal.register_personal(registry, context)  # 个人知识体系：画像 + raw items

    # ── daily.get (每日记录) ──
    async def daily_get(date: str = "", **kwargs: Any) -> dict[str, Any]:
        """获取或创建指定日期的日记。date 格式 YYYY-MM-DD，默认今天。"""
        import datetime

        if not date:
            date = datetime.date.today().isoformat()
        note_id = f"_daily_{date}"
        repo = getattr(context, "personal_note_repository", None)
        if not repo:
            return {"error": "notes repository unavailable"}
        # P2 修复：使用 get_note 按 ID 直接查找，避免全量 list_notes 的 O(n) 扫描
        existing = await asyncio.to_thread(repo.get_note, note_id)
        if existing:
            return {"note": existing, "date": date}
        new_note: dict[str, Any] = {
            "id": note_id,
            # 稳定 remote_id：add_note 以它作为 desk-notes 文件名，使同一天日记可被 get_note 找回（避免重复创建）
            "remote_id": note_id,
            "title": f"记录 {date}",
            "content": "",
            "tags": ["日记"],
        }
        await asyncio.to_thread(repo.add_note, new_note)
        return {"note": new_note, "date": date, "created": True}

    registry.register("daily.get", daily_get)

    # ── daily.append (追加内容到今日记录) ──
    async def daily_append(content: str = "", source: str = "", date: str = "", **kwargs: Any) -> dict[str, Any]:
        """追加内容到指定日期的日记底部。"""
        if not content.strip():
            return {"error": "content required"}
        import datetime

        if not date:
            date = datetime.date.today().isoformat()
        note_id = f"_daily_{date}"
        repo = getattr(context, "personal_note_repository", None)
        if not repo:
            return {"error": "notes repository unavailable"}
        await daily_get(date=date)
        now_str = datetime.datetime.now().strftime("%H:%M")
        source_tag = f" [{source}]" if source else ""
        append_text = f"\n\n---\n**{now_str}{source_tag}**\n{content.strip()}"
        # P2 修复：使用 get_note 按 ID 直接查找，避免全量 list_notes 的 O(n) 扫描
        found = await asyncio.to_thread(repo.get_note, note_id)
        existing = dict(found) if found else None
        if existing:
            existing["content"] = (existing.get("content") or "") + append_text
            existing["updated_at"] = datetime.datetime.now().isoformat()
            await asyncio.to_thread(repo.update_note, note_id, existing)
            return {"success": True, "date": date}
        return {"error": "daily note not found"}

    registry.register("daily.append", daily_append)

    # ── data.import_chat (导入聊天记录) ──
    async def data_import_chat(
        file_path: str = "",
        format: str = "",  # 可选: chatgpt/claude/kimi/markdown
        max_conv: int = 50,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """导入 ChatGPT/Claude/Kimi/豆包 聊天记录到知识系统。

        Args:
            file_path: 聊天记录文件路径。支持绝对路径，或相对于 transfer_files 的
                相对路径/文件名（方便手机端上传后导入）。
            format: 强制指定格式，留空则按文件名自动探测。
            max_conv: 最多导入多少条对话。
        """
        import os

        from integrations.chat_importer import import_chat_file

        # 手机端通过 transfer.upload 上传的文件位于 data/transfer_files/，
        # 这里允许直接传文件名或相对路径。
        resolved_path = file_path
        if resolved_path and not os.path.isabs(resolved_path):
            ds = getattr(context, "local_data_store", None)
            base_dir = getattr(ds, "base_dir", None) if ds else None
            if base_dir:
                candidate = os.path.join(base_dir, "transfer_files", os.path.basename(resolved_path))
                if os.path.isfile(candidate):
                    resolved_path = candidate

        # B-02: 任意路径读取旁路封堵 —— 绝对路径分支必须落在白名单内
        # （应用数据目录 / Documents / Downloads / Desktop）。transfer_files
        # 相对路径解析结果位于 data/base_dir 内，守卫天然放行。
        if resolved_path:
            from backend.handlers._path_guard import is_within_import_whitelist

            if not is_within_import_whitelist(resolved_path, context):
                return {
                    "success": False,
                    "error": "文件不在允许导入的白名单内（仅限应用数据目录 / Documents / Downloads / Desktop）",
                }

        mem_extractor = getattr(context, "memory_extractor", None)
        ai = getattr(context, "ai_client", None)
        chat_repo = getattr(context, "chat_session_repository", None)
        mem_index = getattr(context, "memory_index_service", None)
        raw_item_repo = getattr(context, "raw_item_repo", None)
        # ★ 修复：import_chat_file 是同步函数（含文件读取/AI调用/存储），
        # 直接在 async handler 中调用会阻塞事件循环，导致其他 WebSocket
        # 客户端全部卡住直到导入完成。包裹 to_thread 释放事件循环。
        return await asyncio.to_thread(
            import_chat_file,
            resolved_path,
            ai_client=ai,
            memory_extractor=mem_extractor,
            chat_repo=chat_repo,
            memory_index_service=mem_index,
            raw_item_repo=raw_item_repo,
            format_hint=format,
            max_conversations=max_conv,
        )

    registry.register("data.import_chat", data_import_chat)

    # ── data.import_chat_folder (批量导入目录) ──
    async def data_import_chat_folder(
        folder_path: str = "",
        format: str = "",
        max_conv: int = 50,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """批量导入目录下所有支持的聊天记录文件。"""
        from integrations.chat_importer import import_chat_file
        import os

        # B-02: 任意路径读取旁路封堵 —— 目录导入必须落在白名单内
        # （应用数据目录 / Documents / Downloads / Desktop），否则拒绝。
        # 远程已认证客户端可借无边界导入枚举并回读桌面任意目录文件。
        from backend.handlers._path_guard import is_within_import_whitelist

        if not folder_path:
            return {"success": False, "error": "目录路径为空"}
        if not is_within_import_whitelist(folder_path, context):
            return {
                "success": False,
                "error": "目录不在允许导入的白名单内（仅限应用数据目录 / Documents / Downloads / Desktop）",
            }
        if not os.path.isdir(folder_path):
            return {"success": False, "error": "目录不存在"}
        mem_extractor = getattr(context, "memory_extractor", None)
        ai = getattr(context, "ai_client", None)
        chat_repo = getattr(context, "chat_session_repository", None)
        mem_index = getattr(context, "memory_index_service", None)
        raw_item_repo = getattr(context, "raw_item_repo", None)

        # 收集待导入文件（json/md 各 AI 导出 + txt/csv 微信导出）
        file_list: list[tuple[str, str]] = []  # (fname, fpath)
        for fname in os.listdir(folder_path):
            if not fname.endswith((".json", ".md", ".txt", ".csv")):
                continue
            fpath = os.path.join(folder_path, fname)
            if os.path.isfile(fpath):
                file_list.append((fname, fpath))

        total: dict[str, Any] = {
            "success": True,
            "total": 0,
            "imported": 0,
            "skipped": 0,
            "categories": {},
            "sessions_saved": 0,
            "memories_stored": 0,
            "files": [],
        }

        # P2 优化：使用 Semaphore(3) 并发导入，替代串行处理
        sem = asyncio.Semaphore(3)

        async def _import_one(fname: str, fpath: str) -> dict[str, Any]:
            async with sem:
                return await asyncio.to_thread(
                    import_chat_file,
                    fpath,
                    ai_client=ai,
                    memory_extractor=mem_extractor,
                    chat_repo=chat_repo,
                    memory_index_service=mem_index,
                    raw_item_repo=raw_item_repo,
                    format_hint=format,
                    max_conversations=max_conv,
                )

        results = await asyncio.gather(
            *[_import_one(fname, fpath) for fname, fpath in file_list],
            return_exceptions=True,
        )

        for file_item, result in zip(file_list, results):
            if isinstance(result, Exception):
                # 审查修复：记录导入失败异常，便于排查文件格式/解析问题
                logger.warning(
                    "import_chat_file failed for %s: %s",
                    file_item[0],
                    result,
                    exc_info=True,
                )
                total["files"].append(
                    {
                        "file": file_item[0],
                        "success": False,
                        "imported": 0,
                        "sessions_saved": 0,
                        "memories_stored": 0,
                    }
                )
                continue
            if not isinstance(result, dict):
                # gather(return_exceptions=True) 正常情况下 result 为 dict 或异常；
                # 防御非 dict 非 Exception 的意外返回，避免下方对 result 做 .get 时崩溃。
                logger.warning("import_chat_file returned unexpected non-dict for %s", file_item[0])
                continue
            total["total"] += result.get("total", 0)
            total["imported"] += result.get("imported", 0)
            total["skipped"] += result.get("skipped", 0)
            total["sessions_saved"] += result.get("sessions_saved", 0)
            total["memories_stored"] += result.get("memories_stored", 0)
            for cat, cnt in result.get("categories", {}).items():
                total["categories"][cat] = total["categories"].get(cat, 0) + cnt
            total["files"].append(
                {
                    "file": file_item[0],
                    "success": result.get("success", False),
                    "imported": result.get("imported", 0),
                    "sessions_saved": result.get("sessions_saved", 0),
                    "memories_stored": result.get("memories_stored", 0),
                }
            )
        return total

    registry.register("data.import_chat_folder", data_import_chat_folder)

    # ── kimi.sync_once (Kimi 对话自动同步) ──
    async def kimi_sync_once(max_chats: int = 50, **kwargs: Any) -> dict[str, Any]:
        """从 Kimi 网页端自动同步对话到思忆集。"""
        from integrations.kimi_sync import KimiConnectClient, KimiSyncService
        from core.settings_keys import (
            KEY_KIMI_AUTH_TOKEN,
            KEY_KIMI_BASE_URL,
            KEY_KIMI_DEVICE_ID,
            KEY_KIMI_LANGUAGE,
            KEY_KIMI_PLATFORM,
            KEY_KIMI_REFRESH_TOKEN,
            KEY_KIMI_SESSION_ID,
            KEY_KIMI_TRAFFIC_ID,
            KEY_KIMI_VERSION,
        )

        repo = getattr(context, "settings_repository", None)
        if repo is None:
            return {"success": False, "error": "settings_repository 不可用"}

        async def _get(key: str, default: Any = None) -> Any:
            return await asyncio.to_thread(repo.get_setting, key, default)

        # P2 优化：先并行读取 auth_token 与 refresh_token，再并行读取其余设置
        auth_token, refresh_token = await asyncio.gather(
            _get(KEY_KIMI_AUTH_TOKEN, ""),
            _get(KEY_KIMI_REFRESH_TOKEN, ""),
        )
        if not auth_token:
            return {"success": False, "error": "Kimi auth_token 未配置"}

        # 并行读取剩余 6 项设置
        base_url, device_id, session_id, platform, version, language, traffic_id = await asyncio.gather(
            _get(KEY_KIMI_BASE_URL, "https://www.kimi.com"),
            _get(KEY_KIMI_DEVICE_ID, ""),
            _get(KEY_KIMI_SESSION_ID, ""),
            _get(KEY_KIMI_PLATFORM, "web"),
            _get(KEY_KIMI_VERSION, "1.0.0"),
            _get(KEY_KIMI_LANGUAGE, "zh-CN"),
            _get(KEY_KIMI_TRAFFIC_ID, ""),
        )

        config = {
            "base_url": base_url,
            "auth_token": auth_token,
            "refresh_token": refresh_token,
            "device_id": device_id,
            "session_id": session_id,
            "platform": platform,
            "version": version,
            "language": language,
            "traffic_id": traffic_id,
        }

        async def _persist(access_token: str, new_refresh: str | None) -> None:
            # 刷新成功后把新 token 写回设置存储
            await asyncio.to_thread(repo.set_setting, KEY_KIMI_AUTH_TOKEN, access_token)
            if new_refresh:
                await asyncio.to_thread(repo.set_setting, KEY_KIMI_REFRESH_TOKEN, new_refresh)

        client = KimiConnectClient(config, persist=_persist)
        service = KimiSyncService(client, context)
        return await service.sync_once(max_chats=max_chats)

    registry.register("kimi.sync_once", kimi_sync_once)

    # ── knowledge.unified_query (统一知识查询) ──
    async def knowledge_unified_query(text: str = "", limit: int = 15, **kwargs: Any) -> dict[str, Any]:
        """统一查询记忆/知识图/RAG/笔记/词库/资讯。"""
        from knowledge.unified_service import UnifiedKnowledgeService

        mem = getattr(context, "memory_index_service", None)
        kg = getattr(context, "knowledge_graph_store", None)
        # R0-B20：修复属性名拼写变体——生产注入名为 personal_note_repository
        notes = getattr(context, "personal_note_repository", None)
        wb = getattr(context, "vocabulary_repository", None)
        info = getattr(context, "info_item_repository", None)
        service = UnifiedKnowledgeService(
            memory_search=mem,
            knowledge_graph=kg,
            rag_pipeline=None,
            note_service=notes,
            wordbook_repo=wb,
            info_item_repo=info,
        )
        # ★ 修复：service.query 是同步函数（含向量搜索/图遍历/文件I/O），
        # 直接调用会阻塞事件循环。包裹 to_thread 释放事件循环。
        return await asyncio.to_thread(service.query, text, limit=limit)

    registry.register_with_schema(
        "knowledge.unified_query",
        knowledge_unified_query,
        schema={"properties": {"limit": {"type": "integer"}, "text": {"type": "string"}}, "type": "object"},
    )

    # ── knowledge.ingest_conversation (对话入知识库) ──
    async def knowledge_ingest_conversation(
        session_id: int | str = "",
        max_messages: int = 200,
        auto_write: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """将指定会话的对话内容提取为记忆并写入知识库。"""
        chat_repo = getattr(context, "chat_session_repository", None)
        mem_extractor = getattr(context, "memory_extractor", None)
        mem_index = getattr(context, "memory_index_service", None)

        if not chat_repo:
            return {"error": "chat session repository unavailable"}
        if not mem_extractor:
            return {"error": "memory extractor unavailable"}
        if not mem_index:
            return {"error": "memory index service unavailable"}

        try:
            # PD-4（2026-09-17）：旧码 int(session_id) 只认数字，与 chat 系
            # （save_messages 等）接受的 UUID client_id 两 id 空间不接合 →
            # 前端传 UUID 恒 "invalid session_id"。复用 _chat_memory 的解析器：
            # 优先 sessions_index 匹配 client_id，数字串直转，其余 sha1 稳定映射
            # （与 save_messages 写入侧同一函数，保证读到同一会话）。
            from backend.handlers._chat_memory import _resolve_session_id

            sid, _client = _resolve_session_id(chat_repo, session_id)
        except (ValueError, TypeError):
            sid = None
        if sid is None:
            return {"error": "invalid session_id"}

        messages = await asyncio.to_thread(chat_repo.get_recent_messages, sid, count=max_messages)
        if not messages:
            return {"error": "no messages found", "session_id": sid}

        normalized = []
        for msg in messages:
            speaker = str(msg.get("speaker") or msg.get("role") or "").lower()
            text = str(msg.get("message") or msg.get("content") or "").strip()
            if not text:
                continue
            role = "user" if speaker in ("user", "human") else "assistant"
            normalized.append({"role": role, "content": text})

        if not normalized:
            return {"error": "no valid messages", "session_id": sid}

        def _extract():
            return mem_extractor.extract(
                normalized,
                conversation_id=str(sid),
            )

        result = await asyncio.to_thread(_extract)

        stored = 0
        if auto_write:
            # 串行写入：index_memory 内部执行 load_json → append → save_json 的
            # read-modify-write，并行调用会导致数据覆盖丢失记忆条目
            for candidate in result.auto_write:
                entry = await asyncio.to_thread(mem_index.index_memory, candidate.to_dict())
                if entry:
                    stored += 1

        return {
            "session_id": sid,
            "message_count": len(normalized),
            "extracted": len(result.candidates),
            "auto_write": len(result.auto_write),
            "stored": stored,
            "duplicates": len(result.duplicates),
            "merged": len(result.merged),
            "conflicts": len(result.conflicts),
            "needs_confirm": len(result.needs_confirm),
            "discarded": len(result.discarded),
        }

    registry.register_with_schema(
        "knowledge.ingest_conversation",
        knowledge_ingest_conversation,
        schema={
            "properties": {"auto_write": {"type": "boolean"}, "max_messages": {"type": "integer"}, "session_id": {"type": ["integer", "string"]}},
            "type": "object",
        },
    )

    # ── knowledge.organize (主动整理知识体系) ──
    async def knowledge_organize(
        memory_limit: int = 120,
        days: int | None = 30,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """主动整理个人知识体系，返回 AI 分析报告。"""
        from services.knowledge_organizer import KnowledgeOrganizer

        organizer = KnowledgeOrganizer(
            ai_client=context.ai_client,
            memory_index_service=getattr(context, "memory_index_service", None),
            structured_memory_store=getattr(context, "structured_memory_store", None),
            user_profile_service=getattr(context, "user_profile_service", None),
            ai_text_service=getattr(context, "ai_text_service", None),
        )

        def _run():
            return organizer.organize(memory_limit=memory_limit, days=days)

        return await asyncio.to_thread(_run)

    registry.register_with_schema(
        "knowledge.organize",
        knowledge_organize,
        schema={"properties": {"days": {"type": "integer"}, "memory_limit": {"type": "integer"}}, "type": "object"},
    )

    # ── File operations (capability-backed RPC) ──
    def _capability_router():
        """解析能力路由：真实挂载点是 autonomous_assistant_manager.router，
        context.capability_router 仅作兼容兜底。"""
        cap = getattr(context, "capability_router", None)
        if cap is not None and hasattr(cap, "execute_direct"):
            return cap
        router = getattr(getattr(context, "autonomous_assistant_manager", None), "router", None)
        if router is not None and hasattr(router, "execute_direct"):
            return router
        return None

    async def file_read(path: str = "", **kwargs: Any) -> dict[str, Any]:
        """读取文件内容。通过 capability_router 执行（支持沙箱/权限控制）。"""
        if not path:
            return {"error": "path required"}
        router = _capability_router()
        if router:
            try:
                return await router.execute_direct("file.read", {"path": path})
            except Exception as exc:
                logger.exception("远程读文件失败")
                return {"error": str(exc)}
        return {"error": "capability_router not available"}

    registry.register("file.read", file_read)

    async def file_write(path: str = "", content: str = "", **kwargs: Any) -> dict[str, Any]:
        """写入文件（破坏性操作）。

        D6（2026-09-17）修复：
        ① 路径守卫先行 —— 白名单根外的路径直接返回明确原因，不再被 broker 的
           llm_source_high_risk 高危标签先行拦下（合法根内路径此前也被拒 → 写面
           整体坏死）；
        ② file.write 已纳入 _R4_METHODS：未审批调用在 dispatch 层入 sandbox 审批
           队列（返回 pending，不执行）；批准后由 sandbox.approve 以
           skip_approval=True 重放。此处据 is_r4_approved_replay() 把**真实发生过
           的**人工授权传给 broker，而不是无条件 approved=True 免审。
        """
        if not path:
            return {"error": "path required"}
        router = _capability_router()
        if not router:
            return {"error": "capability_router not available"}
        rejection = _write_path_rejection(router, path)
        if rejection is not None:
            return {
                "success": False,
                "error": f"path rejected: {rejection}",
                "reason": rejection,
                "path": path,
            }
        try:
            return await router.execute_direct(
                "file.write",
                {"path": path, "content": content},
                approved=is_r4_approved_replay(),
            )
        except Exception as exc:
            logger.exception("远程写文件失败")
            return {"error": str(exc)}

    async def file_find(pattern: str = "", root: str = ".", **kwargs: Any) -> dict[str, Any]:
        """搜索匹配 pattern 的文件。"""
        if not pattern:
            return {"error": "pattern required"}
        router = _capability_router()
        if router:
            try:
                return await router.execute_direct("file.find", {"pattern": pattern, "root": root})
            except Exception as exc:
                logger.exception("远程查找文件失败")
                return {"error": str(exc)}
        return {"error": "capability_router not available"}

    registry.register("file.find", file_find)

    # 2026-10-15 恢复：file.read / file.find 经全族复核重新注册——58e5fa8（09-23
    # 波次①-c）以「前端零调用、无测试耦合」下线，实测该判定不成立（前端文件面板
    # 在调、测试面 81 处引用），且连带删走了测试造成「无耦合」假象。同批恢复的
    # 还有 evolution×7 / api_keys.rotation×7 / agent×5 / trae×7 等共 59 个。
    # file.write 保留：它是 _R4_METHODS 集中清单的 R4 sandbox 规范样本，配有真实的
    # register_precheck（越界路径静态预检 → -32602，DoS 驱逐防护），并被
    # test_adjudication_r23 / test_r2b_transfer_r4 / test_r2_verify 三套安全回归测试
    # 用作参考方法。非纯僵尸，本波不下线，遗留 lead 决策（详见审计波次①-c 报告）。
    registry.register("file.write", file_write)

    # 裁决 2（2026-09-17 F-P2-①）：file.write R4 **入队前静态预检**。
    # 越界路径（白名单根外 / workspace 写）此前先入 sandbox 审批队列、重放时
    # 才被 handler 内同一份 _write_path_rejection 拒 → 审批面板垃圾条目 +
    # 反复提交刷满 _MAX_PENDING=200 驱逐最旧合法条目（DoS 面）。
    # 复用现成判定（FileCapabilityDomain._resolve_write_path），router 经
    # _capability_router() 惰性解析（与 file_write handler 同一挂载点），
    # 预检拒绝形态经 register_precheck → dispatch 转 -32602，与正常参数拒绝同形。
    def _file_write_precheck(_method: str, params: dict[str, Any]) -> str | None:
        path = str((params or {}).get("path") or "")
        if not path:
            return None  # 空 path 交既有 handler/schema 语义，不在预检层改变行为
        router = _capability_router()
        if router is None:
            return None  # 路由不可用 → 不可判 → 照常入队（预检是增益非闸门）
        return _write_path_rejection(router, path)

    registry.register_precheck("file.write", _file_write_precheck)

    # ── System ping (heartbeat) ──
    async def siyi_ping(**kwargs: Any) -> dict[str, str]:
        return {"pong": "ok"}

    registry.register("siyi.ping", siyi_ping)

    # ── V2.1 批次2：R1-R4 风险分级集中标注 ─────────────────────────
    # R4 清单（强制 sandbox 审批）统一在模块级 _R4_METHODS 维护（见文件顶部）。
    from backend.method_registry import RISK_R4 as _R4

    for _m in _R4_METHODS:
        if registry.has_method(_m):
            registry.set_risk(_m, _R4)
    # settings.set 按参数键动态判级：敏感键（*api_key*/token/secret/password 等）→ R4，
    # 普通偏好键 → R3。与 settings.get 脱敏（批次1#8）配套，见 test_method_registry_risk.py。
    try:
        from core.env_loader import _is_sensitive_key as _is_sens_key

        def _settings_set_risk(method: str, params: dict[str, Any]) -> str:
            key = str((params or {}).get("key") or "")
            return _R4 if _is_sens_key(key) else "R3"

        registry.set_risk_resolver("settings.set", _settings_set_risk)
    except Exception as exc:  # noqa: BLE001
        logger.warning("settings.set risk_resolver 未注入（非致命）: %s", exc)
