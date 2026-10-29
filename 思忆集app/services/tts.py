"""TTS 输出服务：文字转语音朗读。"""
from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtTextToSpeech import QTextToSpeech

logger = logging.getLogger("siyi.tts")


class TTSService(QObject):
    started = pyqtSignal()
    finished = pyqtSignal()
    paused = pyqtSignal()
    resumed = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._tts = QTextToSpeech(self)
        self._is_speaking = False
        self._tts.stateChanged.connect(self._on_state_changed)
        self._tts.setRate(0.0)
        self._tts.setPitch(0.0)
        self._tts.setVolume(0.8)
        self._prefer_chinese_voice()
        logger.info("TTS 初始化完成: 引擎=%s, 语音=%s, 语速=%.1f, 音量=%.1f",
                     self._tts.engine(), self._tts.voice().name() if self._tts.voice() else "default",
                     self._tts.rate(), self._tts.volume())

    def _prefer_chinese_voice(self) -> None:
        for voice in self._tts.availableVoices():
            name = voice.name()
            if any(lang in name for lang in ("zh", "Chinese", "中文")):
                self._tts.setVoice(voice)
                logger.info("已选择中文语音: %s", name)
                return

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    @property
    def is_paused(self) -> bool:
        return self._tts.state() == QTextToSpeech.State.Paused

    def speak(self, text: str) -> None:
        if not text or not text.strip():
            return
        if self._is_speaking:
            self.stop()
        self._is_speaking = True
        self._tts.say(text)
        self.started.emit()
        logger.debug("TTS 开始朗读: %.50s...", text)

    def stop(self) -> None:
        if self._is_speaking:
            self._tts.stop()
            self._is_speaking = False
            logger.debug("TTS 已停止")

    def pause(self) -> None:
        if self._is_speaking and self._tts.state() == QTextToSpeech.State.Speaking:
            self._tts.pause()

    def resume(self) -> None:
        if self._tts.state() == QTextToSpeech.State.Paused:
            self._tts.resume()
            self.resumed.emit()

    def set_rate(self, rate: float) -> None:
        self._tts.setRate(max(-1.0, min(1.0, rate)))

    def set_volume(self, volume: float) -> None:
        self._tts.setVolume(max(0.0, min(1.0, volume)))

    def set_pitch(self, pitch: float) -> None:
        self._tts.setPitch(max(-1.0, min(1.0, pitch)))

    def get_voices(self) -> list[dict[str, Any]]:
        return [{"name": v.name(), "gender": v.gender().name if hasattr(v.gender(), "name") else str(v.gender()), "age": v.age()} for v in self._tts.availableVoices()]

    def _on_state_changed(self, state: QTextToSpeech.State) -> None:
        if state == QTextToSpeech.State.Ready and self._is_speaking:
            self._is_speaking = False
            self.finished.emit()
            logger.debug("TTS 朗读完成")
        elif state == QTextToSpeech.State.Paused:
            self.paused.emit()
        elif state == QTextToSpeech.State.Speaking:
            if not self._is_speaking:
                self._is_speaking = True
                self.started.emit()
