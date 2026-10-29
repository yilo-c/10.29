"""OCR / image text extraction JSON-RPC handler."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.ocr")


def register_ocr(registry, context) -> None:
    """Register OCR-related JSON-RPC methods."""

    # ── ocr.extract_text ──
    async def ocr_extract_text(
        image_path: str = "",
        language: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Extract text from an image file.

        Args:
            image_path: Path to the image file on disk.
            language: Tesseract language code (e.g. "chi_sim+eng"/"eng");
                留空走 settings ``ocr_language``，仍为空默认 ``chi_sim+eng``。

        Returns:
            {"success": true, "text": "..."} or {"success": false, "error": "..."}
        """
        if not image_path:
            return {"success": False, "error": "image_path is required"}

        path = Path(image_path)
        # 安全：限制仅允许图片文件扩展名，防止用作文件存在性 oracle
        _ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"}
        if path.suffix.lower() not in _ALLOWED_IMAGE_EXTS:
            return {"success": False, "error": "仅允许图片文件（png/jpg/jpeg/gif/bmp/tiff/webp）"}
        if not path.is_file():
            return {"success": False, "error": f"File not found: {image_path}"}

        # P1-2: 改 import services.ocr_service（不再反向依赖 UI 层）
        from services.ocr_service import extract_text_from_image

        settings_repo = getattr(context, "settings_repository", None)
        # P1 修复：OCR 是 CPU 密集型同步操作，包裹 to_thread 避免阻塞 event loop
        # P2.2: 传入 settings_repository 让 service 读取 ocr_tesseract_path / ocr_language
        text = await asyncio.to_thread(
            extract_text_from_image,
            str(path.resolve()),
            language=language,
            settings_repository=settings_repo,
        )
        if text:
            return {"success": True, "text": text}
        return {
            "success": False,
            "error": (
                "No text extracted from image（可能未安装 pytesseract "
                "(pip install -e \".[ocr]\")、Tesseract 引擎未配置、"
                "或图片无文字内容；调用 ocr.status 查看诊断）"
            ),
        }

    registry.register("ocr.extract_text", ocr_extract_text)

    # ── ocr.extract_base64 ──
    async def ocr_extract_base64(
        data: str = "",
        language: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Extract text from a base64 image string."""
        if not data:
            return {"success": False, "error": "data is required"}
        tmp_path: str | None = None
        try:
            import base64
            # Strip data URL prefix
            raw = base64.b64decode(data.split(",")[-1] if "," in data else data)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(raw)
                tmp_path = tmp.name
            from services.ocr_service import extract_text_from_image
            settings_repo = getattr(context, "settings_repository", None)
            text = await asyncio.to_thread(
                extract_text_from_image,
                tmp_path,
                language=language,
                settings_repository=settings_repo,
            )
            if text:
                return {"success": True, "text": text}
            return {
                "success": False,
                "error": (
                    "No text extracted（可能未安装 pytesseract 或 Tesseract 引擎未配置；"
                    "调用 ocr.status 查看诊断）"
                ),
            }
        except ImportError:
            return {"success": False, "error": "Tesseract/OCR not installed"}
        except Exception as exc:
            logger.warning("ocr base64 failed: %s", exc)
            return {"success": False, "error": str(exc)}
        finally:
            # P1-2: 用 try/finally 确保 tmp 文件在异常路径也被删除
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    registry.register("ocr.extract_base64", ocr_extract_base64)

    # ── ocr.status (P2.2) ──
    async def ocr_status(**kwargs: Any) -> dict[str, Any]:
        """返回 OCR 能力诊断：pytesseract 版本、tesseract 二进制路径/版本/语言包。

        供前端在调用 ``ocr.extract_text`` 前探测能力，避免每次都走失败路径。
        与 ``transcribe.status`` 严格对称。
        """
        from services.ocr_service import get_ocr_status

        settings_repo = getattr(context, "settings_repository", None)
        # get_ocr_status 是同步 + 可能调用 subprocess（tesseract --version/--list-langs），
        # 包 to_thread 避免阻塞事件循环
        return await asyncio.to_thread(
            get_ocr_status, settings_repository=settings_repo,
        )

    registry.register("ocr.status", ocr_status)

