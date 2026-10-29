"""字幕/转录相关混入 — 从 bridge_content.py 提取。"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Protocol


class TranscriptHost(Protocol):
    """TranscriptMixin 依赖的宿主契约 — 由 BrowserAssistantBridgeService 运行时注入。"""
    def _normalize_text(self, value: Any, *, limit: int) -> str: ...
    def _track_event(self, event_name: str, payload: dict[str, Any]) -> None: ...


class TranscriptMixin:
    """视频字幕转录相关方法。"""
    # 宿主契约：由 BrowserAssistantBridgeService 运行时注入
    if TYPE_CHECKING:
        def _normalize_text(self, value: Any, *, limit: int) -> str: ...
        def _track_event(self, event_name: str, payload: dict[str, Any]) -> None: ...

    _SUBTITLE_STATUS_LABELS = {
        "absent": "无字幕",
        "text_ready": "字幕就绪",
        "container_only": "仅有容器，无文本",
        "api_confirmed_none": "API确认无字幕",
        "api_no_text": "API返回空文本",
        "cid_not_resolved": "无法解析CID",
        "api_error": "字幕接口异常",
        "manual_required": "需手动粘贴",
        "backend_fallback": "后端回退提取",
        "provided": "用户提供",
        "auto_subtitle": "自动提取",
    }

    _TIMESTAMP_RE = re.compile(r"^(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\s+(.*)")

    # ── 核心入口 ──

    def transcribe_subtitle(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_transcript_text = self._normalize_text(payload.get("transcriptText") or payload.get("transcript_text"), limit=20000)
        subtitle_text = self._normalize_text(payload.get("subtitleText") or payload.get("subtitle_text"), limit=20000)
        transcript_text = raw_transcript_text or subtitle_text
        if transcript_text:
            source = "provided" if raw_transcript_text else "auto_subtitle"
            self._track_event("browser.bridge.transcribe_subtitle", {"source": source, "length": len(transcript_text)})
            return {
                "ok": True,
                "source": source,
                "transcriptText": transcript_text,
                "requiresManualInput": False,
            }

        self._track_event("browser.bridge.transcribe_subtitle", {"source": "manual_required", "length": 0})
        return {
            "ok": True,
            "source": "manual_required",
            "transcriptText": "",
            "requiresManualInput": True,
            "message": "当前环境尚未接入自动语音转录，请先粘贴字幕或转录文本。",
        }

    # ── 字幕辅助 ──

    def _subtitle_status_to_label(self, status: str) -> str:
        return self._SUBTITLE_STATUS_LABELS.get(status, status or "未知")

    @staticmethod
    def _parse_subtitle_segments(text: str) -> list[dict[str, str]]:
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if not lines:
            return []

        ts_re = TranscriptMixin._TIMESTAMP_RE
        segments: list[dict[str, str]] = []
        ts_count = 0
        for line in lines:
            m = ts_re.match(line)
            if m:
                ts_count += 1
                segments.append({"time": m.group(1), "text": m.group(2).strip()})
            elif segments:
                segments[-1]["text"] += " " + line
            else:
                segments.append({"time": "", "text": line})

        if ts_count >= max(1, len(lines) * 0.3):
            return [s for s in segments if s["text"].strip()]

        all_text = " ".join(ln for ln in lines)
        parts = re.split(r"(?<=[。！？!?；;\.])\s*|\s{2,}", all_text)
        if len(parts) <= 2 and len(all_text) > 100:
            parts = re.split(r"\s+", all_text)
        result: list[dict[str, str]] = []
        buf = ""
        for p in parts:
            buf = (buf + " " + p).strip() if buf else p
            if len(buf) >= 20:
                result.append({"time": "", "text": buf})
                buf = ""
        if buf:
            result.append({"time": "", "text": buf})
        return result

    def _fetch_bilibili_subtitle_fallback(self, url: str) -> tuple[str, str]:
        import urllib.request

        bvid_match = re.search(r"/video/(BV[\w]+)", url, re.IGNORECASE)
        if not bvid_match:
            return ("", "no_bvid")
        bvid = bvid_match.group(1)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Referer": "https://www.bilibili.com/"}
        try:
            info_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
            req = urllib.request.Request(info_url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                info_data = json.loads(resp.read())
            if info_data.get("code", -1) != 0:
                return ("", f'info_api_code_{info_data.get("code")}')
            cid = info_data.get("data", {}).get("cid")
            if not cid:
                pages = info_data.get("data", {}).get("pages", [])
                cid = pages[0].get("cid") if pages else None
            if not cid:
                return ("", "no_cid")
        except Exception:
            return ("", "info_api_error")
        try:
            player_url = f"https://api.bilibili.com/x/player/wbi/v2?bvid={bvid}&cid={cid}"
            req = urllib.request.Request(player_url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                player_data = json.loads(resp.read())
            if player_data.get("code", -1) != 0:
                return ("", f'backend_player_code_{player_data.get("code")}')
            subtitles = player_data.get("data", {}).get("subtitle", {}).get("subtitles", [])
            if not subtitles:
                return ("", "backend_no_subtitles")
            preferred = next((s for s in subtitles if (s.get("lan") or "").startswith("zh")), subtitles[0])
            subtitle_url = str(preferred.get("subtitle_url") or "").strip()
            if subtitle_url.startswith("//"):
                subtitle_url = f"https:{subtitle_url}"
            if not subtitle_url:
                return ("", "backend_no_url")
            req = urllib.request.Request(subtitle_url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                sub_data = json.loads(resp.read())
            body = sub_data.get("body", [])
            if not isinstance(body, list):
                return ("", "backend_invalid_body")
            lines = [str(item.get("content") or item.get("text") or "").strip() for item in body if isinstance(item, dict)]
            text = "\n".join(line for line in lines if line)
            return (text, "backend_api_ready" if text else "backend_empty")
        except Exception:
            return ("", "backend_player_api_error")
