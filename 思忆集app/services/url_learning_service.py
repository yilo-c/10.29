"""URL learning service — learn from URLs, local files, PDF, Markdown, DOCX, and HTML.

Core service logic. Utility functions moved to _url_learning_utils.py.
"""

from __future__ import annotations

import io
import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from .ai_service import AIServiceError, create_fallback_client
from .prompt_shield import wrap_untrusted
from .web_fetch import WebFetchService

from ._url_learning_utils import (
    STOP_WORDS,
    _ORAL_BAN_PHRASES,
    _PROMO_NOISE_PHRASES,
    _build_course_outline_summary,
    _build_game_lineup_summary,
    _build_mechanism_guide_summary,
    _build_promotional_summary,
    _classify_content_kind,
    _clean_text,
    _dedupe_lines,
    _dedupe_sentences,
    _derive_title_from_url,
    _extract_course_outline_key_points,
    _extract_game_lineup_key_points,
    _extract_html_segments,
    _extract_mechanism_guide_key_points,
    _extract_meta_description,
    _extract_tech_terms_from_sentence,
    _extract_title,
    _is_course_outline_video,
    _is_game_lineup_video,
    _is_mechanism_guide_video,
    _is_promotional_video,
    _normalize_title_topic,
    _normalize_url,
    _preprocess_summary_text,
    _remove_noise_blocks,
    _split_sentences,
    _text_overlap_ratio,
)

try:
    import pypdf
except ImportError:
    pypdf = None  # type: ignore[assignment]

try:
    import docx
except ImportError:
    docx = None  # type: ignore[assignment]

try:
    import jieba
except ImportError:
    jieba = None


logger = logging.getLogger("siyi.url_learning")


class UrlLearningError(RuntimeError):
    """网址学习接入失败。"""


@dataclass(frozen=True)
class UrlLearningResult:
    url: str
    domain: str
    title: str
    content_kind: str
    summary: str
    summary_source: str
    summary_model: str | None
    key_points: list[str]
    study_suggestions: list[str]
    extracted_text: str
    preview_text: str
    content_length: int
    warnings: list[str]


def learn_from_url(
    url: str,
    session: requests.Session | None = None,
    timeout: int = 15,
    settings_repository=None,
    llm_client=None,
) -> UrlLearningResult:
    """从 URL 学习 — 仅支持 http/https，SSRF 防护由 WebFetchService 提供。

    P1.3 收敛：
    - 不再支持 `file://` 与本地路径（请使用 `learn_from_file`）
    - 不再直接 `requests.get`，复用 `WebFetchService.fetch_raw` 走 SSRF 防护 + 重定向校验
    - `session` 参数保留向后兼容，但已忽略（由 WebFetchService 内部管理 Session）
    """
    # P1.3: 在 _normalize_url 之前检查原始输入 — _normalize_url 会把 file:///etc/passwd
    # 误转为 https://file:///etc/passwd 导致 _is_local_file_path 失效
    if _is_local_file_path(url or ""):
        raise UrlLearningError("URL 学习不再支持 file:// 或本地路径；请使用 learn_from_file 函数或 file_path 参数")

    normalized_url = _normalize_url(url)

    # 双重保险：normalize 后再次检查（防止 _normalize_url 把 file://x 转成 https://file://x）
    if _is_local_file_path(normalized_url):
        raise UrlLearningError("URL 学习不再支持 file:// 或本地路径；请使用 learn_from_file 函数或 file_path 参数")

    # P1.3: 复用 WebFetchService 的 SSRF 防护 + 重定向安全校验
    fetch_result = WebFetchService().fetch_raw(normalized_url, timeout=timeout)
    if not fetch_result.ok:
        raise UrlLearningError(f"读取网址失败: {fetch_result.error}")

    content_type = fetch_result.content_type.lower()
    response_content = fetch_result.content
    response_text = fetch_result.text

    if "application/pdf" in content_type:
        return build_learning_result_from_pdf(
            normalized_url,
            response_content,
            settings_repository=settings_repository,
            llm_client=llm_client,
        )

    if "text/plain" in content_type:
        text = _clean_text(response_text)
        result = _build_learning_result(
            normalized_url,
            title=_derive_title_from_url(normalized_url),
            text=text,
            content_type=content_type,
            warnings=[],
        )
        return _enhance_result_with_ai(result, settings_repository=settings_repository, llm_client=llm_client)

    if "text/html" in content_type or not content_type:
        return build_learning_result_from_html(
            normalized_url,
            response_text,
            content_type=content_type,
            settings_repository=settings_repository,
            llm_client=llm_client,
        )

    try:
        text = response_text
        if text.strip():
            result = _build_learning_result(
                normalized_url,
                title=_derive_title_from_url(normalized_url),
                text=text,
                content_type=content_type,
                warnings=["不符合标准 HTML 格式，已尝试按文本处理。"],
            )
            return _enhance_result_with_ai(result, settings_repository=settings_repository, llm_client=llm_client)
    except Exception:
        logger.warning("all download methods failed for URL, raising UrlLearningError")

    raise UrlLearningError("当前支持公开网页、文本页、PDF、Markdown 和 Word 文档。不支持该类型资源。")


