"""BrowserBridgeContentMixin — 汇总所有内容处理混入。

继承 SummaryMixin / TranscriptMixin / DiagnosticsMixin 提供：
网页总结、字幕转录、笔记/任务创建、诊断快照、阅读列表保存。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from services._url_learning_utils import _correct_domain_terms, _dedupe_text_blocks, _strip_packaging_text

from ._bridge_diagnostics import DiagnosticsMixin
from ._bridge_summary import SummaryMixin
from ._bridge_transcript import TranscriptMixin

# word.add 的单一真源（浏览器桥 add-word 端点与 JSON-RPC word.add 共用）
from backend.handlers._wordbook import add_word_entry

# F-5: 复用外部内容集成的阅读列表持久化 helper
from backend.handlers._external_content import (
    _add_article,
    _dedupe_existing,
    _ensure_knowledge_entity,
    _get_articles_path_from_store,
    _link_source_entity,
    _load_articles,
    _save_articles,
)

logger = logging.getLogger("siyi.browser_bridge")


class BrowserBridgeContentHost(Protocol):
    """BrowserBridgeContentMixin 依赖的宿主契约 — 由 BrowserAssistantBridgeService 运行时注入。"""

    ai_service: Any
    inbox_capture_service: Any
    content_organizer_service: Any
    memory_extractor: Any
    chat_session_repository: Any
    memory_index_service: Any
    vocabulary_repository: Any


class BrowserBridgeContentMixin(SummaryMixin, TranscriptMixin, DiagnosticsMixin):
    """内容处理混入，为 BrowserAssistantBridgeService 提供总结/转录/笔记/任务等能力。"""

    # 宿主契约：由 BrowserAssistantBridgeService 运行时注入
    if TYPE_CHECKING:
        ai_service: Any
        inbox_capture_service: Any
        content_organizer_service: Any
        memory_extractor: Any
        chat_session_repository: Any
        memory_index_service: Any
        vocabulary_repository: Any

    def explain_question(self, payload: dict[str, Any]) -> dict[str, Any]:
        """根据用户的问题和页面上下文提供 LLM 深度解释。

        2026-10-17 修复：生产注入的 ai_service 是 JudgeAwareClient（app_runtime/
        _context_builder.py: ai_service=ctx.ai_client），它只有 chat_completion
        接口、没有 explain_concept —— 旧实现直接调 explain_concept 导致侧栏聊天
        提问路径 100% 崩溃（'JudgeAwareClient' object has no attribute
        'explain_concept'）。现改为：优先用 explain_concept（兼容测试假件/未来
        实现），否则回退 chat_completion（返回 OpenAI 风格 dict 或 str 双形态提取，
        与 inbox_capture_service 等消费方同惯例）。
        """
        question = str(payload.get("question") or "").strip()
        context = str(payload.get("context") or "").strip()
        page_text = str(payload.get("pageText") or payload.get("page_text") or "").strip()

        if not question:
            raise ValueError("question 参数不能为空")

        if self.ai_service is None:
            raise RuntimeError("AI 服务未初始化")

        explainer = getattr(self.ai_service, "explain_concept", None)
        if callable(explainer):
            explanation = explainer(
                concept=question,
                context=context,
                page_text=page_text,
                max_tokens=800,
            )
        else:
            prompt_parts = [f"用户问题：{question}"]
            if context:
                prompt_parts.append(f"页面上下文：{context}")
            if page_text:
                prompt_parts.append(f"页面正文（节选）：\n{page_text[:4000]}")
            prompt_parts.append("请结合页面内容直接回答用户问题，简明扼要，用中文。")
            response = self.ai_service.chat_completion(
                messages=[
                    {"role": "system", "content": "你是思忆集浏览器助手中的页面对话助手，基于用户提供的网页上下文回答问题。"},
                    {"role": "user", "content": "\n\n".join(prompt_parts)},
                ],
                temperature=0.3,
                max_tokens=800,
            )
            if isinstance(response, dict):
                choices = response.get("choices", [])
                explanation = (choices[0].get("message", {}) or {}).get("content", "") if choices else ""
            elif isinstance(response, str):
                explanation = response
            else:
                explanation = ""
            explanation = str(explanation or "").strip()
            if not explanation:
                raise RuntimeError("AI 未返回解释内容")

        return {
            "ok": True,
            "explanation": explanation,
            "question": question,
        }

    # ── 收件/笔记/任务 ──

    def capture_page_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = self._build_capture_text(payload)
        title = str(payload.get("title") or "").strip() or "浏览器收件"
        url = str(payload.get("url") or "").strip()
        # AG-C1②：后端判空。扩展端只回传 title/url（甚至只有标题）时，
        # _build_capture_text 仍会拼出"标题：…"这类装饰行，text 非空便穿过
        # inbox_capture_service 的非空判定（:83）落进收件箱，攒出一堆无正文空条目。
        # 判空口径与 :83 既有语义对齐——看真身载荷、不看装饰行：
        # 正文（选区/摘要/全文）全空 **且** 无 url 可回溯 → 拒收，不入库。
        # 有 url 时仍放行（书签式收件是合法用法，url 本身即可回溯内容）。
        if not self._capture_body(payload) and not url:
            self._track_event("browser.bridge.capture_rejected", {"reason": "empty_payload"})
            return {"ok": False, "error": "empty_payload", "reason": "没有可收件的内容。"}
        meta = {
            "url": url,
            "selection_length": len(str(payload.get("selectionText") or "")),
            "content_kind": str(payload.get("contentKind") or "").strip(),
        }
        executed_action = self._extract_executed_recommended_action(payload)

        if self.inbox_capture_service is not None:
            result = self.inbox_capture_service.capture(
                text,
                source="browser_extension",
                title=title,
                tags=["browser", "web"],
                metadata=meta,
                skip_guard=True,
            )
            if not result.get("ok"):
                return {"ok": False, "error": result.get("reason", "capture_failed")}
            entry = result["entry"]
        elif self.workbench_service is not None:
            entry = self.workbench_service.capture_quick_entry(
                text,
                source="browser_extension",
                title=title,
                tags=["browser", "web"],
                metadata=meta,
            )
        else:
            return {"ok": False, "error": "workbench_service_unavailable"}

        self._track_event("browser.bridge.captured", {"entry_id": entry.get("id"), "url": payload.get("url")})
        self._queue_assistant_event(
            "browser.captured",
            {
                "title": entry.get("title") or str(payload.get("title") or "浏览器收件"),
                "url": str(payload.get("url") or "").strip(),
                "entry_id": entry.get("id"),
                "contentKind": str(payload.get("contentKind") or "").strip(),
                "executedRecommendedAction": executed_action,
            },
        )
        return {"ok": True, "entry": entry}

    def save_page_to_reading(self, payload: dict[str, Any]) -> dict[str, Any]:
        """将当前页面保存到阅读列表（saved_articles.json）。"""
        title = str(payload.get("title") or "").strip() or "网页阅读项"
        url = str(payload.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "url required"}

        # 尝试从 settings_repository 拿到数据目录；否则用 helper 的 fallback
        settings_repo = getattr(self, "settings_repository", None)
        local_data_store = getattr(settings_repo, "data_store", None) if settings_repo else None
        articles_path = _get_articles_path_from_store(local_data_store)

        source = str(payload.get("source") or "browser_extension").strip() or "browser_extension"
        external_id = str(payload.get("externalId") or url)
        summary = str(payload.get("summary") or "").strip()
        tags = payload.get("tags")
        if not isinstance(tags, list):
            tags = ["browser", "web"]
        tags = [str(t).strip() for t in tags if str(t).strip()]

        data = _load_articles(articles_path)
        existing_id = _dedupe_existing(data, source, external_id)
        if existing_id:
            _save_articles(articles_path, data)
            return {
                "ok": True,
                "articleId": existing_id,
                "title": title,
                "url": url,
                "skipped": True,
            }

        article_id = _add_article(
            data,
            source,
            external_id,
            title,
            url,
            summary=summary,
            tags=tags,
            extra={
                "saved_from": "browser_extension",
                "content_kind": str(payload.get("contentKind") or "").strip(),
            },
        )
        _save_articles(articles_path, data)

        graph_store = getattr(self, "memory_index_service", None)
        try:
            entity_id = _ensure_knowledge_entity(graph_store, article_id, title, source, url)
            if entity_id:
                _link_source_entity(graph_store, entity_id, source)
        except Exception as exc:
            logger.debug("save_page_to_reading knowledge link failed: %s", exc)

        self._track_event("browser.bridge.reading_saved", {"article_id": article_id, "url": url})
        self._queue_assistant_event(
            "browser.reading_saved",
            {
                "title": title,
                "url": url,
                "article_id": article_id,
                "source": source,
            },
        )
        return {"ok": True, "articleId": article_id, "title": title, "url": url}

    def create_note_from_page(self, payload: dict[str, Any]) -> dict[str, Any]:
        content_organizer = self.content_organizer_service
        title = str(payload.get("title") or "").strip() or "网页摘要"
        content = self._build_note_markdown(payload)
        url = str(payload.get("url") or "").strip()
        content_kind = str(payload.get("contentKind") or "article").strip()
        executed_action = self._extract_executed_recommended_action(payload)

        if content_organizer is not None:
            from services.content_organizer_service import ContentOrganizeRequest

            request = ContentOrganizeRequest(
                title=title,
                content=content,
                source_type="browser_extension",
                source_url=url,
                tags=["browser", "summary", content_kind],
                category="网页摘要",
            )
            result = content_organizer.convert_to_note(request)
            if not result.get("success"):
                return {"ok": False, "error": result.get("error", "convert_failed")}
            note_id = result.get("note_id")
        elif self.note_repository is not None:
            note_id = self.note_repository.add_note(
                {
                    "title": title,
                    "content": content,
                    "category": "网页摘要",
                    "tags": ["browser", "summary", content_kind],
                    "folder_path": "/网页摘要",
                }
            )
        else:
            return {"ok": False, "error": "note_repository_unavailable"}

        self._track_event("browser.bridge.note_created", {"note_id": note_id, "url": url})
        self._queue_assistant_event(
            "browser.note_created",
            {
                "title": title,
                "url": url,
                "note_id": note_id,
                "contentKind": content_kind,
                "executedRecommendedAction": executed_action,
            },
        )
        return {"ok": True, "noteId": note_id, "title": title}

    def create_task_from_page(self, payload: dict[str, Any]) -> dict[str, Any]:
        content_organizer = self.content_organizer_service
        title = str(payload.get("taskTitle") or payload.get("title") or "网页后续行动").strip() or "网页后续行动"
        summary = str(payload.get("summary") or "").strip()
        url = str(payload.get("url") or "").strip()
        content_kind = str(payload.get("contentKind") or "").strip()
        executed_action = self._extract_executed_recommended_action(payload)

        if content_organizer is not None:
            from services.content_organizer_service import ContentOrganizeRequest

            description_parts = []
            if summary:
                description_parts.append(summary)
            if url:
                description_parts.append(f"来源：{url}")
            request = ContentOrganizeRequest(
                title=title,
                content="\n\n".join(description_parts),
                source_type="browser_extension",
                source_url=url,
                tags=["browser"] if content_kind else [],
            )
            result = content_organizer.convert_to_task(request)
            if not result.get("success"):
                return {"ok": False, "error": result.get("error", "convert_failed")}
            task_id = result.get("task_id")
        elif self.task_repository is not None:
            description_parts = []
            if summary:
                description_parts.append(summary)
            if url:
                description_parts.append(f"来源：{url}")
            task_id = self.task_repository.add_task(
                {
                    "name": title,
                    "description": "\n\n".join(description_parts),
                    "source": "browser_extension",
                    "is_completed": False,
                }
            )
        else:
            return {"ok": False, "error": "task_repository_unavailable"}

        self._track_event("browser.bridge.task_created", {"task_id": task_id, "url": url})
        self._queue_assistant_event(
            "browser.task_created",
            {
                "title": title,
                "url": url,
                "task_id": task_id,
                "contentKind": content_kind,
                "executedRecommendedAction": executed_action,
            },
        )
        return {"ok": True, "taskId": task_id, "title": title}

    def import_conversations(self, payload: dict[str, Any]) -> dict[str, Any]:
        """从浏览器扩展批量导入对话到思忆集。

        payload 格式：
            {
                "conversations": [
                    {
                        "title": "对话标题",
                        "timestamp": "2026-07-06T12:42:54.072Z",
                        "messages": [
                            {"role": "user", "content": "..."},
                            {"role": "assistant", "content": "..."}
                        ]
                    }
                ]
            }
        """
        conversations = payload.get("conversations") or []
        if not isinstance(conversations, list):
            return {"ok": False, "error": "conversations 必须是数组"}

        if self.memory_extractor is None and self.chat_session_repository is None:
            return {"ok": False, "error": "memory_extractor 和 chat_session_repository 均不可用"}

        try:
            from integrations.chat_importer import import_conversations as _import_conversations

            result = _import_conversations(
                conversations,
                ai_client=self.ai_service,
                memory_extractor=self.memory_extractor,
                chat_repo=self.chat_session_repository,
                memory_index_service=self.memory_index_service,
            )
        except Exception as exc:
            logger.warning("导入对话失败: %s", exc)
            return {"ok": False, "error": str(exc)}

        self._track_event(
            "browser.bridge.conversations_imported",
            {
                "total": result.get("total", 0),
                "imported": result.get("imported", 0),
                "sessions_saved": result.get("sessions_saved", 0),
                "memories_stored": result.get("memories_stored", 0),
            },
        )
        return {"ok": True, **result}

    def add_word_from_page(self, payload: dict[str, Any]) -> dict[str, Any]:
        """把网页划词结果写入词库（浏览器桥 /add-word 端点）。

        真实能力名：JSON-RPC ``word.add``（backend/handlers/_wordbook.py）。
        本方法只做 HTTP payload → ``word.add`` 参数的适配，核心写入复用
        ``add_word_entry``，确保与桌面端词本走同一份校验、截断与事件语义。

        payload: {"text"|"word": str, "url"?: str, "title"?: str, "meaning"?: str,
                  "wordbookId"?: int}
        """
        repo = self.vocabulary_repository
        if repo is None:
            return {"ok": False, "error": "vocabulary_unavailable"}
        text = str(payload.get("text") or payload.get("word") or "").strip()
        if not text:
            return {"ok": False, "error": "text_required"}
        wordbook_id = self._safe_int(payload.get("wordbookId") or payload.get("wordbook_id"), default=0)
        if wordbook_id <= 0:
            wordbook_id = self._resolve_default_wordbook_id(repo)
        result = add_word_entry(
            repo,
            wordbook_id=wordbook_id,
            word=text,
            meaning=str(payload.get("meaning") or ""),
            example=str(payload.get("example") or ""),
            source=str(payload.get("source") or "browser_extension"),
        )
        if not result.get("success"):
            return {"ok": False, "error": str(result.get("error") or "add_word_failed")}
        word_id = result.get("id")
        self._track_event(
            "browser.bridge.word_added",
            {"url": str(payload.get("url") or ""), "wordbook_id": wordbook_id, "word_id": word_id},
        )
        return {
            "ok": True,
            "success": True,
            "word": text,
            "wordId": word_id,
            "wordbookId": wordbook_id,
            "id": word_id,
        }

    # ── 工具方法 ──

    @staticmethod
    def _resolve_default_wordbook_id(repo: Any) -> int:
        """取第一个词本 ID 作为默认落点（取不到时回退 1=默认词本）。"""
        try:
            books = repo.list_wordbooks() or []
        except Exception:
            logger.warning("读取词本列表失败，回退默认词本 1", exc_info=True)
            return 1
        for book in books:
            if not isinstance(book, dict):
                continue
            book_id = BrowserBridgeContentMixin._safe_int(book.get("id"), default=0)
            if book_id > 0:
                return book_id
        return 1

    @staticmethod
    def _extract_executed_recommended_action(payload: dict[str, Any]) -> dict[str, str]:
        raw = payload.get("executedRecommendedAction") or payload.get("executed_recommended_action") or {}
        if not isinstance(raw, dict):
            return {}
        return {
            "actionId": str(raw.get("actionId") or "").strip(),
            "label": str(raw.get("label") or "").strip(),
            "executedAt": str(raw.get("executedAt") or "").strip(),
        }

    def _resolve_browser_content_kind(self, payload: dict[str, Any]) -> str:
        raw_kind = str(payload.get("contentKind") or payload.get("content_kind") or "").strip().lower()
        mapping = {
            "article": "article",
            "video_page": "video_page",
            "document_page": "document_page",
            "image_page": "image_page",
            "mixed_page": "mixed_page",
            "news_page": "news_page",
            "网页文章": "article",
            "视频页": "video_page",
            "文档页": "document_page",
            "图片页": "image_page",
            "资讯页": "news_page",
        }
        if raw_kind in mapping:
            return mapping[raw_kind]
        url = str(payload.get("url") or "").lower()
        title = str(payload.get("title") or "").lower()
        media_hints = payload.get("mediaHints") or payload.get("media_hints") or {}
        if isinstance(media_hints, dict) and media_hints.get("hasDocumentContainer"):
            return "document_page"
        if isinstance(media_hints, dict) and media_hints.get("hasVideo"):
            return "video_page"
        if any(token in url for token in ("news", "finance", "world", "politics")) or any(
            token in title for token in ("快讯", "新闻", "资讯", "发布")
        ):
            return "news_page"
        return "article"

    @staticmethod
    def _map_browser_kind_to_learning_kind(browser_kind: str) -> str:
        mapping = {
            "video_page": "视频页",
            "document_page": "文档页",
            "image_page": "图片页",
            "news_page": "资讯页",
            "mixed_page": "网页文章",
            "article": "网页文章",
        }
        return mapping.get(str(browser_kind or "article"), "网页文章")

    @staticmethod
    def _resolve_browser_summary_mode(browser_kind: str, *, url: str = "", title: str = "") -> str:
        if browser_kind == "video_page":
            return "video_focus"
        if browser_kind == "document_page":
            return "document_focus"
        if browser_kind == "news_page":
            return "news_focus"
        combined = f"{url} {title}".lower()
        if any(token in combined for token in ("news", "资讯", "快讯", "政策", "发布")):
            return "news_focus"
        return "general_focus"

    @staticmethod
    def _safe_int(value: Any, *, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _normalize_text(self, value: Any, *, limit: int) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = "\n".join(line.strip() for line in text.split("\n"))
        text = "\n".join(line for line in text.split("\n") if line)
        return text[:limit].strip()

    def _capture_body(self, payload: dict[str, Any]) -> str:
        """真正的正文载荷（选区 / 摘要 / 全文三取其一），不含标题链接等装饰行。"""
        selection = self._normalize_text(payload.get("selectionText") or payload.get("selection_text"), limit=12000)
        summary = self._normalize_text(payload.get("summary"), limit=4000)
        page_text = self._normalize_text(payload.get("pageText") or payload.get("page_text"), limit=8000)
        return selection or summary or page_text

    def _build_capture_text(self, payload: dict[str, Any]) -> str:
        title = str(payload.get("title") or "").strip()
        url = str(payload.get("url") or "").strip()
        content_kind = str(payload.get("contentKind") or "").strip()
        body = self._capture_body(payload)
        lines = []
        if title:
            lines.append(f"标题：{title}")
        if url:
            lines.append(f"链接：{url}")
        if content_kind:
            lines.append(f"内容类型：{content_kind}")
        if body:
            lines.extend(["", body])
        return "\n".join(lines).strip()

    def _build_note_markdown(self, payload: dict[str, Any]) -> str:
        title = str(payload.get("title") or "网页摘要").strip() or "网页摘要"
        url = str(payload.get("url") or "").strip()
        content_kind = str(payload.get("contentKind") or "").strip()
        summary = self._normalize_text(payload.get("summary"), limit=6000)
        key_points = payload.get("keyPoints") or payload.get("key_points") or []
        study_suggestions = payload.get("studySuggestions") or payload.get("study_suggestions") or []
        action_hints = payload.get("actionHints") or payload.get("action_hints") or []
        lines = [f"# {title}"]
        if url:
            lines.extend(["", f"来源：{url}"])
        if content_kind:
            lines.extend(["", f"内容类型：{content_kind}"])
        if summary:
            lines.extend(["", "## 摘要", summary])
        if isinstance(key_points, list) and key_points:
            lines.extend(["", "## 关键点"])
            lines.extend(f"- {str(item).strip()}" for item in key_points if str(item).strip())
        if isinstance(study_suggestions, list) and study_suggestions:
            lines.extend(["", "## 后续建议"])
            lines.extend(f"- {str(item).strip()}" for item in study_suggestions if str(item).strip())
        if isinstance(action_hints, list) and action_hints:
            lines.extend(["", "## 建议动作"])
            lines.extend(f"- {str(item).strip()}" for item in action_hints if str(item).strip())
        return "\n".join(lines).strip() + "\n"
