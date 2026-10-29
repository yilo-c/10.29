"""行为日志采集器 — 核心服务。

职责：
- 采集文件变动、浏览器行为、App 状态等行为事件
- 持久化到 JSONL 文件（append-only）
- 通过 EventBus 广播实时事件
- 管理会话周期
- 支持按分类/时间范围查询
- 隐私分级控制（local 级别不可外送）
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from domain.models.behavior import BehaviorEvent

logger = logging.getLogger("siyi.behavior")

EVENT_FILE = "behavior_events.jsonl"
ARCHIVE_DIR = "behavior_archives"
DEFAULT_RETENTION_DAYS = 180  # 6 个月
MAX_FILE_SIZE_MB = 50
KEEP_LINES_ON_ROTATION = 5000


class BehaviorLogger:
    """行为日志采集器核心服务。"""

    def __init__(
        self,
        data_dir: str,
        event_bus: Any | None = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        self._data_dir = data_dir
        self._event_bus = event_bus
        self._retention_days = retention_days
        self._event_path = os.path.join(data_dir, EVENT_FILE)
        self._archive_dir = os.path.join(data_dir, ARCHIVE_DIR)
        self._session_id: str = ""
        self._started = False
        self._lock = threading.Lock()

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        """启动日志采集器，创建新会话。"""
        self._session_id = uuid.uuid4().hex[:16]
        self._started = True
        os.makedirs(self._data_dir, exist_ok=True)
        self._write(
            BehaviorEvent(
                category="system",
                action="app_startup",
                source="app_hook",
                detail={"session_id": self._session_id},
            )
        )
        logger.info("BehaviorLogger started, session=%s", self._session_id)

    def stop(self) -> None:
        """停止日志采集器。"""
        if not self._started:
            return
        self._write(
            BehaviorEvent(
                category="system",
                action="app_shutdown",
                source="app_hook",
                detail={"session_id": self._session_id},
            )
        )
        self._started = False
        logger.info("BehaviorLogger stopped, session=%s", self._session_id)

    def record(self, event: BehaviorEvent) -> None:
        """记录一条行为事件。如果采集器未启动则丢弃。"""
        if not self._started:
            return
        if not event.action:
            logger.warning("dropping behavior event without action: %s", event.model_dump())
            return
        self._write(event)

    def _write(self, event: BehaviorEvent) -> None:
        """线程安全地写入事件到 JSONL 并广播到 EventBus。"""
        event = event.model_copy(
            update={
                "id": event.id or uuid.uuid4().hex[:16],
                "timestamp": event.timestamp or datetime.now(timezone.utc).isoformat(),
                "session_id": event.session_id or self._session_id,
            }
        )
        line = json.dumps(event.model_dump(), ensure_ascii=False)

        with self._lock:
            try:
                with open(self._event_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as e:
                logger.error("failed to write behavior event: %s", e)
                return
            self._should_rotate()

        if self._event_bus is not None:
            try:
                from core.events import AppEvent

                self._event_bus.emit(
                    AppEvent(
                        event_type=f"behavior.{event.category}.{event.action}",
                        payload=event.model_dump(),
                        source="behavior_logger",
                    )
                )
            except Exception:
                logger.exception("failed to emit behavior event to bus")

    def query(
        self,
        category: str | None = None,
        action: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """查询行为日志，按条件过滤。"""
        if not os.path.isfile(self._event_path):
            return []
        result: list[dict[str, Any]] = []
        with open(self._event_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if category and ev.get("category") != category:
                    continue
                if action and ev.get("action") != action:
                    continue
                ts = ev.get("timestamp", "")
                if since and ts and ts < since:
                    continue
                if until and ts and ts > until:
                    continue
                result.append(ev)
                if len(result) >= limit:
                    break
        return result

    def archive_old_events(self) -> int:
        """归档超出保留期限的旧事件。返回已归档条数。"""
        with self._lock:
            return self._archive_old_events_locked()

    def _archive_old_events_locked(self) -> int:
        if not os.path.isfile(self._event_path):
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self._retention_days)).isoformat()
        kept: list[str] = []
        archived: list[str] = []
        with open(self._event_path, encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                    if ev.get("timestamp", "") < cutoff:
                        archived.append(line)
                    else:
                        kept.append(line)
                except json.JSONDecodeError:
                    kept.append(line)

        if not archived:
            return 0

        os.makedirs(self._archive_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")
        archive_path = os.path.join(self._archive_dir, f"behavior_events_{stamp}.jsonl")

        tmp = None
        try:
            with open(archive_path, "a", encoding="utf-8") as f:
                f.writelines(archived)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self._event_path), suffix=".jsonl")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(kept)
            os.replace(tmp, self._event_path)
            tmp = None
        except OSError as e:
            logger.error("failed to archive behavior events: %s", e)
            return 0
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    logger.debug("Non-critical error (see details)", exc_info=True)


        logger.info("archived %d behavior events to %s", len(archived), archive_path)
        return len(archived)

    def _should_rotate(self) -> None:
        """如果事件文件过大，执行轮转。（调用方已持锁）"""
        if not os.path.isfile(self._event_path):
            return
        try:
            size_mb = os.path.getsize(self._event_path) / (1024 * 1024)
        except OSError:
            return
        if size_mb < MAX_FILE_SIZE_MB:
            return
        with open(self._event_path, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= KEEP_LINES_ON_ROTATION:
            return
        kept = lines[-KEEP_LINES_ON_ROTATION:]
        archived_line_count = len(lines) - KEEP_LINES_ON_ROTATION
        archived_part = lines[:archived_line_count]
        os.makedirs(self._archive_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_path = os.path.join(self._archive_dir, f"behavior_events_overflow_{stamp}.jsonl")
        tmp = None
        try:
            with open(archive_path, "a", encoding="utf-8") as f:
                f.writelines(archived_part)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self._event_path), suffix=".jsonl")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(kept)
            os.replace(tmp, self._event_path)
            tmp = None
        except OSError as e:
            logger.warning("rotation failed: %s", e)
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    logger.debug("Non-critical error (see details)", exc_info=True)

