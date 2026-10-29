# -*- coding: utf-8 -*-
"""SQLAlchemy ORM 模型 - 个人数据表

这些表目前由 raw SQL CRUD 函数驱动（api/db/personal.py & vocabulary.py），
ORM 模型仅用于 Base.metadata.create_all() 建表。
"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, Integer

from .base import Base


class PersonalNote(Base):
    __tablename__ = "personal_notes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    remote_id: Mapped[str] = mapped_column(Text, unique=True, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags_json: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    category: Mapped[str] = mapped_column(Text, nullable=True)
    folder_path: Mapped[str] = mapped_column(String, nullable=False, default="/")
    favorite: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    related_news_url: Mapped[str] = mapped_column(Text, nullable=True)
    linked_task_id: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    deleted_at: Mapped[str] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    device_id: Mapped[str] = mapped_column(Text, nullable=True)
    sync_state: Mapped[str] = mapped_column(String, nullable=False, default="synced")
    last_synced_at: Mapped[str] = mapped_column(Text, nullable=True)


class PersonalTask(Base):
    __tablename__ = "personal_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    remote_id: Mapped[str] = mapped_column(Text, unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=True)
    is_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reminder_time: Mapped[str] = mapped_column(Text, nullable=True)
    linked_note_id: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    deleted_at: Mapped[str] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    device_id: Mapped[str] = mapped_column(Text, nullable=True)
    sync_state: Mapped[str] = mapped_column(String, nullable=False, default="synced")
    last_synced_at: Mapped[str] = mapped_column(Text, nullable=True)


class PersonalNewsItem(Base):
    __tablename__ = "personal_news_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    remote_id: Mapped[str] = mapped_column(Text, unique=True, nullable=True)
    item_key: Mapped[str] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(Text, nullable=True)
    published_at: Mapped[str] = mapped_column(Text, nullable=True)
    image_url: Mapped[str] = mapped_column(Text, nullable=True)
    is_favorite: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    deleted_at: Mapped[str] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    device_id: Mapped[str] = mapped_column(Text, nullable=True)
    sync_state: Mapped[str] = mapped_column(String, nullable=False, default="synced")
    last_synced_at: Mapped[str] = mapped_column(Text, nullable=True)


class PersonalTranslationHistory(Base):
    __tablename__ = "personal_translation_history"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    remote_id: Mapped[str] = mapped_column(Text, unique=True, nullable=True)
    history_key: Mapped[str] = mapped_column(Text, nullable=True)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    translated_text: Mapped[str] = mapped_column(Text, nullable=False)
    from_lang: Mapped[str] = mapped_column(String, nullable=False)
    to_lang: Mapped[str] = mapped_column(String, nullable=False)
    raw_result_json: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    deleted_at: Mapped[str] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    device_id: Mapped[str] = mapped_column(Text, nullable=True)
    sync_state: Mapped[str] = mapped_column(String, nullable=False, default="synced")
    last_synced_at: Mapped[str] = mapped_column(Text, nullable=True)


class PersonalWordbook(Base):
    __tablename__ = "personal_wordbooks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    remote_id: Mapped[str] = mapped_column(Text, unique=True, nullable=True)
    book_key: Mapped[str] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    deleted_at: Mapped[str] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    device_id: Mapped[str] = mapped_column(Text, nullable=True)
    sync_state: Mapped[str] = mapped_column(String, nullable=False, default="synced")
    last_synced_at: Mapped[str] = mapped_column(Text, nullable=True)


class PersonalWord(Base):
    __tablename__ = "personal_words"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    remote_id: Mapped[str] = mapped_column(Text, unique=True, nullable=True)
    word_key: Mapped[str] = mapped_column(Text, nullable=True)
    word: Mapped[str] = mapped_column(String, nullable=False)
    phonetic: Mapped[str] = mapped_column(Text, nullable=True)
    meaning: Mapped[str] = mapped_column(Text, nullable=True)
    example: Mapped[str] = mapped_column(Text, nullable=True)
    wordbook_remote_id: Mapped[str] = mapped_column(Text, nullable=True)
    wordbook_name: Mapped[str] = mapped_column(Text, nullable=True)
    wordbook_key: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    deleted_at: Mapped[str] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    device_id: Mapped[str] = mapped_column(Text, nullable=True)
    sync_state: Mapped[str] = mapped_column(String, nullable=False, default="synced")
    last_synced_at: Mapped[str] = mapped_column(Text, nullable=True)
