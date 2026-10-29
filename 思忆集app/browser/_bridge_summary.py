"""Summary 相关混入 — 从 bridge_content.py 提取。"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Protocol

from services._url_learning_utils import _correct_domain_terms, _dedupe_text_blocks, _strip_packaging_text
from services.url_learning_service import _extract_keywords_with_freq, build_learning_result_from_browser_context


class SummaryHost(Protocol):
    """SummaryMixin 依赖的宿主契约 — 由 BrowserAssistantBridgeService 运行时注入。"""
    settings_repository: Any
    workbench_service: Any

    def _resolve_browser_content_kind(self, payload: dict[str, Any]) -> str: ...
    def _normalize_text(self, value: Any, *, limit: int) -> str: ...
    def _fetch_bilibili_subtitle_fallback(self, url: str) -> tuple[str, str]: ...
    def _collect_snapshot(self) -> dict[str, Any]: ...
    def _subtitle_status_to_label(self, status: str) -> str: ...
    def _track_event(self, event_name: str, payload: dict[str, Any]) -> None: ...
    def _queue_assistant_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None: ...

    @staticmethod
    def _map_browser_kind_to_learning_kind(browser_kind: str) -> str: ...
    @staticmethod
    def _resolve_browser_summary_mode(browser_kind: str, *, url: str = "", title: str = "") -> str: ...
    @staticmethod
    def _safe_int(value: Any, *, default: int = 0) -> int: ...
    @staticmethod
    def _parse_subtitle_segments(text: str) -> list[dict[str, str]]: ...


class SummaryMixin:
    """网页总结/提炼相关方法。"""
    # 宿主契约：由 BrowserAssistantBridgeService 运行时注入
    if TYPE_CHECKING:
        settings_repository: Any
        workbench_service: Any

        def _resolve_browser_content_kind(self, payload: dict[str, Any]) -> str: ...
        def _normalize_text(self, value: Any, *, limit: int) -> str: ...
        def _fetch_bilibili_subtitle_fallback(self, url: str) -> tuple[str, str]: ...
        def _collect_snapshot(self) -> dict[str, Any]: ...
        def _subtitle_status_to_label(self, status: str) -> str: ...
        def _track_event(self, event_name: str, payload: dict[str, Any]) -> None: ...
        def _queue_assistant_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None: ...

        @staticmethod
        def _map_browser_kind_to_learning_kind(browser_kind: str) -> str: ...
        @staticmethod
        def _resolve_browser_summary_mode(browser_kind: str, *, url: str = "", title: str = "") -> str: ...
        @staticmethod
        def _safe_int(value: Any, *, default: int = 0) -> int: ...
        @staticmethod
        def _parse_subtitle_segments(text: str) -> list[dict[str, str]]: ...

    _SUMMARY_TYPE_LABELS = {
        "structured": "结构化提炼",
        "general": "通用提炼",
        "subtitle_driven": "字幕驱动提炼",
        "ai": "AI提炼",
    }

    # ── 核心入口 ──

    def summarize_page(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = str(payload.get("url") or "").strip()
        title = str(payload.get("title") or "").strip()
        browser_content_kind = self._resolve_browser_content_kind(payload)
        learning_content_kind = self._map_browser_kind_to_learning_kind(browser_content_kind)
        summary_mode = self._resolve_browser_summary_mode(browser_content_kind, url=url, title=title)
        selection_text = self._normalize_text(payload.get("selectionText") or payload.get("selection_text"), limit=12000)
        page_text = self._normalize_text(payload.get("pageText") or payload.get("page_text"), limit=50000)
        description_text = self._normalize_text(payload.get("descriptionText") or payload.get("description_text"), limit=6000)
        subtitle_text = self._normalize_text(payload.get("subtitleText") or payload.get("subtitle_text"), limit=20000)
        raw_transcript_text = self._normalize_text(payload.get("transcriptText") or payload.get("transcript_text"), limit=20000)
        transcript_text = raw_transcript_text or subtitle_text

        selection_text = _dedupe_text_blocks(_correct_domain_terms(_strip_packaging_text(selection_text), title=title))
        page_text = _dedupe_text_blocks(_correct_domain_terms(_strip_packaging_text(page_text), title=title))
        description_text = _strip_packaging_text(description_text)
        transcript_text = _dedupe_text_blocks(_correct_domain_terms(_strip_packaging_text(transcript_text), title=title))

        backend_subtitle_status = ""
        if not transcript_text and "bilibili.com" in url:
            backend_subtitle, backend_subtitle_status = self._fetch_bilibili_subtitle_fallback(url)
            if backend_subtitle:
                subtitle_text = backend_subtitle
                transcript_text = backend_subtitle
        media_hints = payload.get("mediaHints") or payload.get("media_hints") or {}
        extraction_diagnostics = payload.get("extractionDiagnostics") or payload.get("extraction_diagnostics") or {}
        subtitle_signal_known = isinstance(media_hints, dict) and "hasSubtitle" in media_hints
        has_subtitle_signal = bool(media_hints.get("hasSubtitle")) if isinstance(media_hints, dict) else False
        subtitle_status = self._normalize_text(
            payload.get("subtitleStatus") or payload.get("subtitle_status") or (media_hints.get("subtitleStatus") if isinstance(media_hints, dict) else ""),
            limit=64,
        ).lower()
        visible_text_length = len(selection_text or page_text)
        strong_selection_signal = len(selection_text) >= 20
        description_length = len(description_text)
        noise_score = self._safe_int(extraction_diagnostics.get("noiseScore"), default=0)
        content_confidence = self._safe_int(extraction_diagnostics.get("contentConfidence"), default=0)
        subtitle_unavailable = subtitle_status in {"", "absent", "container_only", "unknown_error", "api_no_text", "api_confirmed_none"}
        degraded_video_text_signal = bool(
            browser_content_kind == "video_page"
            and not transcript_text
            and not strong_selection_signal
            and (
                (subtitle_signal_known and not has_subtitle_signal)
                or subtitle_unavailable
                or visible_text_length < 800
                or description_length < 160
                or noise_score >= 4
                or (content_confidence and content_confidence <= 45)
            )
        )
        route_warnings = self._build_summary_route_warnings(
            summary_mode,
            payload,
            browser_kind=browser_content_kind,
            selection_text=selection_text,
            page_text=page_text,
            description_text=description_text,
            transcript_text=transcript_text,
            subtitle_status=subtitle_status,
            noise_score=noise_score,
            content_confidence=content_confidence,
            degraded_video_text_signal=degraded_video_text_signal,
        )

        result = build_learning_result_from_browser_context(
            url,
            title=title,
            selection_text=selection_text,
            page_text=page_text,
            transcript_text=transcript_text,
            description_text=description_text,
            content_kind_override=learning_content_kind,
            extra_warnings=route_warnings,
            settings_repository=self.settings_repository,
        )
        snapshot = self._collect_snapshot()
        suggestions = []
        if self.workbench_service is not None:
            try:
                suggestions = self.workbench_service.build_context_suggestions(snapshot)
            except Exception as exc:
                import logging
                logging.getLogger("siyi.browser_bridge").debug("build_context_suggestions failed: %s", exc)
        normalized_key_points = self._normalize_key_points_for_browser_kind(
            browser_content_kind,
            payload,
            list(result.key_points or []),
            result.summary,
        )
        precise_summary = self._compose_precise_summary(
            result.summary,
            normalized_key_points,
            selection_text or page_text,
        )
        reading_topic = self._infer_reading_topic(result.title, selection_text or page_text, normalized_key_points)
        action_hints = self._build_action_hints(
            browser_content_kind,
            precise_summary,
            normalized_key_points,
            degraded_video_text_signal=degraded_video_text_signal,
        )
        recommended_actions = self._build_recommended_actions(
            browser_content_kind,
            precise_summary,
            normalized_key_points,
            degraded_video_text_signal=degraded_video_text_signal,
        )
        top_recommended_action = self._select_top_recommended_action(recommended_actions)

        summary_conclusion = self._build_summary_conclusion(precise_summary, normalized_key_points)
        summary_next_step = self._build_next_step_hint(browser_content_kind, action_hints)
        source_label = "AI总结" if result.summary_source == "ai" else "本地提炼"
        summary_type, summary_type_label = self._resolve_summary_type(
            browser_kind=browser_content_kind,
            summary_source=result.summary_source,
            transcript_text=transcript_text,
            key_points=normalized_key_points,
        )

        source_text_for_nlp = selection_text or page_text or ""
        kw_freq_pairs = _extract_keywords_with_freq(source_text_for_nlp)
        nlp_debug = {
            "topKeywords": [{"word": kw, "freq": freq} for kw, freq in kw_freq_pairs[:8]],
            "sentenceCount": len(normalized_key_points),
            "textLength": len(source_text_for_nlp),
        }

        result_views: dict[str, Any] = {
            "summary": {
                "conclusion": summary_conclusion,
                "keyPoints": normalized_key_points[:6],
                "nextStep": summary_next_step,
                "sourceLabel": source_label,
                "summaryType": summary_type,
                "summaryTypeLabel": summary_type_label,
                "nlpDebug": nlp_debug,
            },
        }
        effective_subtitle = (transcript_text or subtitle_text or "").strip()
        subtitle_status_label = self._subtitle_status_to_label(subtitle_status or "absent")
        if browser_content_kind == "video_page":
            subtitle_lang = str(payload.get("extractionDiagnostics", {}).get("subtitleLan") or "")
            if effective_subtitle:
                segments = self._parse_subtitle_segments(effective_subtitle)
                result_views["subtitle"] = {
                    "text": effective_subtitle,
                    "segments": segments,
                    "source": "自动提取" if subtitle_text else ("手动粘贴" if raw_transcript_text else "后端回退"),
                    "language": subtitle_lang or "zh-CN",
                    "length": len(effective_subtitle),
                    "available": True,
                    "statusLabel": "字幕就绪",
                }
            else:
                result_views["subtitle"] = {
                    "text": "",
                    "source": "",
                    "language": "",
                    "length": 0,
                    "available": False,
                    "status": subtitle_status or "absent",
                    "statusLabel": subtitle_status_label,
                    "hint": "此视频暂无CC字幕，你可以手动粘贴转录文本来补充总结。",
                }

        from datetime import datetime, timezone
        response = {
            "ok": True,
            "summary": precise_summary,
            "keyPoints": normalized_key_points,
            "resultViews": result_views,
            "studySuggestions": list(result.study_suggestions or []),
            "contextSuggestions": [item.get("text") if isinstance(item, dict) else str(item) for item in suggestions],
            "actionHints": action_hints,
            "recommendedActions": recommended_actions,
            "topRecommendedAction": top_recommended_action,
            "meta": {
                "url": result.url,
                "title": result.title,
                "domain": result.domain,
                "contentKind": browser_content_kind,
                "learningContentKind": result.content_kind,
                "summaryMode": summary_mode,
                "summarySource": result.summary_source,
                "summaryType": summary_type,
                "summaryTypeLabel": summary_type_label,
                "summaryModel": result.summary_model,
                "contentLength": result.content_length,
                "warnings": list(result.warnings or []),
                "readingTopic": reading_topic,
                "degradedVideoTextSignal": degraded_video_text_signal,
                "transcriptUsed": bool(transcript_text),
                "transcriptFallback": False,
                "transcriptLength": len(transcript_text),
                "transcriptSource": "provided" if raw_transcript_text else ("auto_subtitle" if subtitle_text and not backend_subtitle_status else ("backend_fallback" if backend_subtitle_status and transcript_text else "")),
                "descriptionLength": len(description_text),
                "subtitleLength": len(subtitle_text),
                "subtitleStatus": subtitle_status or ("text_ready" if transcript_text else "absent"),
                "contentConfidence": content_confidence,
                "noiseScore": noise_score,
                "rootHint": str(payload.get("rootHint") or "").strip(),
                "extractionMode": str(payload.get("extractionMode") or "").strip(),
                "topRecommendedActionId": str((top_recommended_action or {}).get("actionId") or "").strip(),
                "capturedAt": datetime.now(timezone.utc).isoformat(),
            },
        }
        self._track_event(
            "browser.bridge.summarized",
            {
                "url": result.url,
                "summary_source": result.summary_source,
                "has_selection": bool(selection_text),
                "content_length": result.content_length,
                "content_kind": browser_content_kind,
                "summary_mode": summary_mode,
            },
        )
        self._queue_assistant_event(
            "browser.summarized",
            {
                "title": result.title,
                "url": result.url,
                "summary": precise_summary,
                "warnings": list(result.warnings or []),
                "contentKind": browser_content_kind,
                "keyPoints": normalized_key_points,
                "actionHints": list(action_hints),
                "recommendedActions": list(recommended_actions),
                "topRecommendedAction": dict(top_recommended_action or {}),
            },
        )
        return response

    def summarize_with_transcript(self, payload: dict[str, Any]) -> dict[str, Any]:
        transcript_text = self._normalize_text(payload.get("transcriptText") or payload.get("transcript_text"), limit=20000)
        description_text = self._normalize_text(payload.get("descriptionText") or payload.get("description_text"), limit=6000)
        patched_payload = dict(payload or {})

        if not transcript_text:
            fallback_result = self.summarize_page(patched_payload)
            meta = dict(fallback_result.get("meta") or {})
            warnings = list(meta.get("warnings") or [])
            warnings.append("未提供转录文本，已回退到常规网页总结。")
            meta["warnings"] = self._dedupe_points(warnings, limit=8)
            meta["transcriptUsed"] = False
            meta["transcriptFallback"] = True
            meta["transcriptLength"] = 0
            meta["descriptionLength"] = len(description_text)
            fallback_result["meta"] = meta
            return fallback_result

        title = self._normalize_text(payload.get("title"), limit=200)
        page_text = self._normalize_text(payload.get("pageText") or payload.get("page_text"), limit=50000)
        merged_parts = []
        if title:
            merged_parts.append(title)
        if description_text:
            merged_parts.append(description_text)
        merged_parts.append(transcript_text)
        if page_text:
            merged_parts.append(page_text[:8000])

        patched_payload["selectionText"] = transcript_text
        merged_page_text = _dedupe_text_blocks("\n\n".join(merged_parts))
        patched_payload["pageText"] = merged_page_text
        patched_payload["transcriptText"] = transcript_text
        patched_payload["descriptionText"] = description_text

        result = self.summarize_page(patched_payload)
        meta = dict(result.get("meta") or {})
        warnings = list(meta.get("warnings") or [])
        warnings.append("本次总结已合并标题、简介与转录文本三路信号。")
        meta["warnings"] = self._dedupe_points(warnings, limit=8)
        meta["transcriptUsed"] = True
        meta["transcriptFallback"] = False
        meta["transcriptLength"] = len(transcript_text)
        meta["descriptionLength"] = len(description_text)
        result["meta"] = meta
        return result

    # ── 总结构建辅助 ──

    def _resolve_summary_type(
        self,
        *,
        browser_kind: str,
        summary_source: str,
        transcript_text: str,
        key_points: list[str],
    ) -> tuple[str, str]:
        if summary_source == "ai":
            summary_type = "ai"
        elif transcript_text and browser_kind == "video_page":
            summary_type = "subtitle_driven"
        elif any("：" in str(point or "") for point in key_points):
            summary_type = "structured"
        else:
            summary_type = "general"
        return summary_type, self._SUMMARY_TYPE_LABELS.get(summary_type, "通用提炼")

    def _build_summary_conclusion(self, precise_summary: str, key_points: list[str]) -> str:
        text = (precise_summary or "").strip()
        if not text:
            return ""
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("重点摘要："):
                return line[len("重点摘要："):].strip()
            if line.startswith("核心结论："):
                return line[len("核心结论："):].strip()
            if line and not line.startswith("补充重点") and not line.startswith("关键信息") and not line.startswith("关键点"):
                return line if len(line) <= 200 else line[:197] + "..."
        return text[:200]

    def _build_next_step_hint(self, browser_kind: str, action_hints: list[str]) -> str:
        if action_hints:
            return action_hints[0]
        if browser_kind == "video_page":
            return "可将视频要点转成笔记，加深记忆"
        return ""

    def _compose_precise_summary(self, summary: str, key_points: list[str] | None, source_text: str) -> str:
        normalized_summary = _correct_domain_terms(self._normalize_text(summary, limit=1600))
        points = self._dedupe_points(key_points or [], limit=5)
        points = [_correct_domain_terms(p) for p in points]
        source = self._normalize_text(source_text, limit=1200)
        noise_pattern = re.compile(
            r"(一键三连|点赞|投币|收藏|分享|关注|弹幕|评论留言|打包带走|"
            r"拜托.*重要|如果.*有用.*请|长按点赞|免费.*带走|支持一下|展开更多|收起|"
            r"置顶评论|置顶平论|配套资料|配套籽料|自取哦|"
            r"评论.*666|留言.*666|666即可|全部免费|全网最|"
            r"零基础小白专用|小白专用课|这对我真的很重要|"
            r"无论是新手|无论.*皆可|拜托.*重要|支持一下哦)"
        )

        cleaned_summary = noise_pattern.sub("", normalized_summary).strip()
        if cleaned_summary and len(cleaned_summary) < 20:
            cleaned_summary = ""
        if cleaned_summary and len(cleaned_summary) > 120:
            first_sentence_end = min(
                (cleaned_summary.find(c) for c in "。！？\n" if cleaned_summary.find(c) > 0),
                default=120,
            )
            cleaned_summary = cleaned_summary[:first_sentence_end].rstrip("。！？!?\n") + "。"

        if cleaned_summary:
            summary_compare = re.sub(r"\s+", "", cleaned_summary)
            filtered_points = []
            for point in points:
                point_compare = re.sub(r"\s+", "", point)
                if point_compare and (point_compare in summary_compare or summary_compare.rstrip("。") in point_compare):
                    continue
                filtered_points.append(point)
            points = self._dedupe_points(filtered_points, limit=4)
        else:
            points = self._dedupe_points(points, limit=4)

        if cleaned_summary and points:
            detail_lines = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(points[:4]))
            return f"重点摘要：{cleaned_summary}\n\n关键点：\n{detail_lines}"
        if points:
            first = points[0]
            detail_lines = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(points[:4]))
            return f"重点摘要：{first}\n\n关键点：\n{detail_lines}"
        if cleaned_summary:
            return f"重点摘要：{cleaned_summary}"
        if source:
            clean_source = noise_pattern.sub("", source).strip()
            clean_source = _correct_domain_terms(clean_source)
            fallback = clean_source[:200] if clean_source else source[:200]
            return f"重点摘要：{fallback}"
        return "当前页面可提取内容较少，建议补充可读文本后再总结。"

    def _normalize_key_points_for_browser_kind(
        self,
        browser_kind: str,
        payload: dict[str, Any],
        key_points: list[str] | None,
        summary: str,
    ) -> list[str]:
        cleaned = self._dedupe_points(key_points or [], limit=4)
        if browser_kind != "video_page":
            return cleaned
        if len(cleaned) >= 3:
            return cleaned[:4]
        extracted = self._extract_video_key_points(payload, summary)
        merged = self._dedupe_points(cleaned + extracted, limit=4)
        if merged:
            return merged
        fallback = [
            "视频页可见文本较少，建议先补充字幕、简介或评论区核心信息后再总结。",
            "若是课程视频，优先记录可执行步骤、关键概念和风险点。",
        ]
        return self._dedupe_points(fallback, limit=4)

    def _extract_video_key_points(self, payload: dict[str, Any], summary: str) -> list[str]:
        selected = self._normalize_text(payload.get("selectionText") or payload.get("selection_text"), limit=8000)
        description_text = self._normalize_text(payload.get("descriptionText") or payload.get("description_text"), limit=3000)
        transcript_text = self._normalize_text(
            payload.get("transcriptText") or payload.get("transcript_text") or payload.get("subtitleText") or payload.get("subtitle_text"),
            limit=12000,
        )
        page_text = self._normalize_text(payload.get("pageText") or payload.get("page_text"), limit=20000)
        title = self._normalize_text(payload.get("title"), limit=200)
        candidates_text = "\n".join(item for item in [title, description_text, selected, transcript_text, summary, page_text] if item)
        if not candidates_text:
            return []

        noise_tokens = {"播放", "弹幕", "点赞", "投币", "收藏", "分享", "相关推荐", "评论", "合集",
                        "一键三连", "打包带走", "拜托", "支持一下", "长按点赞", "免费", "展开更多", "收起"}

        raw_lines = [
            segment.strip()
            for segment in re.split(r"[\n\r]|(?<=[。！？!?；;])", candidates_text)
            if segment and segment.strip()
        ]
        if len(raw_lines) <= 3 and len(candidates_text) > 200:
            space_split = re.split(r"\s+", candidates_text)
            merged = []
            buf = ""
            for part in space_split:
                buf = (buf + " " + part).strip() if buf else part
                if len(buf) >= 25:
                    merged.append(buf)
                    buf = ""
            if buf:
                merged.append(buf)
            if len(merged) > 3:
                raw_lines = merged

        weighted: list[tuple[int, str]] = []
        for index, line in enumerate(raw_lines[:80]):
            normalized = re.sub(r"\s+", " ", line).strip(" -:：;；。")
            if len(normalized) < 8:
                continue
            noise_count = sum(1 for token in noise_tokens if token in normalized)
            if noise_count >= 2:
                continue
            if re.fullmatch(r"[\d:\s]+", normalized):
                continue
            if len(normalized) > 88:
                normalized = normalized[:88].rstrip() + "..."
            score = 0
            if re.search(r"\b\d{1,2}:\d{2}\b", normalized):
                score += 3
            if re.search(r"第[一二三四五六七八九十0-9]+", normalized):
                score += 2
            for token in ["核心", "重点", "结论", "步骤", "风险", "建议", "实战", "案例", "注意", "任务", "方法",
                          "架构", "原理", "部署", "微调", "训练", "模型", "概念", "环境", "搭建", "入门", "进阶"]:
                if token in normalized:
                    score += 2
            score += max(0, 4 - index // 6)
            score += min(len(normalized) // 22, 3)
            score -= noise_count * 3
            weighted.append((score, normalized))

        weighted.sort(key=lambda item: item[0], reverse=True)
        points = [item[1] for item in weighted[:12]]
        return self._dedupe_points(points, limit=6)

    @staticmethod
    def _dedupe_points(points: list[str], *, limit: int = 6) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for raw in points:
            text = str(raw or "").strip()
            if not text:
                continue
            if len(text) > 120:
                text = text[:117].rstrip() + "..."
            key = re.sub(r"\s+", "", text.lower())
            if key in seen:
                continue
            is_dup = False
            for existing_key in seen:
                shorter, longer = (key, existing_key) if len(key) <= len(existing_key) else (existing_key, key)
                if shorter in longer:
                    is_dup = True
                    break
                overlap = len(set(shorter) & set(longer))
                if min(len(set(shorter)), len(set(longer))) > 0 and overlap / min(len(set(shorter)), len(set(longer))) > 0.7:
                    is_dup = True
                    break
            if is_dup:
                continue
            seen.add(key)
            result.append(text)
            if len(result) >= limit:
                break
        return result

    def _infer_reading_topic(self, title: str, text: str, key_points: list[str]) -> str:
        if key_points:
            first = str(key_points[0]).strip()
            if first:
                return first[:80]
        title_text = str(title or "").strip()
        if title_text:
            return title_text[:80]
        normalized = self._normalize_text(text, limit=120)
        return normalized[:80]

    # ── 路由警告与动作提示 (static) ──

    @staticmethod
    def _build_summary_route_warnings(
        summary_mode: str,
        payload: dict[str, Any],
        *,
        browser_kind: str = "article",
        selection_text: str = "",
        page_text: str = "",
        description_text: str = "",
        transcript_text: str = "",
        subtitle_status: str = "",
        noise_score: int = 0,
        content_confidence: int = 0,
        degraded_video_text_signal: bool = False,
    ) -> list[str]:
        warnings = []
        root_hint = str(payload.get("rootHint") or "").strip()
        media_hints = payload.get("mediaHints") or payload.get("media_hints") or {}
        has_subtitle_signal = False
        if isinstance(media_hints, dict):
            has_subtitle_signal = bool(media_hints.get("hasSubtitle"))
        visible_text_length = len(str(selection_text or "").strip() or str(page_text or "").strip())
        if root_hint:
            warnings.append(f"浏览器侧当前主提取节点：{root_hint}。")
        if content_confidence:
            warnings.append(f"当前提取置信度约为 {content_confidence}/100。")
        if summary_mode == "video_focus":
            warnings.append("当前按视频页模式整理，优先关注标题、简介、字幕与可执行片段。")
            if subtitle_status:
                warnings.append(f"当前字幕状态：{subtitle_status}。")
            if transcript_text:
                warnings.append("当前已提取到字幕或转录文本，本次总结会优先使用这一路信号。")
            if noise_score >= 4:
                warnings.append("当前视频页可见文本可能混入推荐、互动或评论区信息，系统已尽量按简介与字幕优先整理。")
            if degraded_video_text_signal or ((not has_subtitle_signal or subtitle_status in {"", "absent", "container_only", "unknown_error"}) and visible_text_length < 800):
                warnings.append("当前视频页未检测到稳定字幕且主体文本信号偏弱，本次总结会主要基于标题与简介，可能仍不够稳定。")
            elif not transcript_text and len(description_text) < 160:
                warnings.append("当前视频页简介偏短，若需要更稳的总结，建议补充字幕或手动选中重点片段。")
        elif summary_mode == "document_focus":
            warnings.append("当前按文档页模式整理，优先关注结构化条目、规则和可沉淀知识点。")
        elif summary_mode == "news_focus":
            warnings.append("当前按资讯页模式整理，优先关注事件主旨、影响点和后续动作。")
        elif browser_kind == "video_page" and degraded_video_text_signal:
            warnings.append("当前视频页文本信号较弱，建议补充字幕、手动选中重点或贴入转录文本后再总结。")
        return warnings

    @staticmethod
    def _build_action_hints(
        browser_kind: str,
        summary: str,
        key_points: list[str] | None = None,
        *,
        degraded_video_text_signal: bool = False,
    ) -> list[str]:
        hints: list[str] = []
        points = list(key_points or [])
        summary_text = str(summary or "")
        if browser_kind == "video_page":
            hints.append("先收进收件箱，后续结合字幕或时间点继续精炼。")
            hints.append("如果内容可执行，直接转成任务而不是只保留摘要。")
            hints.append("如果是课程或教程，建议沉淀成结构化笔记。")
        elif browser_kind == "document_page":
            hints.append("优先转成笔记，保留目录化和知识点结构。")
            hints.append("如果包含流程、规范或步骤，再补一条执行任务。")
            hints.append("长文档建议先沉淀关键条目，再安排二次精读。")
        elif browser_kind == "news_page":
            hints.append("先进入收件箱或资讯视图，避免直接把资讯堆成长笔记。")
            hints.append("如果和当前项目有关，补一条后续跟进任务。")
            hints.append("只有需要长期引用时再转成正式笔记。")
        else:
            hints.append("先判断这页内容更适合进收件箱还是直接沉淀成笔记。")
            hints.append("如果摘要里已有明确行动项，顺手生成任务。")
        combined = " ".join(points) + " " + summary_text
        if degraded_video_text_signal and browser_kind == "video_page":
            hints.insert(0, "当前视频页缺少字幕且文本信号弱，建议先收件，补充转录内容后再生成任务。")
        elif any(token in combined for token in ("需要", "修复", "处理", "安排", "执行", "todo", "任务")):
            hints.insert(0, "这页内容带有执行信号，建议优先生成任务。")
        return hints[:4]

    @staticmethod
    def _build_recommended_actions(
        browser_kind: str,
        summary: str,
        key_points: list[str] | None = None,
        *,
        degraded_video_text_signal: bool = False,
    ) -> list[dict[str, str]]:
        combined = " ".join(list(key_points or [])) + " " + str(summary or "")
        has_execution_signal = any(
            token in combined for token in ("需要", "修复", "处理", "安排", "执行", "todo", "任务")
        )
        summary_length = len(str(summary or "").strip())

        def make_action(action_id: str, label: str, reason: str, *, priority: int, enabled: bool = True, disabled_reason: str = ""):
            return {
                "actionId": action_id,
                "label": label,
                "reason": reason,
                "priority": str(priority),
                "enabled": "true" if enabled else "false",
                "disabledReason": disabled_reason,
            }

        if browser_kind == "video_page":
            actions = [
                make_action(
                    "transcribe_subtitle",
                    "补字幕转录",
                    "当前视频缺少有效字幕，建议先补充转录文本再总结。",
                    priority=130 if degraded_video_text_signal else 50,
                    enabled=degraded_video_text_signal,
                    disabled_reason="当前已具备字幕或文本信号，无需补转录。" if not degraded_video_text_signal else "",
                ),
                make_action(
                    "paste_transcript",
                    "粘贴转录后重试",
                    "可直接粘贴转录文本，系统会合并标题与简介进行二次总结。",
                    priority=120 if degraded_video_text_signal else 55,
                    enabled=degraded_video_text_signal,
                    disabled_reason="当前文本信号充足，可直接总结。" if not degraded_video_text_signal else "",
                ),
                make_action(
                    "capture",
                    "先存到收件箱",
                    "视频页更适合先收件，再补字幕或时间点精炼。",
                    priority=110 if degraded_video_text_signal else 90,
                ),
                make_action(
                    "createTask",
                    "提炼成任务",
                    "如果视频含执行步骤，直接转任务更实用。",
                    priority=70 if degraded_video_text_signal else (100 if has_execution_signal else 70),
                    enabled=(has_execution_signal and not degraded_video_text_signal),
                    disabled_reason=(
                        "当前视频缺少字幕且文本信号弱，建议先收件并补充转录后再生成任务。"
                        if degraded_video_text_signal
                        else ("当前摘要里还没有明显执行信号，先收件或记笔记更稳。" if not has_execution_signal else "")
                    ),
                ),
                make_action(
                    "createNote",
                    "整理成笔记",
                    "课程或教程类视频适合沉淀成结构化笔记。",
                    priority=85 if degraded_video_text_signal else 80,
                ),
            ]
        elif browser_kind == "document_page":
            actions = [
                make_action("createNote", "优先转成笔记", "文档页更适合保留目录和知识点结构。", priority=100),
                make_action(
                    "createTask",
                    "补执行任务",
                    "如果文档包含流程或规范，可追加一条执行任务。",
                    priority=85 if has_execution_signal else 60,
                    enabled=has_execution_signal,
                    disabled_reason="当前更像知识沉淀材料，暂不建议直接生成任务。" if not has_execution_signal else "",
                ),
                make_action("capture", "先进入收件箱", "如果还没看完，可先收件避免信息丢失。", priority=70),
            ]
        elif browser_kind == "news_page":
            actions = [
                make_action("capture", "先收进收件箱", "资讯内容适合先归档，再判断是否长期跟踪。", priority=100),
                make_action(
                    "createTask",
                    "生成跟进任务",
                    "如果资讯影响当前项目，适合补一条跟进任务。",
                    priority=90 if has_execution_signal else 65,
                    enabled=has_execution_signal,
                    disabled_reason="这条资讯当前还没有明确执行信号，先收件更合适。" if not has_execution_signal else "",
                ),
                make_action(
                    "createNote",
                    "转成正式笔记",
                    "只有需要长期引用时再沉淀成正式笔记。",
                    priority=60,
                    enabled=summary_length >= 60,
                    disabled_reason="当前摘要还偏短，先收件再补内容更合适。" if summary_length < 60 else "",
                ),
            ]
        else:
            actions = [
                make_action("capture", "先存到收件箱", "不确定时先收件，后续再整理。", priority=95),
                make_action(
                    "createNote",
                    "整理成笔记",
                    "适合长期保留的内容可以直接沉淀成笔记。",
                    priority=80,
                    enabled=summary_length >= 40,
                    disabled_reason="当前摘要过短，直接记笔记的信息密度还不够。" if summary_length < 40 else "",
                ),
                make_action(
                    "createTask",
                    "生成任务",
                    "如果有明确执行项，直接转任务更高效。",
                    priority=100 if has_execution_signal else 60,
                    enabled=has_execution_signal,
                    disabled_reason="当前内容更像信息浏览，暂不建议直接生成任务。" if not has_execution_signal else "",
                ),
            ]
        seen: set[str] = set()
        deduped: list[dict[str, str]] = []
        for item in actions:
            action_id = str(item.get("actionId") or "").strip()
            if not action_id or action_id in seen:
                continue
            seen.add(action_id)
            deduped.append(item)
        return sorted(deduped, key=lambda item: int(str(item.get("priority") or "0")), reverse=True)[:3]

    @staticmethod
    def _select_top_recommended_action(recommended_actions: list[dict[str, str]] | None) -> dict[str, str]:
        items = list(recommended_actions or [])
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("enabled") or "true").strip().lower() != "false":
                return dict(item)
        return dict(items[0]) if items and isinstance(items[0], dict) else {}
