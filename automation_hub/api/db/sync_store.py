# -*- coding: utf-8 -*-
"""跨设备同步存储层（P4.2-P0a / P0b）。

将 ``api/routes/sync.py`` 原先的 4 个内存字典 / 列表迁移至 SQLite，并
通过 ``SyncCursor`` 表 + 进程内 ``threading.RLock`` 保护并发推送下的
游标单调递增。

约定
----
* ``SyncCursor`` 中保留键 ``device_id == "__global__"`` / ``resource_type == "__all__"``
  作为全局递增游标。
* 所有写操作必须在 ``with get_db_session() as session:`` 内调用；
  顺序号分配额外用 :data:`_GLOBAL_SEQUENCE_LOCK` 串行化。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.sync import SyncConflict, SyncCursor, SyncDevice, SyncEnvelopeRecord

# ── 全局游标互斥锁 ─────────────────────────────────────────────────
#
# 单进程内的并发推送会竞争同一行 ``sync_cursors``，SQLite 在 WAL 模式下
# 同样允许多事务读但写仍是串行的；这里额外用一个 ``RLock`` 把"读取-自增-
# 写回"做成原子操作，避免两个 push 拿到相同的 cursor 值。
_GLOBAL_SEQUENCE_LOCK = threading.RLock()

GLOBAL_CURSOR_DEVICE = "__global__"
GLOBAL_CURSOR_RESOURCE = "__all__"


# ── 工具函数 ────────────────────────────────────────────────────


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _envelope_to_dict(record: SyncEnvelopeRecord) -> dict[str, Any]:
    """把 ORM 行还原成 ``/sync/pull`` 返回所需的 dict。"""
    return {
        "envelope_id": record.envelope_id,
        "device_id": record.device_id,
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "action": record.action,
        "payload": record.payload_json or {},
        "version": record.version,
        "cursor": record.cursor,
        "created_at": record.envelope_created_at,
        "checksum": record.checksum,
        "received_at": record.received_at.isoformat() if record.received_at else "",
    }


# ── 游标管理（P0b） ──────────────────────────────────────────────


def get_global_cursor(session: Session) -> int:
    """读取全局游标（最大已分配序号）。"""
    cursor_row = session.get(SyncCursor, (GLOBAL_CURSOR_DEVICE, GLOBAL_CURSOR_RESOURCE))
    return int(cursor_row.max_cursor) if cursor_row else 0


def allocate_next_cursor(session: Session, count: int = 1) -> int:
    """原子地分配下一个全局游标。

    ``count > 1`` 时会一次性预分配 ``count`` 个序号并返回第一个；调用方
    自己按 ``+0/+1/.../+count-1`` 使用。
    """
    if count < 1:
        raise ValueError("count must be >= 1")

    # AG-HUB 游标竞态产品修复（2026-09-13）：旧实现「锁内读改写 + flush」但提交
    # 在调用方手里——并发下其他连接看不到未提交的 max_cursor → 重号/丢号。
    # 改为锁内用**独立连接**完成 upsert + 原子自增（RETURNING）+ 立即提交：
    # 序号发放即持久、绝不随调用方后续回滚（全局序号允许空洞、禁止复用）；
    # 进程内由 _GLOBAL_SEQUENCE_LOCK 串行避免 SQLITE_BUSY 抖动。
    engine = session.get_bind()
    with _GLOBAL_SEQUENCE_LOCK:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT OR IGNORE INTO sync_cursors (device_id, resource_type, max_cursor) "
                     "VALUES (:d, :r, 0)"),
                {"d": GLOBAL_CURSOR_DEVICE, "r": GLOBAL_CURSOR_RESOURCE},
            )
            row = conn.execute(
                text("UPDATE sync_cursors SET max_cursor = max_cursor + :n "
                     "WHERE device_id = :d AND resource_type = :r "
                     "RETURNING max_cursor"),
                {"n": count, "d": GLOBAL_CURSOR_DEVICE, "r": GLOBAL_CURSOR_RESOURCE},
            ).first()
            if row is None:  # 防御：upsert 后 UPDATE 未命中（不应发生）
                raise RuntimeError("sync cursor row missing after upsert")
            first = int(row[0]) - count + 1
    return first


# ── 信封 CRUD ──────────────────────────────────────────────────


def insert_envelope(
    session: Session,
    *,
    envelope_id: str,
    device_id: str,
    resource_type: str,
    resource_id: str,
    action: str,
    payload: dict[str, Any] | None,
    version: int,
    cursor: int,
    created_at: str = "",
    checksum: str = "",
) -> SyncEnvelopeRecord:
    """插入单个信封（cursor 由调用方提前分配）。"""
    record = SyncEnvelopeRecord(
        envelope_id=envelope_id,
        device_id=device_id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        payload_json=payload or {},
        version=version,
        cursor=cursor,
        envelope_created_at=created_at or "",
        checksum=checksum or "",
        received_at=_now_utc(),
    )
    session.add(record)
    return record


def query_envelopes(
    session: Session,
    *,
    since: int = 0,
    resource_type: str = "",
    limit: int = 100,
) -> tuple[list[dict[str, Any]], bool]:
    """按 cursor > since 增量查询信封。

    返回 ``(envelopes, has_more)``。``has_more`` 在数据库中还有未返回行时为 True。
    """
    effective_limit = max(1, min(int(limit), 200))
    stmt = select(SyncEnvelopeRecord).where(SyncEnvelopeRecord.cursor > since)
    if resource_type:
        stmt = stmt.where(SyncEnvelopeRecord.resource_type == resource_type)
    stmt = stmt.order_by(SyncEnvelopeRecord.cursor.asc()).limit(effective_limit + 1)
    rows = list(session.execute(stmt).scalars())
    has_more = len(rows) > effective_limit
    page = rows[:effective_limit]
    return [_envelope_to_dict(r) for r in page], has_more


def get_max_envelope_cursor(session: Session) -> int:
    """返回当前最大已落库游标。"""
    stmt = select(SyncEnvelopeRecord.cursor).order_by(SyncEnvelopeRecord.cursor.desc()).limit(1)
    val = session.execute(stmt).scalar_one_or_none()
    return int(val) if val is not None else 0


# ── 设备心跳 / 列表 ─────────────────────────────────────────────


def upsert_device(
    session: Session,
    *,
    device_id: str,
    status: str,
    last_seen_at: str,
    extra: dict[str, Any] | None = None,
    friendly_name: str | None = None,
    device_type: str | None = None,
) -> SyncDevice:
    """新增或更新设备心跳。"""
    extra_payload = extra or {}
    device = session.get(SyncDevice, device_id)
    if device is None:
        device = SyncDevice(
            device_id=device_id,
            friendly_name=friendly_name or extra_payload.get("name", ""),
            device_type=device_type or extra_payload.get("type", "unknown"),
            status=status,
            last_seen_at=last_seen_at,
            extra_json=extra_payload,
            is_active=True,
        )
        session.add(device)
    else:
        device.status = status
        device.last_seen_at = last_seen_at
        device.extra_json = extra_payload
        if friendly_name:
            device.friendly_name = friendly_name
        if device_type:
            device.device_type = device_type
        device.is_active = True
    return device


def list_devices(session: Session) -> list[dict[str, Any]]:
    stmt = select(SyncDevice).where(SyncDevice.is_active.is_(True))
    devices = list(session.execute(stmt).scalars())
    return [
        {
            "device_id": d.device_id,
            "status": d.status or "unknown",
            "last_seen_at": d.last_seen_at,
            "extra": d.extra_json or {},
        }
        for d in devices
    ]


# ── 冲突 ────────────────────────────────────────────────────────


def insert_conflict(
    session: Session,
    *,
    conflict_id: str,
    device_id: str,
    entity_type: str,
    entity_id: str,
    reason: str,
    resolution: str = "",
    local_version: int = 0,
    remote_version: int = 0,
    local_payload: dict[str, Any] | None = None,
    remote_payload: dict[str, Any] | None = None,
    generated_at: str = "",
) -> SyncConflict:
    record = SyncConflict(
        conflict_id=conflict_id,
        device_id=device_id,
        entity_type=entity_type,
        entity_id=entity_id,
        reason=reason,
        resolution=resolution,
        local_version=local_version,
        remote_version=remote_version,
        local_payload=local_payload or {},
        remote_payload=remote_payload or {},
        generated_at=generated_at or _now_utc().isoformat(),
        status="open",
    )
    session.add(record)
    return record


def list_conflicts_for_device(session: Session, device_id: str) -> list[dict[str, Any]]:
    if not device_id:
        return []
    stmt = (
        select(SyncConflict)
        .where(SyncConflict.device_id == device_id, SyncConflict.status == "open")
        .order_by(SyncConflict.id.asc())
    )
    rows = list(session.execute(stmt).scalars())
    return [
        {
            "conflict_id": r.conflict_id,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "reason": r.reason,
            "resolution": r.resolution,
            "generated_at": r.generated_at,
        }
        for r in rows
    ]


def bulk_insert_envelopes(
    session: Session,
    envelopes: Iterable[dict[str, Any]],
) -> list[SyncEnvelopeRecord]:
    """批量插入信封，连续分配 cursor。"""
    items = list(envelopes)
    if not items:
        return []
    start = allocate_next_cursor(session, count=len(items))
    records: list[SyncEnvelopeRecord] = []
    for offset, env in enumerate(items):
        records.append(
            insert_envelope(
                session,
                envelope_id=env["envelope_id"],
                device_id=env.get("device_id", ""),
                resource_type=env.get("resource_type", ""),
                resource_id=env.get("resource_id", ""),
                action=env.get("action", ""),
                payload=env.get("payload"),
                version=int(env.get("version", 0) or 0),
                cursor=start + offset,
                created_at=str(env.get("created_at", "") or ""),
                checksum=str(env.get("checksum", "") or ""),
            )
        )
    return records
