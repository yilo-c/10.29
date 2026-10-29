"""OCR / image text extraction service.

P1-2 修复：本模块从 ui/translate/ocr.py 移到 services/，使 backend/ 不再
反向 import UI 层。原 UI 模块通过 from services.ocr_service import ... 重导出
保持兼容。

P2.2 增强：
- 新增 ``get_ocr_status()`` 返回 pytesseract 与 tesseract 二进制的可用性诊断
- 新增 ``resolve_tesseract_binary()`` 在 PATH/常见安装路径/env/settings 间探测
- ``extract_text_from_image()`` 支持通过 settings_repository 读取 ``ocr_language``
  与 ``ocr_tesseract_path`` 覆盖

依赖（可选）：
- Python 层：``pip install -e ".[ocr]"``（拉入 pytesseract；Pillow 已在核心依赖）
- 系统层：Tesseract OCR 引擎
  * Windows: https://github.com/UB-Mannheim/tesseract/wiki
  * macOS:   ``brew install tesseract``（中文语言包 ``brew install tesseract-lang``）
  * Linux:   ``apt install tesseract-ocr tesseract-ocr-chi-sim``
- 环境变量：
  * ``TESSERACT_CMD``: 显式指定 tesseract 二进制路径（优先级最高）
  * ``TESSDATA_PREFIX``: 指定 tessdata 语言包目录
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any, Mapping

from core.subprocess_text import run_text

logger = logging.getLogger("siyi.services.ocr")

# Windows 下 Tesseract 官方安装包的默认安装路径（UB-Mannheim build）
# 探测顺序：先 64 位 Program Files，后 32 位
_WIN_TESSERACT_CANDIDATES: tuple[str, ...] = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


def _resolve_tesseract_binary(
    *,
    env: Mapping[str, str] | None = None,
    settings_repository: Any = None,
) -> str | None:
    """探测 tesseract 二进制路径。

    优先级：
    1. settings ``ocr_tesseract_path``（用户在 UI 中显式配置）
    2. 环境变量 ``TESSERACT_CMD``（shell export）
    3. ``shutil.which("tesseract")``（PATH 查找）
    4. Windows 常见安装路径硬编码探测

    Returns:
        二进制绝对路径，或 ``None``（未找到）。
    """
    env = env if env is not None else os.environ

    # 1. settings 覆盖
    if settings_repository is not None:
        try:
            configured = str(settings_repository.get_setting("ocr_tesseract_path", "") or "").strip()
        except Exception:
            configured = ""
        if configured and os.path.isfile(configured):
            return configured

    # 2. TESSERACT_CMD 环境变量
    env_path = env.get("TESSERACT_CMD", "").strip()
    if env_path and os.path.isfile(env_path):
        return env_path

    # 3. PATH 查找（Linux/macOS 主路径，Windows 也支持）
    in_path = shutil.which("tesseract") or shutil.which("tesseract.exe")
    if in_path:
        return in_path

    # 4. Windows 常见安装路径（仅 Windows）
    if os.name == "nt":
        for candidate in _WIN_TESSERACT_CANDIDATES:
            if os.path.isfile(candidate):
                return candidate

    return None


def _query_tesseract_version(binary_path: str) -> str | None:
    """执行 ``tesseract --version`` 拿版本号。

    Returns:
        首行版本字符串（如 ``tesseract v5.3.4``），或 ``None``。
    """
    try:
        result = run_text([binary_path, "--version"], timeout=5)
        # --version 走 stderr（tesseract 的怪癖）
        text = (result.stderr or result.stdout or "").strip()
        if text:
            return text.splitlines()[0]
    except Exception as exc:
        logger.debug("query tesseract --version failed: %s", exc)
    return None


def _query_tesseract_languages(binary_path: str) -> list[str]:
    """执行 ``tesseract --list-langs`` 拿可用语言包列表。

    Returns:
        语言代码列表（如 ``["chi_sim", "eng", "osd"]``），失败返回空列表。
    """
    try:
        result = run_text([binary_path, "--list-langs"], timeout=5)
        text = (result.stderr or result.stdout or "").strip()
        # 输出格式：第一行 "List of available languages..."，后续每行一个 lang code
        langs: list[str] = []
        for line in text.splitlines()[1:]:
            code = line.strip()
            if code and not code.startswith("List"):
                langs.append(code)
        return sorted(langs)
    except Exception as exc:
        logger.debug("query tesseract --list-langs failed: %s", exc)
        return []


def get_ocr_status(
    *,
    env: Mapping[str, str] | None = None,
    settings_repository: Any = None,
) -> dict[str, Any]:
    """返回 OCR 能力诊断信息。

    供 ``ocr.status`` RPC 与前端探测使用，避免每次都走失败路径。

    Returns:
        ``{
            "available": bool,                # pytesseract 可 import + 二进制可执行
            "pytesseract_version": str|None,  # pytesseract.__version__
            "tesseract_binary_path": str|None,# 找到的二进制路径
            "tesseract_version": str|None,    # tesseract --version 首行
            "languages": list[str],           # 可用语言包代码
            "tessdata_prefix": str|None,      # TESSDATA_PREFIX 环境变量
        }``
    """
    env = env if env is not None else os.environ

    # 探测 Python 层
    try:
        import pytesseract

        py_version = getattr(pytesseract, "__version__", None)
    except ImportError:
        return {
            "available": False,
            "pytesseract_version": None,
            "tesseract_binary_path": None,
            "tesseract_version": None,
            "languages": [],
            "tessdata_prefix": env.get("TESSDATA_PREFIX") or None,
        }

    # 探测系统层二进制
    binary_path = _resolve_tesseract_binary(
        env=env,
        settings_repository=settings_repository,
    )
    if binary_path is None:
        return {
            "available": False,
            "pytesseract_version": py_version,
            "tesseract_binary_path": None,
            "tesseract_version": None,
            "languages": [],
            "tessdata_prefix": env.get("TESSDATA_PREFIX") or None,
        }

    # 二进制找到后，再查版本与语言包
    tess_version = _query_tesseract_version(binary_path)
    languages = _query_tesseract_languages(binary_path)

    return {
        "available": True,
        "pytesseract_version": py_version,
        "tesseract_binary_path": binary_path,
        "tesseract_version": tess_version,
        "languages": languages,
        "tessdata_prefix": env.get("TESSDATA_PREFIX") or None,
    }


def extract_text_from_image(
    image_path: str,
    *,
    language: str | None = None,
    settings_repository: Any = None,
) -> str | None:
    """从图片提取文字。

    优先使用 pytesseract（如有安装），否则返回 ``None``。
    调用方应检查返回值并给出降级提示。

    Args:
        image_path: 图片文件绝对路径。
        language: 显式 Tesseract 语言代码（如 ``"chi_sim+eng"``/``"eng"``）；
            ``None`` 时使用 settings 中的 ``ocr_language``，仍为空则默认 ``chi_sim+eng``。
        settings_repository: 设置仓储，用于读取 ``ocr_tesseract_path`` 与 ``ocr_language``。
    """
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        logger.warning('OCR 功能需要安装依赖: pip install -e ".[ocr]"\n同时需要安装 Tesseract OCR 引擎: https://github.com/tesseract-ocr/tesseract')
        return None

    # P2.2: 注入二进制路径（如果设置/环境变量提供了）
    binary_path = _resolve_tesseract_binary(settings_repository=settings_repository)
    if binary_path:
        try:
            pytesseract.pytesseract.tesseract_cmd = binary_path
        except Exception:
            # 不致命：让 pytesseract 自己再走一遍默认查找
            logger.debug("set tesseract_cmd failed", exc_info=True)

    # 语言优先级：显式参数 > settings > 默认 chi_sim+eng
    if language is None and settings_repository is not None:
        try:
            language = str(settings_repository.get_setting("ocr_language", "") or "").strip()
        except Exception:
            language = ""
    if not language:
        language = "chi_sim+eng"

    try:
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img, lang=language)
        text = text.strip()
        if text:
            logger.info("OCR 提取文字成功: %d 字符 (lang=%s)", len(text), language)
            return text
        logger.info("OCR 未识别到文字 (lang=%s)", language)
        return None
    except Exception as exc:
        logger.warning("OCR 提取文字失败: %s", exc, exc_info=True)
        return None
