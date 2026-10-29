# -*- coding: utf-8 -*-
"""音频转写 JSON-RPC handler（P2.1）。

与 ``backend/handlers/_ocr.py`` 严格对称：
- ``transcribe.transcribe_file(audio_path, language?)`` — 文件路径入参
- ``transcribe.transcribe_base64(data, language?)`` — base64 入参
- 返回结构对齐 ``{"success": True, "text": "..."}`` / ``{"success": False, "error": "..."}``
- CPU 密集（模型推理）必须包 ``asyncio.to_thread``
- 临时文件用 try/finally 确保异常路径也清理
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.transcription")

_ALLOWED_AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".aac", ".opus")


def register_transcription(registry, context) -> None:
    """Register transcription-related JSON-RPC methods."""

    async def transcribe_file(
        audio_path: str = "",
        language: "str | None" = None,
        **kwargs: "Any",
    ) -> "dict[str, Any]":
        """转写本地音频文件为文本。

        Args:
            audio_path: 音频文件路径。支持 wav/mp3/m4a/flac/ogg/webm/aac/opus。
            language: 显式语言代码（如 "zh"/"en"）；留空走 settings 中的
                ``transcription_language``，仍为空则自动检测。

        Returns:
            ``{"success": true, "text": "..."}`` 或 ``{"success": false, "error": "..."}``

        依赖：
            需 ``pip install -e ".[transcription]"`` 安装 faster-whisper；
            非 WAV 格式还需系统层 ffmpeg（由 faster-whisper 内部 ``av`` 包调用）。
        """
        if not audio_path:
            return {"success": False, "error": "audio_path is required"}
        path = Path(audio_path)
        if path.suffix.lower() not in _ALLOWED_AUDIO_EXTS:
            return {
                "success": False,
                "error": "仅允许音频文件（wav/mp3/m4a/flac/ogg/webm/aac/opus）",
            }
        if not path.is_file():
            return {"success": False, "error": f"File not found: {audio_path}"}
        from services.transcription_service import transcribe_audio_file

        settings_repo = getattr(context, "settings_repository", None)
        text = await asyncio.to_thread(
            transcribe_audio_file,
            str(path.resolve()),
            language=language,
            settings_repository=settings_repo,
        )
        if text:
            return {"success": True, "text": text}
        return {
            "success": False,
            "error": "转写失败：可能未安装 faster-whisper（pip install -e \"."
            "[transcription]\"），或音频解码失败（非 WAV 格式需系统层 ffmpeg），或音频无语音内容",
        }

    async def transcribe_base64(
        data: str = "",
        language: "str | None" = None,
        format: str = "wav",
        **kwargs: "Any",
    ) -> "dict[str, Any]":
        """转写 base64 编码的音频数据。

        Args:
            data: base64 编码的音频字节（可含 ``data:audio/wav;base64,`` 前缀）。
            language: 同 ``transcribe_file``。
            format: 音频格式提示（wav/mp3/m4a/...），用于临时文件后缀。

        Returns:
            同 ``transcribe_file``。
        """
        if not data:
            return {"success": False, "error": "data is required"}
        fmt_norm = (format or "wav").lstrip(".").lower()
        if fmt_norm not in {ext.lstrip(".") for ext in _ALLOWED_AUDIO_EXTS}:
            return {"success": False, "error": f"不支持的音频格式: {format}"}
        suffix = "." + fmt_norm
        tmp_path = None
        try:
            import base64

            raw = base64.b64decode(data.split(",")[-1] if "," in data else data)
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(raw)
                tmp_path = tmp.name
            from services.transcription_service import transcribe_audio_file

            settings_repo = getattr(context, "settings_repository", None)
            text = await asyncio.to_thread(
                transcribe_audio_file,
                tmp_path,
                language=language,
                settings_repository=settings_repo,
            )
            if text:
                return {"success": True, "text": text}
            return {
                "success": False,
                "error": "转写失败：可能未安装 faster-whisper，或音频解码失败，或音频无语音内容",
            }
        except Exception as exc:
            logger.warning("transcribe base64 failed: %s", exc)
            return {"success": False, "error": str(exc)}
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    async def transcribe_status(**kwargs: "Any") -> "dict[str, Any]":
        """返回转写能力状态：依赖是否已安装、当前缓存了哪些模型。

        供前端在调用 ``transcribe.transcribe_file`` 前探测能力，避免每次都
        走一遍失败路径。
        """
        try:
            import faster_whisper

            available = True
            version = getattr(faster_whisper, "__version__", "unknown")
        except ImportError:
            available = False
            version = None
        from services.transcription_service import get_cache_status

        return {
            "available": available,
            "version": version,
            "cached_models": get_cache_status(),
        }

    registry.register("transcribe.transcribe_file", transcribe_file)
    registry.register("transcribe.transcribe_base64", transcribe_base64)
    registry.register("transcribe.status", transcribe_status)
