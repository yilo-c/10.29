"""资讯采集服务 — 多源并发抓取 + AI 翻译 + 去重 + 结构化简报生成。

源列表（20个）：
  英文（自动翻译）：HackerNews、Dev.to、Lobsters、GitHub Trending、Product Hunt、
                  TechCrunch、Ars Technica、The Verge、Smashing Magazine、MIT科技评论
  中文：Solidot、开源中国、掘金、36氪、InfoQ中文、少数派、
        机器之心、阮一峰周刊、爱范儿、cnBeta
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

import aiohttp

from services.info_categorizer import InfoCategorizer, categorize_item
from services.info_constants import (
    DEFAULT_TOPICS,
    _EXPERIMENTAL_SOURCES,
    get_effective_section_meta,
    get_effective_section_order,
    get_section_keywords,
    has_cjk,
    set_section_keywords,
    source_display,
)
from services.info_translator import InfoTranslator
from services.prompt_shield import wrap_untrusted

logger = logging.getLogger("siyi.services.info_collector")


BRIEF_MAX_ITEMS = 40
DEDUP_SIMILARITY_THRESHOLD = 0.72

# 安C2：简报 prompt 中外部资讯段的定界来源标注（见 services/prompt_shield.py）
_BRIEF_UNTRUSTED_SOURCE = "资讯聚合源(HN/Dev.to/GitHub/Reddit/RSS等)"

# 源重试
MAX_RETRIES = 2
RETRY_BACKOFF_BASE = 1.5  # 指数退避: 0s, 1.5s, 3s


# ─────────────────────────────────────────────────────────────
# topics→采集联动（2026-10-15 用户拍板：此前 topics 只进简报 prompt 影响
# AI 挑选，抓取层完全固定——用户在关注领域里加新话题对"抓到什么"毫无影响。
# 这里做条目级联动：不删条目（不减功能），按相关度重排让相关条目占满候选池头部。
# ─────────────────────────────────────────────────────────────
def _topic_contains(haystack: str, needle: str) -> bool:
    """topic 命中判断：纯 ASCII 词用词边界匹配（避免 "ai" 误中 "email"），
    其余（中文等）用子串匹配。"""
    if not needle:
        return False
    if needle.isascii() and needle.isalnum():
        return re.search(rf"\b{re.escape(needle)}\b", haystack) is not None
    return needle in haystack


def apply_topic_linkage(items: list[dict[str, Any]], topics: list[str]) -> list[dict[str, Any]]:
    """按用户关注领域（topics）给条目打相关度分并重排。

    计分规则：
      - 标题命中 topic +2，摘要命中 +1（大小写不敏感；ASCII 词带边界）；
      - 条目 category 所属板块的关键词命中 topic 再 +1（topics→板块桥接，
        例如 topic "AI" 经 ai 板块关键词表覆盖 OpenAI/GPT 等派生词）；
      - 按联动分降序、热度分降序稳定排序；不删除任何条目。

    无有效 topics 时退回原热度排序（与旧行为一致）。
    """
    topic_low = [str(t).strip().lower() for t in (topics or []) if str(t).strip()]
    if not topic_low:
        items.sort(key=lambda x: x.get("score", 0), reverse=True)
        return items

    # topic → 相关板块集合（topic 出现在板块关键词表中，或与板块 id 相等）
    sec_keywords = get_section_keywords()
    topic_sections: dict[str, set[str]] = {}
    for sec_key, kws in sec_keywords.items():
        kws_low = {str(k).strip().lower() for k in (kws or []) if str(k).strip()}
        kws_low.add(str(sec_key).strip().lower())
        for t in topic_low:
            if t in kws_low:
                topic_sections.setdefault(t, set()).add(str(sec_key).strip().lower())

    for item in items:
        title_low = str(item.get("title", "")).lower()
        summary_low = str(item.get("summary", "")).lower()
        # AI 翻译后的中文字段（_post_process_ai 回填）也参与命中——
        # 中文 topic（如"大模型"）只有翻译后才可能命中英文源条目
        title_low += " " + str(item.get("title_zh", "")).lower()
        summary_low += " " + str(item.get("summary_zh", "")).lower()
        cat = str(item.get("category", "")).strip().lower()
        s = 0
        for t in topic_low:
            if _topic_contains(title_low, t):
                s += 2
            if _topic_contains(summary_low, t):
                s += 1
            if cat and cat in topic_sections.get(t, set()):
                s += 1
        item["_topic_score"] = s

    matched = sum(1 for i in items if i.get("_topic_score", 0) > 0)
    logger.info(
        "info collect: topic linkage matched %d/%d items (topics=%s)",
        matched,
        len(items),
        topics,
    )
    items.sort(key=lambda x: (x.get("_topic_score", 0), x.get("score", 0)), reverse=True)
    return items


def build_brief_prompt(
    now_str: str,
    topics: list[str],
    sections_desc: str,
    section_instructions: str,
    raw_text: str,
    total_count: int,
    compact: bool = False,
) -> str:
    """构造简报生成 prompt（2026-10-15 用户拍板"编辑型收紧"）。

    相对旧版的变化：总条目 10-20→10-15、每条点评 2-3 句→1-2 句、
    去掉条目间 "---" 分隔线要求；compact=True 供内核通道使用
    （规避 Windows 命令行 8191 字符上限），用单行紧凑版条目格式。
    """
    entry_format = (
        "## 每条条目的格式要求\n"
        "- 标题使用中文，加粗显示；如需保留英文原名请用中文（English Name）格式，例如：**DeepSeek发布新模型（DeepSeek-V4）**\n"
        "- 标题后附上原文链接，锚文本统一用「原文」：[原文](URL)\n"
        "- 附上**1-2句点评**：说明这个技术/项目/新闻为什么值得关注，点到即止，不要复读摘要\n"
        "- 标注来源（如 HackerNews、GitHub Trending、Solidot 等）\n"
        "- 每条条目独占 4 行：标题+链接 / 点评 / 来源，**条目与条目之间必须空一行**；内容单薄的板块可以整个省略\n"
        "- 每个板块下精选2-4条最有价值的条目，不要全部罗列\n"
        if not compact
        else "## 条目格式（紧凑）\n- 加粗中文标题 + [原文](URL) + 1-2句点评 + 来源标注；条目间空一行；总条目10-15条，宁精勿滥\n"
    )
    return (
        f"你是一位资深科技编辑，请根据以下最新技术资讯，生成一份高质量的中文技术简报。\n"
        f"日期：{now_str}\n"
        f"用户关注领域：{'、'.join(topics)}\n"
        f"本次资讯涵盖板块：{sections_desc}\n\n"
        f"## 输出要求\n"
        f"严格按照以下结构输出Markdown格式的简报：\n\n"
        f"### 📰 今日速览\n"
        f"用2-3句话概括今天最重要的2-3个技术动向，要求精炼有观点。\n\n"
        f"{section_instructions}"
        f"### 💡 编辑推荐\n"
        f"挑选1-2条最值得深度阅读的内容，说明推荐理由（1-2句）。\n\n"
        f"{entry_format}\n"
        f"## 筛选原则\n"
        f"1. 优先选择与用户关注领域相关的内容（{', '.join(topics)}）；"
        f"条目已按相关度预排序，排在前面的优先入选\n"
        f"2. 去除重复/同质化内容（同一条新闻只保留最佳报道源）\n"
        f"3. 按重要性和影响力排序\n"
        f"4. 严格按照上面列出的板块输出，不要自行创造新板块名称\n"
        f"5. 总条目数控制在10-15条，宁精勿滥\n\n"
        f"## 原始资讯数据\n"
        f"（共{total_count}条，已按板块自动预分类并按关注领域相关度排序；"
        f"标题含中文翻译，摘要已翻译或为中文原文）\n\n"
        f"{raw_text}"
    )


# AI 调用限流
AI_SEMAPHORE = asyncio.Semaphore(3)  # 最多 3 个 AI 调用并行


# ─────────────────────────────────────────────────────────────
# 以下模块级状态/纯函数由抽离模块 services/_info_collector_utils.py
# 透明 re-export，类体与外部 import 均无需改动。
# 超时常量 ITEM_TIMEOUT/SHORT_TIMEOUT/TOTAL_TIMEOUT 也移入 utils，
# 供 info_collector_service 与 info_fetchers.mixin 共用（避免循环导入）。
# ─────────────────────────────────────────────────────────────
from services._info_collector_utils import (
    _cache,
    _CACHE_MAX_ITEMS,
    _save_persisted_cache,
    _strip_html,
    _title_similarity,
    _maybe_call,
    TOTAL_TIMEOUT,
)

# T3.6 公共别名：capability_router/_plugin_tools/插件/测试经本模块导入；
# 冗余 as 别名 = 显式 re-export 标记，防 ruff F401 误删（七维-安C2 收尾）。
from services._info_collector_utils import title_similarity as title_similarity

# 内置源抓取器（26 个 fetcher + 解析辅助）已拆至 services/info_fetchers/mixin.py
from services.info_fetchers.mixin import InfoFetchersMixin


class InfoCollectorService(InfoFetchersMixin):
    def __init__(
        self,
        ai_client: Any = None,
        info_item_repository: Any = None,
        source_registry: Any = None,
        rss_subscriber: Any = None,
        settings_repository: Any = None,
        event_bus: Any = None,
        agent_gateway: Any = None,
    ) -> None:
        self._ai_client: Any = None
        self._primary_ai: Any = None
        self._fallback_ai: Any = None
        self._settings_repo: Any = settings_repository
        self._info_item_repository: Any = info_item_repository
        self._agent_gateway: Any = agent_gateway

        inner = getattr(ai_client, "_inner", None)
        fallback_client = inner if inner is not None else ai_client

        if fallback_client is not None:
            self._ai_client = fallback_client
            self._primary_ai = getattr(fallback_client, "primary", None)
            self._fallback_ai = getattr(fallback_client, "fallback", None)
            if self._primary_ai is None and hasattr(fallback_client, "chat_completion"):
                self._primary_ai = fallback_client

        self._translator = InfoTranslator(self._call_ai)
        self._categorizer = InfoCategorizer(self._call_ai, self._primary_ai)

        # C1: 资讯源注册表 — 可配置启用/禁用/自定义源。
        # 共享实例（context.info_source_registry）让 RPC 层的变更即时生效；
        # R2-A1：未注入时按 settings 现读自建（而非无 settings 裸建），
        # 保证 info.sources.* 已持久化到 settings 的用户配置仍被采集器消费。
        if source_registry is not None:
            self._source_registry = source_registry
        else:
            from services.info_source_registry import InfoSourceRegistry

            logger.warning(
                "InfoCollectorService 未注入 source_registry，退回按 settings 自建；RPC 侧未落盘的纯内存变更不会即时反映到本次采集",
            )
            self._source_registry = InfoSourceRegistry(settings_repository=self._settings_repo)

        # C3: RSS/Atom 订阅器 — 独立于 source_registry 的 feed 订阅。
        # 共享实例（context.rss_subscriber）让 RPC rss.* 与 collect() 联动；
        # 未注入时不创建（collect() 中跳过 RSS 订阅抓取），保持向后兼容。
        self._rss_subscriber = rss_subscriber

    async def _call_ai(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int = 1500,
        timeout: float = 30.0,
        use_semaphore: bool = False,
        allow_local: bool = True,
    ) -> dict[str, Any]:
        # AI 调用限流：避免瞬间并发太多 API 请求
        if use_semaphore:
            await AI_SEMAPHORE.acquire()

        try:
            clients = [c for c in (self._primary_ai, self._fallback_ai) if c is not None]
            # 尝试本地 Ollama 作为第三级回退
            # P0（2026-08-17）：原 from ai.client.config import get_local_client
            # 引用了不存在的函数 -> ImportError 被 except 静默吞掉，本地回退
            # 永远不生效（DeepSeek 402 欠费时简报生成直接失败）。改用真实 API：
            # resolve_local_openai_compatible_config(settings) + OpenAICompatibleClient。
            local_client: Any = None
            try:
                from ai.client.config import resolve_local_openai_compatible_config
                from ai.client.base import OpenAICompatibleClient

                local_cfg = resolve_local_openai_compatible_config(
                    settings_repository=self._settings_repo,
                )
                if local_cfg and local_cfg.base_url:
                    # 用户决策（2026-08-17）：简报生成只用云端模型（本地 3B 太慢，
                    # 4000 token 简报要数分钟）；allow_local=False 时跳过本地兜底。
                    # 翻译/分类等小任务仍允许本地（快且省云端额度）。
                    if not allow_local:
                        logger.info(
                            "info _call_ai: cloud-only mode, skip local fallback (%s)",
                            local_cfg.base_url,
                        )
                    else:
                        local = OpenAICompatibleClient(
                            api_key=local_cfg.api_key,
                            api_base=local_cfg.base_url,
                            model=local_cfg.model,
                            # 本地小模型生成慢：client 自身 httpx 超时同步放宽
                            timeout=max(local_cfg.timeout, 300),
                        )
                        if local not in clients:
                            clients.append(local)
                            local_client = local
            except Exception:
                logger.debug("local Ollama fallback unavailable", exc_info=True)

            if not clients:
                return {}  # 所有模型不可用，返回空

            last_exc: Exception | None = None
            for client in clients:
                try:
                    # 本地模型（qwen 3B 级）生成速度慢且免费：放宽等待时限，
                    # 避免云端欠费降级本地后简报仍因 wait_for 超时失败
                    # （2026-08-17：DeepSeek 402 -> local 兜底但 30s 超时空失败）。
                    wait_timeout = timeout if client is not local_client else max(timeout, 180.0)
                    result = await self._chat_with_length_retry(
                        client,
                        messages,
                        temperature,
                        max_tokens,
                        wait_timeout,
                    )
                    if result:
                        return result
                except Exception as exc:
                    last_exc = exc
                    # P0（2026-08-17）：debug 级在生产不可见，402/超时等根因
                    # 无法排查（"生成简报失败"只能看到 all clients failed）。
                    logger.warning(
                        "info AI call failed with %s: %s",
                        type(client).__name__,
                        exc,
                    )
                    continue

            logger.warning(
                "info _call_ai: all %d clients failed (last_error=%s)",
                len(clients),
                last_exc,
            )
            return {}
        finally:
            if use_semaphore:
                AI_SEMAPHORE.release()

    async def _chat_with_length_retry(
        self,
        client: Any,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        wait_timeout: float,
    ) -> dict[str, Any] | None:
        """单客户端调用，遇 finish_reason=length（输出被截断）自动重试。

        思考型模型（deepseek-v4-flash 默认思考）的推理 token 计入 max_tokens 预算，
        预算不足会先耗尽输出 → finish_reason=length 且可见内容为空。此时用更大的
        max_tokens 重试一次，避免简报/翻译生成空内容（2026-08-18 修复）。

        重试仍失败返回 None，由上层切换到下一个客户端。非空但被截断的内容
        （部分简报）直接返回，避免为已可用的输出浪费重试。
        """
        current_max = max_tokens
        for attempt in range(2):
            resp = asyncio.to_thread(
                client.chat_completion,
                messages=messages,
                temperature=temperature,
                max_tokens=current_max,
            )
            result = await asyncio.wait_for(resp, timeout=wait_timeout)
            if isinstance(result, dict):
                content = result.get("content", "")
                if content:
                    return {"choices": [{"message": {"content": content}}]}
                choices = result.get("choices")
                if choices:
                    choice0 = choices[0]
                    msg = choice0.get("message", {})
                    content = msg.get("content", "")
                    if content:
                        return result
                    finish = choice0.get("finish_reason", "")
                    if finish == "length" and attempt == 0:
                        # 思考预算耗尽：放大输出预算重试一次（上限 16K，控制成本）
                        current_max = min(16384, max_tokens * 2)
                        logger.info(
                            "info _call_ai: finish_reason=length empty content — retry with max_tokens=%d (model=%s)",
                            current_max,
                            getattr(client, "model", "?"),
                        )
                        continue
                    logger.warning(
                        "info _call_ai: empty content (finish_reason=%s, model=%s)",
                        finish,
                        getattr(client, "model", "?"),
                    )
            elif isinstance(result, str) and result.strip():
                return {"choices": [{"message": {"content": result}}]}
            else:
                logger.warning("info _call_ai: unexpected response from %s", type(client).__name__)
            return None
        return None

    def _persist_items_to_repo(self, items: list[dict[str, Any]], topics: list[str]) -> None:
        if self._info_item_repository is None or not items:
            return
        try:
            records = []
            now = datetime.now(timezone.utc).isoformat()
            for item in items:
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                records.append(
                    {
                        "url": url,
                        "title": item.get("title", ""),
                        "title_zh": item.get("title_zh") or item.get("title", ""),
                        "summary": item.get("summary", ""),
                        "summary_zh": item.get("summary_zh") or item.get("summary", ""),
                        "source": item.get("source", ""),
                        "source_label": item.get("source_display") or item.get("source", ""),
                        "category": item.get("category", "general"),
                        "score": float(item.get("score", 0) or 0),
                        "collected_at": now,
                        "tags": list(topics),
                    }
                )
            result = self._info_item_repository.upsert_items(records)
            logger.info("persisted info items: %s", result)
        except Exception as exc:
            logger.warning("persist info items failed: %s", exc)

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        if isinstance(exc, asyncio.TimeoutError):
            return "timeout"
        if isinstance(exc, aiohttp.ClientConnectorError):
            return "network_error"
        if isinstance(exc, aiohttp.ClientResponseError):
            return "http_error"
        msg = str(exc).lower()
        if "timeout" in msg:
            return "timeout"
        if "connect" in msg or "dns" in msg or "refused" in msg:
            return "network_error"
        if "parse" in msg or "json" in msg or "xml" in msg:
            return "parse_error"
        return "unknown"

    @staticmethod
    def _user_friendly_error(exc: Exception) -> str:
        if isinstance(exc, asyncio.TimeoutError):
            return "请求超时（服务器无响应）"
        if isinstance(exc, aiohttp.ClientConnectorError):
            return "网络连接失败"
        if isinstance(exc, aiohttp.ClientResponseError):
            return f"HTTP {exc.status}: {exc.message}"
        msg = str(exc)
        if "Timeout" in msg:
            return "请求超时"
        if "connect" in msg.lower() or "dns" in msg.lower():
            return "无法连接到服务器"
        return msg[:200]

    async def collect(
        self,
        topics: list[str] | None = None,
        generate_brief: bool = True,
        progress_callback: Any | None = None,
        return_early: bool = False,
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """采集资讯并返回结果。

        Args:
            topics: 兴趣话题，为 None 时使用 DEFAULT_TOPICS
            generate_brief: 是否调用 AI 生成 markdown 简报
            progress_callback: 进度回调（stream 模式用）
            return_early: 方案一 — 抓取+去重后立即写缓存并返回，AI 处理
                （翻译/分类/简报）异步在后台执行。后台完成后通过返回的
                ``_background_task`` 字段供 handler 挂接事件推送。
                返回的 dict 会带 ``ai_pending: True`` 标记。

        Returns:
            {"brief": str, "items": list[dict], "errors": list[dict]}
            return_early=True 时额外包含 ``ai_pending: True``。
        """
        topics_list = topics or DEFAULT_TOPICS

        # C1: 资讯源可配置 — 通过 registry 解析启用列表，支持显式传入 sources 子集。
        # 未传 sources 时使用 registry 中所有启用源；传入时仅采集"已启用且被请求"的源，
        # 并记录被跳过的源（请求了但被禁用或未知）。
        from services.info_source_registry import FETCHER_METHOD_MAP

        enabled_sources = self._source_registry.get_enabled_sources()
        # 空列表 sources 表示"不限源"（板块未配置源时的回退），等同未传 → 抓全源。
        if sources:
            requested_set = {str(s).strip() for s in sources if str(s).strip()}
            enabled_names = {s.name for s in enabled_sources}
            for _skip in requested_set - enabled_names:
                logger.info("info collect: skipping source '%s' (disabled or unknown)", _skip)
            active_sources = [s for s in enabled_sources if s.name in requested_set]
        else:
            active_sources = enabled_sources

        errors: list[dict[str, str]] = []
        # 先确定参与采集的源名列表（用于进度回调与结果对齐），跳过无 fetcher 的源。
        # 内置源走专用 fetcher，自定义源（is_custom + rss/xml）走通用 _fetch_rss。
        source_names: list[str] = []
        for _src in active_sources:
            _method = FETCHER_METHOD_MAP.get(_src.name)
            if _method or (_src.is_custom and _src.parser_type in ("rss", "xml")):
                source_names.append(_src.name)
            else:
                logger.warning(
                    "info collect: source '%s' has no fetcher (parser_type=%s, custom=%s), skipping",
                    _src.name,
                    _src.parser_type,
                    _src.is_custom,
                )

        if progress_callback:
            await _maybe_call(progress_callback, "collecting", {"phase": "start", "sources": source_names})

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=TOTAL_TIMEOUT),
            trust_env=True,
        ) as session:
            coros = []
            for _src in active_sources:
                _method = FETCHER_METHOD_MAP.get(_src.name)
                if _method:
                    coros.append(getattr(self, _method)(session))
                elif _src.is_custom and _src.parser_type in ("rss", "xml"):
                    coros.append(self._fetch_rss(session, _src.url, _src.name, limit=10))
            results = await asyncio.gather(*coros, return_exceptions=True) if coros else []

        all_items: list[dict[str, Any]] = []
        source_stats: dict[str, int] = {}
        failed_sources: list[str] = []
        for name, result in zip(source_names, results):
            is_experimental = name in _EXPERIMENTAL_SOURCES
            if isinstance(result, Exception):
                if is_experimental:
                    # 实验性源静默降级，不污染错误列表
                    logger.debug("experimental source %s failed (silent): %s", name, result)
                else:
                    err_type = self._classify_error(result)
                    errors.append({"source": name, "error": self._user_friendly_error(result), "type": err_type})
                    failed_sources.append(name)
                    logger.warning("source %s failed [%s]: %s", name, err_type, result)
            elif result:
                all_items.extend(result)
                source_stats[name] = len(result)
            elif not is_experimental:
                # 核心源返回空数据才报"暂无数据"
                errors.append({"source": name, "error": "暂无数据", "type": "empty"})
                failed_sources.append(name)

        # C3: 抓取 RSS/Atom 订阅并合并进同一流水线（翻译/分类/去重）
        # RSS 订阅项标记为 source="rss:{feed_name}"，与内置源区分。
        rss_item_count = 0
        rss_sources_ok: list[str] = []
        if self._rss_subscriber is not None:
            try:
                rss_results = await self._rss_subscriber.fetch_all()
            except Exception as exc:
                logger.warning("rss fetch_all failed: %s", exc)
                rss_results = {}
            for feed_url, feed_items in rss_results.items():
                if not feed_items:
                    continue
                feed_name = self._rss_feed_name(feed_url)
                rss_source_tag = f"rss:{feed_name}" if feed_name else "rss"
                converted = self._convert_rss_items(feed_items, rss_source_tag)
                if converted:
                    all_items.extend(converted)
                    source_stats[rss_source_tag] = len(converted)
                    rss_sources_ok.append(rss_source_tag)
                    rss_item_count += len(converted)
            if rss_item_count:
                logger.info(
                    "info collect: %d items from %d RSS feeds",
                    rss_item_count,
                    len(rss_sources_ok),
                )

        if progress_callback:
            await _maybe_call(
                progress_callback,
                "collecting",
                {
                    "phase": "fetched",
                    "items": len(all_items),
                    "sources_ok": list(source_stats.keys()),
                    "sources_failed": failed_sources,
                },
            )

        logger.info(
            "info collect: %d items from %d sources, stats=%s",
            len(all_items),
            len(source_stats),
            source_stats,
        )

        if not all_items:
            logger.warning(
                "info collect: all sources failed/empty, errors=%s",
                [{"source": e["source"], "type": e.get("type", "")} for e in errors],
            )
            return {"brief": "", "items": [], "errors": errors}

        all_items = self._dedup_items(all_items)
        logger.info("after dedup: %d items", len(all_items))

        if progress_callback:
            await _maybe_call(
                progress_callback,
                "collecting",
                {
                    "phase": "deduped",
                    "items": len(all_items),
                },
            )

        # 方案一：抓取+去重后立即写缓存，让前端能马上读到 items。
        # source_display 预填充（不依赖 AI），确保前端来源显示正常。
        for item in all_items:
            item["source_display"] = source_display(item.get("source", ""))
            # 关键词预分类（本地，无 AI 调用，毫秒级）
            if not item.get("category"):
                item["category"] = categorize_item(item)[0]

        # topics→采集联动：按关注领域相关度重排（不删条目），热度分兜底
        all_items = apply_topic_linkage(all_items, topics_list)
        _cache["items"] = all_items[:_CACHE_MAX_ITEMS]
        _cache["brief"] = ""  # AI 生成后回填
        _cache["cached_at"] = datetime.now(timezone.utc).timestamp()
        _save_persisted_cache()

        self._persist_items_to_repo(all_items, topics_list)

        if progress_callback:
            await _maybe_call(
                progress_callback,
                "collecting",
                {
                    "phase": "persisted",
                    "items": len(all_items),
                },
            )

        # 方案一：return_early 模式立即返回，AI 处理交由 _post_process_async 后台执行
        if return_early:
            logger.info("info collect: return_early=True, items ready (%d), AI processing deferred", len(all_items))
            return {
                "brief": "",
                "items": all_items,
                "errors": errors,
                "ai_pending": True,
            }

        # 同步模式：继续执行 AI 翻译/分类/简报
        brief = await self._post_process_ai(all_items, topics_list, errors, generate_brief, progress_callback)

        if progress_callback:
            await _maybe_call(progress_callback, "collecting", {"phase": "done", "items": len(all_items)})

        return {"brief": brief, "items": all_items, "errors": errors}

    async def _post_process_ai(
        self,
        all_items: list[dict[str, Any]],
        topics_list: list[str],
        errors: list[dict[str, str]],
        generate_brief: bool,
        progress_callback: Any | None = None,
    ) -> str:
        """执行 AI 后处理：翻译 → AI 分类修正 → 简报生成。

        方案一：此方法可被 collect(return_early=True) 后台调用，
        完成后由调用方负责 emit info.collected 事件推送结果到前端。
        """
        if self._primary_ai:
            if progress_callback:
                await _maybe_call(progress_callback, "collecting", {"phase": "translating"})
            await self._translator.translate_items(all_items, errors)

        # AI 修正低置信度条目的分类
        ai_categorize_candidates = self._categorizer.pre_categorize(all_items)
        if ai_categorize_candidates and self._primary_ai:
            # 限制 AI 分类数量，避免过长 prompt 和过多 token
            await self._categorizer.ai_categorize_batch(ai_categorize_candidates[:40], errors)

        # 翻译/分类完成后更新缓存（让后续 info.cached_items 也能读到翻译）
        # 重跑联动：翻译后的中文字段此时才可命中中文 topic
        all_items = apply_topic_linkage(all_items, topics_list)
        _cache["items"] = all_items[:_CACHE_MAX_ITEMS]
        _cache["cached_at"] = datetime.now(timezone.utc).timestamp()
        _save_persisted_cache()

        brief = ""
        if generate_brief and self._primary_ai:
            if progress_callback:
                await _maybe_call(progress_callback, "collecting", {"phase": "generating"})
            brief = await self._generate_brief(all_items, topics_list, errors)
            _cache["brief"] = brief
            _save_persisted_cache()

        return brief

    # ── C3: RSS 订阅条目转换 ──────────────────────────────────────

    def _rss_feed_name(self, feed_url: str) -> str:
        """根据 feed URL 反查订阅名（用于 source="rss:{name}" 标记）。"""
        sub = None
        if self._rss_subscriber is not None:
            try:
                for entry in self._rss_subscriber.list_subscriptions():
                    if entry.get("url") == feed_url:
                        sub = entry
                        break
            except Exception:
                pass
        if sub is None:
            return ""
        return str(sub.get("name") or sub.get("feed_title") or "")

    @staticmethod
    def _convert_rss_items(rss_items: list[dict[str, Any]], source_tag: str) -> list[dict[str, Any]]:
        """将 RSSSubscriber 的归一化条目转换为 InfoCollectorService 内部格式。

        RSS 条目: ``{title, url, summary, published_at, source}``
        内部条目: ``{id, title, url, source, summary, tags, score, publishedAt}``
        其中 ``publishedAt`` 为毫秒时间戳（int），解析失败为 0。
        """
        out: list[dict[str, Any]] = []
        for idx, item in enumerate(rss_items):
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            if not title and not url:
                continue
            ts = 0
            pub = str(item.get("published_at") or "")
            if pub:
                try:
                    dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                    ts = int(dt.timestamp() * 1000)
                except Exception:
                    try:
                        from email.utils import parsedate_to_datetime

                        dt = parsedate_to_datetime(pub)
                        ts = int(dt.timestamp() * 1000)
                    except Exception:
                        pass
            out.append(
                {
                    "id": f"{source_tag}-{idx}-{url}"[:200],
                    "title": title,
                    "url": url,
                    "source": source_tag,
                    "summary": _strip_html(str(item.get("summary") or title))[:500],
                    "tags": [],
                    "score": 0,
                    "publishedAt": ts,
                }
            )
        return out

    @staticmethod
    def _dedup_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen_urls: set[str] = set()
        seen_titles: list[str] = []
        result: list[dict[str, Any]] = []
        for item in items:
            url = (item.get("url") or "").rstrip("/").split("?")[0]
            if not url:
                url = item.get("id", "")
            if url in seen_urls:
                continue
            title = item.get("title", "")
            is_dup = False
            for existing_title in seen_titles:
                if _title_similarity(title, existing_title) > DEDUP_SIMILARITY_THRESHOLD:
                    is_dup = True
                    break
            if is_dup:
                continue
            seen_urls.add(url)
            seen_titles.append(title)
            result.append(item)
        return result

    @staticmethod
    def get_cached() -> dict[str, Any]:
        return {
            "items": _cache.get("items", []),
            "brief": _cache.get("brief", ""),
            "cached_at": _cache.get("cached_at", 0.0),
        }

    # T3.6：公共别名，消除能力路由对私有静态方法的依赖
    # （原调用方直接 import 了类上的私有静态方法，现统一走公共别名）。

    async def _generate_brief(
        self,
        items: list[dict[str, Any]],
        topics: list[str],
        errors: list[dict[str, str]],
    ) -> str:
        selected = items[:BRIEF_MAX_ITEMS]

        for item in selected:
            item["_section"] = item.get("category") or categorize_item(item)[0]

        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in selected:
            sec = item["_section"]
            grouped.setdefault(sec, []).append(item)

        # AI/dev/tool 优先展示，即使条目少也不跳过
        _PRIORITY_SECTIONS = {"ai", "dev", "tool"}
        section_order = get_effective_section_order()
        active_sections = [s for s in section_order if grouped.get(s) and (len(grouped[s]) >= 1 if s in _PRIORITY_SECTIONS else len(grouped[s]) >= 2)]

        def _fmt_item(idx: int, item: dict[str, Any]) -> str:
            title_display = item.get("title_zh") or item.get("title", "")
            title_en = item.get("title", "")
            source_disp = source_display(item.get("source", ""))
            summary_disp = item.get("summary_zh") or _strip_html(item.get("summary", "")) or title_display
            en_line = f"    EN: {title_en}\n" if title_en and title_en != title_display and not has_cjk(title_en) else ""
            return (
                f"[{idx}] {title_display}\n"
                f"{en_line}"
                f"    来源: {source_disp} | 热度: {item.get('score', 0)}\n"
                f"    摘要: {summary_disp}\n"
                f"    链接: {item.get('url', '')}"
            )

        def _fmt_grouped(compact: bool = False) -> str:
            blocks: list[str] = []
            g_idx = 0
            for sec_key in active_sections:
                meta = get_effective_section_meta(sec_key)
                sec_items = grouped[sec_key]
                header = f"## 【{meta['emoji']} {meta['name']}】（共{len(sec_items)}条）"
                lines = [header]
                for item in sec_items:
                    g_idx += 1
                    if compact:
                        # 内核通道紧凑版：标题 + 短摘要（截断到 80 字符），
                        # 控制 prompt 长度，避免 Windows 命令行上限（8191 字符）。
                        summary_disp = item.get("summary_zh") or _strip_html(item.get("summary", "")) or item.get("title_zh") or item.get("title", "")
                        if len(summary_disp) > 80:
                            summary_disp = summary_disp[:80] + "…"
                        lines.append(
                            f"[{g_idx}] {item.get('title_zh') or item.get('title', '')}（{source_display(item.get('source', ''))}）：{summary_disp}"
                        )
                    else:
                        lines.append(_fmt_item(g_idx, item))
                blocks.append("\n\n".join(lines))
            return "\n\n".join(blocks)

        raw_text = wrap_untrusted(_BRIEF_UNTRUSTED_SOURCE, _fmt_grouped())
        now_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y年%m月%d日")

        sections_desc_parts = []
        for sec_key in active_sections:
            meta = get_effective_section_meta(sec_key)
            sections_desc_parts.append(f"{meta['emoji']} {meta['name']}")
        sections_desc = "、".join(sections_desc_parts) if sections_desc_parts else "综合资讯"

        section_instructions = ""
        for sec_key in active_sections:
            meta = get_effective_section_meta(sec_key)
            section_instructions += f"### {meta['emoji']} {meta['name']}\n（相关条目，若无合适内容则省略此分类）\n\n"

        prompt = build_brief_prompt(
            now_str=now_str,
            topics=topics,
            sections_desc=sections_desc,
            section_instructions=section_instructions,
            raw_text=raw_text,
            total_count=len(selected),
            compact=False,
        )

        system_content = (
            "你是一位资深的科技媒体编辑，擅长从海量技术资讯中筛选最有价值的内容，"
            "写出有深度、有观点的中文技术简报。你的风格是：专业、精炼、有判断力，"
            "不罗列信息而是提炼要点，中英对照自然流畅，点评有独到见解。"
            "严格按要求的Markdown格式输出，不要输出任何额外说明。"
            "板块名称必须与prompt中指定的完全一致，不得自行创造板块。"
        )

        async def _cloud_fallback() -> str:
            resp = await self._call_ai(
                use_semaphore=True,
                allow_local=False,  # 简报只用云端（用户决策 2026-08-17：本地太慢）
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                # 2026-10-15 探针实测：v4-flash 思考 token 计入 max_tokens，
                # 4000 时简报会被拦腰截断（finish_reason=length，1087 字断在句中）
                max_tokens=8000,
                timeout=90,
            )
            choice = resp.get("choices", [{}])[0] if isinstance(resp, dict) else {}
            finish = choice.get("finish_reason")
            if finish == "length":
                logger.warning("brief generation hit max_tokens (finish_reason=length), output truncated")
            return str(choice.get("message", {}).get("content", ""))

        # M-cron 收敛：简报生成优先走成熟内核（dsh 等），云端 _call_ai 兜底
        # 内核通道使用紧凑版 prompt（避免 Windows 命令行上限 8191 字符）
        kernel_prompt = prompt
        if len(prompt.encode("utf-8")) > 7000:
            compact_raw = wrap_untrusted(_BRIEF_UNTRUSTED_SOURCE, _fmt_grouped(compact=True))
            kernel_prompt = build_brief_prompt(
                now_str=now_str,
                topics=topics,
                sections_desc=sections_desc,
                section_instructions=section_instructions,
                raw_text=compact_raw,
                total_count=len(selected),
                compact=True,
            )
        try:
            from services.kernel_ai import kernel_generate

            content = ""
            if self._agent_gateway is not None:
                content = kernel_generate(
                    self._agent_gateway,
                    kernel_prompt,
                    system=system_content,
                    timeout_s=300,
                )
            if not content:
                content = await _cloud_fallback()
            if content and not content.strip().startswith("#") and not content.strip().startswith("###"):
                content = content.strip().lstrip("```markdown").lstrip("```").rstrip("```").strip()
            return content
        except Exception as e:
            errors.append({"source": "ai", "error": str(e)})
            logger.warning("AI brief generation failed: %s", e, exc_info=True)


# T3.6：公共别名，消除能力路由对私有静态方法的依赖
# （原调用方直接 import 了类上的私有静态方法，现统一走公共别名）。
def dedup_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """公开别名：信息去重（实现见同名私有静态方法）。"""
    return InfoCollectorService._dedup_items(list(items))


__all__ = [
    "InfoCollectorService",
    "DEFAULT_TOPICS",
    "set_section_keywords",
    "get_section_keywords",
]
