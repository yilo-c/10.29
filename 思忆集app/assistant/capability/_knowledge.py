"""Knowledge capability domain — news, memory recall, web search/fetch, subagent.

Extracted from capability_router.py — KnowledgeCapabilityDomain (was capability_router_knowledge.py).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus
from urllib.request import urlopen
from xml.etree import ElementTree

from ..models import PlannedStep

logger = logging.getLogger("siyi.assistant.capability.knowledge")


class KnowledgeCapabilityDomain:
    """Knowledge/lookup capabilities — news, memory, web, subagent."""

    def __init__(self, router) -> None:
        self.router = router

    def _process_results(
        self,
        items: list[dict[str, Any]],
        *,
        query: str,
        limit: int,
    ) -> dict[str, Any]:
        registry = getattr(self.router, "source_registry", None)
        if registry is None:
            normalized = self._dedupe_items(items, title_key="title", url_key="url")[:limit]
            return {
                "items": normalized,
                "original_count": len(items),
                "deduped_count": len(normalized),
                "final_count": len(normalized),
                "query": query,
                "governed": False,
            }
        processed = registry.process_search_results(items, query=query, max_items=limit)
        processed["governed"] = True
        return processed

    def news_lookup(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query") or "AI OR 软件工程").strip()
        limit = max(1, min(int(payload.get("limit") or 8), 20))
        source_filter = str(payload.get("source") or "all").strip()
        category_filter = str(payload.get("category") or "").strip()
        cache_key = self._build_cache_key("news_lookup", query=query, limit=limit, source=source_filter, category=category_filter)
        cached = self._load_cached_payload("news_cache", cache_key, ttl_seconds=1800)
        if cached is not None:
            return cached
        try:
            rss_url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
            with urlopen(rss_url, timeout=10) as response:
                raw = response.read()
            root = ElementTree.fromstring(raw)
            items = []
            for node in root.findall("./channel/item")[:limit]:
                title = (node.findtext("title") or "").strip()
                link = (node.findtext("link") or "").strip()
                pub_date = (node.findtext("pubDate") or "").strip()
                if not title:
                    continue
                items.append(
                    {
                        "title": title,
                        "url": link,
                        "source": "google_news",
                        "category": "",
                        "summary": "",
                        "published_at": pub_date,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            processed = self._process_results(items, query=query, limit=limit)
            items = list(processed.get("items") or [])
            result = {
                "query": query,
                "source_filter": source_filter,
                "category_filter": category_filter,
                "count": len(items),
                "items": items,
                "governance": {
                    "original_count": processed.get("original_count", len(items)),
                    "deduped_count": processed.get("deduped_count", len(items)),
                    "final_count": processed.get("final_count", len(items)),
                    "governed": bool(processed.get("governed")),
                },
            }
            self._save_cached_payload("news_cache", cache_key, result)
            return result
        except Exception:
            logger.warning("[Knowledge] News lookup failed for query: %s", query, exc_info=True)
            return {
                "query": query,
                "source_filter": source_filter,
                "category_filter": category_filter,
                "count": 0,
                "items": [],
                "governance": {"original_count": 0, "deduped_count": 0, "final_count": 0, "governed": False},
            }

    def news_briefing(self, payload: dict[str, Any]) -> dict[str, Any]:
        topic = str(payload.get("topic") or "AI OR 科技").strip()
        max_items = max(3, min(int(payload.get("max_items") or 10), 30))
        raw_result = self.news_lookup({"query": topic, "limit": max_items, "source": "all"})
        news_items = raw_result.get("items") or []
        if not news_items:
            return {"topic": topic, "briefing": "暂无相关新闻。", "source_count": 0, "items": []}
        lines = []
        for index, item in enumerate(news_items, 1):
            src = item.get("source") or "未知"
            cat = item.get("category") or ""
            ts = item.get("published_at") or ""
            summary = item.get("summary") or ""
            line = f"{index}. [{cat}] {item.get('title', '')} — {src}"
            if ts:
                line += f" ({ts})"
            if summary:
                line += f"\n   {summary}"
            lines.append(line)
        news_text = "\n".join(lines)
        briefing_prompt = (
            f"你是一位专业的新闻编辑。请根据以下{len(news_items)}条新闻标题和摘要，为用户生成一份关于「{topic}」的中文简报摘要。\n\n"
            "要求：\n"
            "1. 按重要性排序，合并相关报道\n"
            "2. 添加简短评论和趋势分析\n"
            "3. 保持客观、简洁，控制在 500 字以内\n"
            "4. 在末尾列出信息来源\n\n"
            f"新闻列表：\n{news_text}"
        )
        try:
            from services.ai_service import create_fallback_client

            ai_client = create_fallback_client(settings_repository=self.router.settings_repository)
            messages = [
                {"role": "system", "content": "你是一位中文新闻简报编辑，善于提炼要点和趋势分析。"},
                {"role": "user", "content": briefing_prompt},
            ]
            briefing_text = ai_client.complete_text(messages, max_tokens=800, temperature=0.4).strip() or "生成失败"
        except Exception as exc:
            logger.warning("LLM 新闻简报生成失败: %s", exc)
            briefing_text = f"## {topic} 新闻速览\n\n" + news_text
        sources_seen = list(dict.fromkeys(item.get("source", "") for item in news_items if item.get("source")))
        return {
            "topic": topic,
            "briefing": briefing_text,
            "source_count": len(sources_seen),
            "sources_used": sources_seen,
            "item_count": len(news_items),
            "items": news_items,
        }

    def memory_recall(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.router.memory_index_service is None:
            return {"success": False, "reason": "memory_index_unavailable"}
        query = str(payload.get("query") or "").strip()
        top_k = max(1, min(int(payload.get("top_k") or 8), 20))
        source_filters = payload.get("source_filters") or []
        if isinstance(source_filters, str):
            source_filters = [part.strip() for part in re.split(r"[\s,，]+", source_filters) if part.strip()]
        result = self.router.memory_index_service.recall(
            query,
            top_k=top_k,
            source_filters=list(source_filters or []),
            days=payload.get("days"),
            start_at=payload.get("start_at"),
            end_at=payload.get("end_at"),
        )
        result["success"] = True
        return result

    def web_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query") or "").strip()
        if not query:
            return {"success": False, "reason": "no_query"}
        num_results = max(1, min(int(payload.get("num_results") or 5), 10))
        try:
            # P1-B（2026-09-18 实测）：原 import 写成 `from web_search_service
            # import WebSearchService`——顶层模块不存在（真源 = services.web_search），
            # ModuleNotFoundError 被本 except 吞掉 → web.search 恒报"No module
            # named 'web_search_service'"，声明+白名单齐全但联网搜索实际全灭。
            from services.web_search import WebSearchService

            svc = WebSearchService(settings_repository=self.router.settings_repository)
            results = svc.search(query, num_results=num_results)
            result_dicts = [r.to_dict() for r in results]
            processed = self._process_results(result_dicts, query=query, limit=num_results)
            governed_results = list(processed.get("items") or [])
            # format_results_for_prompt 走属性访问（r.title/r.url/r.snippet），
            # 治理后的 items 是 dict —— 用轻量包装对齐接口（原实现直接传 dict
            # 会 AttributeError，被外层 except 吞成同款"静默死链"）。
            from types import SimpleNamespace

            fmt_items = [
                SimpleNamespace(
                    title=str(g.get("title") or ""),
                    url=str(g.get("url") or ""),
                    snippet=str(g.get("snippet") or g.get("summary") or ""),
                )
                for g in governed_results
            ]
            formatted = svc.format_results_for_prompt(fmt_items, query)
            return {
                "success": True,
                "query": query,
                "count": len(governed_results),
                "results": governed_results,
                "formatted": formatted,
                "governance": {
                    "original_count": processed.get("original_count", len(result_dicts)),
                    "deduped_count": processed.get("deduped_count", len(governed_results)),
                    "final_count": processed.get("final_count", len(governed_results)),
                    "governed": bool(processed.get("governed")),
                },
            }
        except Exception as exc:
            logger.warning("web.search 失败: %s", exc)
            return {"success": False, "reason": str(exc)}

    def web_fetch(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = str(payload.get("url") or "").strip()
        if not url:
            return {"success": False, "reason": "no_url"}
        try:
            from services.web_fetch import WebFetchService

            svc = WebFetchService()
            result = svc.fetch_sync(url)
            if not result.ok:
                return {"success": False, "reason": result.error, "url": url}
            return {
                "success": True,
                "url": url,
                "title": result.title,
                "content": result.content[:20000],
                "extractor": result.extractor,
                "truncated": result.truncated,
            }
        except Exception as exc:
            logger.warning("web.fetch 失败: %s", exc)
            return {"success": False, "reason": str(exc)}

    def get_subagent_manager(self):
        if self.router.subagent_manager is not None:
            return self.router.subagent_manager
        try:
            from services.ai_service import create_fallback_client
            from services.subagent_service import SubagentManager

            self.router.subagent_manager = SubagentManager(
                data_store=self.router.data_store,
                settings_repository=self.router.settings_repository,
                observability_service=self.router.observability_service,
                ai_client_factory=lambda: create_fallback_client(settings_repository=self.router.settings_repository),
                capability_executor=self.execute_subagent_steps,
                note_repository=self.router.note_repository,
                inbox_repository=self.router.inbox_repository,
                memory_index_service=self.router.memory_index_service,
            )
            return self.router.subagent_manager
        except Exception as exc:
            logger.warning("Subagent manager 初始化失败: %s", exc)
            return None

    def execute_subagent_steps(self, steps: list[dict[str, Any]], parallel: bool) -> dict[str, Any]:
        planned_steps: list[PlannedStep] = []
        for index, item in enumerate(steps or [], start=1):
            if isinstance(item, PlannedStep):
                planned_steps.append(item)
                continue
            if not isinstance(item, dict):
                continue
            planned_steps.append(
                PlannedStep(
                    step_id=str(item.get("step_id") or f"subagent_step_{index}"),
                    action_id=str(item.get("action_id") or "").strip(),
                    payload=dict(item.get("payload") or {}),
                    risk_level=str(item.get("risk_level") or "low"),
                    timeout_seconds=int(item.get("timeout_seconds") or 20),
                    retries=int(item.get("retries") or 1),
                )
            )
        if not planned_steps:
            return {"status": "skipped", "reason": "no_steps", "outcomes": []}
        if not parallel:
            outcomes = []
            for step in planned_steps:
                try:
                    outcomes.append({"action_id": step.action_id, "status": "ok", "payload": self.router.execute(step)})
                except Exception as exc:
                    outcomes.append({"action_id": step.action_id, "status": "failed", "error": str(exc)})
            return {"status": "completed", "outcomes": outcomes, "total": len(outcomes)}
        outcomes = []
        with ThreadPoolExecutor(max_workers=min(3, len(planned_steps))) as executor:
            future_map = {executor.submit(self.router.execute, step): step for step in planned_steps}
            for future, step in future_map.items():
                try:
                    outcomes.append({"action_id": step.action_id, "status": "ok", "payload": future.result()})
                except Exception as exc:
                    outcomes.append({"action_id": step.action_id, "status": "failed", "error": str(exc)})
        return {"status": "completed", "outcomes": outcomes, "total": len(outcomes)}

    def subagent_spawn(self, payload: dict[str, Any]) -> dict[str, Any]:
        manager = self.get_subagent_manager()
        if manager is None:
            return {"success": False, "reason": "subagent_unavailable"}
        workflow = str(payload.get("workflow") or "execute_steps").strip() or "execute_steps"
        run = manager.spawn(workflow, payload=dict(payload or {}), label=str(payload.get("label") or workflow))
        return {"success": True, "run": run}

    def subagent_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        manager = self.get_subagent_manager()
        if manager is None:
            return {"success": False, "reason": "subagent_unavailable"}
        limit = max(1, min(int(payload.get("limit") or 20), 100))
        runs = manager.list_runs(limit=limit)
        return {"success": True, "count": len(runs), "runs": runs}

    def subagent_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        manager = self.get_subagent_manager()
        if manager is None:
            return {"success": False, "reason": "subagent_unavailable"}
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            return {"success": False, "reason": "missing_run_id"}
        run = manager.get_run(run_id)
        return {"success": run is not None, "run": run}

    @staticmethod
    def _dedupe_items(items: list[dict[str, Any]], *, title_key: str = "title", url_key: str = "url") -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            key = (str(item.get(url_key) or "").strip().lower(), str(item.get(title_key) or "").strip().lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    @staticmethod
    def _build_cache_key(namespace: str, **payload: Any) -> str:
        material = json.dumps({"namespace": namespace, **payload}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _load_cached_payload(self, storage_key: str, cache_key: str, *, ttl_seconds: int) -> dict[str, Any] | None:
        try:
            bucket = self.router.data_store.load_json(storage_key, {})
        except Exception:
            return None
        if not isinstance(bucket, dict):
            return None
        entry = bucket.get(cache_key)
        if not isinstance(entry, dict):
            return None
        cached_at = float(entry.get("cached_at") or 0)
        if not cached_at or (time.time() - cached_at) > ttl_seconds:
            return None
        payload = entry.get("payload")
        return dict(payload) if isinstance(payload, dict) else None

    def _save_cached_payload(self, storage_key: str, cache_key: str, payload: dict[str, Any]) -> None:
        def _updater(bucket: dict[str, Any]) -> dict[str, Any]:
            normalized = dict(bucket) if isinstance(bucket, dict) else {}
            normalized[cache_key] = {"cached_at": time.time(), "payload": dict(payload or {})}
            if len(normalized) > 128:
                ordered_keys = sorted(normalized, key=lambda item: float((normalized.get(item) or {}).get("cached_at") or 0), reverse=True)
                normalized = {key: normalized[key] for key in ordered_keys[:128]}
            return normalized

        self.router.data_store.update_json(storage_key, {}, _updater)
