"""SyncResource Protocol — 思忆集资源同步统一协议。

设计参考：
- syncthing 的 Block Exchange Protocol（内容哈希判断变更）
- api_sync_orchestrator.py 中的 ResourceSyncSpec
- docs/sync-protocol-design.md（Sync Envelope JSON Schema、Cursor 增量、冲突矩阵）

用途：
    为所有需要同步的资源类型（notes, tasks, news, translations, wordbooks）
    提供统一的增量同步接口。同步引擎只依赖此协议，不直接感知仓储实现。

身份标注（波次④包B 落 · 蓝本 §3-包C 裁定归本包写入）：
    本模块是**仅信封/冲突协议声明层，非运行时编排真源**。生产同步的运行时真身是
    A 栈 ``api/sync_orchestrator.py``（automation_hub REST）与 B 栈
    ``sync/cloud_sync_client.py`` 的 SyncBridge（Cloudflare Worker），两者均不 import 本模块；
    本模块当前仅被未接线岛屿（sync/channels.py、sync/file_sync_service.py）引用。
    ``SyncState`` 枚举（sync_state 四值词汇）例外地被生产 D 栈 ``sync/note_sync.py``
    re-export 作为单一定义点（波次④包B 上提，纯声明层收敛，不改任何运行时行为）。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

# ── Sync Envelope Schema Constants ─────────────────────────────────

RESOURCE_TYPES: list[str] = [
    "note", "personal_task", "inbox_item", "memory_entry",
    "execution_receipt", "user_preference", "device_record",
    "wordbook_entry", "translation_history", "saved_article",
    "sync_envelope",
]

SYNC_ACTIONS: list[str] = ["create", "update", "delete", "undelete"]

DEVICE_TYPES: list[str] = ["desktop", "android", "ios"]

DEVICE_STATUSES: list[str] = ["active", "revoked", "offline"]


class ConflictStrategy(Enum):
    """冲突解决策略。"""
    LOCAL_WINS = "local_wins"
    REMOTE_WINS = "remote_wins"
    KEEP_BOTH = "keep_both"         # syncthing 风格：保留双方副本
    MANUAL = "manual"               # 标记为冲突，等待用户手动合并


class SyncState(Enum):
    """同步状态枚举 — ``sync_state`` 字段四值的唯一真源（SSOT，波次④包B 上提）。

    原本散定义于 ``sync/note_sync.py:6-11``（4 个硬编码字符串常量，蓝本 §1a-2
    “sync_state 枚举游离于 SSOT 之外”），现上提至此；``sync/note_sync.py`` 的
    ``SYNC_STATE_*`` 常量改为本枚举 ``.value`` 的 re-export，所有既有消费者不破。

    适用载体（蓝本 §1b）：载体①实体 dict 字段 ``sync_state`` 与载体②desk-notes
    ``.sync-meta.json`` sidecar 的同名字段共用该值域；**从不上 wire**（手机端只 LWW，
    Worker 只存密文），故本上提属纯内部声明层收敛，零跨端协议影响。

    新代码请直接消费 ``SyncState.*.value``（tests/test_wave4b_sync_state_ssot_20260924.py
    AST 棘轮锁住散字面量禁止新增）。
    """
    LOCAL_ONLY = "local_only"
    DIRTY = "dirty"
    SYNCED = "synced"
    # 冲突状态：本地和远端同时修改了同一资源，需用户手动解决或等待合并策略处理。
    # 此状态优先级高于 DIRTY — 处于 CONFLICT 的条目不会被自动同步覆盖。
    CONFLICT = "conflict"


@dataclass(frozen=True)
class SyncEnvelope:
    """Sync Envelope — 所有跨设备数据交换的统一信封格式（SSOT）。

    匹配 docs/sync-protocol-design.md 中的 JSON Schema 定义。
    """
    envelope_id: str
    device_id: str
    resource_type: str
    resource_id: str
    action: str
    payload: dict[str, Any] | None
    version: int
    cursor: int
    created_at: str
    checksum: str

    def validate(self) -> None:
        """验证 envelope 所有字段符合 schema 约束。"""
        if self.resource_type not in RESOURCE_TYPES:
            raise ValueError(
                f"Invalid resource_type: {self.resource_type!r}. "
                f"Must be one of {RESOURCE_TYPES}"
            )
        if self.action not in SYNC_ACTIONS:
            raise ValueError(
                f"Invalid action: {self.action!r}. "
                f"Must be one of {SYNC_ACTIONS}"
            )
        if not isinstance(self.envelope_id, str) or not self.envelope_id:
            raise ValueError("envelope_id must be a non-empty string")
        if not isinstance(self.device_id, str) or not self.device_id:
            raise ValueError("device_id must be a non-empty string")
        if not isinstance(self.resource_id, str) or not self.resource_id:
            raise ValueError("resource_id must be a non-empty string")
        if not isinstance(self.version, int) or self.version < 0:
            raise ValueError("version must be a non-negative integer")
        if not isinstance(self.cursor, int) or self.cursor < 0:
            raise ValueError("cursor must be a non-negative integer")
        if self.action == "delete" and self.payload is not None:
            pass  # delete 时 payload 可为 null
        if self.action != "delete" and self.payload is None:
            raise ValueError(f"payload must not be null for action={self.action!r}")

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（JSON 兼容）。"""
        return {
            "envelope_id": self.envelope_id,
            "device_id": self.device_id,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "action": self.action,
            "payload": self.payload,
            "version": self.version,
            "cursor": self.cursor,
            "created_at": self.created_at,
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyncEnvelope:
        """从 dict 反序列化。"""
        return cls(
            envelope_id=str(data["envelope_id"]),
            device_id=str(data["device_id"]),
            resource_type=str(data["resource_type"]),
            resource_id=str(data["resource_id"]),
            action=str(data["action"]),
            payload=data.get("payload"),
            version=int(data.get("version", 0)),
            cursor=int(data.get("cursor", 0)),
            created_at=str(data.get("created_at", "")),
            checksum=str(data.get("checksum", "")),
        )


def generate_envelope_id() -> str:
    """生成 UUID v7 格式的邮包 ID（按时间排序）。"""
    try:
        return str(uuid.uuid7())
    except AttributeError:
        return str(uuid.uuid4())


def compute_checksum(payload: dict[str, Any] | None) -> str:
    """计算 payload JSON 的 SHA-256 哈希用于完整性校验。"""
    if payload is None:
        return hashlib.sha256(b"null").hexdigest()
    normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def create_envelope(
    device_id: str,
    resource_type: str,
    resource_id: str,
    action: str,
    payload: dict[str, Any] | None,
    version: int,
    cursor: int,
) -> SyncEnvelope:
    """工厂方法：创建 SyncEnvelope 并自动计算 checksum 和 created_at。"""
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return SyncEnvelope(
        envelope_id=generate_envelope_id(),
        device_id=device_id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        payload=payload,
        version=version,
        cursor=cursor,
        created_at=now_iso,
        checksum=compute_checksum(payload),
    )


# ── 冲突策略矩阵 ──────────────────────────────────────────────────

# file_* 键缺失为**有意设计**（波次④包B 裁定·保守案，蓝本 §1c 两案择一）：
# file_* 资源（photos/diaries/notes/tasks/memories/settings，见 file_sync_service.SYNC_SUBDIRS）
# 不经 Worker/A 栈同步，本矩阵只覆盖 SyncEnvelope 信封协议内的单数资源名，刻意无 file_* 键。
# 后果已知：sync/file_sync_service.py 的 resolve_conflict_strategy(f"file_{subdir}") 查找
# 恒回落默认 REMOTE_WINS（假复用）；其清理（删假查找或岛屿整删）归波次④包C 处置，
# 本包不补键——若包C 删除 file_sync_service 则补键即成活死码。
# （锚：tests/test_wave4b_sync_state_ssot_20260924.py
#   ::test_matrix_without_file_keys_is_declared_intentional）

CONFLICT_STRATEGY_MATRIX: dict[str, ConflictStrategy] = {
    "note": ConflictStrategy.KEEP_BOTH,                # LWW + 内容合并提示
    "personal_task": ConflictStrategy.REMOTE_WINS,     # 字段级合并，冲突走 LWW
    "inbox_item": ConflictStrategy.REMOTE_WINS,        # LWW
    "memory_entry": ConflictStrategy.MANUAL,           # 用户裁决，不自动覆盖
    "user_preference": ConflictStrategy.REMOTE_WINS,   # 最后一次设置生效
    "device_record": ConflictStrategy.REMOTE_WINS,     # 服务端权威
    "wordbook_entry": ConflictStrategy.REMOTE_WINS,    # LWW
    "translation_history": ConflictStrategy.REMOTE_WINS,
    "saved_article": ConflictStrategy.REMOTE_WINS,
    "execution_receipt": ConflictStrategy.REMOTE_WINS,
    "sync_envelope": ConflictStrategy.REMOTE_WINS,
}


def resolve_conflict_strategy(resource_type: str) -> ConflictStrategy:
    """根据资源类型获取冲突解决策略。"""
    return CONFLICT_STRATEGY_MATRIX.get(resource_type, ConflictStrategy.REMOTE_WINS)


def detect_conflict(local_version: int, remote_version: int) -> bool:
    """基于乐观锁版本号检测冲突。

    remote_version < local_version → 冲突（本地有更新的未同步修改）
    remote_version == local_version → 无冲突
    remote_version > local_version → 无冲突（本地落后，正常应用远端版本）
    """
    return remote_version < local_version


# ── Cursor 与设备管理 ──────────────────────────────────────────────


@dataclass(frozen=True)
class SyncCursor:
    """同步游标：记录某个资源在某个设备上的同步进度。"""
    resource_type: str
    device_id: str
    last_synced_at: str = ""
    version: int = 0
    etag: str = ""


@dataclass(frozen=True)
class DeviceCursor:
    """设备级游标：记录从某个对端设备已拉取到的序号。

    对应 sync-protocol-design.md 中的 sync_cursor（按设备）。
    """
    peer_device_id: str
    cursor: int = 0


@dataclass(frozen=True)
class DeviceRecord:
    """设备身份记录。

    对应 sync-protocol-design.md 中的 DeviceRecord 定义。
    """
    device_id: str
    device_name: str = ""
    device_type: str = "desktop"
    public_key: str = ""
    paired_at: str = ""
    last_seen_at: str = ""
    sync_cursor: int = 0
    status: str = "active"


@dataclass(frozen=True)
class SyncConflict:
    """同步冲突记录。"""
    resource_type: str
    resource_id: str
    local_version: dict[str, Any] = field(default_factory=dict)
    remote_version: dict[str, Any] = field(default_factory=dict)
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved: bool = False
    resolution: str = ""


@dataclass(frozen=True)
class SyncResult:
    """单次同步结果。"""
    resource_type: str
    pulled: int = 0
    pushed: int = 0
    conflicts: int = 0
    errors: list[str] = field(default_factory=list)
    cursor: SyncCursor | None = None


@runtime_checkable
class SyncableResource(Protocol):
    """可同步资源协议——每个需要同步的仓储必须实现。"""

    @property
    def resource_type(self) -> str:
        """资源类型标识符（如 'notes', 'tasks'）。"""
        ...

    def list_pending_sync(self) -> list[dict[str, Any]]:
        """列出本地待推送的变更。"""
        ...

    def merge_remote(self, remote_items: list[dict[str, Any]]) -> list[SyncConflict]:
        """将远端拉取的数据合并到本地，返回冲突列表。"""
        ...

    def mark_synced(self, item_ids: list[str | int]) -> None:
        """标记指定 item 已同步成功。"""
        ...

    def build_sync_payload(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """将本地 item 构建为远端 API 所需的 payload 格式。"""
        ...

    def resolve_conflict(self, conflict: SyncConflict, strategy: ConflictStrategy) -> dict[str, Any]:
        """根据策略解决冲突，返回最终保留的版本。"""
        ...

    def content_hash(self, item: dict[str, Any]) -> str:
        """计算 item 的内容哈希（用于增量同步判断，替代时间戳比较）。"""
        ...


# ── JSON Schema ──────────────────────────────────────────────────────


SYNC_ENVELOPE_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "SyncEnvelope",
    "type": "object",
    "required": [
        "envelope_id", "device_id", "resource_type", "resource_id",
        "action", "payload", "version", "cursor", "created_at", "checksum",
    ],
    "properties": {
        "envelope_id": {
            "type": "string",
            "description": "UUID v7, unique envelope identifier with embedded timestamp",
        },
        "device_id": {
            "type": "string",
            "description": "Device ID, issued by automation-hub on first pairing",
        },
        "resource_type": {
            "type": "string",
            "enum": list(RESOURCE_TYPES),
        },
        "resource_id": {
            "type": "string",
            "description": "Globally unique business object ID",
        },
        "action": {
            "type": "string",
            "enum": list(SYNC_ACTIONS),
            "description": "CRUD action. delete = soft delete (tombstone)",
        },
        "payload": {
            "type": "object",
            "description": "Complete resource JSON. null allowed for delete actions",
        },
        "version": {
            "type": "integer",
            "minimum": 0,
            "description": "Optimistic lock version. Incremented on each update",
        },
        "cursor": {
            "type": "integer",
            "minimum": 0,
            "description": "Device-level monotonically increasing sequence number",
        },
        "created_at": {
            "type": "string",
            "format": "date-time",
            "description": "ISO 8601 UTC",
        },
        "checksum": {
            "type": "string",
            "description": "SHA-256 hash of payload JSON for integrity verification",
        },
    },
}


def validate_envelope_dict(data: dict[str, Any]) -> list[str]:
    """Validate a dict against the SyncEnvelope JSON Schema.

    Returns a list of validation error messages (empty = valid).
    """
    errors: list[str] = []
    for required_key in SYNC_ENVELOPE_JSON_SCHEMA["required"]:
        if required_key not in data:
            errors.append(f"missing required field: {required_key}")

    if "resource_type" in data and data["resource_type"] not in RESOURCE_TYPES:
        errors.append(f"invalid resource_type: {data['resource_type']!r}")
    if "action" in data and data["action"] not in SYNC_ACTIONS:
        errors.append(f"invalid action: {data['action']!r}")
    if "version" in data and (not isinstance(data["version"], int) or data["version"] < 0):
        errors.append(f"version must be a non-negative integer, got {data.get('version')!r}")
    if "cursor" in data and (not isinstance(data["cursor"], int) or data["cursor"] < 0):
        errors.append(f"cursor must be a non-negative integer, got {data.get('cursor')!r}")
    return errors


# ── Tombstone ────────────────────────────────────────────────────────


TOMBSTONE_TTL: dict[str, int] = {
    "note": 30,                 # 30 days
    "personal_task": 30,        # 30 days
    "inbox_item": 7,            # 7 days
    "memory_entry": 90,         # 90 days
    "execution_receipt": 7,     # 7 days
    "user_preference": 30,      # 30 days
    "wordbook_entry": 30,       # 30 days
    "translation_history": 7,   # 7 days
    "saved_article": 30,        # 30 days
}


def get_tombstone_ttl_days(resource_type: str) -> int:
    """Get tombstone TTL in days for a resource type."""
    return TOMBSTONE_TTL.get(resource_type, 30)


@dataclass(frozen=True)
class TombstoneRecord:
    """Tombstone record — soft delete marker.

    delete action does not physically remove data; instead a tombstone
    is written. It is physically purged after TTL expires.
    """
    resource_type: str
    resource_id: str
    deleted_at: str          # ISO 8601
    envelope_id: str = ""
    ttl_days: int = 30

    @classmethod
    def from_envelope(cls, envelope: SyncEnvelope) -> TombstoneRecord:
        """Create a tombstone from a delete-action SyncEnvelope."""
        ttl = get_tombstone_ttl_days(envelope.resource_type)
        return cls(
            resource_type=envelope.resource_type,
            resource_id=envelope.resource_id,
            deleted_at=envelope.created_at,
            envelope_id=envelope.envelope_id,
            ttl_days=ttl,
        )


def is_tombstone_expired(tombstone: TombstoneRecord, reference_time: str | None = None) -> bool:
    """Check if a tombstone record has exceeded its TTL."""
    from datetime import datetime, timezone
    ref = reference_time or datetime.now(timezone.utc).isoformat()
    try:
        deleted = datetime.fromisoformat(tombstone.deleted_at)
        now = datetime.fromisoformat(ref)
        delta_days = (now - deleted).days
        return delta_days >= tombstone.ttl_days
    except (ValueError, TypeError):
        return False


# ── Pending Operation (Offline Queue) ────────────────────────────────


@dataclass(frozen=True)
class PendingOperation:
    """A write operation queued while offline, pending replay on reconnect.

    Matches the offline queue design in sync-protocol-design.md 搂4.
    """
    operation_id: str
    envelope: SyncEnvelope
    queued_at: str                        # ISO 8601
    retry_count: int = 0
    max_retries: int = 5
    priority: int = 1                     # 1=high, 2=medium, 3=low
    last_error: str = ""


PRIORITY_MAP: dict[str, int] = {
    "inbox_item": 1,
    "personal_task": 1,
    "note": 2,
    "memory_entry": 2,
    "translation_history": 3,
    "wordbook_entry": 3,
    "user_preference": 3,
    "saved_article": 3,
    "execution_receipt": 3,
    "device_record": 3,
}


def get_operation_priority(resource_type: str) -> int:
    """Get retry priority for a resource type (1=highest, 3=lowest)."""
    return PRIORITY_MAP.get(resource_type, 2)