def learn_from_file(
    file_path: str,
    settings_repository=None,
    llm_client=None,
) -> UrlLearningResult:
    """从本地文件学习 — 含路径遍历防护与大小限制。

    支持 PDF、Markdown、DOCX、纯文本；其他后缀按纯文本处理。
    路径遍历防护：禁止 `..` 路径段（含规范化后再次检查）。
    """
    safe_path = _validate_local_file_path(file_path)
    return _learn_from_local_file(safe_path, settings_repository=settings_repository, llm_client=llm_client)


def _validate_local_file_path(file_path: str) -> str:
    """校验本地文件路径：禁止 `..` 遍历，规范化路径。"""
    if not file_path or not isinstance(file_path, str):
        raise UrlLearningError("文件路径无效")

    # 同时检查原始与斜杠统一后的路径段
    normalized_slashes = file_path.replace("\\", "/")
    segments = normalized_slashes.split("/")
    if ".." in segments:
        raise UrlLearningError(f"文件路径不允许包含 '..': {file_path}")

    # Windows 上 os.path.normpath 会把正斜杠转反斜杠，并解析 ..
    normalized = os.path.normpath(file_path)
    normalized_segments = normalized.replace("\\", "/").split("/")
    if ".." in normalized_segments:
        raise UrlLearningError(f"文件路径规范化后包含 '..': {file_path}")

    return normalized


def build_learning_result_from_browser_context(
    url: str,
    *,
    title: str = "",
    selection_text: str = "",
    page_text: str = "",
    transcript_text: str = "",
    description_text: str = "",
    content_kind_override: str = "",
    extra_warnings: Iterable[str] | None = None,
    settings_repository=None,
    llm_client=None,
) -> UrlLearningResult:
    normalized_url = _normalize_url(url or "https://browser.local/")
    resolved_title = str(title or "").strip() or _derive_title_from_url(normalized_url)
    selected = _clean_text(selection_text)
    page = _clean_text(page_text)

    warnings: list[str] = []
    source_text = selected or page
    if not source_text:
        raise UrlLearningError("浏览器页面中没有可用于总结的文本。")
    if selected:
        warnings.append("当前摘要优先基于你在网页中选中的内容。")
    else:
        warnings.append("当前摘要基于页面可见正文文本。")
    if len(source_text) < 120:
        warnings.append("当前可用文本较少，摘要会更依赖标题和上下文。")
    if len(page) > 50000:
        warnings.append("页面正文较长，已自动截断到前 50000 个字符用于总结。")
    if extra_warnings:
        warnings.extend(str(item).strip() for item in extra_warnings if str(item).strip())

    result = _build_learning_result(
        normalized_url,
        title=resolved_title,
        text=source_text[:50000],
        content_type="text/plain+browser-context",
        warnings=warnings,
    )
    override = str(content_kind_override or "").strip()
    if override:
        result = replace(
            result,
            content_kind=override,
            study_suggestions=_build_study_suggestions(override, result.extracted_text),
        )
    return _enhance_result_with_ai(
        result,
        settings_repository=settings_repository,
        llm_client=llm_client,
        transcript_text=transcript_text,
        description_text=description_text,
    )


