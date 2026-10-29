# -*- coding: utf-8 -*-
"""知识库 MD 文件仓储 — 内容层（事实源）的文件读写。

职责：
- 目录约束：所有读写都限定在 ``<data_base_dir>/knowledge/`` 根目录内，杜绝路径穿越。
- 原子写：temp file + ``os.replace``（PID+UUID 唯一临时名），os.replace 失败时
  指数退避重试，规避 Windows Defender 扫描期的 PermissionError（项目硬约束）。
- frontmatter（YAML 头）渲染与解析，供机器回灌。
- 时间路径辅助：``YYYY/YYYY-MM/YYYY-MM-DD_类别.md``。

目录结构（第一维度按时间、第二维度按主题）::

    knowledge/
    ├── 00_索引/                     # 全局导航
    ├── YYYY/YYYY-MM/               # 时间轴：当日文件
    │   ├── YYYY-MM-DD_资讯摘要.md
    │   ├── YYYY-MM-DD_对话.md
    │   ├── YYYY-MM-DD_记忆.md
    │   └── YYYY-MM-DD_知识卡片.md
    ├── 主题/<主题>/                  # 主题轴：MOC + 原子卡片
    │   ├── MOC.md
    │   └── 卡片-<slug>.md
    └── 图谱导出/                    # 图谱快照
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from typing import Any

logger = logging.getLogger("siyi.knowledge.md_store")

# 知识库根目录名（相对 data_base_dir）
KNOWLEDGE_ROOT = "knowledge"

# Windows 文件名非法字符
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def slugify(text: str, max_len: int = 24) -> str:
    """把标题转成安全的文件名片段（去掉非法字符、压缩空白、截断）。"""
    s = _INVALID_FILENAME_CHARS.sub(" ", text or "")
    s = re.sub(r"\s+", " ", s).strip().replace(" ", "-")
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:max_len] if s else "未命名"


def render_frontmatter(meta: dict[str, Any]) -> str:
    """把元数据渲染成 YAML 风格的 frontmatter 块（含首尾分隔线）。"""
    lines = ["---"]
    for key, value in (meta or {}).items():
        if isinstance(value, list):
            items = ", ".join(str(v) for v in value)
            lines.append(f"{key}: [{items}]")
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """解析 MD 文件 frontmatter：返回 ``(meta_dict, body)``。

    无 frontmatter 或格式非法时返回 ``({}, content)``。
    """
    text = content or ""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    header = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    meta: dict[str, Any] = {}
    for line in header.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1]
            meta[key] = [v.strip() for v in inner.split(",") if v.strip()]
        else:
            meta[key] = value
    return meta, body


def date_rel_path(year: int, month: int, day: int, kind: str) -> str:
    """生成时间轴相对路径：``YYYY/YYYY-MM/YYYY-MM-DD_<kind>.md``。"""
    return f"{year:04d}/{year:04d}-{month:02d}/{year:04d}-{month:02d}-{day:02d}_{kind}.md"


def extract_date(s: str) -> tuple[int, int, int] | None:
    """从 ``YYYY-MM-DD`` 或 ISO 时间串里解析出 (y, m, d)；失败返回 None。"""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


class MDKnowledgeStore:
    """知识库 MD 文件仓储。

    Args:
        base_dir: 数据根目录（即 ``local_data_store.base_dir``），
            知识库实际位于 ``<base_dir>/knowledge/``。
    """

    def __init__(self, base_dir: str) -> None:
        # normpath 统一分隔符：base_dir 可能以正斜杠传入（如 SIYI_DATA_DIR），
        # 而 resolve() 中 normpath 后的 target 必为系统分隔符，两者不同形则前缀比较恒失败。
        self.root = os.path.normpath(os.path.join(base_dir, KNOWLEDGE_ROOT))
        os.makedirs(self.root, exist_ok=True)

    # ── 路径安全 ────────────────────────────────────────────────────

    def resolve(self, rel: str) -> str:
        """把相对路径解析为绝对路径，并强制限定在知识库根目录内。"""
        rel = (rel or "").replace("\\", "/").lstrip("/")
        root = os.path.normpath(self.root)
        target = os.path.normpath(os.path.join(root, rel))
        if not (target == root or target.startswith(root + os.sep)):
            raise ValueError(f"MD 路径越界: {rel}")
        return target

    # ── 读写 ────────────────────────────────────────────────────────

    @staticmethod
    def _write_atomic(path: str, content: str) -> None:
        """原子写：唯一临时名（PID+UUID）→ 写入 → os.replace（带指数退避）。"""
        tmp = f"{path}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(content)
        delay = 0.05
        for _ in range(6):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(delay)
                delay *= 2
        # 最后一次兜底：仍失败则抛出，交由调用方处理
        try:
            os.replace(tmp, path)
        except PermissionError:
            logger.error("MD 原子写失败（持续 PermissionError）: %s", path)
            raise

    def write(self, rel: str, content: str) -> str:
        """写一个 MD 文件（自动建父目录、原子落盘）。返回绝对路径。"""
        path = self.resolve(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._write_atomic(path, content)
        return path

    def read(self, rel: str) -> str | None:
        path = self.resolve(rel)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None

    def exists(self, rel: str) -> bool:
        return os.path.isfile(self.resolve(rel))

    def list_files(self) -> list[str]:
        """递归列出知识库内全部相对路径（正斜杠）。"""
        result: list[str] = []
        for dirpath, _, filenames in os.walk(self.root):
            for name in filenames:
                if not name.endswith(".md"):
                    continue
                full = os.path.join(dirpath, name)
                result.append(os.path.relpath(full, self.root).replace("\\", "/"))
        result.sort()
        return result


__all__ = [
    "MDKnowledgeStore",
    "render_frontmatter",
    "parse_frontmatter",
    "slugify",
    "date_rel_path",
    "extract_date",
    "KNOWLEDGE_ROOT",
]
