"""截图服务：捕获屏幕并转换为 base64 编码，供聊天输入使用。

用法：
from .screenshot_service import capture_and_encode
    data = capture_and_encode()
    if data:
        chat_input.insert_image_data(data["base64"], data["format"], "截图")
"""
from __future__ import annotations

import base64
import logging
from typing import Any

from PyQt6.QtCore import QByteArray
from PyQt6.QtGui import QGuiApplication, QPixmap

logger = logging.getLogger("siyi.screenshot")


def capture_full_screen() -> QPixmap | None:
    """捕获全屏截图，返回 QPixmap。"""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        logger.warning("无法获取主屏幕")
        return None
    pixmap = screen.grabWindow(0)
    if pixmap is None or pixmap.isNull():
        logger.warning("截图为空")
        return None
    return pixmap


def capture_active_window() -> QPixmap | None:
    """捕获当前活动窗口截图。"""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None
    # grabWindow(0) 在全屏截图上手动裁剪可以实现，但更简单：
    # 直接取全屏截图，后续可由用户裁剪
    return capture_full_screen()


def pixmap_to_base64(pixmap: QPixmap, fmt: str = "png") -> dict[str, Any] | None:
    """将 QPixmap 转换为 base64 编码的图片数据字典。"""
    try:
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice

        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        if not pixmap.save(buf, fmt.upper()):
            logger.warning("图片保存到 buffer 失败")
            return None
        raw_bytes = bytes(buf.data())
        return {
            "base64": base64.b64encode(raw_bytes).decode("ascii"),
            "format": fmt,
            "size": len(raw_bytes),
        }
    except Exception as exc:
        logger.warning("图片编码失败: %s", exc)
        return None


def capture_and_encode(fmt: str = "png") -> dict[str, Any] | None:
    """捕获全屏截图并编码为 base64，一步完成。"""
    pixmap = capture_full_screen()
    if pixmap is None:
        return None
    return pixmap_to_base64(pixmap, fmt)