def build_learning_result_from_html(
    url: str,
    html_text: str,
    content_type: str = "text/html",
    settings_repository=None,
    llm_client=None,
) -> UrlLearningResult:
    normalized_url = _normalize_url(url)
    sanitized_html = _remove_noise_blocks(html_text)
    title = _extract_title(sanitized_html) or _derive_title_from_url(normalized_url)
    meta_description = _extract_meta_description(sanitized_html)
    segments = _extract_html_segments(sanitized_html)
    if meta_description:
        segments.insert(0, meta_description)
    text = _dedupe_lines(segments)
    warnings = []
    if not text:
        raise UrlLearningError("未能从该网页中提取到可用于学习总结的正文内容。")
    content_kind = _classify_content_kind(normalized_url, title, text, content_type)
    if content_kind in {"资源页", "视频页"}:
        warnings.append("当前版本只提取公开可访问的文本信息，不下载文件、网盘资源或视频内容本体。")
    if len(text) < 120:
        warnings.append("该页面可提取的公开文本较少，学习总结会更依赖标题和简介。")
    result = _build_learning_result(normalized_url, title=title, text=text, content_type=content_type, warnings=warnings)
    return _enhance_result_with_ai(result, settings_repository=settings_repository, llm_client=llm_client)


def _build_learning_result(url: str, title: str, text: str, content_type: str, warnings: list[str]) -> UrlLearningResult:
    normalized_text = _clean_text(text)
    preprocessed_text = _preprocess_summary_text(normalized_text, title=title)
    content_kind = _classify_content_kind(url, title, normalized_text, content_type)
    key_points = _extract_key_points(preprocessed_text)
    summary = _build_focus_summary(title, preprocessed_text, key_points)
    study_suggestions = _build_study_suggestions(content_kind, preprocessed_text)
    preview_text = preprocessed_text[:1500]
    return UrlLearningResult(
        url=url,
        domain=urlparse(url).netloc,
        title=title,
        content_kind=content_kind,
        summary=summary,
        summary_source="local",
        summary_model=None,
        key_points=key_points,
        study_suggestions=study_suggestions,
        extracted_text=preprocessed_text,
        preview_text=preview_text,
        content_length=len(preprocessed_text),
        warnings=warnings,
    )


def _enhance_result_with_ai(
    result: UrlLearningResult,
    settings_repository=None,
    llm_client=None,
    transcript_text: str = "",
    description_text: str = "",
) -> UrlLearningResult:
    client = llm_client
    if client is None and settings_repository is not None:
        client = create_fallback_client(settings_repository=settings_repository)
    if client is None or not hasattr(client, "summarize_url_learning"):
        return result
    if hasattr(client, "is_configured") and not client.is_configured():
        return result

    try:
        # 安C2：外部正文/字幕/描述进 AI prompt 前定界+中和（见 services/prompt_shield.py）
        ai_summary = client.summarize_url_learning(
            title=result.title,
            url=result.url,
            content_kind=result.content_kind,
            text=wrap_untrusted(f"网页正文({result.url})", result.extracted_text),
            warnings=result.warnings,
            transcript_text=wrap_untrusted("音视频字幕/转录", transcript_text) if transcript_text else transcript_text,
            description_text=wrap_untrusted("页面描述", description_text) if description_text else description_text,
        )
    except AIServiceError as exc:
        logger.warning("AI 摘要不可用，回退本地摘要: url=%s error=%s", result.url, exc, exc_info=True)
        return replace(
            result,
            warnings=list(result.warnings) + [f"AI 摘要不可用，已回退本地摘要：{exc}"],
        )
    except Exception as exc:
        logger.warning("AI 摘要链路异常，回退本地摘要: url=%s error=%s", result.url, exc)
        return replace(
            result,
            warnings=list(result.warnings) + [f"AI 摘要异常，已回退本地摘要：{exc}"],
        )

    return replace(
        result,
        summary=ai_summary.summary,
        summary_source="ai",
        summary_model=ai_summary.model,
        key_points=ai_summary.key_points or result.key_points,
        study_suggestions=ai_summary.study_suggestions or result.study_suggestions,
    )


