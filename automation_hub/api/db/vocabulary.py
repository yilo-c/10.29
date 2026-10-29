# -*- coding: utf-8 -*-
"""词汇数据 CRUD（单词本 / 单词）。

已从原生 sqlite3 迁移到 SQLAlchemy 2.0 ORM。
保持与旧代码相同的函数签名和返回格式。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from .session import get_db_session, now_iso
from ..models.personal import PersonalWordbook, PersonalWord
from sqlalchemy import select


# ==================== Wordbooks ====================

def list_personal_wordbooks(
    user_id: str,
    *,
    since_updated_at: str | None = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    resolved_limit = max(int(limit or 100), 1)
    with get_db_session(autocommit=False) as session:
        stmt = select(PersonalWordbook).where(PersonalWordbook.user_id == user_id)
        if since_updated_at:
            stmt = stmt.where(PersonalWordbook.updated_at >= since_updated_at)
        stmt = stmt.order_by(PersonalWordbook.updated_at.desc(), PersonalWordbook.id.desc()).limit(resolved_limit)
        rows = session.execute(stmt).scalars().all()
        result = [_row_to_dict(r) for r in rows]
    return result


def upsert_personal_wordbook(
    user_id: str,
    payload: Dict[str, Any],
    *,
    remote_id: str | None = None,
) -> Dict[str, Any]:
    resolved_remote_id = str(remote_id or payload.get("remote_id") or uuid.uuid4()).strip()
    with get_db_session(autocommit=False) as session:
        existing = session.execute(
            select(PersonalWordbook).where(
                PersonalWordbook.user_id == user_id,
                PersonalWordbook.remote_id == resolved_remote_id,
            )
        ).scalar_one_or_none()

        if existing:
            existing.book_key = payload.get("book_key")
            existing.name = str(payload.get("name") or "默认单词本")
            existing.description = payload.get("description")
            existing.created_at = str(payload.get("created_at") or now_iso())
            existing.updated_at = str(payload.get("updated_at") or now_iso())
            existing.deleted_at = payload.get("deleted_at")
            existing.version = max(int(payload.get("version") or 1), 1)
            existing.device_id = payload.get("device_id")
            existing.sync_state = str(payload.get("sync_state") or "synced")
            existing.last_synced_at = payload.get("last_synced_at") or payload.get("updated_at") or now_iso()
            session.flush()
            row_id = existing.id
        else:
            row_id = str(uuid.uuid4())
            wb = PersonalWordbook(
                id=row_id,
                user_id=user_id,
                remote_id=resolved_remote_id,
                book_key=payload.get("book_key"),
                name=str(payload.get("name") or "默认单词本"),
                description=payload.get("description"),
                created_at=str(payload.get("created_at") or now_iso()),
                updated_at=str(payload.get("updated_at") or now_iso()),
                deleted_at=payload.get("deleted_at"),
                version=max(int(payload.get("version") or 1), 1),
                device_id=payload.get("device_id"),
                sync_state=str(payload.get("sync_state") or "synced"),
                last_synced_at=payload.get("last_synced_at") or payload.get("updated_at") or now_iso(),
            )
            session.add(wb)
            session.flush()

        row = session.execute(
            select(PersonalWordbook).where(
                PersonalWordbook.id == row_id,
                PersonalWordbook.user_id == user_id,
            )
        ).scalar_one_or_none()
        session.commit()
        result = _row_to_dict(row)
    return result


def delete_personal_wordbook(
    user_id: str,
    remote_id: str,
    *,
    deleted_at: str | None = None,
) -> Optional[Dict[str, Any]]:
    resolved_deleted_at = deleted_at or now_iso()
    with get_db_session(autocommit=False) as session:
        existing = session.execute(
            select(PersonalWordbook).where(
                PersonalWordbook.user_id == user_id,
                PersonalWordbook.remote_id == remote_id,
            )
        ).scalar_one_or_none()

        if not existing:
            session.commit()
            return None

        current_version = max(int(existing.version or 1), 1)
        existing.deleted_at = resolved_deleted_at
        existing.updated_at = resolved_deleted_at
        existing.sync_state = "synced"
        existing.last_synced_at = resolved_deleted_at
        existing.version = current_version + 1

        row = session.execute(
            select(PersonalWordbook).where(
                PersonalWordbook.id == existing.id,
                PersonalWordbook.user_id == user_id,
            )
        ).scalar_one_or_none()
        session.commit()
        result = _row_to_dict(row) if row else None
    return result


def _row_to_dict(row) -> Dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": row.id,
        "user_id": row.user_id,
        "remote_id": row.remote_id,
        "book_key": row.book_key,
        "name": row.name,
        "description": row.description,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "deleted_at": row.deleted_at,
        "version": row.version,
        "device_id": row.device_id,
        "sync_state": row.sync_state,
        "last_synced_at": row.last_synced_at,
    }


# ==================== Words ====================

def list_personal_words(
    user_id: str,
    *,
    since_updated_at: str | None = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    resolved_limit = max(int(limit or 100), 1)
    with get_db_session(autocommit=False) as session:
        stmt = select(PersonalWord).where(PersonalWord.user_id == user_id)
        if since_updated_at:
            stmt = stmt.where(PersonalWord.updated_at >= since_updated_at)
        stmt = stmt.order_by(PersonalWord.updated_at.desc(), PersonalWord.id.desc()).limit(resolved_limit)
        rows = session.execute(stmt).scalars().all()
        result = [_word_row_to_dict(r) for r in rows]
    return result


def upsert_personal_word(
    user_id: str,
    payload: Dict[str, Any],
    *,
    remote_id: str | None = None,
) -> Dict[str, Any]:
    resolved_remote_id = str(remote_id or payload.get("remote_id") or uuid.uuid4()).strip()
    with get_db_session(autocommit=False) as session:
        existing = session.execute(
            select(PersonalWord).where(
                PersonalWord.user_id == user_id,
                PersonalWord.remote_id == resolved_remote_id,
            )
        ).scalar_one_or_none()

        if existing:
            existing.word_key = payload.get("word_key")
            existing.word = str(payload.get("word") or "")
            existing.phonetic = payload.get("phonetic")
            existing.meaning = payload.get("meaning")
            existing.example = payload.get("example")
            existing.wordbook_remote_id = payload.get("wordbook_remote_id")
            existing.wordbook_name = payload.get("wordbook_name")
            existing.wordbook_key = payload.get("wordbook_key")
            existing.created_at = str(payload.get("created_at") or now_iso())
            existing.updated_at = str(payload.get("updated_at") or now_iso())
            existing.deleted_at = payload.get("deleted_at")
            existing.version = max(int(payload.get("version") or 1), 1)
            existing.device_id = payload.get("device_id")
            existing.sync_state = str(payload.get("sync_state") or "synced")
            existing.last_synced_at = payload.get("last_synced_at") or payload.get("updated_at") or now_iso()
            session.flush()
            row_id = existing.id
        else:
            row_id = str(uuid.uuid4())
            w = PersonalWord(
                id=row_id,
                user_id=user_id,
                remote_id=resolved_remote_id,
                word_key=payload.get("word_key"),
                word=str(payload.get("word") or ""),
                phonetic=payload.get("phonetic"),
                meaning=payload.get("meaning"),
                example=payload.get("example"),
                wordbook_remote_id=payload.get("wordbook_remote_id"),
                wordbook_name=payload.get("wordbook_name"),
                wordbook_key=payload.get("wordbook_key"),
                created_at=str(payload.get("created_at") or now_iso()),
                updated_at=str(payload.get("updated_at") or now_iso()),
                deleted_at=payload.get("deleted_at"),
                version=max(int(payload.get("version") or 1), 1),
                device_id=payload.get("device_id"),
                sync_state=str(payload.get("sync_state") or "synced"),
                last_synced_at=payload.get("last_synced_at") or payload.get("updated_at") or now_iso(),
            )
            session.add(w)
            session.flush()

        row = session.execute(
            select(PersonalWord).where(
                PersonalWord.id == row_id,
                PersonalWord.user_id == user_id,
            )
        ).scalar_one_or_none()
        session.commit()
        result = _word_row_to_dict(row)
    return result


def delete_personal_word(
    user_id: str,
    remote_id: str,
    *,
    deleted_at: str | None = None,
) -> Optional[Dict[str, Any]]:
    resolved_deleted_at = deleted_at or now_iso()
    with get_db_session(autocommit=False) as session:
        existing = session.execute(
            select(PersonalWord).where(
                PersonalWord.user_id == user_id,
                PersonalWord.remote_id == remote_id,
            )
        ).scalar_one_or_none()

        if not existing:
            session.commit()
            return None

        current_version = max(int(existing.version or 1), 1)
        existing.deleted_at = resolved_deleted_at
        existing.updated_at = resolved_deleted_at
        existing.sync_state = "synced"
        existing.last_synced_at = resolved_deleted_at
        existing.version = current_version + 1

        row = session.execute(
            select(PersonalWord).where(
                PersonalWord.id == existing.id,
                PersonalWord.user_id == user_id,
            )
        ).scalar_one_or_none()
        session.commit()
        result = _word_row_to_dict(row) if row else None
    return result


def _word_row_to_dict(row) -> Dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": row.id,
        "user_id": row.user_id,
        "remote_id": row.remote_id,
        "word_key": row.word_key,
        "word": row.word,
        "phonetic": row.phonetic,
        "meaning": row.meaning,
        "example": row.example,
        "wordbook_remote_id": row.wordbook_remote_id,
        "wordbook_name": row.wordbook_name,
        "wordbook_key": row.wordbook_key,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "deleted_at": row.deleted_at,
        "version": row.version,
        "device_id": row.device_id,
        "sync_state": row.sync_state,
        "last_synced_at": row.last_synced_at,
    }
