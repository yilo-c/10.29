"""数据备份与恢复服务 — 防止数据丢失。

功能：
- 完整备份：备份所有数据（笔记、任务、设置、记忆等）
- 增量备份：只备份自上次备份以来的变更
- 定时备份：按 cron 表达式自动备份
- 备份恢复：从备份文件恢复数据
- 备份管理：列出、删除旧备份
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("siyi.services.backup")

# 七维-数C2/安C6（2026-08-29）：settings 含 API key/token 明文，备份产物
# （gzip 仅压缩非加密）即密钥泄露面。进备份前递归剥离敏感键（值置空串
# 占位、保持结构），键名计入日志；恢复后需重新录入凭据。
_SENSITIVE_KEY_PATTERN = re.compile(r"(?i)(api[_-]?key|apikey|token|secret|password|passwd|credential|private[_-]?key)")


def _strip_sensitive_settings(value: Any, _path: str = "") -> tuple[dict[str, Any] | list[Any] | Any, list[str]]:
    """递归剥离 value 中的敏感键（值置空串占位，保持结构）。

    返回 (脱敏副本, 被剥离键的点路径列表)。深拷贝语义：不改写原对象。
    命中键名（忽略大小写）的整个值以空串占位——宁可过度脱敏（如
    tokenizer 误伤）也不向备份产物泄露任何凭据。
    """
    stripped: list[str] = []

    def _walk(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                key = str(k)
                child_path = f"{path}.{key}" if path else key
                if _SENSITIVE_KEY_PATTERN.search(key):
                    out[k] = ""
                    stripped.append(child_path)
                else:
                    out[k] = _walk(v, child_path)
            return out
        if isinstance(node, list):
            return [_walk(item, f"{path}[{i}]") for i, item in enumerate(node)]
        return node

    redacted = _walk(value, "")
    return redacted, stripped


# 七维-数C1（2026-08-29）：备份范围对齐——原备份集仅 notes/tasks/settings/
# memories/assistant_memory_index/assistant_decisions/ai_cache（6 键+1 目录），
# 与 backup-boundary.md 声明不符（P0①）。扩展域分三类：
# 1) 统一存储键域：经 data_store.load_json/save_json 读写（data_store=None 时
#    回退 <data_dir>/<key>.json 旧文件形态，仅供测试/旧机迁移）；
# 2) 文件域：knowledge_graph.json（knowledge/graph_store.py 文件存储，不经统一存储）；
# 3) 目录域：narratives/（narrative_store 单索引）与 chat_sessions/（chat 仓储
#    消息分片，repositories/_chat_session.py）。memories/ 目录由 include_memories
#    既有逻辑覆盖，不在此列。
_EXTENDED_LIST_DOMAINS: tuple[str, ...] = (
    "inbox_entries",
    "words",
    "wordbooks",
    "translations",
    "structured_memory_entries",
    "audit_trail",
    "cron_jobs",
    "chat_sessions",  # 分片索引（list）；消息分片本体见 _EXTENDED_DIR_DOMAINS
)

# blob 键名 -> data_dir 下相对目录名
_EXTENDED_DIR_DOMAINS: tuple[tuple[str, str], ...] = (
    ("narratives", "narratives"),
    ("chat_session_shards", "chat_sessions"),
)


class BackupType(str, Enum):
    """备份类型"""

    FULL = "full"  # 完整备份
    INCREMENTAL = "incremental"  # 增量备份


class BackupStatus(str, Enum):
    """备份状态"""

    SUCCESS = "success"
    FAILED = "failed"
    IN_PROGRESS = "in_progress"


@dataclass
class BackupMetadata:
    """备份元数据"""

    backup_id: str
    backup_type: BackupType
    created_at: str
    size_bytes: int
    file_path: str
    checksum: str
    version: str
    includes: list[str] = field(default_factory=list)
    parent_backup_id: str | None = None  # 增量备份的父备份
    note_count: int = 0
    task_count: int = 0
    memory_count: int = 0
    # 七维-数C1（2026-08-29）：扩展域条目计数；v1 元数据无此字段时缺省空 dict
    domain_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class BackupResult:
    """备份操作结果"""

    success: bool
    backup_id: str | None = None
    file_path: str | None = None
    size_bytes: int = 0
    duration_seconds: float = 0
    error: str | None = None
    metadata: BackupMetadata | None = None


@dataclass
class RestoreResult:
    """恢复操作结果"""

    success: bool
    restored_items: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    checksum: str | None = None  # 恢复时复验通过的备份内容校验和


class BackupConfig:
    """备份配置"""

    def __init__(
        self,
        backup_dir: str | Path | None = None,
        max_backups: int = 10,
        compress: bool = True,
        include_ai_cache: bool = False,
        include_memories: bool = True,
        include_notes: bool = True,
        include_tasks: bool = True,
        include_settings: bool = True,
        include_extended_domains: bool = True,
    ):
        """初始化备份配置。

        Args:
            backup_dir: 备份目录，默认为 data/backups/
            max_backups: 最大备份数量
            compress: 是否压缩备份
            include_ai_cache: 是否包含 AI 缓存
            include_memories: 是否包含记忆
            include_notes: 是否包含笔记
            include_tasks: 是否包含任务
            include_settings: 是否包含设置
            include_extended_domains: 是否包含数C1 扩展域（inbox/words/translations/
                chat_sessions/structured_memory_entries/audit_trail/cron_jobs/
                knowledge_graph.json/narratives//chat_sessions/ 分片）
        """
        self.backup_dir = Path(backup_dir) if backup_dir else None
        self.max_backups = max_backups
        self.compress = compress
        self.include_ai_cache = include_ai_cache
        self.include_memories = include_memories
        self.include_notes = include_notes
        self.include_tasks = include_tasks
        self.include_settings = include_settings
        self.include_extended_domains = include_extended_domains


class BackupService:
    """数据备份与恢复服务。

    使用示例：
        # 初始化
        service = BackupService(data_dir="思忆集app/data")

        # 创建备份
        result = await service.create_backup()
        if result.success:
            print(f"备份成功: {result.file_path}")

        # 恢复备份
        result = await service.restore_backup(backup_id="20260607_120000")

        # 列出备份
        backups = service.list_backups()

        # 定时备份
        await service.schedule_backup("0 2 * * *")  # 每天凌晨2点
    """

    # 七维-数C1（2026-08-29）：备份集扩展至统一存储键域+知识图谱/叙事/chat 分片；
    # 旧 v1 备份与元数据仍可读（恢复只认 data+includes）。
    VERSION = "1.1.0"
    BACKUP_EXTENSION = ".json"
    COMPRESSED_EXTENSION = ".json.gz"

    def __init__(
        self,
        data_dir: str | Path,
        config: BackupConfig | None = None,
        *,
        data_store: Any = None,
    ):
        """初始化备份服务。

        Args:
            data_dir: 数据目录路径
            config: 备份配置
            data_store: LocalDataStore 实例（SQLite 后端）。提供后备份/恢复
                        将通过 data_store.load_json/save_json 读写数据，
                        而非直接读取已废弃的 JSON 文件。
        """
        self._data_dir = Path(data_dir)
        self._config = config or BackupConfig()
        self._data_store = data_store

        # 设置备份目录
        if self._config.backup_dir:
            self._backup_dir = self._config.backup_dir
        else:
            self._backup_dir = self._data_dir / "backups"

        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._metadata_file = self._backup_dir / "backups.json"

        # 加载备份元数据
        self._backups: dict[str, BackupMetadata] = {}
        self._load_metadata()

        # 定时任务
        self._scheduled_task: asyncio.Task | None = None
        self._is_running = False

    def _load_metadata(self) -> None:
        """加载备份元数据"""
        if self._metadata_file.exists():
            try:
                with open(self._metadata_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    metadata = BackupMetadata(
                        backup_id=item["backup_id"],
                        backup_type=BackupType(item["backup_type"]),
                        created_at=item["created_at"],
                        size_bytes=item["size_bytes"],
                        file_path=item["file_path"],
                        checksum=item["checksum"],
                        version=item["version"],
                        includes=item.get("includes", []),
                        parent_backup_id=item.get("parent_backup_id"),
                        note_count=item.get("note_count", 0),
                        task_count=item.get("task_count", 0),
                        memory_count=item.get("memory_count", 0),
                        domain_counts=item.get("domain_counts", {}),
                    )
                    self._backups[metadata.backup_id] = metadata
            except Exception as e:
                logger.warning("加载备份元数据失败: %s", e)

    def _save_metadata(self) -> None:
        """保存备份元数据"""
        data = []
        for metadata in self._backups.values():
            data.append(
                {
                    "backup_id": metadata.backup_id,
                    "backup_type": metadata.backup_type.value,
                    "created_at": metadata.created_at,
                    "size_bytes": metadata.size_bytes,
                    "file_path": metadata.file_path,
                    "checksum": metadata.checksum,
                    "version": metadata.version,
                    "includes": metadata.includes,
                    "parent_backup_id": metadata.parent_backup_id,
                    "note_count": metadata.note_count,
                    "task_count": metadata.task_count,
                    "memory_count": metadata.memory_count,
                    "domain_counts": metadata.domain_counts,
                }
            )

        with open(self._metadata_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _generate_backup_id(self) -> str:
        """生成备份 ID"""
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def _get_backup_path(self, backup_id: str) -> Path:
        """获取备份文件路径"""
        ext = self.COMPRESSED_EXTENSION if self._config.compress else self.BACKUP_EXTENSION
        return self._backup_dir / f"{backup_id}{ext}"

    def _calculate_checksum(self, file_path: Path) -> str:
        """计算文件校验和（基于文件原始字节）。"""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    @staticmethod
    def _content_checksum(content: str) -> str:
        """计算内容字符串的校验和（与备份文件内嵌校验和一致，便于恢复时复验）。"""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _load_domain_payload(self, key: str) -> Any:
        """读取统一存储键域：data_store 优先，回退 <data_dir>/<key>.json 旧文件。

        返回 None 表示该域无数据（备份时跳过）；文件解析失败按 None 处理并告警。
        """
        if self._data_store is not None:
            return self._data_store.load_json(key, None)
        domain_file = self._data_dir / f"{key}.json"
        if not domain_file.exists():
            return None
        try:
            with open(domain_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("备份读取 %s 失败，跳过该域: %s", key, exc)
            return None

    @staticmethod
    def _snapshot_json_dir(dir_path: Path) -> dict[str, Any]:
        """递归收集目录下全部 .json 文件 → {posix 相对路径: 解析后 JSON}。

        供 narratives/ 与 chat_sessions/ 等目录域做备份快照；不可解析文件
        跳过并告警，不阻断整个备份。
        """
        snapshot: dict[str, Any] = {}
        if not dir_path.is_dir():
            return snapshot
        for f in sorted(dir_path.rglob("*.json"), key=lambda p: p.as_posix()):
            rel = f.relative_to(dir_path).as_posix()
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    snapshot[rel] = json.load(fh)
            except Exception as exc:
                logger.warning("备份目录快照跳过不可解析文件 %s: %s", f, exc)
        return snapshot

    @staticmethod
    def _restore_json_dir(dir_path: Path, snapshot: Any) -> int:
        """把目录快照还原到 dir_path，返回恢复的文件数。

        先清空旧目录（语义与 memories 恢复一致）；快照键做路径遍历防护
        （拒绝绝对路径与 ..），防篡改备份借恢复路径写逃逸。
        """
        if not isinstance(snapshot, dict):
            return 0
        if dir_path.exists():
            shutil.rmtree(dir_path)
        dir_path.mkdir(parents=True, exist_ok=True)
        restored_files = 0
        for rel, payload in snapshot.items():
            parts = [p for p in str(rel).replace("\\", "/").split("/") if p not in ("", ".")]
            if not parts or any(p == ".." for p in parts):
                logger.warning("恢复目录快照跳过非法相对路径: %r", rel)
                continue
            target = dir_path.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            restored_files += 1
        return restored_files

    def _preserve_current(self, label: str, current: Any) -> str | None:
        """恢复前把当前数据快照进备份目录（与 notes/tasks/settings 同语义）。

        返回快照文件名（无现行数据时返回 None）。
        """
        if not current:
            return None
        backup_name = f"{label}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(self._backup_dir / backup_name, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
        return backup_name

    async def create_backup(
        self,
        backup_type: BackupType = BackupType.FULL,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> BackupResult:
        """创建备份。

        Args:
            backup_type: 备份类型
            progress_callback: 进度回调 (progress, message)

        Returns:
            BackupResult: 备份结果
        """
        if self._is_running:
            return BackupResult(
                success=False,
                error="已有备份任务正在运行",
            )

        self._is_running = True
        start_time = datetime.now()
        backup_id = self._generate_backup_id()

        try:
            if progress_callback:
                progress_callback(0.0, "开始备份...")

            # 收集数据
            data = {}
            includes = []
            counts = {"notes": 0, "tasks": 0, "memories": 0}
            domain_counts: dict[str, int] = {}  # 七维-数C1：扩展域条目计数

            # 笔记
            if self._config.include_notes:
                notes_data = None
                if self._data_store is not None:
                    notes_data = self._data_store.load_json("notes", [])
                else:
                    notes_file = self._data_dir / "notes.json"
                    if notes_file.exists():
                        with open(notes_file, "r", encoding="utf-8") as f:
                            notes_data = json.load(f)
                if notes_data is not None:
                    data["notes"] = notes_data
                    includes.append("notes")
                    counts["notes"] = len(notes_data) if isinstance(notes_data, list) else 1

            if progress_callback:
                progress_callback(0.25, "已备份笔记")

            # 任务
            if self._config.include_tasks:
                tasks_data = None
                if self._data_store is not None:
                    tasks_data = self._data_store.load_json("tasks", [])
                else:
                    tasks_file = self._data_dir / "todos.json"
                    if tasks_file.exists():
                        with open(tasks_file, "r", encoding="utf-8") as f:
                            tasks_data = json.load(f)
                if tasks_data is not None:
                    data["tasks"] = tasks_data
                    includes.append("tasks")
                    counts["tasks"] = len(tasks_data) if isinstance(tasks_data, list) else 1

            if progress_callback:
                progress_callback(0.5, "已备份任务")

            # 设置
            if self._config.include_settings:
                settings_data = None
                if self._data_store is not None:
                    settings_data = self._data_store.load_json("settings", {})
                else:
                    settings_file = self._data_dir / "settings.json"
                    if settings_file.exists():
                        with open(settings_file, "r", encoding="utf-8") as f:
                            settings_data = json.load(f)
                if settings_data is not None:
                    # 七维-数C2/安C6（2026-08-29）：settings 含 API key/token 明文，
                    # 备份产物（gzip 仅压缩非加密）即密钥泄露面——进备份前递归剥离
                    # 敏感键（值为空串占位，保持结构）。恢复后需重新录入凭据。
                    settings_data, stripped = _strip_sensitive_settings(settings_data)
                    if stripped:
                        logger.warning(
                            "备份已剥离 %d 个敏感设置键（值不进备份，恢复后需重新录入）: %s",
                            len(stripped),
                            ", ".join(stripped[:10]),
                        )
                    data["settings"] = settings_data
                    includes.append("settings")

            if progress_callback:
                progress_callback(0.75, "已备份设置")

            # 记忆
            if self._config.include_memories:
                memories_dir = self._data_dir / "memories"
                if memories_dir.exists():
                    memories = []
                    for mem_file in memories_dir.glob("*.json"):
                        with open(mem_file, "r", encoding="utf-8") as f:
                            memories.append(json.load(f))
                    data["memories"] = memories
                    includes.append("memories")
                    counts["memories"] = len(memories)

            # 语义记忆状态（T5.2）：assistant_memory_index / assistant_decisions
            # 是语义记忆条目与决策的唯一存放地（memory/index.py:663-672），
            # 此前不在备份集内 -> 恢复后语义记忆永久丢失。现随 include_memories 一起备份。
            if self._config.include_memories:
                for _state_key in ("assistant_memory_index", "assistant_decisions"):
                    _state_data = None
                    if self._data_store is not None:
                        _state_data = self._data_store.load_json(_state_key, None)
                    else:
                        _state_file = self._data_dir / f"{_state_key}.json"
                        if _state_file.exists():
                            with open(_state_file, "r", encoding="utf-8") as f:
                                _state_data = json.load(f)
                    if _state_data is not None:
                        data[_state_key] = _state_data
                        includes.append(_state_key)
                        counts.setdefault("memories", 0)
                        counts["memories"] += 1

            # AI 缓存
            if self._config.include_ai_cache:
                cache_data = None
                if self._data_store is not None:
                    cache_data = self._data_store.load_json("ai_cache", {})
                else:
                    cache_file = self._data_dir / "ai_cache.json"
                    if cache_file.exists():
                        with open(cache_file, "r", encoding="utf-8") as f:
                            cache_data = json.load(f)
                if cache_data is not None:
                    data["ai_cache"] = cache_data
                    includes.append("ai_cache")

            # 七维-数C1（2026-08-29）：补齐缺失域——备份范围与 backup-boundary.md
            # 声明对齐（P0①）。统一存储键域走 _load_domain_payload（data_store
            # 优先，旧文件回退）；文件域 knowledge_graph.json；目录域 narratives/
            # 与 chat_sessions/ 消息分片。
            if self._config.include_extended_domains:
                for _key in _EXTENDED_LIST_DOMAINS:
                    _payload = self._load_domain_payload(_key)
                    if _payload is None:
                        continue
                    data[_key] = _payload
                    includes.append(_key)
                    domain_counts[_key] = len(_payload) if isinstance(_payload, (list, dict)) else 1

                kg_file = self._data_dir / "knowledge_graph.json"
                if kg_file.exists():
                    try:
                        with open(kg_file, "r", encoding="utf-8") as f:
                            kg_payload = json.load(f)
                    except Exception as exc:
                        logger.warning("备份读取 knowledge_graph.json 失败，跳过: %s", exc)
                    else:
                        data["knowledge_graph"] = kg_payload
                        includes.append("knowledge_graph")
                        domain_counts["knowledge_graph"] = len(kg_payload.get("entities", {})) if isinstance(kg_payload, dict) else 1

                for _blob_key, _dir_name in _EXTENDED_DIR_DOMAINS:
                    _snap = self._snapshot_json_dir(self._data_dir / _dir_name)
                    if not _snap:
                        continue
                    data[_blob_key] = _snap
                    includes.append(_blob_key)
                    if _blob_key == "narratives":
                        _idx = _snap.get("index.json")
                        domain_counts[_blob_key] = len(_idx) if isinstance(_idx, dict) else len(_snap)
                    else:
                        domain_counts[_blob_key] = len({rel.split("/")[0] for rel in _snap})

            if progress_callback:
                progress_callback(0.9, "正在写入备份文件...")

            # 添加元数据
            backup_data = {
                "version": self.VERSION,
                "backup_id": backup_id,
                "backup_type": backup_type.value,
                "created_at": datetime.now().isoformat(),
                "includes": includes,
                "data": data,
            }

            # 写入文件
            backup_path = self._get_backup_path(backup_id)

            # P1.5：内容校验和（canonical content = 不含 checksum 字段的 JSON）。
            # 恢复端通过剔除 checksum 字段后按相同 json.dumps 重建同一 canonical
            # 内容复算校验和，因此这里必须对 v1（无 checksum）取哈希，再内嵌。
            canonical_content = json.dumps(backup_data, ensure_ascii=False)
            content_checksum = self._content_checksum(canonical_content)
            backup_data["checksum"] = content_checksum
            content = json.dumps(backup_data, ensure_ascii=False)

            if self._config.compress:
                with gzip.open(backup_path, "wt", encoding="utf-8") as f:
                    f.write(content)
            else:
                with open(backup_path, "w", encoding="utf-8") as f:
                    f.write(content)

            size_bytes = backup_path.stat().st_size

            # 保存元数据（checksum 字段统一存内容级校验和，便于恢复复验）
            metadata = BackupMetadata(
                backup_id=backup_id,
                backup_type=backup_type,
                created_at=datetime.now().isoformat(),
                size_bytes=size_bytes,
                file_path=str(backup_path),
                checksum=content_checksum,
                version=self.VERSION,
                includes=includes,
                note_count=counts["notes"],
                task_count=counts["tasks"],
                memory_count=counts["memories"],
                domain_counts=domain_counts,
            )
            self._backups[backup_id] = metadata
            self._save_metadata()

            # 清理旧备份
            self._cleanup_old_backups()

            duration = (datetime.now() - start_time).total_seconds()

            if progress_callback:
                progress_callback(1.0, "备份完成")

            logger.info("备份完成: id=%s, size=%d bytes, duration=%.2fs", backup_id, size_bytes, duration)

            return BackupResult(
                success=True,
                backup_id=backup_id,
                file_path=str(backup_path),
                size_bytes=size_bytes,
                duration_seconds=duration,
                metadata=metadata,
            )

        except Exception as e:
            logger.exception("备份失败: %s", e)
            return BackupResult(
                success=False,
                backup_id=backup_id,
                error=str(e),
                duration_seconds=(datetime.now() - start_time).total_seconds(),
            )

        finally:
            self._is_running = False

    async def restore_backup(
        self,
        backup_id: str | None = None,
        backup_path: str | Path | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> RestoreResult:
        """从备份恢复数据。

        Args:
            backup_id: 备份 ID
            backup_path: 备份文件路径（与 backup_id 二选一）
            progress_callback: 进度回调

        Returns:
            RestoreResult: 恢复结果
        """
        if self._is_running:
            return RestoreResult(
                success=False,
                error="已有备份任务正在运行",
            )

        self._is_running = True
        start_time = datetime.now()
        warnings = []
        restored = {}

        try:
            # 确定备份文件
            if backup_path:
                file_path = Path(backup_path)
            elif backup_id:
                metadata = self._backups.get(backup_id)
                if not metadata:
                    return RestoreResult(
                        success=False,
                        error=f"未找到备份: {backup_id}",
                    )
                file_path = Path(metadata.file_path)
            else:
                return RestoreResult(
                    success=False,
                    error="必须指定 backup_id 或 backup_path",
                )

            if not file_path.exists():
                return RestoreResult(
                    success=False,
                    error=f"备份文件不存在: {file_path}",
                )

            if progress_callback:
                progress_callback(0.0, "读取备份文件...")

            # 读取备份
            if file_path.suffix == ".gz":
                with gzip.open(file_path, "rt", encoding="utf-8") as f:
                    raw_content = f.read()
            else:
                with open(file_path, "r", encoding="utf-8") as f:
                    raw_content = f.read()
            backup_data = json.loads(raw_content)

            # P1.5：恢复前复验校验和，防止损坏/被篡改的备份被静默恢复。
            # 重建 canonical content（剔除 checksum 字段后按相同 json.dumps 复算），
            # 与备份时计算的 content_checksum 比对。优先级：元数据（backup_id 路径）>
            # 文件内嵌校验和（仅 backup_path 路径）。
            expected_checksum = None
            if backup_id:
                metadata = self._backups.get(backup_id)
                if metadata is not None:
                    expected_checksum = metadata.checksum
            embedded_checksum = backup_data.get("checksum")
            if expected_checksum is None and embedded_checksum:
                expected_checksum = embedded_checksum

            if expected_checksum:
                # 重建 canonical content（与 create_backup 的 canonical_content 一致）
                canonical_dict = dict(backup_data)
                canonical_dict.pop("checksum", None)
                canonical_content = json.dumps(canonical_dict, ensure_ascii=False)
                actual_checksum = self._content_checksum(canonical_content)
                if actual_checksum != expected_checksum:
                    logger.error(
                        "备份校验和验证失败: expected=%s actual=%s path=%s",
                        expected_checksum,
                        actual_checksum,
                        file_path,
                    )
                    return RestoreResult(
                        success=False,
                        error="备份校验和验证失败：文件可能已损坏或被篡改，已中止恢复以避免数据破坏",
                        duration_seconds=(datetime.now() - start_time).total_seconds(),
                        warnings=warnings,
                    )

            data = backup_data.get("data", {})
            includes = backup_data.get("includes", [])

            if progress_callback:
                progress_callback(0.2, "开始恢复数据...")

            # 恢复笔记
            if "notes" in data and "notes" in includes:
                if self._data_store is not None:
                    # SQLite 模式：先备份当前数据到备份目录
                    current = self._data_store.load_json("notes", [])
                    if current:
                        backup_name = f"notes_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                        with open(self._backup_dir / backup_name, "w", encoding="utf-8") as f:
                            json.dump(current, f, ensure_ascii=False, indent=2)
                        warnings.append(f"已备份原有笔记到 {backup_name}")
                    self._data_store.save_json("notes", data["notes"])
                else:
                    notes_file = self._data_dir / "notes.json"
                    if notes_file.exists():
                        backup_name = f"notes_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                        shutil.copy(notes_file, self._backup_dir / backup_name)
                        warnings.append(f"已备份原有笔记到 {backup_name}")
                    with open(notes_file, "w", encoding="utf-8") as f:
                        json.dump(data["notes"], f, ensure_ascii=False, indent=2)
                restored["notes"] = len(data["notes"]) if isinstance(data["notes"], list) else 1

            if progress_callback:
                progress_callback(0.4, "已恢复笔记")

            # 恢复任务
            if "tasks" in data and "tasks" in includes:
                if self._data_store is not None:
                    current = self._data_store.load_json("tasks", [])
                    if current:
                        backup_name = f"todos_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                        with open(self._backup_dir / backup_name, "w", encoding="utf-8") as f:
                            json.dump(current, f, ensure_ascii=False, indent=2)
                        warnings.append(f"已备份原有任务到 {backup_name}")
                    self._data_store.save_json("tasks", data["tasks"])
                else:
                    tasks_file = self._data_dir / "todos.json"
                    if tasks_file.exists():
                        backup_name = f"todos_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                        shutil.copy(tasks_file, self._backup_dir / backup_name)
                        warnings.append(f"已备份原有任务到 {backup_name}")
                    with open(tasks_file, "w", encoding="utf-8") as f:
                        json.dump(data["tasks"], f, ensure_ascii=False, indent=2)
                restored["tasks"] = len(data["tasks"]) if isinstance(data["tasks"], list) else 1

            if progress_callback:
                progress_callback(0.6, "已恢复任务")

            # 恢复设置
            if "settings" in data and "settings" in includes:
                if self._data_store is not None:
                    current = self._data_store.load_json("settings", {})
                    if current:
                        backup_name = f"settings_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                        with open(self._backup_dir / backup_name, "w", encoding="utf-8") as f:
                            json.dump(current, f, ensure_ascii=False, indent=2)
                        warnings.append(f"已备份原有设置到 {backup_name}")
                    self._data_store.save_json("settings", data["settings"])
                else:
                    settings_file = self._data_dir / "settings.json"
                    if settings_file.exists():
                        backup_name = f"settings_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                        shutil.copy(settings_file, self._backup_dir / backup_name)
                        warnings.append(f"已备份原有设置到 {backup_name}")
                    with open(settings_file, "w", encoding="utf-8") as f:
                        json.dump(data["settings"], f, ensure_ascii=False, indent=2)
                restored["settings"] = 1

            # 恢复记忆
            if "memories" in data and "memories" in includes:
                memories_dir = self._data_dir / "memories"
                memories_dir.mkdir(parents=True, exist_ok=True)

                # 清空现有记忆
                for mem_file in memories_dir.glob("*.json"):
                    mem_file.unlink()

                # 写入备份的记忆
                for i, memory in enumerate(data["memories"]):
                    mem_file = memories_dir / f"memory_{i}.json"
                    with open(mem_file, "w", encoding="utf-8") as f:
                        json.dump(memory, f, ensure_ascii=False, indent=2)

                restored["memories"] = len(data["memories"])

            # 恢复语义记忆状态（T5.2）
            for _state_key in ("assistant_memory_index", "assistant_decisions"):
                if _state_key in data and _state_key in includes:
                    if self._data_store is not None:
                        self._data_store.save_json(_state_key, data[_state_key])
                    else:
                        _state_file = self._data_dir / f"{_state_key}.json"
                        with open(_state_file, "w", encoding="utf-8") as f:
                            json.dump(data[_state_key], f, ensure_ascii=False, indent=2)
                    _restored_entries = data[_state_key].get("entries") if isinstance(data[_state_key], dict) else None
                    restored.setdefault(
                        _state_key,
                        len(_restored_entries) if _restored_entries else 1,
                    )

            # 七维-数C1：恢复扩展域（与备份集对齐；只认 blob 的 data+includes，
            # 旧 v1 备份不含这些域时自然跳过，保持向后兼容）。
            for _key in _EXTENDED_LIST_DOMAINS:
                if _key not in data or _key not in includes:
                    continue
                if self._data_store is not None:
                    _saved = self._preserve_current(_key, self._data_store.load_json(_key, None))
                    if _saved:
                        warnings.append(f"已备份原有 {_key} 到 {_saved}")
                    self._data_store.save_json(_key, data[_key])
                else:
                    _domain_file = self._data_dir / f"{_key}.json"
                    _current = None
                    if _domain_file.exists():
                        try:
                            with open(_domain_file, "r", encoding="utf-8") as f:
                                _current = json.load(f)
                        except Exception:
                            _current = None
                    _saved = self._preserve_current(_key, _current)
                    if _saved:
                        warnings.append(f"已备份原有 {_key} 到 {_saved}")
                    with open(_domain_file, "w", encoding="utf-8") as f:
                        json.dump(data[_key], f, ensure_ascii=False, indent=2)
                restored[_key] = len(data[_key]) if isinstance(data[_key], (list, dict)) else 1

            if "knowledge_graph" in data and "knowledge_graph" in includes:
                kg_file = self._data_dir / "knowledge_graph.json"
                _kg_current = None
                if kg_file.exists():
                    try:
                        with open(kg_file, "r", encoding="utf-8") as f:
                            _kg_current = json.load(f)
                    except Exception:
                        _kg_current = None
                _saved = self._preserve_current("knowledge_graph", _kg_current)
                if _saved:
                    warnings.append(f"已备份原有知识图谱到 {_saved}")
                with open(kg_file, "w", encoding="utf-8") as f:
                    json.dump(data["knowledge_graph"], f, ensure_ascii=False, indent=2)
                restored["knowledge_graph"] = len(data["knowledge_graph"].get("entities", {})) if isinstance(data["knowledge_graph"], dict) else 1

            for _blob_key, _dir_name in _EXTENDED_DIR_DOMAINS:
                if _blob_key not in data or _blob_key not in includes:
                    continue
                restored[_blob_key] = self._restore_json_dir(self._data_dir / _dir_name, data[_blob_key])

            if progress_callback:
                progress_callback(1.0, "恢复完成")

            duration = (datetime.now() - start_time).total_seconds()

            logger.info("恢复完成: restored=%s, duration=%.2fs", restored, duration)

            return RestoreResult(
                success=True,
                restored_items=restored,
                duration_seconds=duration,
                warnings=warnings,
                checksum=expected_checksum,
            )

        except Exception as e:
            logger.exception("恢复失败: %s", e)
            return RestoreResult(
                success=False,
                error=str(e),
                duration_seconds=(datetime.now() - start_time).total_seconds(),
                warnings=warnings,
            )

        finally:
            self._is_running = False

    def list_backups(self) -> list[BackupMetadata]:
        """列出所有备份"""
        return sorted(
            self._backups.values(),
            key=lambda m: m.created_at,
            reverse=True,
        )

    def get_backup(self, backup_id: str) -> BackupMetadata | None:
        """获取指定备份的元数据"""
        return self._backups.get(backup_id)

    def delete_backup(self, backup_id: str) -> bool:
        """删除指定备份"""
        metadata = self._backups.get(backup_id)
        if not metadata:
            return False

        try:
            file_path = Path(metadata.file_path)
            if file_path.exists():
                file_path.unlink()

            del self._backups[backup_id]
            self._save_metadata()

            logger.info("已删除备份: %s", backup_id)
            return True

        except Exception as e:
            logger.warning("删除备份失败: %s", e)
            return False

    def _cleanup_old_backups(self) -> None:
        """清理旧备份，保留最大数量"""
        if len(self._backups) <= self._config.max_backups:
            return

        # 按创建时间排序，删除最旧的
        sorted_backups = sorted(
            self._backups.items(),
            key=lambda x: x[1].created_at,
        )

        to_delete = len(self._backups) - self._config.max_backups
        for i, (backup_id, _) in enumerate(sorted_backups):
            if i >= to_delete:
                break
            self.delete_backup(backup_id)

    async def schedule_backup(self, cron_expr: str) -> None:
        """设置定时备份。

        Args:
            cron_expr: Cron 表达式（简化版，支持 "分 时 日 月 周"）
                      例如: "0 2 * * *" 表示每天凌晨2点
        """
        if self._scheduled_task:
            self._scheduled_task.cancel()

        async def _schedule_loop():
            while True:
                # 解析 cron 表达式，计算下次执行时间
                next_run = self._calculate_next_run(cron_expr)
                wait_seconds = (next_run - datetime.now()).total_seconds()

                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)

                # 执行备份
                logger.info("执行定时备份...")
                result = await self.create_backup()

                if result.success:
                    logger.info("定时备份完成: %s", result.backup_id)
                else:
                    logger.error("定时备份失败: %s", result.error)

        self._scheduled_task = asyncio.create_task(_schedule_loop())
        logger.info("已设置定时备份: %s", cron_expr)

    def _calculate_next_run(self, cron_expr: str) -> datetime:
        """计算下次执行时间（简化版 cron 解析）"""
        parts = cron_expr.split()
        if len(parts) != 5:
            raise ValueError(f"无效的 cron 表达式: {cron_expr}")

        minute, hour, day, month, weekday = parts

        now = datetime.now()
        next_run = now.replace(second=0, microsecond=0)

        # 简单实现：只支持具体数字和 *
        if minute != "*":
            next_run = next_run.replace(minute=int(minute))
        if hour != "*":
            next_run = next_run.replace(hour=int(hour))

        # 如果已经过了今天的时间，推到明天
        if next_run <= now:
            next_run += timedelta(days=1)

        return next_run

    def stop_schedule(self) -> None:
        """停止定时备份"""
        if self._scheduled_task:
            self._scheduled_task.cancel()
            self._scheduled_task = None
            logger.info("已停止定时备份")

    def get_stats(self) -> dict[str, Any]:
        """获取备份统计"""
        total_size = sum(b.size_bytes for b in self._backups.values())
        return {
            "backup_count": len(self._backups),
            "total_size_bytes": total_size,
            "backup_dir": str(self._backup_dir),
            "max_backups": self._config.max_backups,
            "scheduled": self._scheduled_task is not None,
        }


# 便捷函数
async def create_backup(
    data_dir: str | Path,
    compress: bool = True,
) -> BackupResult:
    """创建备份的便捷函数"""
    service = BackupService(data_dir, BackupConfig(compress=compress))
    return await service.create_backup()


async def restore_backup(
    data_dir: str | Path,
    backup_id: str | None = None,
    backup_path: str | Path | None = None,
) -> RestoreResult:
    """恢复备份的便捷函数"""
    service = BackupService(data_dir)
    return await service.restore_backup(backup_id=backup_id, backup_path=backup_path)
