"""local_agent.session — 本地会话管理。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = ""
    channel: str = "cli"
    messages: list[Message] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_active_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def add_message(self, role: str, content: str, **meta: Any) -> Message:
        msg = Message(role=role, content=content, metadata=meta)
        self.messages.append(msg)
        self.last_active_at = msg.timestamp
        return msg

    def get_history(self, limit: int = 50) -> list[dict[str, str]]:
        tail = self.messages[-limit:]
        return [{"role": m.role, "content": m.content} for m in tail]


class SessionManager:
    """线程安全的简易会话管理器。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str | None = None, **kwargs: Any) -> Session:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        session = Session(session_id=session_id or str(uuid.uuid4()), **kwargs)
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def active_count(self) -> int:
        return len(self._sessions)
