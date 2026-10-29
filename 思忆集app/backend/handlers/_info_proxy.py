"""资讯代理 — 桌面端抓取国内源，手机端通过 RPC 调用。

支持两种模式：
  1. source 模式（source="zhihu"|"weibo"|"all"）：抓取国内热榜源
  2. url 模式（url="https://..."）：代理获取任意 URL 的 HTML，供手机端 fetchPageTitle 绕过 CORS
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from backend.handlers._async_rpc import POLLER_SCHEMA
from backend.handlers._async_rpc import poll_job_result as _poll_job_result

logger = logging.getLogger("siyi.backend.handlers.info_proxy")

TIMEOUT = aiohttp.ClientTimeout(total=15)
# H7: 单源响应正文上限 — 防止伪造 / 劫持 CDN 返回 GB 级 payload 耗光内存。
# zhihu / weibo / tophub 正常响应 < 100KB；这里给 5MB 兜底已足够宽松。
MAX_RESPONSE_BYTES = 5 * 1024 * 1024

# 国内可用源
# 注：tophub 已于 2026-06 移除 — api.tophubdata.com/entries 返回 404，API 已下线。
SOURCES = {
    "zhihu": "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=10",
    "weibo": "https://weibo.com/ajax/side/hotSearch",
}

# 仅允许这些主机响应 — 即使 SOURCES 被运行时改写，也防止意外的 SSRF。
_ALLOWED_HOSTS = frozenset(
    {
        "www.zhihu.com",
        "weibo.com",
    }
)

# P2-1: 按 host 分发 Referer，避免对所有站点都伪装成知乎来源 → 反爬识别 / 日志失真。
# 任何未在表中的 host 都不会带 Referer 头。
_HOST_REFERER = {
    "www.zhihu.com": "https://www.zhihu.com/",
    "weibo.com": "https://weibo.com/",
}


def register_info_proxy(registry, context) -> None:
    """Register info proxy JSON-RPC methods."""

    # ── info.fetch_proxy ──
    async def info_fetch_proxy(source: str = "", url: str = "", **kwargs: Any) -> dict[str, Any]:
        """Fetch articles from domestic sources OR proxy-fetch a URL.

        模式 1 (source): source="zhihu"|"weibo"|"all" → 抓取国内热榜源
          Returns: { "items": [...], "source": str }
        模式 2 (url): url="https://..." → 代理获取任意 URL 的 HTML（绕过 CORS）
          供手机端 fetchPageTitle 使用。
          Returns: { "content": str, "url": str, "status": int }
        """
        # 模式 2: 通用 URL 代理（手机端 fetchPageTitle 依赖此模式）
        if url:
            return await _proxy_fetch_url(url)

        # 模式 1: 国内源抓取
        items: list[dict[str, Any]] = []

        async def fetch_url(name: str, src_url: str, parser_type: str = "json") -> list[dict[str, Any]]:
            # H7: host 白名单 — 即便 SOURCES 配置被改也不会去到任意主机
            try:
                from urllib.parse import urlparse

                host = (urlparse(src_url).hostname or "").lower()
                if host not in _ALLOWED_HOSTS:
                    logger.warning("info.fetch_proxy 拒绝非白名单主机: %s", host)
                    return []
            except Exception:
                logger.exception("info.fetch_proxy 校验来源 URL host 白名单失败")
                return []
            try:
                async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    }
                    # P2-1: 按 host 选择 Referer；未知 host 不带 Referer。
                    referer = _HOST_REFERER.get(host)
                    if referer:
                        headers["Referer"] = referer
                    async with session.get(src_url, headers=headers) as resp:
                        if resp.status != 200:
                            return []
                        # H7: 限制响应大小 — content.read 提前知道字节数，
                        # text() 在乱序响应下可能拉到 GB 级 buffer
                        raw_bytes = await resp.content.read(MAX_RESPONSE_BYTES + 1)
                        if len(raw_bytes) > MAX_RESPONSE_BYTES:
                            logger.warning(
                                "info.fetch_proxy %s 响应超 %d 字节，已截断/拒绝",
                                name,
                                MAX_RESPONSE_BYTES,
                            )
                            return []
                        raw = raw_bytes.decode(resp.charset or "utf-8", errors="replace")

                        if parser_type == "zhihu":
                            return _parse_zhihu(raw, name)
                        elif parser_type == "weibo":
                            return _parse_weibo(raw, name)
                        return []
            except Exception:
                logger.exception("info.fetch_proxy 抓取 %s 失败", name)
                return []

        if source == "all":
            tasks = [
                fetch_url("zhihu", SOURCES["zhihu"], "zhihu"),
                fetch_url("weibo", SOURCES["weibo"], "weibo"),
            ]
            results = await asyncio.gather(*tasks)
            for r in results:
                items.extend(r)
        elif source in ("zhihu", "weibo"):
            items = await fetch_url(source, SOURCES.get(source, ""), source)

        return {"items": items, "source": source}

    registry.register("info.fetch_proxy", info_fetch_proxy)

    # ── info.personalize ── 资讯个性化排序
    async def info_personalize(items: list[dict[str, Any]] | None = None, **kwargs: Any) -> dict[str, Any]:
        """根据用户记忆/画像/关注话题对资讯条目打分排序。

        输入：items = [{title, source, snippet, ...}]
        输出：{ "scores": {url|title: score}, "order": [url1, url2, ...], "keywords_matched": int }
        """
        items = items or []
        if not items:
            return {"scores": {}, "order": [], "keywords_matched": 0}

        # 1. 提取用户关注话题（从 user_profile 或 memory）
        focus_topics: list[str] = []
        try:
            profile_svc = getattr(context, "user_profile_service", None)
            if profile_svc is not None:
                profile = profile_svc.load() if hasattr(profile_svc, "load") else {}
                focus_topics = list(profile.get("long_term", {}).get("focus_topics", []))
        except Exception:
            logger.debug("user profile load failed", exc_info=True)

        # 2. 关键词从近期记忆补充（最近 50 条）
        recent_keywords: set[str] = set()
        try:
            mem = getattr(context, "memory_index_service", None)
            if mem is not None and hasattr(mem, "list_recent"):
                recents = mem.list_recent(limit=50)
                for r in recents or []:
                    text = str(r.get("text") or r.get("content") or "")
                    # 简单中英文分词：按空格 + 中文连续段
                    for tok in text.replace("，", " ").replace("。", " ").split():
                        if 2 <= len(tok) <= 12:
                            recent_keywords.add(tok.lower())
        except Exception:
            logger.debug("memory recent failed", exc_info=True)

        keyword_pool = set(t.lower() for t in focus_topics) | recent_keywords

        # 3. 打分：标题/摘要中命中关键词数 → 分数
        scores: dict[str, float] = {}
        total_matched = 0
        for it in items:
            key = str(it.get("url") or it.get("title") or "")
            if not key:
                continue
            text = (str(it.get("title") or "") + " " + str(it.get("snippet") or it.get("summary") or "")).lower()
            matched = sum(1 for kw in keyword_pool if kw and kw in text)
            total_matched += matched
            scores[key] = float(matched) / max(1, len(keyword_pool)) if keyword_pool else 0.5

        order = sorted(scores.keys(), key=lambda k: -scores[k])
        return {"scores": scores, "order": order, "keywords_matched": total_matched}

    registry.register("info.personalize", info_personalize)

    # ── report.generate ── 立即生成周报并投递到收件箱
    async def report_generate(**kwargs: Any) -> dict[str, Any]:
        """手动触发周报生成（与 weekly_report_scheduler 同链路）。"""
        try:
            from backend.weekly_report_scheduler import _collect_weekly_stats, _generate_report_text, _post_to_inbox

            stats = await _collect_weekly_stats(context)
            text = await _generate_report_text(context, stats)
            await _post_to_inbox(context, text)
            return {"success": True, "report_length": len(text), "stats": stats}
        except Exception as exc:
            logger.warning("report.generate failed: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}

    registry.register("report.generate", report_generate)

    # ── report.daily_summary ── 立即生成日报并投递到收件箱（F-3）
    async def report_daily_summary(target_date: str = "", **kwargs: Any) -> dict[str, Any]:
        """手动触发日报生成（与 weekly_report_scheduler 同链路，同步返回终局结果）。

        S3-2 回退（2026-09-16 架构复审，audit §1）：入队信封版对本 RPC 秒回
        job_id，但 report.daily_summary 前端**零消费者**（日报正文走 cron 自动
        投递收件箱 + daily_summary.ready 事件，不经此 RPC），信封化改动属
        「无消费方的死改动」且与其余五处同批引入契约风险 → 回退同步直调
        run_report_daily_summary。异步骨架（worker/.status/completed 事件）保留
        待 S3-2b 若有真实按需触发需求再翻转。
        """
        return await run_report_daily_summary(context, target_date=target_date)

    registry.register("report.daily_summary", report_daily_summary)

    async def report_daily_summary_status(
        job_id: str = "",
        task_id: str = "",
        wait_seconds: float = 0.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """查询 report.daily_summary 异步任务的状态与结果。"""
        return await _poll_job_result(
            context,
            job_id=job_id,
            task_id=task_id,
            capability_id="report.daily_summary",
            wait_seconds=wait_seconds,
        )

    registry.register_with_schema("report.daily_summary.status", report_daily_summary_status, schema=POLLER_SCHEMA)

    # ── info.sections.* 板块管理 ──
    async def info_sections_list(**kwargs: Any) -> dict[str, Any]:
        """获取所有板块（默认 + 用户自定义合并后的 effective 结果）。"""
        from services.info_constants import (
            _DEFAULT_SECTION_KEYWORDS,
            _SECTION_META,
            _SECTION_ORDER,
            get_effective_sections,
            get_all_sections,
        )

        settings = getattr(context, "settings_repository", None)
        user_sections: list[dict] = []
        if settings:
            try:
                raw = await asyncio.to_thread(settings.get_setting, "user_info_sections", None)
                if isinstance(raw, list):
                    user_sections = raw
            except Exception:
                logger.exception("info.sections.list 读取用户板块设置失败")
                pass
        effective = get_effective_sections(user_sections)
        all_sections = get_all_sections(user_sections)
        return {
            "default": {
                "meta": {k: _SECTION_META.get(k, {"name": k, "emoji": "📎"}) for k in _SECTION_ORDER},
                "order": list(_SECTION_ORDER),
                "keywords": dict(_DEFAULT_SECTION_KEYWORDS),
            },
            "user": user_sections,
            "effective": effective,
            "all": all_sections,
        }

    registry.register("info.sections.list", info_sections_list)

    async def info_sections_create(
        name: str = "", emoji: str = "📌", keywords: list[str] | None = None, sources: list[str] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """创建自定义板块。"""
        if not name or not name.strip():
            return {"success": False, "error": "板块名称不能为空"}
        from services.info_constants import get_effective_sections, set_user_sections, all_source_names

        settings = getattr(context, "settings_repository", None)
        if not settings:
            return {"success": False, "error": "settings 不可用"}

        valid_sources = set(all_source_names())
        sanitized_sources = [s for s in (sources or []) if s and str(s) in valid_sources]

        # 读 SQLite + 修改 + 写 SQLite 整段包裹 to_thread
        def _do_create():
            user_sections: list[dict] = []
            try:
                raw = settings.get_setting("user_info_sections", None)
                if isinstance(raw, list):
                    user_sections = raw
            except Exception:
                logger.exception("info.sections.create 读取用户板块设置失败")
                pass
            # 生成唯一 id
            import re

            sid_base = re.sub(r"[^\w]", "_", name.strip().lower()) or "custom"
            sid = f"user_{sid_base}"
            counter = 1
            existing_ids = {s.get("id", "") for s in user_sections}
            while sid in existing_ids:
                sid = f"user_{sid_base}_{counter}"
                counter += 1
            new_section = {
                "id": sid,
                "name": name.strip(),
                "emoji": emoji,
                "keywords": keywords or [],
                "sources": sanitized_sources,
                "enabled": True,
                "is_default": False,
            }
            user_sections.append(new_section)
            try:
                settings.set_setting("user_info_sections", user_sections)
                set_user_sections(user_sections)
            except Exception as exc:
                logger.exception("info.sections.create 保存板块设置失败")
                return {"success": False, "error": f"保存失败: {exc}"}
            effective = get_effective_sections(user_sections)
            return {"success": True, "section": new_section, "effective": effective}

        return await asyncio.to_thread(_do_create)

    registry.register("info.sections.create", info_sections_create)

    async def info_sections_update(
        section_id: str = "",
        name: str | None = None,
        emoji: str | None = None,
        keywords: list[str] | None = None,
        enabled: bool | None = None,
        sources: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """更新板块配置。"""
        if not section_id:
            return {"success": False, "error": "缺少 section_id"}
        from services.info_constants import get_effective_sections, set_user_sections, all_source_names

        settings = getattr(context, "settings_repository", None)
        if not settings:
            return {"success": False, "error": "settings 不可用"}

        valid_sources = set(all_source_names())
        # sources 显式传入时（含空列表表示"清空=不限源"）才更新
        sanitized_sources: list[str] | None = None
        if sources is not None:
            sanitized_sources = [s for s in sources if s and str(s) in valid_sources]

        # 读 + 修改 + 写 整段包裹 to_thread
        def _do_update():
            user_sections: list[dict] = []
            try:
                raw = settings.get_setting("user_info_sections", None)
                if isinstance(raw, list):
                    user_sections = raw
            except Exception:
                logger.exception("info.sections.update 读取用户板块设置失败")
                pass
            # 查找已有配置
            found = None
            for s in user_sections:
                if s.get("id") == section_id:
                    found = s
                    break
            if found is None:
                # 是默认板块，创建新的用户覆盖项
                found = {"id": section_id}
                user_sections.append(found)
            if name is not None:
                found["name"] = str(name)
            if emoji is not None:
                found["emoji"] = str(emoji)
            if keywords is not None:
                found["keywords"] = [str(k) for k in keywords if k]
            if enabled is not None:
                found["enabled"] = bool(enabled)
            if sanitized_sources is not None:
                found["sources"] = sanitized_sources
            try:
                settings.set_setting("user_info_sections", user_sections)
                set_user_sections(user_sections)
            except Exception as exc:
                logger.exception("info.sections.update 保存板块设置失败")
                return {"success": False, "error": f"保存失败: {exc}"}
            effective = get_effective_sections(user_sections)
            return {"success": True, "section": found, "effective": effective}

        return await asyncio.to_thread(_do_update)

    registry.register("info.sections.update", info_sections_update)

    async def info_sections_delete(section_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """删除板块。

        自定义板块：从 user_info_sections 中彻底移除。
        默认板块：写入 enabled=False 覆盖项，从 effective 列表中隐藏（不再抓取）。
        """
        if not section_id:
            return {"success": False, "error": "缺少 section_id"}
        settings = getattr(context, "settings_repository", None)
        if not settings:
            return {"success": False, "error": "settings 不可用"}

        # 读 + 修改 + 写 整段包裹 to_thread
        def _do_delete():
            from services.info_constants import set_user_sections, get_effective_sections, _default_sections

            user_sections: list[dict] = []
            try:
                raw = settings.get_setting("user_info_sections", None)
                if isinstance(raw, list):
                    user_sections = raw
            except Exception:
                logger.exception("info.sections.delete 读取用户板块设置失败")
                pass

            # general 是系统兜底分类（不参与抓取），"彻底删除"= 写入 removed 覆盖项
            if section_id == "general":
                found = None
                for s in user_sections:
                    if s.get("id") == "general":
                        found = s
                        break
                if found is None:
                    found = {"id": "general"}
                    user_sections.append(found)
                found["removed"] = True
                found["enabled"] = False
                try:
                    settings.set_setting("user_info_sections", user_sections)
                    set_user_sections(user_sections)
                except Exception as exc:
                    logger.exception("info.sections.delete 保存 general 板块设置失败")
                    return {"success": False, "error": f"保存失败: {exc}"}
                effective = get_effective_sections(user_sections)
                return {"success": True, "deleted": found, "effective": effective}

            # 判断是否为默认板块
            default_ids = {s["id"] for s in _default_sections()}
            is_default = section_id in default_ids

            removed = None
            if is_default:
                # 默认板块：写入 enabled=False 覆盖项（隐藏，不抓取）
                found = None
                for s in user_sections:
                    if s.get("id") == section_id:
                        found = s
                        break
                if found is None:
                    found = {"id": section_id}
                    user_sections.append(found)
                found["enabled"] = False
                removed = dict(found)
            else:
                # 自定义板块：彻底移除
                new_list = []
                for s in user_sections:
                    if s.get("id") == section_id:
                        removed = s
                    else:
                        new_list.append(s)
                user_sections = new_list

            if removed is None:
                return {"success": False, "error": "板块不存在"}
            try:
                settings.set_setting("user_info_sections", user_sections)
                set_user_sections(user_sections)
            except Exception as exc:
                logger.exception("info.sections.delete 保存板块设置失败")
                return {"success": False, "error": f"保存失败: {exc}"}
            effective = get_effective_sections(user_sections)
            return {"success": True, "deleted": removed, "effective": effective}

        return await asyncio.to_thread(_do_delete)

    # 2026-10-15（id 口径专项审计）：R4 删除方法补 schema（required section_id）——
    # 参数名错误（如实测传 id）原本会入队审批 → 批准重放才 -32602，产生垃圾审批项。
    # 配合 method_registry 的校验前置，非法参数在入队前即被拒绝。
    registry.register_with_schema(
        "info.sections.delete",
        info_sections_delete,
        schema={
            "type": "object",
            "properties": {"section_id": {"type": "string"}},
            "required": ["section_id"],
        },
    )

    async def info_sections_ai_suggest_keywords(name: str = "", count: int = 12, **kwargs: Any) -> dict[str, Any]:
        """AI 根据板块名称推荐关键词。"""
        if not name or not name.strip():
            return {"success": False, "error": "板块名称不能为空"}
        ai_client = getattr(context, "ai_client", None)
        if ai_client is None:
            return {"success": False, "error": "AI 客户端不可用"}
        prompt = (
            f"你是一个信息检索和信息架构专家。用户创建了一个名为「{name.strip()}」的信息追踪板块。\n\n"
            f"请推荐 {count} 个搜索关键词，用于从每日技术/科技资讯中筛选与该板块相关的内容。\n\n"
            f"要求：\n"
            f"- 关键词涵盖该领域的核心技术、热门话题、重要公司/产品、代表人物\n"
            f"- 中英文混合，英文术语保留原文\n"
            f"- 按重要性和覆盖面排序（最重要的排前面）\n"
            f'- 关键词应具体，避免过于宽泛（如用 "React" 而非 "前端框架"）\n'
            f"- 只返回 JSON 字符串数组，不要其他内容\n\n"
            f'示例输出格式：["关键词1", "关键词2", ...]'
        )
        try:
            import asyncio, json, re

            resp = await asyncio.wait_for(
                asyncio.to_thread(
                    ai_client.complete_text,
                    [{"role": "user", "content": prompt}],
                    max_tokens=500,
                    temperature=0.3,
                ),
                timeout=30,
            )
            text = (resp or "").strip()
            # 尝试提取 JSON 数组
            match = re.search(r"\[.*?\]", text, re.DOTALL)
            if match:
                keywords = json.loads(match.group(0))
                if isinstance(keywords, list):
                    keywords = [str(k) for k in keywords[:count] if k]
                    return {"success": True, "keywords": keywords, "name": name.strip()}
            # Fallback: 按行解析
            lines = [l.strip().rstrip(",").strip("\"'") for l in text.split("\n") if l.strip()]
            keywords = [l for l in lines if l and not l.startswith(("{", "}", "[", "]", "```"))][:count]
            if keywords:
                return {"success": True, "keywords": keywords, "name": name.strip()}
            return {"success": False, "error": "AI 返回格式无法解析", "raw": text[:200]}
        except Exception as exc:
            logger.warning("info.sections.ai_suggest_keywords failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("info.sections.ai_suggest_keywords", info_sections_ai_suggest_keywords)

    # ── info.sources.list ── 可用信息源列表（供前端多选 + 抓取时限源）
    async def info_sources_list(**kwargs: Any) -> dict[str, Any]:
        """返回全部可用信息源（name → 展示名/主页），供前端勾选板块信息源。"""
        from services.info_constants import all_source_names, source_display, source_url

        names = all_source_names()
        return {
            "success": True,
            "sources": [
                {
                    "name": n,
                    "display": source_display(n),
                    "url": source_url(n),
                }
                for n in names
            ],
        }

    registry.register("info.sources.list", info_sources_list)


async def run_report_daily_summary(context: Any, *, target_date: str = "") -> dict[str, Any]:
    """日报生成真实现 —— cron worker 与 RPC 降级共用（S3-2）。

    传 context 给 run_daily_summary，使 cron worker 用自身 AppContext 走
    与调度器自跑相同的链路（投递收件箱 + daily_summary.ready 事件）。
    """
    try:
        from backend.weekly_report_scheduler import run_daily_summary

        return await run_daily_summary(target_date=(target_date or None), context=context)
    except Exception as exc:
        logger.warning("report.daily_summary failed: %s", exc, exc_info=True)
        return {"success": False, "error": str(exc)}


def _parse_zhihu(raw: str, src: str) -> list[dict[str, Any]]:
    """Parse Zhihu hot list JSON."""
    import json

    try:
        data = json.loads(raw)
        items = []
        for entry in (data.get("data") or [])[:10]:
            target = entry.get("target") or {}
            title = target.get("title") or target.get("title_area", {}).get("text", "")
            if not title:
                continue
            items.append(
                {
                    "title": title,
                    "url": target.get("url") or f"https://www.zhihu.com/question/{target.get('id', '')}",
                    "source": src,
                    "summary": target.get("excerpt", "")[:200] or title,
                    "tags": [],
                    "category": "domestic",
                }
            )
        return items
    except Exception:
        logger.exception("解析知乎热榜 JSON 数据失败")
        return []


def _parse_weibo(raw: str, src: str) -> list[dict[str, Any]]:
    """Parse Weibo hot search JSON."""
    import json, re

    try:
        data = json.loads(raw)
        items = []
        band_list = data.get("data", {}).get("band_list") or data.get("data", {}).get("realtime") or []
        for entry in band_list[:10]:
            title = (entry.get("word") or entry.get("note") or entry.get("topic", "")).strip()
            if not title:
                continue
            # Clean HTML
            title = re.sub(r"<[^>]+>", "", title)
            items.append(
                {
                    "title": title,
                    "url": f"https://s.weibo.com/weibo?q={title}",
                    "source": src,
                    "summary": f"微博热搜: {title}",
                    "tags": [entry.get("category", "") or ""],
                    "category": "internet",
                }
            )
        return items
    except Exception:
        logger.exception("解析微博热搜 JSON 数据失败")
        return []


def _parse_tophub(raw: str, src: str) -> list[dict[str, Any]]:
    """Parse Tophub aggregated hot list.

    .. deprecated:: 2026-06
       api.tophubdata.com 已返回 404，此解析器保留仅为向后兼容，
       新代码不应再调用。SOURCES 中已移除 tophub。
    """
    import json

    try:
        data = json.loads(raw)
        items: list[Any] = []

        entries = data.get("data", []) if isinstance(data, dict) else data
        for entry in (entries or [])[:15]:
            title = entry.get("title") or entry.get("item", {}).get("title", "")
            if not title:
                continue
            items.append(
                {
                    "title": str(title),
                    "url": entry.get("url", "") or f"https://www.google.com/search?q={title}",
                    "source": src,
                    "summary": str(entry.get("content", "") or title)[:200],
                    "tags": [],
                    "category": "internet",
                }
            )
        return items
    except Exception:
        logger.exception("解析 tophub 热榜 JSON 数据失败")
        return []


async def _proxy_fetch_url(url: str) -> dict[str, Any]:
    """代理获取任意 URL 的 HTML 内容（绕过 CORS），供手机端 fetchPageTitle 使用。

    安全措施：
      - 只允许 http/https 协议
      - SSRF 防护：拒绝内网/私有网段（复用 services.url_safety.validate_url）
      - 重定向逐跳校验（防止跳转到内网）
      - 限制响应大小（MAX_RESPONSE_BYTES）
      - 限制超时（TIMEOUT）

    Returns:
        {"content": str, "url": str, "status": int}
        失败时 status=0, content=""
    """

    from services.url_safety import SSRFBlocked, validate_url

    try:
        # SSRF 防护：校验协议 + DNS 解析 + 内网 IP 拦截
        validate_url(url)
    except SSRFBlocked as exc:
        logger.warning("info.fetch_proxy SSRF 拦截: %s (url=%s)", exc, url[:100])
        return {"content": "", "url": url, "status": 0}
    except Exception:
        logger.exception("info.fetch_proxy 校验 URL 安全时发生异常 (url=%s)", url[:100])
        return {"content": "", "url": url, "status": 0}

    try:
        # 禁用自动重定向，手动逐跳校验防 SSRF
        current_url = url
        max_redirects = 5
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            for _ in range(max_redirects + 1):
                async with session.get(current_url, headers=headers, allow_redirects=False) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location", "")
                        if not location:
                            break
                        # 解析相对路径重定向
                        from urllib.parse import urljoin

                        current_url = urljoin(current_url, location)
                        try:
                            validate_url(current_url)
                        except SSRFBlocked as exc:
                            logger.warning(
                                "info.fetch_proxy 重定向 SSRF 拦截: %s (url=%s)",
                                exc,
                                current_url[:100],
                            )
                            return {"content": "", "url": url, "status": 0}
                        continue
                    # 非重定向响应，读取内容
                    raw_bytes = await resp.content.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw_bytes) > MAX_RESPONSE_BYTES:
                        logger.warning(
                            "info.fetch_proxy url 模式响应超 %d 字节，已截断",
                            MAX_RESPONSE_BYTES,
                        )
                        raw_bytes = raw_bytes[:MAX_RESPONSE_BYTES]
                    content = raw_bytes.decode(resp.charset or "utf-8", errors="replace")
                    return {"content": content, "url": current_url, "status": resp.status}
            logger.warning("info.fetch_proxy 超过最大重定向次数 %d", max_redirects)
            return {"content": "", "url": url, "status": 0}
    except Exception as exc:
        logger.debug("info.fetch_proxy url 模式失败: %s", exc)
        return {"content": "", "url": url, "status": 0}
