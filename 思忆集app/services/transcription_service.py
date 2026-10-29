"""本地音频转写服务（基于 faster-whisper）。

P2.1：填补无头后端无 STT 能力的缺口。与 ``services/ocr_service.py`` 严格对称：

- 重依赖（``faster-whisper`` → CTranslate2 + tokenizers）通过 lazy import 引入，
  不安装也能让模块加载成功；
- ``transcribe_audio_file()`` 在依赖缺失时返回 ``None``，由调用方给出降级提示；
- 模型实例按 ``(model_size, device, compute_type)`` 缓存到模块级 dict，
  避免每次调用都重新加载（faster-whisper 加载 base 模型约 2-5s）。

依赖（可选）：
    pip install -e ".[transcription]"

非 WAV 格式（mp3/m4a/flac/ogg/webm）由 faster-whisper 内部通过 ``av`` 包解码，
``av`` 依赖系统层 ffmpeg 共享库；如果未安装会在 decode 阶段抛 ``ImportError``，
本服务统一捕获并返回 ``None`` + warning 日志。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("siyi.services.transcription")

# ---------------------------------------------------------------------------
# 模型缓存
# ---------------------------------------------------------------------------
# key = (model_size, device, compute_type)
# value = faster_whisper.WhisperModel 实例
#
# faster-whisper 的 WhisperModel 是线程安全的（内部 CTranslate2 translator 支持
# 并发 transcribe），因此可以跨请求复用同一实例；仅加载过程需要加锁避免重复下载。
_MODEL_CACHE: dict[tuple[str, str, str], Any] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def _get_or_load_model(
    model_size: str,
    device: str,
    compute_type: str,
    cache_dir: str | None,
) -> Any:
    """按 ``(model_size, device, compute_type)`` 复用 WhisperModel 实例。

    raises ImportError: faster-whisper 未安装时
    raises Exception: 模型下载/加载失败（网络异常、磁盘满等）
    """
    key = (model_size, device, compute_type)
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached

    with _MODEL_CACHE_LOCK:
        # double-check：等锁期间可能已被其他线程加载
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            return cached

        from faster_whisper import WhisperModel

        kwargs: dict[str, Any] = {
            "device": device,
            "compute_type": compute_type,
        }
        if cache_dir:
            # 允许用户把模型下载到自定义目录（默认 ~/.cache/huggingface）
            kwargs["download_root"] = cache_dir

        logger.info(
            "加载 faster-whisper 模型: size=%s device=%s compute=%s cache_dir=%s",
            model_size, device, compute_type, cache_dir or "(default)",
        )
        model = WhisperModel(model_size, **kwargs)
        _MODEL_CACHE[key] = model
        return model


def _resolve_config(settings_repository: Any = None) -> dict[str, Any]:
    """从 settings 读取转写配置，缺省值与 settings_schema DEFAULTS 一致。"""
    defaults = {
        "model_size": "base",
        "device": "cpu",
        "compute_type": "int8",
        "language": "",
        "cache_dir": "",
    }
    if settings_repository is None:
        return defaults

    def _get(key: str, default: str) -> str:
        try:
            val = settings_repository.get_setting(key, default)
        except Exception:
            return default
        return str(val) if val is not None else default

    return {
        "model_size": _get("transcription_model_size", defaults["model_size"]) or defaults["model_size"],
        "device": _get("transcription_device", defaults["device"]) or defaults["device"],
        "compute_type": _get("transcription_compute_type", defaults["compute_type"]) or defaults["compute_type"],
        "language": _get("transcription_language", ""),
        "cache_dir": _get("transcription_model_cache_dir", ""),
    }


def transcribe_audio_file(
    audio_path: str,
    *,
    language: str | None = None,
    settings_repository: Any = None,
) -> str | None:
    """转写本地音频文件为文本。

    Args:
        audio_path: 音频文件绝对路径。支持 wav/mp3/m4a/flac/ogg/webm（非 WAV
            格式依赖 faster-whisper 内部的 ``av`` 包 + 系统 ffmpeg）。
        language: 显式语言代码（如 ``"zh"``/``"en"``）；``None`` 时使用
            settings 中的 ``transcription_language``，仍为空则自动检测。
        settings_repository: 设置仓储，用于读取模型配置。``None`` 时用默认值。

    Returns:
        转写文本；依赖缺失或转写失败时返回 ``None``，调用方应检查并降级提示。
    """
    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except ImportError:
        logger.warning(
            '音频转写功能需要安装依赖: pip install -e ".[transcription]"\n'
            "首次使用会从 HuggingFace 下载 Whisper 模型（base ≈ 145MB）"
        )
        return None

    cfg = _resolve_config(settings_repository)
    try:
        model = _get_or_load_model(
            cfg["model_size"],
            cfg["device"],
            cfg["compute_type"],
            cache_dir=cfg["cache_dir"] or None,
        )
    except Exception as exc:
        logger.warning("加载 faster-whisper 模型失败: %s", exc, exc_info=True)
        return None

    effective_lang = language or cfg["language"] or None

    try:
        # faster-whisper 返回 (segments_generator, info)
        segments, _info = model.transcribe(
            audio_path,
            language=effective_lang,
            vad_filter=True,
        )
        parts = []
        for seg in segments:
            text = (seg.text or "").strip()
            if text:
                parts.append(text)
        if not parts:
            logger.info("音频转写完成但未识别到内容: %s", audio_path)
            return None
        transcript = " ".join(parts)
        logger.info(
            "音频转写成功: %d 字符 (源: %s)",
            len(transcript), Path(audio_path).name,
        )
        return transcript
    except Exception as exc:
        # av/ffmpeg 缺失、文件损坏、格式不支持等
        logger.warning("音频转写失败 (%s): %s", audio_path, exc, exc_info=True)
        return None


def transcribe_audio_bytes(
    data: bytes,
    *,
    suffix: str = ".wav",
    language: str | None = None,
    settings_repository: Any = None,
) -> str | None:
    """转写内存中的音频字节流。

    将字节写入临时文件后调用 ``transcribe_audio_file``，调用方无需关心
    临时文件清理（本函数内部 try/finally 删除）。

    Args:
        data: 音频原始字节（PCM/WAV/MP3 等均可，由 faster-whisper 解码）。
        suffix: 临时文件后缀，用于提示解码器格式。默认 ``.wav``。
        language: 同 ``transcribe_audio_file``。
        settings_repository: 同 ``transcribe_audio_file``。
    """
    if not data:
        return None

    import os
    import tempfile

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        return transcribe_audio_file(
            tmp_path,
            language=language,
            settings_repository=settings_repository,
        )
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def clear_model_cache() -> None:
    """清空模型缓存。供测试或用户切换模型后强制重载使用。"""
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()


def get_cache_status() -> dict[str, bool]:
    """返回当前缓存的模型键集合（供 UI 展示「模型已加载」状态）。"""
    with _MODEL_CACHE_LOCK:
        return {f"{s}/{d}/{c}": True for s, d, c in _MODEL_CACHE.keys()}
