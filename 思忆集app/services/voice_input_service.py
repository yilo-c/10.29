"""语音输入服务：录音 + Whisper API 转文字。

使用 PyQt6 的 QAudioSource 录制音频（无额外依赖），
通过 OpenAI Whisper API 转录音频为文字。

用法：
    service = VoiceInputService()
    service.start_recording()
    # ... 用户点击停止 ...
    service.stop_recording()
    service.transcription_ready.connect(lambda text: chat_input.setText(text))
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from PyQt6.QtCore import QByteArray, QObject, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

logger = logging.getLogger("siyi.voice_input")

SAMPLE_RATE = 16000  # Whisper 最佳采样率
SAMPLE_WIDTH = 2      # 16-bit
CHANNELS = 1          # 单声道


def _make_temp_path() -> str:
    """生成临时 WAV 文件路径。"""
    temp_dir = Path(tempfile.gettempdir()) / "siyi_voice"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return str(temp_dir / f"voice_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav")


def _save_wav(buffer: bytes, output_path: str) -> None:
    """将 PCM 数据保存为 WAV 文件。"""
    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(buffer)


def _cleanup_old_temp_files(max_age_hours: int = 24) -> None:
    """删除临时目录中超过指定时间的旧 WAV 文件。"""
    temp_dir = Path(tempfile.gettempdir()) / "siyi_voice"
    if not temp_dir.exists():
        return
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for f in temp_dir.iterdir():
        if f.is_file() and f.suffix.lower() == ".wav":
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except Exception as exc:
                logger.warning("清理旧临时语音文件失败 %s: %s", f.name, exc, exc_info=True)
    if removed:
        logger.info("已清理 %d 个过期临时语音文件", removed)


class VoiceRecorder(QObject):
    """录音器 — 使用 QAudioSource 从麦克风采集音频。

    Signals:
        recording_started: 开始录音
        recording_stopped(str): 录音完成，发出 WAV 文件路径
        level_changed(float): 音频电平 (0.0-1.0)，可用于 UI 反馈
        error_occurred(str): 录音出错
    """

    recording_started = pyqtSignal()
    recording_stopped = pyqtSignal(str)
    level_changed = pyqtSignal(float)
    error_occurred = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._audio_source: QAudioSource | None = None
        self._input_device = None
        self._buffer = QByteArray()
        self._output_path: str = ""
        self._is_recording = False

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    def start_recording(self, output_path: str | None = None) -> None:
        """开始录音。"""
        if self._is_recording:
            return

        self._output_path = output_path or _make_temp_path()
        self._buffer.clear()

        fmt = QAudioFormat()
        fmt.setSampleRate(SAMPLE_RATE)
        fmt.setChannelCount(CHANNELS)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

        device = QMediaDevices.defaultAudioInput()
        if not device.isFormatSupported(fmt):
            logger.warning("请求的音频格式不受支持，使用设备首选格式")
            fmt = device.preferredFormat()
            logger.info("设备首选格式: %s Hz, %d 通道", fmt.sampleRate(), fmt.channelCount())

        self._audio_source = QAudioSource(device, fmt)
        if self._audio_source is None:
            self.error_occurred.emit("无法创建音频源")
            return

        try:
            self._input_device = self._audio_source.start()
        except Exception as exc:
            self.error_occurred.emit(f"启动录音失败: {exc}")
            self._audio_source = None
            return

        if self._input_device is not None:
            self._input_device.readyRead.connect(self._on_ready_read)
            self._input_device.bytesReady.connect(self._on_bytes_ready)
        else:
            self._audio_source = None
            self.error_occurred.emit("无法获取音频输入设备")
            return

        self._is_recording = True
        self.recording_started.emit()
        logger.info("录音开始: %s", self._output_path)

    def _on_ready_read(self) -> None:
        """读取可用音频数据。"""
        data = self._input_device.readAll()
        if data and not data.isEmpty():
            self._buffer.append(data)

    def _on_bytes_ready(self) -> None:
        """数据可用时读取，计算电平用于 UI 反馈。"""
        if self._input_device is None:
            return
        data = self._input_device.readAll()
        if data and not data.isEmpty():
            self._buffer.append(data)
            # 计算简单 RMS 电平
            raw = bytes(data.data())
            if len(raw) >= 2:
                samples = len(raw) // 2
                if samples > 0:
                    total = 0
                    for i in range(samples):
                        s = int.from_bytes(raw[i * 2 : i * 2 + 2], "little", signed=True)
                        total += abs(s)
                    level = min(1.0, total / (samples * 32768) * 3)
                    self.level_changed.emit(level)

    def stop_recording(self) -> None:
        """停止录音并保存 WAV 文件。"""
        if not self._is_recording:
            return
        self._is_recording = False

        if self._audio_source:
            try:
                self._audio_source.stop()
            except Exception:
                logger.debug("停止录音时关闭音频源失败", exc_info=True)
            self._audio_source.deleteLater()
            self._audio_source = None

        if self._input_device is not None:
            try:
                self._input_device.readyRead.disconnect()
                self._input_device.bytesReady.disconnect()
            except Exception:
                logger.debug("停止录音时断开信号连接失败", exc_info=True)
            self._input_device = None

        try:
            _save_wav(bytes(self._buffer.data()), self._output_path)
            file_size = os.path.getsize(self._output_path)
            logger.info("录音完成: %s (%d bytes)", self._output_path, file_size)
            self.recording_stopped.emit(self._output_path)
        except Exception as exc:
            self.error_occurred.emit(f"保存录音失败: {exc}")

    def cancel_recording(self) -> None:
        """取消录音（不保存文件）。"""
        if not self._is_recording:
            return
        self._is_recording = False

        if self._audio_source:
            try:
                self._audio_source.stop()
            except Exception:
                logger.debug("取消录音时停止音频源失败", exc_info=True)
            self._audio_source.deleteLater()
            self._audio_source = None
        self._input_device = None
        self._buffer.clear()

        if self._output_path and os.path.exists(self._output_path):
            try:
                os.remove(self._output_path)
            except Exception:
                logger.debug("取消录音时删除临时文件失败: %s", self._output_path, exc_info=True)


# ── Whisper API 转写 ───────────────────────────────────────────


def transcribe_audio(
    audio_path: str,
    api_key: str,
    base_url: str = "https://api.openai.com",
    language: str = "zh",
    model: str = "whisper-1",
    timeout: int = 60,
) -> str:
    """调用 OpenAI Whisper API 转录音频为文字。

    Args:
        audio_path: WAV 音频文件路径
        api_key: OpenAI API 密钥
        base_url: API 基础 URL（兼容 OpenAI 格式的提供商）
        language: 语言代码（zh/en/ja 等）
        model: Whisper 模型名称
        timeout: 请求超时（秒）

    Returns:
        转录后的文字

    Raises:
        requests.RequestException: API 请求失败
        ValueError: 响应格式异常
    """
    url = f"{base_url.rstrip('/')}/v1/audio/transcriptions"
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    with open(audio_path, "rb") as f:
        files = {"file": ("audio.wav", f, "audio/wav")}
        data: dict[str, Any] = {"model": model}
        if language:
            data["language"] = language

        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            files=files,
            data=data,
            timeout=timeout,
        )

    if resp.status_code == 413:
        raise ValueError("音频文件过大，请缩短录音时长")
    resp.raise_for_status()

    result = resp.json()
    text = result.get("text", "").strip()
    if not text:
        raise ValueError("Whisper 返回空文本，请重试")
    return text


def transcribe_audio_with_retry(
    audio_path: str,
    api_key: str,
    base_url: str = "https://api.openai.com",
    language: str = "zh",
    model: str = "whisper-1",
    max_retries: int = 2,
) -> str:
    """带重试的音频转录。"""
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return transcribe_audio(
                audio_path=audio_path,
                api_key=api_key,
                base_url=base_url,
                language=language,
                model=model,
            )
        except (requests.RequestException, ValueError, FileNotFoundError) as exc:
            last_error = exc
            if attempt < max_retries:
                wait = (attempt + 1) * 2
                logger.warning("转录重试 %d/%d: %s", attempt + 1, max_retries, exc)
                time.sleep(wait)
    raise last_error  # type: ignore[misc]


class VoiceInputService(QObject):
    """语音输入服务 — 整合录音 + 转录。

    Signals:
        transcription_ready(str): 转录完成，发出文字
        recording_started: 开始录音
        recording_stopped: 录音完成（正在转录）
        error_occurred(str): 出错
    """

    transcription_ready = pyqtSignal(str)
    recording_started = pyqtSignal()
    recording_stopped = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._recorder = VoiceRecorder(self)
        self._recorder.recording_started.connect(self.recording_started)
        self._recorder.recording_stopped.connect(self._on_recording_stopped)
        self._recorder.error_occurred.connect(self.error_occurred)
        self._recorder.level_changed.connect(self._on_level_changed)

        self._api_key: str = ""
        self._api_base: str = "https://api.openai.com"
        self._language: str = "zh"
        self._last_audio_path: str = ""

        # 启动时清理过期临时语音文件
        _cleanup_old_temp_files()

    @property
    def is_recording(self) -> bool:
        return self._recorder.is_recording

    def configure(self, api_key: str, api_base: str = "https://api.openai.com", language: str = "zh") -> None:
        """配置 API 参数。"""
        self._api_key = api_key
        self._api_base = api_base
        self._language = language

    def start_recording(self) -> None:
        """开始录音。"""
        if not self._api_key:
            self.error_occurred.emit("请先在设置中配置 LLM API 密钥以使用语音输入")
            return
        self._recorder.start_recording()

    def stop_recording(self) -> None:
        """停止录音并触发转录。"""
        self._recorder.stop_recording()

    def cancel_recording(self) -> None:
        """取消录音。"""
        self._recorder.cancel_recording()

    def _on_recording_stopped(self, audio_path: str) -> None:
        """录音完成，开始转录。"""
        self._last_audio_path = audio_path
        self.recording_stopped.emit()

        try:
            text = transcribe_audio_with_retry(
                audio_path=audio_path,
                api_key=self._api_key,
                base_url=self._api_base,
                language=self._language,
            )
            self.transcription_ready.emit(text)
            # 转录成功后删除临时 WAV 文件
            try:
                os.remove(audio_path)
                logger.debug("已删除临时语音文件: %s", audio_path)
            except OSError as exc:
                logger.warning("删除临时语音文件失败 %s: %s", audio_path, exc)
        except Exception as exc:
            self.error_occurred.emit(f"语音转文字失败：{exc}")

    def _on_level_changed(self, level: float) -> None:
        """音频电平更新（可连接 UI 波形显示）。"""
        # 预留：电平变化可在 UI 上显示录音音量指示
        logger.debug("Audio level changed: %s", level)
