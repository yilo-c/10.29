# -*- coding: utf-8 -*-
"""个人数据 CRUD（笔记 / 任务 / 新闻收藏 / 翻译历史）。

已从原生 sqlite3 迁移到 SQLAlchemy 2.0 ORM。
使用泛型工厂函数消除重复的 upsert/delete/list 模式。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Dict, List, Optional, TypeVar

from .session import get_db_session, now_iso
from ..models.personal import (
    PersonalNote,
    PersonalTask,
    PersonalNewsItem,
    PersonalTranslationHistory,
)
from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

M = TypeVar("M")


def _list_entities(
    model_cls: type[M],
    user_id: str,
    normalize_fn: Callable[[M], dict],
    *,
    since_updated_at: str | None = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    resolved_limit = max(int(limit or 100), 1)
    with get_db_session(autocommit=False) as session:
        stmt = select(model_cls).where(model_cls.user_id == user_id)  # type: ignore[attr-defined]
        if since_updated_at:
            stmt = stmt.where(model_cls.updated_at >= since_updated_at)  # type: ignore[attr-defined]
        stmt = stmt.order_by(model_cls.updated_at.desc(), model_cls.id.desc()).limit(resolved_limit)  # type: ignore[attr-defined]
        rows = session.execute(stmt).scalars().all()
        return [normalize_fn(row) for row in rows]


def _upsert_entity(
    model_cls: type[M],
    user_id: str,
    payload: Dict[str, Any],
    *,
    remote_id: str | None = None,
    update_fn: Callable[[M, Dict[str, Any], SASession], int | str | None],
    create_fn: Callable[[str, str, str, Dict[str, Any]], M],
    normalize_fn: Callable[[M], dict],
) -> Dict[str, Any]:
    resolved_remote_id = str(remote_id or payload.get("remote_id") or uuid.uuid4()).strip()
    with get_db_session(autocommit=False) as session:
        existing = session.execute(
            select(model_cls).where(
                model_cls.user_id == user_id,  # type: ignore[attr-defined]
                model_cls.remote_id == resolved_remote_id,  # type: ignore[attr-defined]
            )
        ).scalar_one_or_none()

        if existing:
            row_id = update_fn(existing, payload, session)
            if row_id is None:
                row_id = existing.id  # type: ignore[attr-defined]
        else:
            row_id = str(uuid.uuid4())
            entity = create_fn(row_id, user_id, resolved_remote_id, payload)
            session.add(entity)
            session.flush()

        row = session.execute(
            select(model_cls).where(
                model_cls.id == row_id,  # type: ignore[attr-defined]
                model_cls.user_id == user_id,  # type: ignore[attr-defined]
            )
        ).scalar_one_or_none()
        session.commit()
        return normalize_fn(row)


def _delete_entity(
    model_cls: type[M],
    user_id: str,
    remote_id: str,
    normalize_fn: Callable[[Any], dict],
    *,
    deleted_at: str | None = None,
    extra_cleanup: Callable[[M], None] | None = None,
) -> Optional[Dict[str, Any]]:
    resolved_deleted_at = deleted_at or now_iso()
    with get_db_session(autocommit=False) as session:
        existing = session.execute(
            select(model_cls).where(
                model_cls.user_id == user_id,  # type: ignore[attr-defined]
                model_cls.remote_id == remote_id,  # type: ignore[attr-defined]
            )
        ).scalar_one_or_none()

        if not existing:
            session.commit()
            return None

        current_version = max(int(existing.version or 1), 1)  # type: ignore[attr-defined]
        existing.deleted_at = resolved_deleted_at  # type: ignore[attr-defined]
        existing.updated_at = resolved_deleted_at  # type: ignore[attr-defined]
        existing.sync_state = "synced"  # type: ignore[attr-defined]
        existing.last_synced_at = resolved_deleted_at  # type: ignore[attr-defined]
        existing.version = current_version + 1  # type: ignore[attr-defined]
        if extra_cleanup:
            extra_cleanup(existing)

        row = session.execute(
            select(model_cls).where(
                model_cls.id == existing.id,  # type: ignore[attr-defined]
                model_cls.user_id == user_id,  # type: ignore[attr-defined]
            )
        ).scalar_one_or_none()
        session.commit()
        return normalize_fn(row) if row else None


# ==================== Personal Notes ====================


def _update_note(existing: PersonalNote, payload: dict, _session: SASession) -> None:
    existing.title = str(payload.get("title") or "未命名笔记")
    existing.content = str(payload.get("content") or "")
    existing.tags_json = json.dumps(payload.get("tags") or [], ensure_ascii=False)
    existing.category = payload.get("category")
    existing.folder_path = str(payload.get("folder_path") or existing.folder_path or "/")
    if "favorite" in payload:
        existing.favorite = 1 if payload["favorite"] else 0
    existing.related_news_url = payload.get("related_news_url")
    existing.linked_task_id = payload.get("linked_task_id")
    existing.created_at = str(payload.get("created_at") or existing.created_at or now_iso())
    existing.updated_at = str(payload.get("updated_at") or now_iso())
    existing.deleted_at = payload.get("deleted_at")
    existing.version = max(int(payload.get("version") or 1), 1)
    existing.device_id = payload.get("device_id")
    existing.sync_state = str(payload.get("sync_state") or "synced")
    existing.last_synced_at = payload.get("last_synced_at") or payload.get("updated_at") or now_iso()
    _session.flush()


def _create_note(row_id: str, user_id: str, remote_id: str, payload: dict) -> PersonalNote:
    return PersonalNote(
        id=row_id,
        user_id=user_id,
        remote_id=remote_id,
        title=str(payload.get("title") or "未命名笔记"),
        content=str(payload.get("content") or ""),
        tags_json=json.dumps(payload.get("tags") or [], ensure_ascii=False),
        category=payload.get("category"),
        folder_path=str(payload.get("folder_path") or "/"),
        favorite=1 if bool(payload.get("favorite", False)) else 0,
        related_news_url=payload.get("related_news_url"),
        linked_task_id=payload.get("linked_task_id"),
        created_at=str(payload.get("created_at") or now_iso()),
        updated_at=str(payload.get("updated_at") or now_iso()),
        deleted_at=payload.get("deleted_at"),
        version=max(int(payload.get("version") or 1), 1),
        device_id=payload.get("device_id"),
        sync_state=str(payload.get("sync_state") or "synced"),
        last_synced_at=payload.get("last_synced_at") or payload.get("updated_at") or now_iso(),
    )


def _normalize_personal_note_row(row) -> Dict[str, Any]:
    if row is None:
        return {}
    item = {
        "id": row.id,
        "user_id": row.user_id,
        "remote_id": row.remote_id,
        "title": row.title,
        "content": row.content,
        "category": row.category,
        "folder_path": row.folder_path,
        "related_news_url": row.related_news_url,
        "linked_task_id": row.linked_task_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "deleted_at": row.deleted_at,
        "version": row.version,
        "device_id": row.device_id,
        "sync_state": row.sync_state,
        "last_synced_at": row.last_synced_at,
        "favorite": bool(row.favorite),
    }
    try:
        item["tags"] = json.loads(row.tags_json or "[]")
    except Exception:
        item["tags"] = []
    return item


def get_personal_note(user_id: str, remote_id: str) -> Optional[Dict[str, Any]]:
    with get_db_session(autocommit=False) as session:
        stmt = select(PersonalNote).where(
            PersonalNote.user_id == user_id,
            PersonalNote.remote_id == remote_id,
        )
        row = session.execute(stmt).scalar_one_or_none()
        return _normalize_personal_note_row(row) if row else None


def list_personal_notes(
    user_id: str,
    *,
    since_updated_at: str | None = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    return _list_entities(PersonalNote, user_id, _normalize_personal_note_row, since_updated_at=since_updated_at, limit=limit)


def upsert_personal_note(
    user_id: str,
    payload: Dict[str, Any],
    *,
    remote_id: str | None = None,
) -> Dict[str, Any]:
    return _upsert_entity(PersonalNote, user_id, payload, remote_id=remote_id, update_fn=_update_note, create_fn=_create_note, normalize_fn=_normalize_personal_note_row)


def delete_personal_note(
    user_id: str,
    remote_id: str,
    *,
    deleted_at: str | None = None,
) -> Optional[Dict[str, Any]]:
    return _delete_entity(PersonalNote, user_id, remote_id, _normalize_personal_note_row, deleted_at=deleted_at)


# ==================== Personal Tasks ====================


def _update_task(existing: PersonalTask, payload: dict, _session: SASession) -> None:
    existing.name = str(payload.get("name") or "未命名任务")
    existing.note = payload.get("note")
    existing.is_completed = 1 if bool(payload.get("is_completed", payload.get("completed", False))) else 0
    existing.reminder_time = payload.get("reminder_time")
    existing.linked_note_id = payload.get("linked_note_id")
    existing.created_at = str(payload.get("created_at") or existing.created_at or now_iso())
    existing.updated_at = str(payload.get("updated_at") or now_iso())
    existing.deleted_at = payload.get("deleted_at")
    existing.version = max(int(payload.get("version") or 1), 1)
    existing.device_id = payload.get("device_id")
    existing.sync_state = str(payload.get("sync_state") or "synced")
    existing.last_synced_at = payload.get("last_synced_at") or payload.get("updated_at") or now_iso()
    _session.flush()


def _create_task(row_id: str, user_id: str, remote_id: str, payload: dict) -> PersonalTask:
    return PersonalTask(
        id=row_id,
        user_id=user_id,
        remote_id=remote_id,
        name=str(payload.get("name") or "未命名任务"),
        note=payload.get("note"),
        is_completed=1 if bool(payload.get("is_completed", payload.get("completed", False))) else 0,
        reminder_time=payload.get("reminder_time"),
        linked_note_id=payload.get("linked_note_id"),
        created_at=str(payload.get("created_at") or now_iso()),
        updated_at=str(payload.get("updated_at") or now_iso()),
        deleted_at=payload.get("deleted_at"),
        version=max(int(payload.get("version") or 1), 1),
        device_id=payload.get("device_id"),
        sync_state=str(payload.get("sync_state") or "synced"),
        last_synced_at=payload.get("last_synced_at") or payload.get("updated_at") or now_iso(),
    )


def _normalize_personal_task_row(row) -> Dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": row.id,
        "user_id": row.user_id,
        "remote_id": row.remote_id,
        "name": row.name,
        "note": row.note,
        "reminder_time": row.reminder_time,
        "linked_note_id": row.linked_note_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "deleted_at": row.deleted_at,
        "version": row.version,
        "device_id": row.device_id,
        "sync_state": row.sync_state,
        "last_synced_at": row.last_synced_at,
        "is_completed": bool(row.is_completed),
        "completed": bool(row.is_completed),
    }


def get_personal_task(user_id: str, remote_id: str) -> Optional[Dict[str, Any]]:
    with get_db_session(autocommit=False) as session:
        stmt = select(PersonalTask).where(
            PersonalTask.user_id == user_id,
            PersonalTask.remote_id == remote_id,
        )
        row = session.execute(stmt).scalar_one_or_none()
        return _normalize_personal_task_row(row) if row else None


def list_personal_tasks(
    user_id: str,
    *,
    since_updated_at: str | None = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    return _list_entities(PersonalTask, user_id, _normalize_personal_task_row, since_updated_at=since_updated_at, limit=limit)


def upsert_personal_task(
    user_id: str,
    payload: Dict[str, Any],
    *,
    remote_id: str | None = None,
) -> Dict[str, Any]:
    return _upsert_entity(PersonalTask, user_id, payload, remote_id=remote_id, update_fn=_update_task, create_fn=_create_task, normalize_fn=_normalize_personal_task_row)


def delete_personal_task(
    user_id: str,
    remote_id: str,
    *,
    deleted_at: str | None = None,
) -> Optional[Dict[str, Any]]:
    return _delete_entity(PersonalTask, user_id, remote_id, _normalize_personal_task_row, deleted_at=deleted_at)


# ==================== Personal News ====================


def _update_news_item(existing: PersonalNewsItem, payload: dict, _session: SASession) -> None:
    existing.item_key = payload.get("key") or payload.get("url") or payload.get("title")
    existing.title = str(payload.get("title") or "未命名新闻收藏")
    existing.url = payload.get("url")
    existing.summary = payload.get("summary")
    existing.content = payload.get("content")
    existing.source = payload.get("source")
    existing.category = payload.get("category")
    existing.published_at = payload.get("published_at")
    existing.image_url = payload.get("image_url")
    if "is_favorite" in payload:
        existing.is_favorite = 1 if payload["is_favorite"] else 0
    existing.created_at = str(payload.get("created_at") or now_iso())
    existing.updated_at = str(payload.get("updated_at") or now_iso())
    existing.deleted_at = payload.get("deleted_at")
    existing.version = max(int(payload.get("version") or 1), 1)
    existing.device_id = payload.get("device_id")
    existing.sync_state = str(payload.get("sync_state") or "synced")
    existing.last_synced_at = payload.get("last_synced_at") or payload.get("updated_at") or now_iso()
    _session.flush()


def _create_news_item(row_id: str, user_id: str, remote_id: str, payload: dict) -> PersonalNewsItem:
    return PersonalNewsItem(
        id=row_id,
        user_id=user_id,
        remote_id=remote_id,
        item_key=payload.get("key") or payload.get("url") or payload.get("title"),
        title=str(payload.get("title") or "未命名新闻收藏"),
        url=payload.get("url"),
        summary=payload.get("summary"),
        content=payload.get("content"),
        source=payload.get("source"),
        category=payload.get("category"),
        published_at=payload.get("published_at"),
        image_url=payload.get("image_url"),
        is_favorite=1 if bool(payload.get("is_favorite", True)) else 0,
        created_at=str(payload.get("created_at") or now_iso()),
        updated_at=str(payload.get("updated_at") or now_iso()),
        deleted_at=payload.get("deleted_at"),
        version=max(int(payload.get("version") or 1), 1),
        device_id=payload.get("device_id"),
        sync_state=str(payload.get("sync_state") or "synced"),
        last_synced_at=payload.get("last_synced_at") or payload.get("updated_at") or now_iso(),
    )


def _normalize_personal_news_item_row(row) -> Dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": row.id,
        "user_id": row.user_id,
        "remote_id": row.remote_id,
        "item_key": row.item_key,
        "title": row.title,
        "url": row.url,
        "summary": row.summary,
        "content": row.content,
        "source": row.source,
        "category": row.category,
        "published_at": row.published_at,
        "image_url": row.image_url,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "deleted_at": row.deleted_at,
        "version": row.version,
        "device_id": row.device_id,
        "sync_state": row.sync_state,
        "last_synced_at": row.last_synced_at,
        "is_favorite": bool(row.is_favorite),
        "key": row.item_key or row.url or row.title,
    }


def list_personal_news_items(
    user_id: str,
    *,
    since_updated_at: str | None = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    return _list_entities(PersonalNewsItem, user_id, _normalize_personal_news_item_row, since_updated_at=since_updated_at, limit=limit)


def upsert_personal_news_item(
    user_id: str,
    payload: Dict[str, Any],
    *,
    remote_id: str | None = None,
) -> Dict[str, Any]:
    return _upsert_entity(PersonalNewsItem, user_id, payload, remote_id=remote_id, update_fn=_update_news_item, create_fn=_create_news_item, normalize_fn=_normalize_personal_news_item_row)


def delete_personal_news_item(
    user_id: str,
    remote_id: str,
    *,
    deleted_at: str | None = None,
) -> Optional[Dict[str, Any]]:
    def _cleanup(item: PersonalNewsItem) -> None:
        item.is_favorite = 0
    return _delete_entity(PersonalNewsItem, user_id, remote_id, _normalize_personal_news_item_row, deleted_at=deleted_at, extra_cleanup=_cleanup)


# ==================== Personal Translations ====================


def _update_translation(existing: PersonalTranslationHistory, payload: dict, _session: SASession) -> None:
    raw_result = payload.get("raw_result")
    raw_result_json = json.dumps(raw_result, ensure_ascii=False) if raw_result is not None else None
    existing.history_key = payload.get("history_key")
    existing.source_text = str(payload.get("source_text") or "")
    existing.translated_text = str(payload.get("translated_text") or "")
    existing.from_lang = str(payload.get("from_lang") or "auto")
    existing.to_lang = str(payload.get("to_lang") or "zh-CHS")
    existing.raw_result_json = raw_result_json
    existing.created_at = str(payload.get("created_at") or now_iso())
    existing.updated_at = str(payload.get("updated_at") or now_iso())
    existing.deleted_at = payload.get("deleted_at")
    existing.version = max(int(payload.get("version") or 1), 1)
    existing.device_id = payload.get("device_id")
    existing.sync_state = str(payload.get("sync_state") or "synced")
    existing.last_synced_at = payload.get("last_synced_at") or payload.get("updated_at") or now_iso()
    _session.flush()


def _create_translation(row_id: str, user_id: str, remote_id: str, payload: dict) -> PersonalTranslationHistory:
    raw_result = payload.get("raw_result")
    raw_result_json = json.dumps(raw_result, ensure_ascii=False) if raw_result is not None else None
    return PersonalTranslationHistory(
        id=row_id,
        user_id=user_id,
        remote_id=remote_id,
        history_key=payload.get("history_key"),
        source_text=str(payload.get("source_text") or ""),
        translated_text=str(payload.get("translated_text") or ""),
        from_lang=str(payload.get("from_lang") or "auto"),
        to_lang=str(payload.get("to_lang") or "zh-CHS"),
        raw_result_json=raw_result_json,
        created_at=str(payload.get("created_at") or now_iso()),
        updated_at=str(payload.get("updated_at") or now_iso()),
        deleted_at=payload.get("deleted_at"),
        version=max(int(payload.get("version") or 1), 1),
        device_id=payload.get("device_id"),
        sync_state=str(payload.get("sync_state") or "synced"),
        last_synced_at=payload.get("last_synced_at") or payload.get("updated_at") or now_iso(),
    )


def _normalize_personal_translation_row(row) -> Dict[str, Any]:
    if row is None:
        return {}
    item = {
        "id": row.id,
        "user_id": row.user_id,
        "remote_id": row.remote_id,
        "history_key": row.history_key,
        "source_text": row.source_text,
        "translated_text": row.translated_text,
        "from_lang": row.from_lang,
        "to_lang": row.to_lang,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "deleted_at": row.deleted_at,
        "version": row.version,
        "device_id": row.device_id,
        "sync_state": row.sync_state,
        "last_synced_at": row.last_synced_at,
    }
    raw_json = row.raw_result_json
    if raw_json:
        try:
            item["raw_result"] = json.loads(raw_json)
        except Exception:
            item["raw_result"] = raw_json
    else:
        item["raw_result"] = None
    return item


def list_personal_translation_history(
    user_id: str,
    *,
    since_updated_at: str | None = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    return _list_entities(PersonalTranslationHistory, user_id, _normalize_personal_translation_row, since_updated_at=since_updated_at, limit=limit)


def upsert_personal_translation_record(
    user_id: str,
    payload: Dict[str, Any],
    *,
    remote_id: str | None = None,
) -> Dict[str, Any]:
    return _upsert_entity(PersonalTranslationHistory, user_id, payload, remote_id=remote_id, update_fn=_update_translation, create_fn=_create_translation, normalize_fn=_normalize_personal_translation_row)


def delete_personal_translation_record(
    user_id: str,
    remote_id: str,
    *,
    deleted_at: str | None = None,
) -> Optional[Dict[str, Any]]:
    return _delete_entity(PersonalTranslationHistory, user_id, remote_id, _normalize_personal_translation_row, deleted_at=deleted_at)
