from __future__ import annotations

import gzip
import json
import logging
import os
import re
import tempfile
import traceback
import zipfile
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("siyi.observability")

# P2-2: 复用 exception_handler 的脱敏逻辑（避免逻辑重复）
from core.exception_handler import sanitize_value as _sanitize_value_shared, REDACT_HINT as _REDACT_HINT_SHARED

SEVERITY_BLOCKING = "blocking"
SEVERITY_DEGRADED = "degraded"
SEVERITY_INFO = "info"


class ObservabilityService:
    """轻量本地可观测性服务：事件埋点、错误分级、复现包导出。"""

    EVENT_FILE_NAME = "telemetry_events.jsonl"
    DEFAULT_EVENT_LIMIT = 5000
    MAX_EVENT_FILE_SIZE_MB = 50  # 最大 50 MB
    ARCHIVE_DIR_NAME = "event_archives"
    REDACT_HINT = "***REDACTED***"

    def __init__(self, data_store):
        self.data_store = data_store
        self.base_dir = getattr(data_store, "base_dir", os.getcwd())
        self.event_file_path = os.path.join(self.base_dir, self.EVENT_FILE_NAME)
        self.archive_dir = os.path.join(self.base_dir, self.ARCHIVE_DIR_NAME)
        # P3-3: 异常去重 — 相同异常签名在窗口内只记录一次
        self._exception_dedupe: dict[str, float] = {}  # signature → first_seen_timestamp
        self._dedupe_window = 60.0  # 60 秒去重窗口

    def classify_error(self, exc: Exception | None, message: str = "") -> str:
        text = f"{message} {exc or ''}".lower()
        if any(word in text for word in ("crash", "fatal", "traceback", "启动失败", "崩溃")):
            return SEVERITY_BLOCKING
        if any(word in text for word in ("timeout", "fallback", "降级", "重试", "同步失败", "network")):
            return SEVERITY_DEGRADED
        return SEVERITY_INFO

    def track_event(self, event_name: str, severity: str = SEVERITY_INFO, payload: dict[str, Any] | None = None):
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": str(event_name or "unknown").strip() or "unknown",
            "severity": severity if severity in {SEVERITY_BLOCKING, SEVERITY_DEGRADED, SEVERITY_INFO} else SEVERITY_INFO,
            "payload": self._sanitize(payload or {}),
        }
        os.makedirs(self.base_dir, exist_ok=True)
        with open(self.event_file_path, "a", encoding="utf-8") as file_handle:
            file_handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._truncate_events_if_needed()

    def track_exception(self, event_name: str, exc: Exception, payload: dict[str, Any] | None = None):
        # P3-3: 异常去重 — 相同签名在窗口内只记录一次，避免日志洪水
        import time as _time
        signature = f"{event_name}:{exc.__class__.__name__}:{str(exc)[:200]}"
        now = _time.time()
        # 清理过期条目
        cutoff = now - self._dedupe_window
        expired = [k for k, v in self._exception_dedupe.items() if v < cutoff]
        for k in expired:
            del self._exception_dedupe[k]

        if signature in self._exception_dedupe:
            # 已在窗口内记录过，跳过
            return

        self._exception_dedupe[signature] = now
        merged = dict(payload or {})
        merged.update(
            {
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
                "traceback": traceback.format_exc(limit=20),
            }
        )
        self.track_event(event_name, severity=self.classify_error(exc, str(exc)), payload=merged)

    def export_repro_bundle(self, output_zip_path: str = "") -> dict[str, Any]:
        target_path = output_zip_path.strip()
        if not target_path:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target_path = os.path.join(tempfile.gettempdir(), f"siyi_repro_bundle_{stamp}.zip")

        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        settings_payload = {}
        if self.data_store is not None:
            settings_payload = self._sanitize(self.data_store.load_json("settings", {}))

        events_payload = self._load_recent_events(limit=200)
        env_payload = self._sanitize(dict(os.environ))
        startup_log_path = os.path.join(tempfile.gettempdir(), "siyi_startup_latest.log")

        with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("metadata.json", json.dumps({
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "schema_version": 1,
                "contains": ["settings.redacted.json", "events.recent.json", "env.redacted.json", "startup_latest.log"],
            }, ensure_ascii=False, indent=2))
            archive.writestr("settings.redacted.json", json.dumps(settings_payload, ensure_ascii=False, indent=2))
            archive.writestr("events.recent.json", json.dumps(events_payload, ensure_ascii=False, indent=2))
            archive.writestr("env.redacted.json", json.dumps(env_payload, ensure_ascii=False, indent=2))
            if os.path.isfile(startup_log_path):
                archive.write(startup_log_path, arcname="startup_latest.log")

        return {
            "output_zip": target_path,
            "event_count": len(events_payload),
        }

    def get_recent_events(self, limit: int = 200) -> list[dict[str, Any]]:
        """公开 API：返回最近的遥测事件列表。"""
        return self._load_recent_events(limit=limit)

    def _load_recent_events(self, limit: int = 200):
        if not os.path.isfile(self.event_file_path):
            return []
        with open(self.event_file_path, encoding="utf-8") as file_handle:
            lines = file_handle.readlines()[-max(1, int(limit)) :]
        result = []
        for line in lines:
            try:
                result.append(json.loads(line))
            except Exception:
                continue
        return result

    def _truncate_events_if_needed(self):
        """根据大小和条数限制完成日志轮转。支持归档和压缩。"""
        if not os.path.isfile(self.event_file_path):
            return

        file_size_bytes = os.path.getsize(self.event_file_path)
        file_size_mb = file_size_bytes / (1024 * 1024)

        # 读取当前事件文件
        with open(self.event_file_path, encoding="utf-8") as f:
            lines = f.readlines()

        # 检查是否需要轮转（大小超限或条数超限）
        needs_rotation = (
            file_size_mb > self.MAX_EVENT_FILE_SIZE_MB
            or len(lines) > self.DEFAULT_EVENT_LIMIT
        )

        if needs_rotation and len(lines) > 1000:
            # 轮转旧事件到归档目录
            self._rotate_events_to_archive(lines)
            return

        # 简单截断（保留最近 N 条）
        if len(lines) > self.DEFAULT_EVENT_LIMIT:
            kept = lines[-self.DEFAULT_EVENT_LIMIT :]
            with open(self.event_file_path, "w", encoding="utf-8") as f:
                f.writelines(kept)

    def _rotate_events_to_archive(self, lines: list):
        """将旧事件轮转到归档目录，保留最近 N 条在当前文件。"""
        os.makedirs(self.archive_dir, exist_ok=True)

        # 按时间戳分类
        today = datetime.now().strftime("%Y-%m-%d")
        archive_filename = f"telemetry_events_{today}.jsonl.gz"
        archive_path = os.path.join(self.archive_dir, archive_filename)

        # 只保留最近 1000 条在活动文件；移动旧事件到归档
        split_point = max(0, len(lines) - 1000)
        old_lines = lines[:split_point]
        kept_lines = lines[split_point:]

        if old_lines:
            # 追加到归档文件（支持 gzip 压缩）
            try:
                with gzip.open(archive_path, "at", encoding="utf-8") as f:
                    for line in old_lines:
                        f.write(line)
            except Exception as e:
                # 归档失败则不删除原数据
                logger.warning("事件轮转到归档失败，保留所有事件：%s", e)
                return

        # 写回活动文件
        with open(self.event_file_path, "w", encoding="utf-8") as f:
            f.writelines(kept_lines)

        # 清理过旧的归档（保留最近 30 天）
        self._cleanup_old_archives()

    def _cleanup_old_archives(self):
        """删除超过 30 天的事件归档。"""
        if not os.path.isdir(self.archive_dir):
            return

        cutoff_date = datetime.now() - timedelta(days=30)
        try:
            for filename in os.listdir(self.archive_dir):
                if not filename.startswith("telemetry_events_"):
                    continue
                file_path = os.path.join(self.archive_dir, filename)
                file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                if file_mtime < cutoff_date:
                    os.remove(file_path)
        except Exception as e:
            logger.debug("清理过旧归档失败：%s", e)

    def _sanitize(self, value):
        # P2-2: 复用 exception_handler 的统一脱敏逻辑（保持向后兼容）
        return _sanitize_value_shared(value)

    def _sanitize_text(self, text: str) -> str:
        # P2-2: 复用 exception_handler.sanitize_text
        from core.exception_handler import sanitize_text
        return sanitize_text(text)

    def _is_sensitive_key(self, key: str) -> bool:
        # P2-2: 复用 exception_handler._is_sensitive_key
        from core.exception_handler import _is_sensitive_key
        return _is_sensitive_key(key)
