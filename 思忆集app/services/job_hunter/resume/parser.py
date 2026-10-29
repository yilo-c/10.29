"""简历文件解析器。

支持 PDF、Word（docx/doc）、纯文本、Markdown 以及图片 OCR fallback。
所有文件内容均在本地处理，不上传云端。
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 10485760
RESUME_MAX_TEXT_LENGTH = 12000
MAX_RESUME_FILES = 20
MAX_OCR_PAGES = 10
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md", ".png", ".jpg", ".jpeg"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


@dataclass
class ResumeDocument:
    """解析后的简历文档。"""

    id: str
    filename: str
    format: str
    text: str
    file_path: str
    size: int
    truncated: bool = False

    def to_dict(self, data_dir: str = "") -> dict[str, Any]:
        rel_path = self.file_path
        if data_dir and self.file_path.startswith(data_dir):
            rel_path = os.path.relpath(self.file_path, data_dir)
        return {
            "id": self.id,
            "filename": self.filename,
            "format": self.format,
            "text": self.text,
            "file_path": rel_path,
            "size": self.size,
            "truncated": self.truncated,
        }


class ResumeParser:
    """简历文件解析器。"""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = data_dir
        self._resumes_dir = os.path.join(data_dir, "resumes")
        os.makedirs(self._resumes_dir, exist_ok=True)

    def parse_bytes(self, filename: str, file_bytes: bytes) -> ResumeDocument:
        """从文件字节解析简历文本。"""
        if len(file_bytes) > MAX_FILE_SIZE:
            raise ValueError(f"文件大小超过限制 {MAX_FILE_SIZE // 1024 // 1024}MB")

        ext = os.path.splitext(filename)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"不支持的文件格式: {ext}，仅支持 {', '.join(SUPPORTED_EXTENSIONS)}")

        self._validate_file_header(file_bytes, ext)

        doc_id = uuid.uuid4().hex[:12]
        saved_name = f"{doc_id}{ext}"
        file_path = os.path.join(self._resumes_dir, saved_name)
        with open(file_path, "wb") as f:
            f.write(file_bytes)

        text = self._extract_text(file_path, ext)
        text = _clean_text(text)
        truncated = False
        if len(text) > RESUME_MAX_TEXT_LENGTH:
            text = text[:RESUME_MAX_TEXT_LENGTH]
            truncated = True

        self._cleanup_old_files()

        return ResumeDocument(
            id=doc_id,
            filename=filename,
            format=ext,
            text=text,
            file_path=file_path,
            size=len(file_bytes),
            truncated=truncated,
        )

    def _cleanup_old_files(self) -> None:
        """保留最新的 MAX_RESUME_FILES 份简历文件，防止磁盘无限增长。"""
        try:
            files = [
                os.path.join(self._resumes_dir, f)
                for f in os.listdir(self._resumes_dir)
                if os.path.isfile(os.path.join(self._resumes_dir, f))
            ]
            if len(files) <= MAX_RESUME_FILES:
                return
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for old in files[MAX_RESUME_FILES:]:
                try:
                    os.remove(old)
                    logger.debug("cleaned up old resume file: %s", old)
                except Exception as exc:
                    logger.warning("remove old resume file failed: %s", exc)
        except Exception as exc:
            logger.warning("cleanup old resume files failed: %s", exc)

    @staticmethod
    def _validate_file_header(file_bytes: bytes, ext: str) -> None:
        """根据 magic bytes 校验文件内容是否与扩展名一致。"""
        if ext == ".pdf":
            if not file_bytes.startswith(b"%PDF"):
                raise ValueError("文件内容不是有效的 PDF")
        elif ext in (".docx", ".doc"):
            if file_bytes.startswith(b"PK"):
                if ext == ".doc":
                    raise ValueError(".doc 文件实际为 .docx，请修改扩展名")
            elif file_bytes.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
                if ext == ".docx":
                    raise ValueError(".docx 文件实际为 .doc，请修改扩展名")
            else:
                raise ValueError("文件内容不是有效的 Word 文档")
        elif ext in IMAGE_EXTENSIONS:
            if ext == ".png" and not file_bytes.startswith(b"\x89PNG"):
                raise ValueError("文件内容不是有效的 PNG 图片")
            if ext in (".jpg", ".jpeg") and not file_bytes.startswith(b"\xff\xd8"):
                raise ValueError("文件内容不是有效的 JPEG 图片")

    def _extract_text(self, file_path: str, ext: str) -> str:
        if ext == ".pdf":
            return self._extract_pdf(file_path)
        if ext in (".docx", ".doc"):
            return self._extract_docx(file_path)
        if ext in (".txt", ".md"):
            return self._extract_text_file(file_path)
        if ext in IMAGE_EXTENSIONS:
            return self._extract_image(file_path)
        return ""

    @staticmethod
    def _extract_pdf(file_path: str) -> str:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("需要安装 pypdf 库才能处理 PDF 文件") from exc
        try:
            reader = PdfReader(file_path)
            parts = []
            for page in reader.pages:
                page_text = page.extract_text() or ""
                if page_text.strip():
                    parts.append(page_text)
            text = "\n".join(parts)
            # 文本过短通常意味着是扫描件，走 OCR fallback
            if len(text.strip()) < 100:
                logger.info("pdf text too short, try ocr fallback")
                ocr_text = ResumeParser._extract_pdf_with_ocr(file_path)
                if ocr_text:
                    return ocr_text
            return text
        except Exception as exc:
            logger.warning("extract pdf failed: %s", exc)
            return ""

    @staticmethod
    def _extract_pdf_with_ocr(file_path: str) -> str:
        """扫描版 PDF fallback：pdf2image + OCR（可选依赖）。"""
        try:
            from pdf2image import convert_from_path

            from services.ocr_service import extract_text_from_image
        except ImportError:
            logger.warning("pdf2image or ocr_service not available, skip pdf ocr fallback")
            return ""
        try:
            images = convert_from_path(
                file_path,
                dpi=200,
                fmt="png",
                first_page=1,
                last_page=MAX_OCR_PAGES,
            )
            parts = []
            for idx, img in enumerate(images):
                import tempfile

                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    img_path = tmp.name
                try:
                    img.save(img_path, "PNG")
                    page_text = extract_text_from_image(img_path)
                    if page_text:
                        parts.append(page_text)
                finally:
                    try:
                        os.remove(img_path)
                    except Exception:
                        pass
            return "\n".join(parts)
        except Exception as exc:
            logger.warning("pdf ocr fallback failed: %s", exc)
            return ""

    @staticmethod
    def _extract_docx(file_path: str) -> str:
        try:
            from docx import Document
        except ImportError as exc:
            raise RuntimeError("需要安装 python-docx 库才能处理 Word 文档") from exc
        try:
            doc = Document(file_path)
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n".join(paragraphs)
        except Exception as exc:
            logger.warning("extract docx failed: %s", exc)
            return ""

    @staticmethod
    def _extract_text_file(file_path: str) -> str:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="gbk", errors="ignore") as f:
                return f.read()
        except Exception as exc:
            logger.warning("extract text file failed: %s", exc)
            return ""

    @staticmethod
    def _extract_image(file_path: str) -> str:
        """图片 OCR fallback，复用 services.ocr_service。"""
        try:
            from services.ocr_service import extract_text_from_image
        except ImportError:
            logger.warning("ocr_service not available")
            return ""
        try:
            text = extract_text_from_image(file_path)
            return text or ""
        except Exception as exc:
            logger.warning("ocr image failed: %s", exc)
            return ""


def _clean_text(text: str) -> str:
    """基础清洗：合并断行、规范空格、移除页眉页脚常见标记。"""
    if not text:
        return ""
    text = re.sub(r"\n\s*\n+", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n(\S)\n", r"\1 ", text)
    return text.strip()
