"""URL learning JSON-RPC handler — extract, summarize, and learn from URLs and local files."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.url_learning")


# V2.1 批次1#8-1: url_learning.learn 本地文件读取目录白名单。
# 仅允许学习以下目录内的文件：
#   - 应用数据目录（data/，含用户自建笔记/知识 md）
#   - 用户常用文档目录 Documents / Downloads / Desktop
# 阻止跨设备/越权调用读取 .env、凭据、系统文件等任意路径。
# B-02: 白名单逻辑已抽为公共 util backend/handlers/_path_guard.py
# （data.import_chat_folder 双入口复用）；此处保留薄包装以维持既有
# test_p1_8_hemostasis.py 的 import 路径（backend.handlers._url_learning）。
def _allowed_learn_bases(context) -> tuple[str, ...]:
    """B-02 委托公共 util：允许读取的根目录集合。"""
    from backend.handlers._path_guard import allowed_import_bases

    return allowed_import_bases(context)


def _is_within_learn_whitelist(file_path: str, context) -> bool:
    """B-02 委托公共 util：规范化后判断路径是否落在白名单内（防符号链接/.. 绕过）。"""
    from backend.handlers._path_guard import is_within_import_whitelist

    return is_within_import_whitelist(file_path, context)


def register_url_learning(registry, context) -> None:
    """Register URL learning-related JSON-RPC methods."""

    # ── url_learning.learn ──
    async def url_learning_learn(url: str = "", file_path: str = "", **kwargs: Any) -> dict[str, Any]:
        """Learn from a URL or local file — extract summary, key points, study suggestions.

        Args:
            url: Remote URL (http/https only — SSRF 防护由 WebFetchService 提供)
            file_path: Local file path (alternative to url — 含路径遍历防护)

        Returns:
            Structured UrlLearningResult as dict with fields:
            url, domain, title, content_kind, summary, summary_source,
            key_points, study_suggestions, extracted_text (truncated),
            preview_text, content_length, warnings
        """
        if not url and not file_path:
            return {"error": "请提供 url 或 file_path"}

        # V2.1 批次1#8-1: file_path 目录白名单（服务层 _validate_local_file_path
        # 已拦 `..` 遍历，此处再加根目录边界，防跨设备越权读取任意文件）。
        if file_path and not _is_within_learn_whitelist(file_path, context):
            logger.warning(
                "url_learning.learn 拒绝读取白名单外文件: %s",
                str(file_path)[:120],
            )
            return {"error": "文件路径不在允许的学习目录范围内"}

        try:
            # P1.3: 显式分流 — file_path 走 learn_from_file（含路径遍历防护），
            # url 走 learn_from_url（含 SSRF 防护，拒绝 file://）
            if file_path:
                from services.url_learning_service import learn_from_file

                result = await asyncio.to_thread(
                    learn_from_file,
                    file_path,
                    settings_repository=context.settings_repository,
                    llm_client=context.ai_client,
                )
            else:
                from services.url_learning_service import learn_from_url

                result = await asyncio.to_thread(
                    learn_from_url,
                    url=url,
                    timeout=kwargs.get("timeout", 30),
                    settings_repository=context.settings_repository,
                    llm_client=context.ai_client,
                )
            return {
                "url": result.url,
                "domain": result.domain,
                "title": result.title,
                "content_kind": result.content_kind,
                "summary": result.summary,
                "summary_source": result.summary_source,
                "summary_model": result.summary_model,
                "key_points": result.key_points,
                "study_suggestions": result.study_suggestions,
                "preview_text": result.preview_text,
                "content_length": result.content_length,
                "warnings": result.warnings,
                "extracted_text": result.extracted_text[:3000] if len(result.extracted_text) > 3000 else result.extracted_text,
            }
        except Exception as exc:
            logger.warning("url_learning.learn failed: %s", exc)
            return {"error": str(exc)}

    registry.register("url_learning.learn", url_learning_learn)