def _is_local_file_path(url: str) -> bool:
    url_lower = (url or "").lower()
    return url_lower.startswith("file://") or (os.path.isfile(url) or (len(url) > 2 and url[1] == ":" and url[0].isalpha()))


def _learn_from_local_file(
    file_path: str,
    settings_repository=None,
    llm_client=None,
) -> UrlLearningResult:
    if not os.path.isfile(file_path):
        raise UrlLearningError(f"文件不存在：{file_path}")

    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    if file_size_mb > 50:
        raise UrlLearningError(f"文件过大（{file_size_mb:.1f} MB），当前支持最大 50 MB 文件。")

    suffix = Path(file_path).suffix.lower()
    file_url = f"file://{file_path}"

    if suffix == ".pdf":
        with open(file_path, "rb") as f:
            content = f.read()
        return build_learning_result_from_pdf(
            file_url,
            content,
            settings_repository=settings_repository,
            llm_client=llm_client,
        )

    if suffix == ".md":
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
        return build_learning_result_from_markdown(
            file_url,
            content,
            settings_repository=settings_repository,
            llm_client=llm_client,
        )

    if suffix in {".docx", ".doc"}:
        with open(file_path, "rb") as f:
            content = f.read()
        return build_learning_result_from_docx(
            file_url,
            content,
            settings_repository=settings_repository,
            llm_client=llm_client,
        )

    try:
        with open(file_path, encoding="utf-8") as f:
            text = f.read()
        result = _build_learning_result(
            file_url,
            title=Path(file_path).stem,
            text=text,
            content_type="text/plain",
            warnings=[f"文件类型 {suffix} 按纯文本处理。"],
        )
        return _enhance_result_with_ai(result, settings_repository=settings_repository, llm_client=llm_client)
    except (OSError, UnicodeDecodeError) as exc:
        raise UrlLearningError(f"无法读取文件内容：{exc}")


def build_learning_result_from_pdf(
    url: str,
    pdf_bytes: bytes,
    settings_repository=None,
    llm_client=None,
) -> UrlLearningResult:
    if pypdf is None:
        raise UrlLearningError("需要安装 pypdf 库才能处理 PDF 文件。请运行：pip install pypdf")

    try:
        pdf_reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages_text = []
        for page_num, page in enumerate(pdf_reader.pages, 1):
            try:
                text = page.extract_text()
                if text.strip():
                    pages_text.append(text)
            except Exception as e:
                logger.warning("PDF 页面 %d 提取失败：%s", page_num, e, exc_info=True)

        if not pages_text:
            raise UrlLearningError("无法从 PDF 中提取任何文本内容。")

        full_text = "\n".join(pages_text)
        title = _derive_title_from_url(url)
        warnings = []
        if len(pdf_reader.pages) > 100:
            full_text = full_text[:50000]
            warnings.append("PDF 文档超过 100 页，只提取前 50000 个字符用于总结。")

        result = _build_learning_result(
            url,
            title=title,
            text=full_text,
            content_type="application/pdf",
            warnings=warnings,
        )
        return _enhance_result_with_ai(result, settings_repository=settings_repository, llm_client=llm_client)

    except Exception as exc:
        raise UrlLearningError(f"PDF 提取失败：{exc}")


def build_learning_result_from_markdown(
    url: str,
    markdown_text: str,
    settings_repository=None,
    llm_client=None,
) -> UrlLearningResult:
    text = _clean_text(markdown_text)
    if not text:
        raise UrlLearningError("该 Markdown 文件没有可读取的内容。")

    title = _derive_title_from_url(url)
    lines = text.split("\n")
    for line in lines[:10]:
        if line.strip().startswith("#"):
            title = line.strip().lstrip("#").strip()
            break

    result = _build_learning_result(
        url,
        title=title,
        text=text,
        content_type="text/markdown",
        warnings=[],
    )
    return _enhance_result_with_ai(result, settings_repository=settings_repository, llm_client=llm_client)


