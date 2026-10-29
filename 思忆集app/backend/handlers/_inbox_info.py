"""info.* JSON-RPC handlers：资讯聚合（brief / cached_items / auto_config / summarize / 关键词配置）。

从原 _inbox_data.py 拆分而来。info.brief 在方法内构造 InfoCollectorService 实例，
经 `asyncio.create_task` 启后台 AI；后台任务通过共享的 ``spawn_bg_task`` 管理强引用，
依赖链（ai_client / info_item_repository / info_source_registry / agent_gateway 等）
从传入的 ``context`` 直接取用，不破坏原有依赖。

info.brief 是"双模式单方法名"：非流式（dispatch）返回 dict，流式（dispatch_stream）
返回 async generator —— 形态契约见 ``info_brief`` 的 docstring 与 D-PG-4 注释。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from backend.handlers._bg_tasks import spawn_bg_task

logger = logging.getLogger("siyi.backend.handlers.inbox_info")


def register_inbox_info(registry, context) -> None:
    """注册 info.* 命名空间下的 RPC 方法。"""

    # ── info.brief (普通 + stream 双模式) ──
    # D-PG-4（2026-09-17）流式形态契约：dispatch_stream 对 handler 的返回值直接
    # `await stream.__anext__()`（backend/method_registry.py:515-533），要求返回值
    # 本身是 async generator；而 dispatch（无 stream:true）对 async generator 返回值
    # 会降级为「收集 chunk 列表」（method_registry.py:428-448）。二者互斥，所以
    # info.brief 这种"双模式单方法名"必须由下面这个 **sync 入口**分流：
    #   * 不注入 send_chunk（非流式 dispatch）→ 返回协程 → dict 契约原样不变；
    #   * 注入 send_chunk（stream:true）→ 返回 async generator → dispatch_stream 可用。

    def _make_collector():
        from services.info_collector_service import InfoCollectorService

        return InfoCollectorService(
            ai_client=context.ai_client,
            info_item_repository=context.info_item_repository,
            source_registry=getattr(context, "info_source_registry", None),
            settings_repository=getattr(context, "settings_repository", None),
            agent_gateway=getattr(context, "agent_gateway", None),
        )

    def _resolve_sources(sources: list[str] | None) -> list[str] | None:
        """``sources`` 兜底：未传时取"启用板块所关联信息源并集"。

        该并集为空（板块未配置源）时 collect 会退回"不限源、抓全源"，语义不变。
        """
        if sources is None:
            from services.info_constants import get_active_source_names

            return get_active_source_names()
        return sources

    async def _emit_collected(result: dict[str, Any]) -> None:
        try:
            from core.events import AppEvent, event_bus

            event_bus.emit(AppEvent("info.collected", payload=result))
        except Exception as exc:
            # P2 修复：资讯收集事件推送失败无日志，前端可能收不到更新通知
            logger.debug("info.brief failed to emit info.collected event: %s", exc, exc_info=True)

    async def _run_background_ai(
        collector,
        initial_items: list[dict[str, Any]],
        topics_list: list[str],
        errors: list[dict[str, str]],
        generate_brief: bool,
    ) -> None:
        """方案一：后台执行 AI 翻译/分类/简报，完成后 emit info.collected。

        前端已通过 collect(return_early=True) 的返回值拿到 items，
        这里完成后推送带 brief 的 info.collected 事件让前端更新简报 tab。
        """
        try:
            brief = await collector._post_process_ai(
                initial_items,
                topics_list,
                errors,
                generate_brief,
            )
            # 推送 AI 处理完成的结果（含 brief 和翻译后的 items）
            await _emit_collected(
                {
                    "brief": brief,
                    "items": initial_items,
                    "errors": errors,
                    "ai_completed": True,
                }
            )
            logger.info(
                "info.brief background AI done: brief=%d chars, items=%d",
                len(brief),
                len(initial_items),
            )
        except Exception as exc:
            logger.warning("info.brief background AI failed: %s", exc, exc_info=True)
            # 推送失败通知，前端可提示"AI 简报生成失败，查看原始条目"
            await _emit_collected(
                {
                    "brief": "",
                    "items": initial_items,
                    "errors": errors + [{"source": "ai", "error": str(exc)[:200], "type": "ai_error"}],
                    "ai_completed": False,
                }
            )

    async def _info_brief_plain(
        topics: list[str] | None = None,
        no_ai: bool = False,
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """非流式（dispatch）形态：契约与改造前逐字一致。

        方案一：collect(return_early=True) 立即返回抓取到的 items，AI 翻译/分类/简报
        在后台异步执行，完成后通过 info.collected 事件推送更新到前端。这样用户永远
        能立即看到抓取到的条目，不依赖 AI 速度。
        """
        sources = _resolve_sources(sources)
        collector = _make_collector()

        # no_ai=True（feed tab）时也用 return_early=True 跳过所有 AI 处理
        # （翻译/分类/简报都不需要），让 feed tab 响应在 ~20s 内
        if no_ai:
            result = await collector.collect(
                topics=topics,
                sources=sources,
                generate_brief=False,
                return_early=True,
            )
            await _emit_collected(result)
            return result

        result = await collector.collect(
            topics=topics,
            sources=sources,
            generate_brief=True,
            return_early=True,
        )

        # 立即推送一次（让前端马上显示 items，ai_pending 标记表示简报待生成）
        await _emit_collected(result)

        # 启动后台 AI 任务（翻译/分类/简报）
        # 使用 asyncio.create_task fire-and-forget，需保留强引用避免被 GC
        topics_list = topics or ["AI", "前端", "生产力", "开源"]
        spawn_bg_task(
            _run_background_ai(
                collector,
                result["items"],
                topics_list,
                result.get("errors", []),
                True,
            )
        )
        # collector 实例随函数结束被 GC，但 task 已由事件循环持有并被 _bg_tasks 保留强引用

        return result

    async def _info_brief_stream(
        topics: list[str] | None = None,
        no_ai: bool = False,
        sources: list[str] | None = None,
        send_chunk: Any = None,
    ) -> AsyncIterator[str]:
        """流式（dispatch_stream）形态：**async generator**。

        采集各阶段经 ``send_chunk`` 下发 ``info_progress`` chunk（形状不变），
        终帧 yield 采集结果 JSON。采集异常等边界也保证 yield 一条明确终帧
        （error 记录），绝不静默空流。
        """
        sources = _resolve_sources(sources)
        collector = _make_collector()

        async def _progress(event_type: str, payload: dict[str, Any]) -> None:
            await send_chunk(
                json.dumps({"event_type": event_type, **payload}, ensure_ascii=False, default=str),
                chunk_type="info_progress",
            )

        try:
            result = await collector.collect(
                topics=topics,
                sources=sources,
                generate_brief=not no_ai,
                progress_callback=_progress,
            )
            await _emit_collected(result)
        except Exception as exc:
            logger.warning("info.brief stream collect failed: %s", exc, exc_info=True)
            result = {
                "brief": "",
                "items": [],
                "errors": [{"source": "info", "error": str(exc)[:200], "type": "collect_error"}],
            }
        yield json.dumps(result, ensure_ascii=False, default=str)

    def info_brief(
        topics: list[str] | None = None,
        no_ai: bool = False,
        sources: list[str] | None = None,
        send_chunk: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Fetch info from external sources and generate AI brief（双形态入口）。

        普通请求（dispatch 不注入 send_chunk）返回完整 dict；stream 请求由
        dispatch_stream 注入 send_chunk，返回 async generator：采集各阶段发送
        ``info_progress`` chunk，最后 yield 一条 JSON 结果。

        ``sources``：限定本次采集的信息源子集。前端勾选板块信息源后传入；
        未传时使用"启用板块所关联信息源并集"，作为板块配置的兜底默认。
        若该并集为空（板块未配置源），collect 会退回"不限源、抓全源"。

        为什么不是 ``async def``：dispatch_stream 直接 ``await handler(...)`` 的返回值
        ``.__anext__()``，若本函数是 ``async def``，返回值就是协程对象，
        stream:true 必抛 ``AttributeError: 'coroutine' object has no attribute
        '__anext__'``（一条 chunk 都到不了客户端）；而非流式又必须保持 dict 而非
        "chunk 列表"。故此处用 sync 分流，把 async generator 形态落在
        ``_info_brief_stream``、dict 形态落在 ``_info_brief_plain``。
        """
        if send_chunk is None:
            return _info_brief_plain(topics=topics, no_ai=no_ai, sources=sources)
        return _info_brief_stream(
            topics=topics,
            no_ai=no_ai,
            sources=sources,
            send_chunk=send_chunk,
        )

    registry.register("info.brief", info_brief)

    # ── info.cached_items ──
    # 前端 InfoModule 在 info.brief 失败时回退调用此方法获取缓存，
    # 避免网络抖动导致 UI 完全空白。
    async def info_cached_items(**kwargs: Any) -> dict[str, Any]:
        """返回最近一次成功抓取的缓存 items。

        Returns:
            {"items": [...], "brief": str, "cached_at": float(epoch秒)}
            cached_at 为 0 表示尚无缓存。
        """
        from services.info_collector_service import InfoCollectorService

        return InfoCollectorService.get_cached()

    registry.register("info.cached_items", info_cached_items)

    # ── info.auto_config ──
    async def info_auto_config(
        enabled: bool = True,
        interval: int = 3600,
        topics: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Configure periodic info collection."""
        from backend import info_scheduler

        return info_scheduler.configure(enabled=enabled, interval=interval, topics=topics)

    registry.register("info.auto_config", info_auto_config)

    # ── info.summarize (AI 摘要单条资讯) ──
    # 前端 InfoFeedView.tsx 调用，期望返回 {summary: str, error: str}
    async def info_summarize(
        title: str = "",
        url: str = "",
        summary: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """对单条资讯调用 AI 生成更详细的摘要。

        Args:
            title: 资讯标题
            url: 资讯原文 URL（可选，会尝试抓取正文补充上下文）
            summary: 资讯已有简短摘要（来自 info.brief 抓取）

        Returns:
            {"summary": str, "error": str}  error 非空表示失败
        """
        ai_client = getattr(context, "ai_client", None)
        if not ai_client:
            return {"summary": "", "error": "ai_client unavailable"}

        # 拼接上下文：标题 + 已有摘要 + 可选 URL 正文
        context_parts: list[str] = []
        if title:
            context_parts.append(f"标题：{title}")
        if summary:
            context_parts.append(f"简述：{summary}")
        # 尝试抓取 URL 正文补充上下文（非关键，失败则跳过）
        if url:
            fetch_svc = getattr(context, "web_fetch_service", None)
            if fetch_svc:
                try:
                    fetched = await asyncio.wait_for(fetch_svc.fetch(url), timeout=8.0)
                    if fetched:
                        # 截断避免 prompt 过长
                        context_parts.append(f"正文片段：{str(fetched)[:2000]}")
                except Exception as exc:
                    logger.debug("info.summarize fetch url skipped: %s", exc)
        if not context_parts:
            return {"summary": "", "error": "no content to summarize"}

        prompt = (
            "请基于以下资讯内容，生成一段 100-200 字的中文摘要，"
            "突出核心事实、关键数据和影响。只返回摘要正文，不要标题、不要解释。\n\n" + "\n".join(context_parts)
        )
        try:
            result = await asyncio.to_thread(
                ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=400,
            )
            text = (result or "").strip()
            if not text:
                return {"summary": "", "error": "ai returned empty"}
            return {"summary": text, "error": ""}
        except Exception as exc:
            logger.warning("info.summarize failed: %s", exc)
            return {"summary": "", "error": str(exc)}

    registry.register("info.summarize", info_summarize)

    # ── info.set_section_keywords / info.get_section_keywords (P4: 分类关键词配置) ──
    async def info_set_section_keywords(keywords: dict[str, list[str]] | None = None, **kwargs: Any) -> dict[str, Any]:
        """设置资讯分类关键词表，持久化到 settings。

        同时为兼容自定义板块，把 keywords 同步回 user_info_sections 中对应 section，
        避免 info.sections.list 与 info.get_section_keywords 返回的关键词不一致。
        """
        from services.info_collector_service import set_section_keywords as svc_set_kw
        from services.info_constants import set_user_sections

        keywords = keywords or {}
        svc_set_kw(keywords)
        settings = context.settings_repository
        if settings:
            settings.set_setting("info_section_keywords", dict(keywords))
            # 同步到 user_info_sections，使自定义板块关键词保持一致
            try:
                raw = settings.get_setting("user_info_sections", None)
                user_sections = list(raw) if isinstance(raw, list) else []
                changed = False
                existing_ids = {s.get("id", "") for s in user_sections}
                for sid, kws in keywords.items():
                    if sid not in existing_ids:
                        continue
                    for s in user_sections:
                        if s.get("id") == sid:
                            s["keywords"] = list(kws)
                            changed = True
                            break
                if changed:
                    settings.set_setting("user_info_sections", user_sections)
                    set_user_sections(user_sections)
            except Exception:
                logger.exception("同步用户信息板块关键词失败")
                pass
        return {"success": True, "keywords": keywords}

    registry.register("info.set_section_keywords", info_set_section_keywords)

    async def info_get_section_keywords(**kwargs: Any) -> dict[str, Any]:
        """获取当前分类关键词表（优先 settings，回退默认）。"""
        from services.info_collector_service import get_section_keywords

        return {"keywords": get_section_keywords()}

    registry.register("info.get_section_keywords", info_get_section_keywords)
