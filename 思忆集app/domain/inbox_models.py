"""统一收件箱条目模型。

所有收件来源（浏览器桥、桌面手动、助手指令、悬浮助手）
统一使用 InboxEntry 作为标准模型，确保同一条输入从不同来源
进入后产生一致的字段结构。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

# ── 标准来源常量 ──────────────────────────────────────────
SOURCE_MANUAL = "manual"
SOURCE_BROWSER = "browser_extension"
SOURCE_BROWSER_LEGACY = "browser"  # 旧版浏览器扩展来源，标准化时映射到 browser_extension
SOURCE_BATCH = "batch"
SOURCE_ASSISTANT = "assistant"
SOURCE_FLOATING = "floating"
SOURCE_SCHEDULED = "scheduled"
SOURCE_ANOMALY = "anomaly"
SOURCE_TASK_RESULT = "task_result"
SOURCE_PROACTIVE = "proactive"
SOURCE_PLUGIN = "plugin"

# ── 标准状态常量 ──────────────────────────────────────────
STATUS_NEW = "new"
STATUS_READ = "read"
STATUS_ORGANIZED = "organized"
STATUS_ARCHIVED = "archived"
STATUS_DISMISSED = "dismissed"

# ── 三层流转状态 ──────────────────────────────────────────
LAYER_INBOX = "inbox"
LAYER_DAILY = "daily"
LAYER_INSIGHTS = "insights"
ALL_LAYERS = frozenset({LAYER_INBOX, LAYER_DAILY, LAYER_INSIGHTS})

ALL_SOURCES = frozenset({
    SOURCE_MANUAL, SOURCE_BROWSER, SOURCE_BROWSER_LEGACY, SOURCE_BATCH,
    SOURCE_ASSISTANT, SOURCE_FLOATING, SOURCE_SCHEDULED, SOURCE_ANOMALY,
    SOURCE_TASK_RESULT, SOURCE_PROACTIVE, SOURCE_PLUGIN,
})

ALL_STATUSES = frozenset({
    STATUS_NEW, STATUS_READ, STATUS_ORGANIZED,
    STATUS_ARCHIVED, STATUS_DISMISSED,
})

# ── 标准元数据键 ──────────────────────────────────────────
META_URL = "url"
META_CONTENT_KIND = "content_kind"
META_SELECTION_LENGTH = "selection_length"
META_INBOX_FILE_PATH = "inbox_file_path"


@dataclass
class InboxEntry:
    """统一收件箱条目。

    所有收件来源必须产生此结构的条目。
    可选字段（title／tags／metadata）使用空值默认值，
    而非 None，确保 JSON 序列化一致性。
    """

    # 核心字段
    title: str = ""
    content: str = ""
    source: str = SOURCE_MANUAL
    status: str = STATUS_NEW
    layer: str = LAYER_INBOX
    tags: list[str] = field(default_factory=list)

    # 元数据（来源特定扩展信息）
    metadata: dict[str, Any] = field(default_factory=dict)

    # 时间戳（由仓储层自动填充）
    created_at: str = ""
    updated_at: str = ""
    daily_at: str = ""      # 进入 daily 层的时间
    insights_at: str = ""   # 进入 insights 层的时间

    # ── 便捷属性 ──

    @property
    def source_url(self) -> str:
        """从 metadata 中提取来源 URL，无 URL 时返回空字符串。"""
        return str(self.metadata.get(META_URL) or "").strip()

    @property
    def content_kind(self) -> str:
        """从 metadata 中提取内容类型（article/video_page/document_page/news_page 等）。"""
        return str(self.metadata.get(META_CONTENT_KIND) or "").strip()

    @property
    def inbox_file_path(self) -> str:
        """从 metadata 中提取收件文件路径。"""
        return str(self.metadata.get(META_INBOX_FILE_PATH) or "").strip()

    # ── 序列化 ──

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（供仓储层写入 JSON）。"""
        return {
            "title": str(self.title or "").strip(),
            "content": str(self.content or "").strip(),
            "source": self.source if self.source in ALL_SOURCES else SOURCE_MANUAL,
            "status": self.status if self.status in ALL_STATUSES else STATUS_NEW,
            "layer": self.layer if self.layer in ALL_LAYERS else LAYER_INBOX,
            "tags": [str(t).strip() for t in (self.tags or []) if str(t).strip()],
            "metadata": dict(self.metadata or {}),
            "created_at": str(self.created_at or "").strip(),
            "updated_at": str(self.updated_at or "").strip(),
            "daily_at": str(self.daily_at or "").strip(),
            "insights_at": str(self.insights_at or "").strip(),
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> InboxEntry:
        """从仓储层字典反序列化。"""
        return InboxEntry(
            title=str(data.get("title") or "").strip(),
            content=str(data.get("content") or "").strip(),
            source=str(data.get("source") or SOURCE_MANUAL).strip(),
            status=str(data.get("status") or STATUS_NEW).strip(),
            layer=str(data.get("layer") or LAYER_INBOX).strip(),
            tags=list(data.get("tags") or []),
            metadata=dict(data.get("metadata") or {}),
            created_at=str(data.get("created_at") or "").strip(),
            updated_at=str(data.get("updated_at") or "").strip(),
            daily_at=str(data.get("daily_at") or "").strip(),
            insights_at=str(data.get("insights_at") or "").strip(),
        )

    @staticmethod
    def normalize(raw: dict[str, Any]) -> dict[str, Any]:
        """将任意来源的原始条目字典标准化为统一字段结构。

        确保同一条输入从浏览器、桌面或助手进入后字段一致：
        - source 被映射到标准来源值
        - status 被映射到标准状态值
        - title 和 content 为空时设为空字符串
        - metadata 始终是 dict
        """
        source = str(raw.get("source") or SOURCE_MANUAL).strip().lower()
        if source not in ALL_SOURCES:
            source = SOURCE_MANUAL
        elif source == SOURCE_BROWSER_LEGACY:
            source = SOURCE_BROWSER
        status = str(raw.get("status") or STATUS_NEW).strip().lower()
        if status not in ALL_STATUSES:
            status = STATUS_NEW
        layer = str(raw.get("layer") or LAYER_INBOX).strip().lower()
        if layer not in ALL_LAYERS:
            layer = LAYER_INBOX
        return {
            "title": str(raw.get("title") or "").strip(),
            "content": str(raw.get("content") or "").strip(),
            "source": source,
            "status": status,
            "layer": layer,
            "tags": [str(t).strip() for t in (raw.get("tags") or []) if str(t).strip()],
            "metadata": dict(raw.get("metadata") or {}),
            "created_at": str(raw.get("created_at") or "").strip(),
            "updated_at": str(raw.get("updated_at") or "").strip(),
            "daily_at": str(raw.get("daily_at") or "").strip(),
            "insights_at": str(raw.get("insights_at") or "").strip(),
        }
