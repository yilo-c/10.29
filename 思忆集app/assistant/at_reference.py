"""
@ 引用系统核心引擎

管理 @ 引用的注册、搜索、内容解析和引用链记录。

引用类型:
  - memory: 记忆片段（从 memory_index 检索）
  - file: 本地文件
  - device: 设备信息
  - time: 时间范围
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("siyi.at_reference")

# ── 数据模型 ──────────────────────────────────────────────────────

REFERENCE_CATEGORIES = {
    "memory": "[记忆]",
    "file": "[文件]",
    "device": "[设备]",
    "time": "[时间]",
}


@dataclass
class AtReference:
    """一条 @ 引用元数据。"""
    ref_type: str               # memory | file | device | time
    ref_id: str                 # 唯一标识
    display_name: str           # 菜单显示名
    category_label: str         # 分类标签 e.g. [记忆]
    resolved_content: str = ""  # 解析后的实际内容
    source_info: str = ""       # 来源说明（如文件路径、设备名）
    selected_at: str = ""       # 选中时间


@dataclass
class ReferenceChain:
    """一次对话中使用的 @ 引用链。"""
    message_id: str
    references: list[dict[str, str]] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "references": list(self.references),
            "created_at": self.created_at,
        }


# ── 引擎 ──────────────────────────────────────────────────────────

class AtReferenceEngine:
    """@ 引用引擎：搜索、解析、追踪引用链。"""

    _MAX_FILE_SIZE = 512 * 1024  # 512KB
    _MAX_RESOLVED_LENGTH = 4000
    _MAX_SEARCH_RESULTS = 20

    def __init__(
        self,
        *,
        memory_index_service=None,
        data_store=None,
        settings_repository=None,
    ):
        self._memory_service = memory_index_service
        self._data_store = data_store
        self._settings_repo = settings_repository
        self._search_providers: dict[str, Callable] = {}

        # 注册默认搜索 provider
        self._register_default_providers()

    def _register_default_providers(self):
        self.register_search_provider("memory", self._search_memories)
        self.register_search_provider("file", self._search_files)
        self.register_search_provider("device", self._search_devices)
        self.register_search_provider("time", self._search_time_ranges)

    def register_search_provider(self, ref_type: str, provider: Callable):
        """注册自定义搜索 provider。"""
        self._search_providers[ref_type] = provider

    # ── 搜索 ─────────────────────────────────────────────────────

    def search(self, query: str, category: str | None = None) -> list[AtReference]:
        """搜索所有类型或指定类型的引用候选。"""
        if not query:
            return self._get_recent_references(category)

        results: list[AtReference] = []
        if category:
            provider = self._search_providers.get(category)
            if provider:
                results.extend(provider(query))
        else:
            for ref_type, provider in self._search_providers.items():
                try:
                    results.extend(provider(query))
                except Exception as exc:
                    logger.warning("搜索 %s 失败", ref_type, exc_info=True)

        # 按相关性排序: 优先显示 memory, 然后 file/device/time
        type_priority = {"memory": 0, "file": 1, "device": 2, "time": 3}
        results.sort(key=lambda r: type_priority.get(r.ref_type, 99))
        return results[:self._MAX_SEARCH_RESULTS]

    def search_categorized(self, query: str) -> dict[str, list[AtReference]]:
        """按分类返回搜索结果。"""
        categorized: dict[str, list[AtReference]] = {}
        for ref_type in self._search_providers:
            categorized[ref_type] = []

        for ref in self.search(query):
            if ref.ref_type in categorized:
                categorized[ref.ref_type].append(ref)
        return categorized

    def _get_recent_references(self, category: str | None = None) -> list[AtReference]:
        """没有输入时返回最近引用项。"""
        results: list[AtReference] = []
        if not category or category == "memory":
            results.extend(self._get_recent_memories())
        if not category or category == "device":
            results.extend(self._get_recent_devices())
        return results[:self._MAX_SEARCH_RESULTS]

    # ── 各个提供者 ─────────────────────────────────────────────

    def _search_memories(self, query: str) -> list[AtReference]:
        """搜索记忆片段。"""
        if self._memory_service is None:
            return []
        try:
            result = self._memory_service.recall(query, top_k=8)
            items = list(result.get("items") or [])[:8]
            refs = []
            for item in items:
                title = str(item.get("title") or "").strip()
                source_type = str(item.get("source_type") or "").strip()
                snippet = str(item.get("snippet") or "")[:120]
                refs.append(AtReference(
                    ref_type="memory",
                    ref_id=str(item.get("entry_id") or item.get("source_id") or title),
                    display_name=f"{title} · {source_type}",
                    category_label=REFERENCE_CATEGORIES["memory"],
                    source_info=f"来源: {source_type}",
                    resolved_content=f"[记忆片段: {title}]\n{snippet}",
                ))
            return refs
        except Exception as exc:
            logger.warning("记忆搜索失败", exc_info=True)
            return []

    def _get_recent_memories(self) -> list[AtReference]:
        """最近记忆作为默认提示。"""
        if self._memory_service is None:
            return []
        try:
            result = self._memory_service.recall("", top_k=5)
            items = list(result.get("items") or [])[:5]
            refs = []
            for item in items:
                title = str(item.get("title") or "").strip()
                source_type = str(item.get("source_type") or "").strip()
                refs.append(AtReference(
                    ref_type="memory",
                    ref_id=str(item.get("entry_id") or item.get("source_id") or title),
                    display_name=f"{title} · {source_type}",
                    category_label=REFERENCE_CATEGORIES["memory"],
                ))
            return refs
        except Exception:
            logger.warning("_search_memories failed", exc_info=True)
            return []

    def _search_files(self, query: str) -> list[AtReference]:
        """搜索本地文件。"""
        search_dirs = self._get_searchable_directories()
        matches: list[tuple[Path, int]] = []

        query_lower = query.lower()
        for directory in search_dirs:
            if not directory.is_dir():
                continue
            try:
                for entry in directory.iterdir():
                    if entry.is_file() and entry.suffix.lower() in {
                        ".py", ".md", ".txt", ".json", ".yaml", ".yml",
                        ".toml", ".cfg", ".ini", ".csv", ".html", ".css",
                    }:
                        name_lower = entry.stem.lower()
                        if query_lower in name_lower or query_lower in entry.name.lower():
                            matches.append((entry, name_lower.count(query_lower)))
            except (PermissionError, OSError):
                continue

        matches.sort(key=lambda x: (-x[1], x[0].name))
        refs = []
        for file_path, _ in matches[:8]:
            try:
                size_kb = file_path.stat().st_size / 1024
            except OSError:
                size_kb = 0
            refs.append(AtReference(
                ref_type="file",
                ref_id=str(file_path),
                display_name=file_path.name,
                category_label=REFERENCE_CATEGORIES["file"],
                source_info=f"{file_path.parent.name}/ ({size_kb:.0f}KB)",
            ))
        return refs

    def _get_searchable_directories(self) -> list[Path]:
        """获取可搜索的目录列表。"""
        dirs = []
        # 项目目录
        base = Path(__file__).resolve().parent
        dirs.append(base)
        data_dir = base / "data"
        if data_dir.is_dir():
            dirs.append(data_dir)
        # 用户 home 目录下的常用目录
        home = Path.home()
        for sub in ("Documents", "Desktop", "notes"):
            p = home / sub
            if p.is_dir():
                dirs.append(p)
        # 配置中的自定义目录
        if self._settings_repo is not None:
            try:
                extra = self._settings_repo.get_setting("at_reference_search_dirs", [])
                if isinstance(extra, list):
                    dirs.extend(Path(d).resolve() for d in extra if d)
            except Exception:
                logger.warning("加载 @ 引用搜索目录配置失败")
        return dirs

    def _search_devices(self, query: str) -> list[AtReference]:
        """搜索设备。"""
        devices = self._get_devices()
        query_lower = query.lower()
        refs = []
        for device in devices:
            name = str(device.get("name") or "").strip()
            device_type = str(device.get("device_type") or device.get("type") or "").strip()
            if query_lower in name.lower():
                refs.append(AtReference(
                    ref_type="device",
                    ref_id=str(device.get("device_id") or name),
                    display_name=name,
                    category_label=REFERENCE_CATEGORIES["device"],
                    source_info=f"设备类型: {device_type}" if device_type else "",
                ))
        return refs

    def _get_recent_devices(self) -> list[AtReference]:
        """获取最近的设备列表。"""
        devices = self._get_devices()
        refs = []
        for device in devices[:5]:
            name = str(device.get("name") or "").strip()
            device_type = str(device.get("device_type") or device.get("type") or "").strip()
            refs.append(AtReference(
                ref_type="device",
                ref_id=str(device.get("device_id") or name),
                display_name=name,
                category_label=REFERENCE_CATEGORIES["device"],
                source_info=f"设备类型: {device_type}" if device_type else "",
            ))
        return refs

    def _get_devices(self) -> list[dict]:
        """获取设备列表。"""
        if self._data_store is None:
            return []
        try:
            devices = self._data_store.load_json("device_registry", [])
            if isinstance(devices, list):
                return devices
        except Exception:
            logger.warning("加载设备注册表失败")
        return []

    def _search_time_ranges(self, query: str) -> list[AtReference]:
        """搜索时间范围表达式。"""
        now = datetime.now()
        presets = [
            ("今天", now.replace(hour=0, minute=0, second=0, microsecond=0), now),
            ("昨天", now - timedelta(days=1), now - timedelta(days=1)),
            ("本周", now - timedelta(days=now.weekday()), now),
            ("本月", now.replace(day=1), now),
            ("最近7天", now - timedelta(days=7), now),
            ("最近30天", now - timedelta(days=30), now),
            ("最近90天", now - timedelta(days=90), now),
        ]
        query_lower = query.lower()
        refs = []
        for label, start, end in presets:
            if query_lower in label.lower() or query_lower in "天 周 月 年 近":
                date_str = f"{start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}"
                refs.append(AtReference(
                    ref_type="time",
                    ref_id=label,
                    display_name=label,
                    category_label=REFERENCE_CATEGORIES["time"],
                    source_info=date_str,
                ))
        return refs

    # ── 内容解析 ─────────────────────────────────────────────────

    def resolve_content(self, ref: AtReference) -> str:
        """将引用解析为实际内容。"""
        try:
            if ref.ref_type == "memory":
                return self._resolve_memory(ref)
            if ref.ref_type == "file":
                return self._resolve_file(ref)
            if ref.ref_type == "device":
                return self._resolve_device(ref)
            if ref.ref_type == "time":
                return self._resolve_time_range(ref)
        except Exception as exc:
            logger.warning("引用解析失败: %s", ref.ref_type, exc_info=True)
            return f"[@{ref.display_name} 解析失败: {exc}]"
        return ""

    def _resolve_memory(self, ref: AtReference) -> str:
        """解析记忆片段完整内容。"""
        if ref.resolved_content:
            return self._truncate_content(ref.resolved_content)
        if self._memory_service is None:
            return f"[记忆: {ref.display_name}]"
        try:
            result = self._memory_service.recall(ref.display_name, top_k=1)
            items = list(result.get("items") or [])[:1]
            if items:
                item = items[0]
                title = str(item.get("title") or "").strip()
                snippet = str(item.get("snippet") or item.get("content") or "").strip()
                content = (
                    f"[记忆片段: {title}]\n"
                    f"来源: {item.get('source_type') or 'unknown'}\n"
                    f"内容: {snippet}"
                )
                return self._truncate_content(content)
        except Exception:
            logger.warning("解析记忆引用内容失败: %s", ref.ref_id)
        return f"[记忆: {ref.display_name}]"

    def _resolve_file(self, ref: AtReference) -> str:
        """读取文件内容。"""
        file_path = Path(ref.ref_id)
        if not file_path.is_file():
            return f"[文件: {ref.display_name} (文件不存在)]"
        try:
            if file_path.stat().st_size > self._MAX_FILE_SIZE:
                return f"[文件: {ref.display_name} (文件过大，仅包含路径)]\n路径: {file_path}"
            content = file_path.read_text(encoding="utf-8", errors="replace")
            if not content.strip():
                return f"[文件: {ref.display_name} (空文件)]"
            return self._truncate_content(
                f"[文件: {ref.display_name}]\n路径: {file_path}\n---\n{content}"
            )
        except (OSError, UnicodeDecodeError) as exc:
            return f"[文件: {ref.display_name} (读取失败: {exc})]"

    def _resolve_device(self, ref: AtReference) -> str:
        """解析设备信息。"""
        devices = self._get_devices()
        for device in devices:
            name = str(device.get("name") or "").strip()
            did = str(device.get("device_id") or "").strip()
            if name == ref.display_name or did == ref.ref_id:
                parts = [f"[设备信息: {name}]"]
                for key in ("device_type", "os", "status", "last_seen"):
                    val = str(device.get(key) or "").strip()
                    if val:
                        parts.append(f"{key}: {val}")
                return self._truncate_content("\n".join(parts))
        return f"[设备: {ref.display_name}]"

    def _resolve_time_range(self, ref: AtReference) -> str:
        """解析时间范围。"""
        now = datetime.now()
        ranges = {
            "今天": (now.replace(hour=0, minute=0, second=0, microsecond=0), now),
            "昨天": (now - timedelta(days=1), now - timedelta(days=1)),
            "本周": (now - timedelta(days=now.weekday()), now),
            "本月": (now.replace(day=1), now),
            "最近7天": (now - timedelta(days=7), now),
            "最近30天": (now - timedelta(days=30), now),
            "最近90天": (now - timedelta(days=90), now),
        }
        start, end = ranges.get(ref.display_name, (now, now))
        return f"[时间范围: {ref.display_name}]\n{start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}"

    # ── 格式化 ───────────────────────────────────────────────────

    def format_for_prompt(self, references: list[AtReference]) -> str:
        """将引用列表格式化为 LLM 提示上下文。"""
        if not references:
            return ""
        parts = ["[用户 @ 引用上下文]"]
        for ref in references:
            content = self.resolve_content(ref)
            if content:
                parts.append(content)
                parts.append("")
        return "\n".join(parts)

    def format_reference_summary(self, references: list[AtReference]) -> list[str]:
        """生成引用摘要标签（用于 UI 显示）。"""
        if not references:
            return []
        return [
            f"{ref.category_label}{ref.display_name}"
            for ref in references
        ]

    # ── 引用链持久化 ─────────────────────────────────────────────

    def _get_chains_path(self) -> Path | None:
        """获取引用链文件的存储路径。"""
        if self._data_store is None:
            return None
        try:
            base = Path(self._data_store.base_dir)
            return base / "at_reference_chains.json"
        except Exception:
            logger.warning("_chains_file_path failed", exc_info=True)
            return None

    def save_reference_chain(self, message_id: str, references: list[AtReference]) -> None:
        """将引用链存入对话记录。"""
        if not references:
            return
        chains_path = self._get_chains_path()
        if chains_path is None:
            return
        chain = ReferenceChain(
            message_id=message_id,
            references=[
                {
                    "ref_type": ref.ref_type,
                    "ref_id": ref.ref_id,
                    "display_name": ref.display_name,
                    "category_label": ref.category_label,
                }
                for ref in references
            ],
            created_at=datetime.now().isoformat(),
        )
        try:
            chains = []
            if chains_path.is_file():
                chains = json.loads(chains_path.read_text(encoding="utf-8"))
            if not isinstance(chains, list):
                chains = []
            chains.append(chain.to_dict())
            chains = chains[-200:]  # 保留最近 200 条
            chains_path.write_text(json.dumps(chains, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("保存引用链失败", exc_info=True)

    def load_reference_chains(self, limit: int = 50) -> list[dict]:
        """加载最近的引用链。"""
        chains_path = self._get_chains_path()
        if chains_path is None:
            return []
        try:
            if chains_path.is_file():
                chains = json.loads(chains_path.read_text(encoding="utf-8"))
                if isinstance(chains, list):
                    return chains[-limit:]
        except Exception:
            logger.warning("加载引用链失败")
        return []

    def get_references_for_message(self, message_id: str) -> list[dict]:
        """获取指定消息的引用链。"""
        chains = self.load_reference_chains(200)
        for chain in chains:
            if chain.get("message_id") == message_id:
                return list(chain.get("references") or [])
        return []

    # ── 工具方法 ─────────────────────────────────────────────────

    @staticmethod
    def extract_at_references(text: str) -> list[str]:
        """从文本中提取 @ 引用标记。"""
        # Python \w 包含 CJK，所以需要明确限制
        refs = re.findall(r"@([a-zA-Z0-9_]+|[一-龥]+)", text)
        return refs

    # ── @ 引用标记格式支持 ──────────────────────────────────────────

    MARK_PATTERN = re.compile(r"\[@([a-zA-Z0-9_]+):([^]]+?):([^]]+?)\]")

    @staticmethod
    def format_mark(ref_type: str, ref_id: str, display_name: str) -> str:
        """将引用格式化为可嵌入文本的标记 [@type:id:display_name]。"""
        # 确保 display_name 中不包含 ] 以避免解析歧义
        safe_name = display_name.replace("]", " ")
        return f"[@{ref_type}:{ref_id}:{safe_name}]"

    @staticmethod
    def parse_mark(text: str) -> dict | None:
        """从文本开头解析单个 [@type:id:name] 标记。"""
        m = AtReferenceEngine.MARK_PATTERN.match(text)
        if m:
            return {
                "ref_type": m.group(1),
                "ref_id": m.group(2),
                "display_name": m.group(3),
            }
        return None

    @classmethod
    def extract_marks_from_text(cls, text: str) -> list[dict]:
        """从文本中提取所有 [@type:id:name] 标记。"""
        return [
            {
                "ref_type": m.group(1),
                "ref_id": m.group(2),
                "display_name": m.group(3),
            }
            for m in cls.MARK_PATTERN.finditer(text)
        ]

    def _resolve_mark_to_ref(self, ref_type: str, ref_id: str, display_name: str) -> AtReference | None:
        """将解析自文本标记的 [@type:id:name] 转换回 AtReference 对象。"""
        try:
            category_label = REFERENCE_CATEGORIES.get(ref_type, f"[{ref_type}]")
            return AtReference(
                ref_type=ref_type,
                ref_id=ref_id,
                display_name=display_name,
                category_label=category_label,
            )
        except Exception as exc:
            logger.warning("标记转 AtReference 失败", exc_info=True)
            return None

    @staticmethod
    def _truncate_content(content: str, max_len: int | None = None) -> str:
        max_len = max_len or AtReferenceEngine._MAX_RESOLVED_LENGTH
        if len(content) <= max_len:
            return content
        return content[:max_len] + f"\n... (内容已截断, 共 {len(content)} 字符)"