def build_learning_result_from_docx(
    url: str,
    docx_bytes: bytes,
    settings_repository=None,
    llm_client=None,
) -> UrlLearningResult:
    if docx is None:
        raise UrlLearningError("需要安装 python-docx 库才能处理 Word 文档。请运行：pip install python-docx")

    try:
        doc = docx.Document(io.BytesIO(docx_bytes))
        paragraphs_text = [p.text for p in doc.paragraphs if p.text.strip()]

        if not paragraphs_text:
            raise UrlLearningError("无法从 Word 文档中提取任何文本内容。")

        full_text = "\n".join(paragraphs_text)
        title = _derive_title_from_url(url)

        result = _build_learning_result(
            url,
            title=title,
            text=full_text,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            warnings=[],
        )
        return _enhance_result_with_ai(result, settings_repository=settings_repository, llm_client=llm_client)

    except Exception as exc:
        raise UrlLearningError(f"Word 文档提取失败：{exc}")


# ---------------------------------------------------------------------------
# 摘要 & 要点提取（与 utils 紧密耦合，保留在主文件中）
# ---------------------------------------------------------------------------


def _build_focus_summary(title: str, text: str, key_points: list[str]) -> str:
    if _is_game_lineup_video(title, text):
        return _build_game_lineup_summary(title, text)
    if _is_mechanism_guide_video(title, text):
        return _build_mechanism_guide_summary(title, text)
    if _is_promotional_video(title, text):
        if _is_course_outline_video(title, text):
            return _build_course_outline_summary(title, text)
        return _build_promotional_summary(title, text)

    topic = _normalize_title_topic(title)
    compact_points = _dedupe_sentences(
        [p[:30].rstrip() + "..." if len(p) > 30 else p for p in key_points if p],
        threshold=0.68,
    )
    keywords = [token for token, _freq in _extract_keywords_with_freq(text)[:3]]
    keyword_text = "、".join(keywords[:2]) if keywords else ""
    aspect_labels = []
    for point in key_points:
        match = re.match(
            r"^(阵容名称|核心精灵|获取方式|技能配置|职责定位|顺风打法|逆风补救|补位精灵|克制对象|背景|方法|步骤|结论|风险|建议)[：:]",
            point,
        )
        if match and match.group(1) not in aspect_labels:
            aspect_labels.append(match.group(1))

    if topic and aspect_labels:
        return f"这页内容主要围绕《{topic}》展开，重点包括{'、'.join(aspect_labels[:3])}。"
    if topic and keyword_text:
        return f"这页内容主要围绕《{topic}》展开，重点涉及{keyword_text}。"
    if topic and compact_points:
        if len(compact_points) >= 2:
            return f"这页内容主要围绕《{topic}》展开，重点涉及{compact_points[0]}和{compact_points[1]}。"
        return f"这页内容主要围绕《{topic}》展开，重点涉及{compact_points[0]}。"
    if compact_points:
        if len(compact_points) >= 2:
            return f"这页内容的重点是{compact_points[0]}，并补充了{compact_points[1]}。"
        return f"这页内容的重点是{compact_points[0]}。"
    if topic:
        return f"这页内容主要围绕《{topic}》展开。"
    return _build_summary(title, text)


