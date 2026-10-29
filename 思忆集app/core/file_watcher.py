"""文件变动监控 — 基于 watchdog 的文件系统事件采集。"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from core.behavior_logger import BehaviorLogger
from domain.models.behavior import BehaviorEvent, CATEGORY_FILE, SOURCE_WATCHER, PRIVACY_LOCAL

logger = logging.getLogger("siyi.file_watcher")

_DEBOUNCE_SECONDS = 1.0


class _DebouncedHandler(FileSystemEventHandler):
    """去抖的事件处理器，合并短时间内的重复事件。"""

    def __init__(self, logger: BehaviorLogger, watched_dirs: set[str]) -> None:
        super().__init__()
        self._logger = logger
        self._watched_dirs = watched_dirs
        self._recent: dict[str, float] = {}
        self._lock = threading.Lock()

    def on_created(self, event: FileSystemEvent) -> None:
        self._record(event, "file_created")

    def on_modified(self, event: FileSystemEvent) -> None:
        self._record(event, "file_modified")

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._record(event, "file_deleted")

    def on_moved(self, event: FileSystemEvent) -> None:
        self._record(event, "file_moved")

    def _record(self, event: FileSystemEvent, action: str) -> None:
        if event.is_directory:
            return
        path = os.fsdecode(event.src_path)
        now = time.monotonic()
        with self._lock:
            last = self._recent.get(path, 0.0)
            if now - last < _DEBOUNCE_SECONDS:
                return
            self._recent[path] = now

        rel = self._relpath(path)
        ext = os.path.splitext(path)[1].lower()
        detail: dict[str, Any] = {"path": rel, "extension": ext}
        if action == "file_moved" and hasattr(event, "dest_path"):
            detail["dest_path"] = self._relpath(os.fsdecode(event.dest_path))

        self._logger.record(
            BehaviorEvent(
                category=CATEGORY_FILE,
                action=action,
                source=SOURCE_WATCHER,
                privacy=PRIVACY_LOCAL,
                detail=detail,
            )
        )

    def _relpath(self, path: str) -> str:
        for d in self._watched_dirs:
            try:
                rel = os.path.relpath(path, d)
                if not rel.startswith(".."):
                    return rel
            except ValueError:
                continue
        return path


class FileWatcher:
    """文件变动监控服务。

    监控指定目录的文件创建、修改、删除、移动事件，
    通过 BehaviorLogger 记录行为日志。
    """

    def __init__(self, behavior_logger: BehaviorLogger, watched_dirs: list[str] | None = None) -> None:
        self._logger = behavior_logger
        self._dirs = watched_dirs or []
        self._observer: Any | None = None  # watchdog.observers 无 py.typed，Observer 在 mypy 非有效类型
        self._handler: _DebouncedHandler | None = None

    @property
    def watched_dirs(self) -> list[str]:
        return list(self._dirs)

    def set_watched_dirs(self, directories: list[str]) -> None:
        self._dirs = list(directories)

    def start(self) -> None:
        """启动所有目录的监控。"""
        resolved = [os.path.abspath(d) for d in self._dirs if os.path.isdir(d)]
        if not resolved:
            logger.info("no valid directories to watch")
            return
        self._handler = _DebouncedHandler(self._logger, set(resolved))
        self._observer = Observer()
        for d in resolved:
            self._observer.schedule(self._handler, d, recursive=True)
            logger.info("watching directory: %s", d)
        try:
            self._observer.start()
        except Exception as exc:
            logger.warning("file watcher start failed (Python 3.13 + watchdog compat?): %s", exc)
            return
        self._logger.record(
            BehaviorEvent(
                category="system",
                action="file_watcher_started",
                source=SOURCE_WATCHER,
                detail={"directories": resolved},
            )
        )

    def stop(self) -> None:
        """停止监控。"""
        if self._observer and self._observer.is_alive():
            self._observer.stop()
            self._observer.join(timeout=5)
            logger.info("file watcher stopped")
