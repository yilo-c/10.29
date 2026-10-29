"""Chat session repository with per-session sharded storage.

Layout:
    chat_sessions.json                      # Lightweight index (list of {id, title, ...})
    chat_sessions/
    └── {session_id}/
        ├── meta.json                       # Full session metadata
        ├── chunk_0.json                    # Frozen chunk 0 (CHUNK_SIZE messages), immutable
        ├── chunk_{n}.json                  # Frozen chunk n
        └── latest.json                     # Hot tail (< CHUNK_SIZE messages), append-only
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from typing import Any

from data._atomic_json import atomic_write_json, quarantine_corrupt_file
from ._protocols import *

logger = logging.getLogger("siyi.repositories.chat_session")

# ── Tuning constants ─────────────────────────────────────────────────────────

CHUNK_SIZE = 200  # messages per frozen chunk


class LocalChatSessionRepository:
    """本地聊天会话仓储，每会话分片存储，热/冷分离。

    设计目标：
    - ``add_message()`` 只写 ``latest.json``，不触及其他文件（每 200 条冻结一次）
    - ``get_recent_messages()`` 只读 ``latest.json`` + 最后一个 chunk 尾部
    - ``get_messages_paginated()`` 精确计算需要读哪些 chunk，不读全部
    - ``list_sessions()`` 只读 ``chat_sessions.json`` 索引，与消息存储解耦
    """

    def __init__(self, data_store):
        self.data_store = data_store
        self._migrated = False
        # 容错三件套·③：记录损坏现场隔离失败的路径，这些路径后续写回必须拒写，
        # 禁止覆写损坏的 JSON 文件（禁 warning-only 后继续写）。
        self._blocked_write_paths: set[str] = set()

    # ── Path helpers ──────────────────────────────────────────────────────

    @property
    def _base_dir(self) -> str:
        return os.path.dirname(self.data_store.paths["chat_sessions"])

    @property
    def _sessions_dir(self) -> str:
        path = os.path.join(self._base_dir, "chat_sessions")
        os.makedirs(path, exist_ok=True)
        return path

    def _session_dir(self, session_id: int) -> str:
        path = os.path.join(self._sessions_dir, str(session_id))
        os.makedirs(path, exist_ok=True)
        return path

    def _chunk_path(self, session_id: int, index: int) -> str:
        return os.path.join(self._session_dir(session_id), f"chunk_{index}.json")

    def _latest_path(self, session_id: int) -> str:
        return os.path.join(self._session_dir(session_id), "latest.json")

    def _meta_path(self, session_id: int) -> str:
        return os.path.join(self._session_dir(session_id), "meta.json")

    def _old_segments_dir(self) -> str:
        return os.path.join(self._base_dir, "chat_segments")

    # ── Chunk I/O ─────────────────────────────────────────────────────────

    def _load_json_file(self, path: str, default: list | dict):
        if not os.path.exists(path):
            return default
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            # 容错三件套（QUA-0）：损坏文件先 os.rename 为 <name>.corrupt-<ts>
            # 保全现场（①）；改名成功以空默认值续用、功能不瘫（②）；改名失败则
            # 封锁该路径后续写回并 error 告警（③），禁止 warning-only 后覆写损坏数据。
            self._quarantine_or_block(path, exc)
            return default

    def _quarantine_or_block(self, path: str, exc: Exception) -> None:
        quarantined = quarantine_corrupt_file(path)
        if quarantined:
            logger.error("损坏 JSON %s 已隔离保全至 %s：%s", path, quarantined, exc)
            self._blocked_write_paths.discard(path)
        else:
            logger.error("损坏 JSON %s 隔离失败，封锁该路径写入以保留现场：%s", path, exc)
            self._blocked_write_paths.add(path)

    def _save_json_file(self, path: str, data: list | dict) -> None:
        if path in self._blocked_write_paths:
            msg = f"损坏现场未能隔离，拒绝覆写 {path}（待人工处理）"
            logger.error(msg)
            raise RuntimeError(msg)
        try:
            atomic_write_json(path, data)
        except Exception:
            logger.error("写入 %s 失败，不静默放行（避免丢失/损坏数据）", path, exc_info=True)
            raise

    def _load_latest(self, session_id: int) -> list[dict[str, Any]]:
        return self._load_json_file(self._latest_path(session_id), [])

    def _save_latest(self, session_id: int, messages: list[dict[str, Any]]) -> None:
        self._save_json_file(self._latest_path(session_id), messages)

    def _load_chunk(self, session_id: int, index: int) -> list[dict[str, Any]]:
        return self._load_json_file(self._chunk_path(session_id, index), [])

    def _save_chunk(self, session_id: int, index: int, messages: list[dict[str, Any]]) -> None:
        self._save_json_file(self._chunk_path(session_id, index), messages)

    def _load_meta(self, session_id: int) -> dict[str, Any]:
        return self._load_json_file(self._meta_path(session_id), {})

    def _save_meta(self, session_id: int, meta: dict[str, Any]) -> None:
        self._save_json_file(self._meta_path(session_id), meta)

    def _delete_session_dir(self, session_id: int) -> None:
        path = os.path.join(self._sessions_dir, str(session_id))
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)

    # ── Migration from old chat_segments/ format ─────────────────────────

    def _migrate_if_needed(self) -> None:
        if self._migrated:
            return
        old_dir = self._old_segments_dir()
        if not os.path.isdir(old_dir):
            self._rebuild_index_from_dirs()
            self._migrated = True
            return

        logger.info("Migrating old chat_segments/ → per-session format ...")
        sessions = self._load_sessions_index()
        migrated = 0
        for s in sessions:
            sid = s.get("id")
            if sid is None:
                continue
            seg_count = s.get("segment_count", 0)
            if seg_count == 0:
                continue
            all_msgs: list[dict[str, Any]] = []
            for idx in range(seg_count):
                old_path = os.path.join(old_dir, f"{sid}_{idx}.json")
                if os.path.exists(old_path):
                    try:
                        with open(old_path, encoding="utf-8") as f:
                            all_msgs.extend(json.load(f))
                    except Exception:
                        pass
            if all_msgs:
                self._write_all_messages(sid, all_msgs)
                migrated += 1

        shutil.rmtree(old_dir, ignore_errors=True)
        # Clean legacy fields from index
        for s in sessions:
            s.pop("messages", None)
            s.pop("segment_count", None)
        self._save_sessions_index(sessions)
        self._rebuild_index_from_dirs()
        self._migrated = True
        logger.info("Migrated %s sessions to per-session format", migrated)

    # ── Index self-healing ───────────────────────────────────────────────

    def _rebuild_index_from_dirs(self) -> None:
        """扫描 chat_sessions/ 目录，把索引中缺失的会话回填进索引。

        历史 bug：部分写入路径（含旧版 chat.save_messages）只写了会话目录而
        未维护索引，导致 list_sessions() 看不见这些会话（用户侧表现为
        “对话记录丢失”）。本方法幂等，仅在进程首次访问时执行一次。
        """
        try:
            sessions = self._load_sessions_index()
            known = {s.get("id") for s in sessions}
            repaired = 0
            for name in os.listdir(self._sessions_dir):
                if not name.isdigit():
                    continue  # 非数字目录（历史脏数据）无法用 int sid 索引，跳过
                sid = int(name)
                if sid in known:
                    continue
                meta = self._load_meta(sid)
                # 直读分片而非 get_messages()：后者会重入 _migrate_if_needed 造成递归
                messages: list[dict[str, Any]] = []
                for idx in range(meta.get("chunk_count", 0)):
                    messages.extend(self._load_chunk(sid, idx))
                messages.extend(self._load_latest(sid))
                if not messages and not meta:
                    continue
                title = meta.get("title") or ""
                if not title:
                    for m in messages:
                        role = m.get("role") or m.get("speaker") or ""
                        content = m.get("content") or m.get("message") or ""
                        if role in ("user", "你") and str(content).strip():
                            title = str(content)[:50]
                            break
                updated_iso = meta.get("updated_at") or ""
                if not updated_iso and messages:
                    ts = messages[-1].get("timestamp")
                    try:
                        if isinstance(ts, (int, float)):
                            # 前端时间戳为毫秒 epoch
                            updated_iso = datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat()
                        elif isinstance(ts, str) and ts:
                            updated_iso = ts
                    except Exception:
                        updated_iso = ""
                now = datetime.now(timezone.utc).isoformat()
                entry = {
                    "id": sid,
                    "title": title or "新对话",
                    "created_at": meta.get("created_at") or updated_iso or now,
                    "updated_at": updated_iso or now,
                    "message_count": len(messages) or meta.get("message_count", 0),
                }
                if meta.get("client_id"):
                    entry["client_id"] = meta["client_id"]
                sessions.append(entry)
                self._save_meta(sid, {**meta, **{k: v for k, v in entry.items() if k != "id"}})
                repaired += 1
            if repaired:
                self._save_sessions_index(sessions)
                logger.info("rebuild_index_from_dirs: 回填 %d 个孤立会话到索引", repaired)
        except Exception:
            logger.exception("rebuild_index_from_dirs failed")

    def _write_all_messages(self, session_id: int, messages: list[dict[str, Any]]) -> None:
        """Replace all messages for a session — freeze full chunks + latest tail."""
        self._delete_session_dir(session_id)
        os.makedirs(self._session_dir(session_id), exist_ok=True)

        if not messages:
            self._save_latest(session_id, [])
            self._save_meta(session_id, {"chunk_count": 0, "message_count": 0})
            return

        chunks = [messages[i:i + CHUNK_SIZE] for i in range(0, len(messages), CHUNK_SIZE)]
        for idx, chunk in enumerate(chunks[:-1]):
            self._save_chunk(session_id, idx, chunk)
        self._save_latest(session_id, chunks[-1])

        self._save_meta(session_id, {
            "chunk_count": len(chunks) - 1,
            "message_count": len(messages),
        })

    # ── Session index CRUD (chat_sessions.json) ──────────────────────────

    def _load_sessions_index(self) -> list[dict[str, Any]]:
        return self.data_store.load_json("chat_sessions", []) if self.data_store is not None else []

    def _save_sessions_index(self, sessions: list[dict[str, Any]]) -> None:
        if self.data_store is None:
            return
        self.data_store.save_json("chat_sessions", sessions)

    def _update_index(self, session_id: int, **changes) -> dict[str, Any] | None:
        """Atomically update fields on a single session in the index."""
        sessions = self._load_sessions_index()
        for s in sessions:
            if s.get("id") == session_id:
                s.update(changes)
                self._save_sessions_index(sessions)
                return s
        return None

    def list_sessions(self) -> list[dict[str, Any]]:
        self._migrate_if_needed()
        sessions = self._load_sessions_index()
        sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        for s in sessions:
            s.pop("messages", None)
            s.pop("segment_count", None)
        return sessions

    def get_session(self, session_id: int) -> dict[str, Any] | None:
        self._migrate_if_needed()
        # Per-session meta is the source of truth (richer)
        meta = self._load_meta(session_id)
        if meta:
            return {"id": session_id, **meta}
        # Fallback to index
        for s in self._load_sessions_index():
            if s.get("id") == session_id:
                s.pop("messages", None)
                s.pop("segment_count", None)
                return dict(s)
        return None

    def create_session(self, title: str = "新对话") -> int:
        self._migrate_if_needed()
        sessions = self._load_sessions_index()
        new_id = self.data_store.next_id(sessions) if self.data_store else 1
        now = datetime.now(timezone.utc).isoformat()
        entry = {"id": new_id, "title": title, "created_at": now, "updated_at": now, "message_count": 0}
        sessions.append(entry)
        self._save_sessions_index(sessions)
        self._save_meta(new_id, dict(entry))
        return new_id

    def update_session(self, session_id: int, new_info: dict[str, Any]) -> None:
        self._migrate_if_needed()
        self._update_index(session_id, **new_info)
        meta = self._load_meta(session_id)
        meta.update(new_info)
        self._save_meta(session_id, meta)

    def delete_session(self, session_id: int) -> None:
        self._migrate_if_needed()
        sessions = self._load_sessions_index()
        sessions = [s for s in sessions if s.get("id") != session_id]
        self._save_sessions_index(sessions)
        self._delete_session_dir(session_id)

    # ── Message operations ───────────────────────────────────────────────

    def sync_messages(self, session_id: int, messages: list[dict[str, Any]]) -> None:
        """Replace all messages for a session."""
        self._migrate_if_needed()
        self._write_all_messages(session_id, messages)
        self._update_index(session_id, message_count=len(messages),
                           updated_at=datetime.now(timezone.utc).isoformat())

    def add_message(self, session_id: int, speaker: str, message: str) -> None:
        """Append a single message — only rewrites ``latest.json`` (O(1) file write)."""
        self._migrate_if_needed()

        msg = {"speaker": speaker, "message": message,
               "timestamp": datetime.now(timezone.utc).isoformat()}

        latest = self._load_latest(session_id)
        latest.append(msg)
        meta = self._load_meta(session_id)

        if len(latest) >= CHUNK_SIZE:
            # Freeze → new chunk, start fresh latest
            chunk_idx = meta.get("chunk_count", 0)
            self._save_chunk(session_id, chunk_idx, latest)
            self._save_latest(session_id, [])
            meta["chunk_count"] = chunk_idx + 1
        else:
            self._save_latest(session_id, latest)

        meta["message_count"] = meta.get("message_count", 0) + 1
        self._save_meta(session_id, meta)

        now = datetime.now(timezone.utc).isoformat()
        self._update_index(session_id, message_count=meta["message_count"], updated_at=now)

        # Auto-generate title from first user message
        if speaker == "你":
            idx_entry = self._update_index(session_id)  # no-op update to fetch the entry
            if idx_entry and idx_entry.get("title", "新对话") == "新对话":
                self.update_session(session_id, {"title": message[:50]})

    def get_messages(self, session_id: int) -> list[dict[str, Any]]:
        """Load all messages — chunks in order + latest tail.

        Also handles migration of old inline ``messages`` stored directly in the
        session index (pre-sharding format).
        """
        self._migrate_if_needed()
        meta = self._load_meta(session_id)

        # Migrate from old inline messages in index (if any)
        if not meta:
            sessions = self._load_sessions_index()
            for s in sessions:
                if s.get("id") == session_id and "messages" in s:
                    msgs = s.pop("messages")
                    self._save_sessions_index(sessions)
                    self._write_all_messages(session_id, msgs)
                    return msgs

        chunk_count = meta.get("chunk_count", 0)
        messages: list[dict[str, Any]] = []
        for idx in range(chunk_count):
            messages.extend(self._load_chunk(session_id, idx))
        messages.extend(self._load_latest(session_id))
        return messages

    def get_recent_messages(self, session_id: int, count: int = CHUNK_SIZE) -> list[dict[str, Any]]:
        """Load only the most recent N messages — avoids loading all chunks.

        Reads ``latest.json`` first, then walks chunks backward until
        ``count`` is satisfied.
        """
        self._migrate_if_needed()
        latest = self._load_latest(session_id)
        if len(latest) >= count:
            return latest[-count:]

        meta = self._load_meta(session_id)
        chunk_count = meta.get("chunk_count", 0)
        if chunk_count == 0:
            return list(latest)

        result = list(latest)
        need = count - len(latest)

        for idx in range(chunk_count - 1, -1, -1):
            if need <= 0:
                break
            chunk = self._load_chunk(session_id, idx)
            if len(chunk) >= need:
                result = chunk[-need:] + result
                need = 0
            else:
                result = list(chunk) + result
                need -= len(chunk)

        return result

    def get_messages_paginated(self, session_id: int, offset: int = 0,
                               limit: int = CHUNK_SIZE) -> list[dict[str, Any]]:
        """Load a page of messages — only reads chunks that contain the requested range."""
        self._migrate_if_needed()
        meta = self._load_meta(session_id)
        chunk_count = meta.get("chunk_count", 0)
        total_frozen = chunk_count * CHUNK_SIZE
        latest = self._load_latest(session_id)
        total = total_frozen + len(latest)

        if offset >= total:
            return []

        end = min(offset + limit, total)
        result: list[dict[str, Any]] = []

        # Chunks that intersect [offset, end)
        first_chunk = offset // CHUNK_SIZE
        last_chunk = (end - 1) // CHUNK_SIZE

        for idx in range(first_chunk, min(last_chunk + 1, chunk_count)):
            chunk = self._load_chunk(session_id, idx)
            lo = max(0, offset - idx * CHUNK_SIZE)
            hi = min(len(chunk), end - idx * CHUNK_SIZE)
            if lo < hi:
                result.extend(chunk[lo:hi])

        # Tail from latest
        if end > total_frozen:
            lo = max(0, offset - total_frozen)
            result.extend(latest[lo:end - total_frozen])

        return result