def _build_summary(title: str, text: str) -> str:
    cleaned_text = _preprocess_summary_text(text)
    if _is_game_lineup_video(title, cleaned_text):
        return _build_game_lineup_summary(title, cleaned_text)

    sentences = _split_sentences(cleaned_text)
    if not sentences:
        return f"这条链接的主题是《{title}》，当前可提取文本较少，建议先查看原文并补充重点。"

    noise_pattern = re.compile(
        r"(一键三连|点赞|投币|收藏|分享|关注|弹幕|评论留言|打包带走|"
        r"拜托|支持一下|免费.*带走|如果.*有用.*请|长按点赞|"
        r"^\d[\d.]*[万]?\s*$|^播放|^展开更多|^收起|"
        r"置顶评论|置顶平论|配套资料|配套籽料|自取哦|"
        r"评论.*666|留言.*666|666即可|全部免费|全网最|"
        r"零基础小白专用|小白专用课|这对我真的很重要)",
        re.IGNORECASE,
    )
    clean_sentences = [
        s
        for s in sentences
        if not noise_pattern.search(s) and not _ORAL_BAN_PHRASES.search(s) and not _PROMO_NOISE_PHRASES.search(s) and len(s) >= 10
    ]
    if not clean_sentences:
        clean_sentences = sentences[:3]

    kw_freq = _extract_keywords_with_freq(cleaned_text)
    kw_freq_dict = dict(kw_freq[:10])
    scored = []
    for idx, sent in enumerate(clean_sentences[:40]):
        sent_lower = sent.lower()
        tf_score = sum(freq for kw, freq in kw_freq_dict.items() if kw in sent_lower)
        length_bonus = min(len(sent) // 20, 3)
        position_bonus = max(0, 3 - idx // 5)
        verbatim_penalty = 2 if len(sent) > 80 else 0
        score = tf_score + length_bonus + position_bonus - verbatim_penalty
        scored.append((score, idx, sent))
    scored.sort(key=lambda x: (-x[0], x[1]))
    pick_count = min(max(2, len(clean_sentences) // 8), 4)
    top = sorted(scored[:pick_count], key=lambda x: x[1])
    picked = _dedupe_sentences([item[2] for item in top], threshold=0.68)

    compressed = []
    for s in picked:
        trimmed = s.rstrip("。！？!?")
        if len(trimmed) > 60:
            trimmed = trimmed[:57].rstrip() + "..."
        compressed.append(trimmed)
    summary = "。".join(compressed) + "。"
    if len(summary) > 300:
        summary = summary[:297].rstrip() + "..."
    return summary


def _extract_key_points(text: str) -> list[str]:
    cleaned_text = _preprocess_summary_text(text)
    if _is_game_lineup_video("", cleaned_text):
        structured_points = _extract_game_lineup_key_points(cleaned_text)
        if structured_points:
            return structured_points
    if _is_mechanism_guide_video("", cleaned_text):
        structured_points = _extract_mechanism_guide_key_points(cleaned_text)
        if structured_points:
            return structured_points
    if _is_course_outline_video("", cleaned_text):
        outline_points = _extract_course_outline_key_points("", cleaned_text)
        if outline_points:
            return outline_points
    if _is_promotional_video("", cleaned_text):
        all_terms: list[str] = []
        for sent in _split_sentences(cleaned_text):
            all_terms.extend(_extract_tech_terms_from_sentence(sent))
        seen: set[str] = set()
        unique: list[str] = []
        for t in all_terms:
            if t.lower() not in seen:
                seen.add(t.lower())
                unique.append(t)
        if unique:
            return [f"提及主题：{'、'.join(unique[:8])}", "本集为导学/宣传内容，暂无实质教学展开"]

    sentences = _split_sentences(cleaned_text)
    if not sentences:
        return []

    noise_pattern = re.compile(
        r"(一键三连|点赞|投币|收藏|分享|关注|弹幕|评论留言|打包带走|"
        r"拜托|如果.*有用.*请|长按点赞|免费.*带走|支持一下|"
        r"^\d[\d.]*[万]?\s*$|^播放|^展开更多|^收起|"
        r"置顶评论|置顶平论|配套资料|配套籽料|自取哦|"
        r"评论.*666|留言.*666|666即可|全部免费|全网最|"
        r"零基础小白专用|小白专用课|这对我真的很重要)",
        re.IGNORECASE,
    )
    clean = [
        s
        for s in sentences
        if not noise_pattern.search(s) and not _ORAL_BAN_PHRASES.search(s) and not _PROMO_NOISE_PHRASES.search(s) and 10 <= len(s) <= 120
    ]
    if not clean:
        clean = [s for s in sentences if len(s) >= 10][:10]

    kw_freq = _extract_keywords_with_freq(cleaned_text)
    kw_freq_dict = dict(kw_freq[:12])
    scored = []
    for index, sentence in enumerate(clean[:50]):
        sent_lower = sentence.lower()
        tf_score = sum(freq for kw, freq in kw_freq_dict.items() if kw in sent_lower)
        if re.search(r"第[一二三四五六七八九十\d]+", sentence):
            tf_score += 3
        for marker in ("核心", "重点", "结论", "步骤", "风险", "建议", "方法", "关键", "原理", "架构", "部署", "实战"):
            if marker in sentence:
                tf_score += 2
        if re.search(r"\d+(\.\d+)?%|\d+倍|最多\d+|一共\d+|保底\d+|只能通过|唯一途径", sentence):
            tf_score += 3
        tf_score += min(len(sentence) // 25, 3)
        tf_score -= index // 8
        if len(sentence) > 80:
            tf_score -= 2
        scored.append((tf_score, index, sentence))

    top = sorted(scored, key=lambda item: (-item[0], item[1]))

    max_points = min(max(3, len(clean) // 6), 6)

    result: list[Any] = []

    truncated_to_orig_idx = {}
    for _score, _idx, sent in top:
        if len(result) >= max_points:
            break
        truncated = sent if len(sent) <= 100 else sent[:97].rstrip() + "..."
        is_dup = False
        for existing in result:
            shorter, longer = (truncated, existing) if len(truncated) <= len(existing) else (existing, truncated)
            if shorter in longer or _text_overlap_ratio(shorter, longer) > 0.6:
                is_dup = True
                break
        if not is_dup:
            result.append(truncated)
            truncated_to_orig_idx[truncated] = _idx

    result.sort(key=lambda s: truncated_to_orig_idx.get(s, 999))
    return _dedupe_sentences(result, threshold=0.68)


def _extract_keywords(text: str) -> list[str]:
    kw_freq = _extract_keywords_with_freq(text)
    return [token for token, _freq in kw_freq]


def _extract_keywords_with_freq(text: str) -> list[tuple[str, int]]:
    if jieba is not None:
        tokens = list(jieba.cut(text))
    else:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[一-鿿]{2,}", text)
    counts: dict[str, int] = {}
    for token in tokens:
        lowered = token.lower().strip()
        if len(lowered) < 2:
            continue
        if re.fullmatch(r"[a-z]{1,2}", lowered):
            continue
        if lowered in STOP_WORDS:
            continue
        if not re.search(r"[一-鿿]|[A-Za-z]", lowered):
            continue
        counts[lowered] = counts.get(lowered, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ordered[:15]


def _build_study_suggestions(content_kind: str, text: str) -> list[str]:
    suggestions = []
    if content_kind == "文档页":
        suggestions.append("先把操作步骤和关键概念拆成笔记，再补一个可复用清单。")
        suggestions.append("把文中的命令、接口或规则单独摘出来，方便后续检索。")
    elif content_kind == "视频页":
        suggestions.append("当前更适合先抓公开标题、简介和字幕信息，再整理成学习提纲。")
        suggestions.append("如果后续有字幕或时间轴，建议再补一次更细的知识点拆分。")
    elif content_kind == "资讯页":
        suggestions.append("先判断这条资讯是否需要进入持续跟踪，再决定是收件、收藏还是生成任务。")
        suggestions.append("如果和当前项目有关，建议顺手记录影响点、时间点和后续动作。")
    elif content_kind == "图片页":
        suggestions.append("优先提取图片中的文字或结构信息，再整理成可检索笔记。")
        suggestions.append("如果图片信息零散，建议补充来源、时间和上下文说明。")
    elif content_kind == "资源页":
        suggestions.append("先确认这是你有权限访问和学习的资源，再整理资源清单与学习目标。")
        suggestions.append("对资源页更适合记录文件用途、优先顺序和后续学习计划。")
    else:
        suggestions.append("建议先把这条链接整理成一条笔记，再拆出 3 到 5 个关键知识点。")
        suggestions.append("如果内容可执行，顺手记录一个后续行动项或复习提醒。")
    if len(text) >= 2000:
        suggestions.append("正文较长，建议二次拆成提纲、关键概念和待实践内容，避免只留一段长摘要。")
    return suggestions[:3]
