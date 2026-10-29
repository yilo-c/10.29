"""ObservabilityService 单元测试：事件追踪、错误分级、脱敏、导出。"""

from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from core.observability import (
    SEVERITY_BLOCKING,
    SEVERITY_DEGRADED,
    SEVERITY_INFO,
    ObservabilityService,
)


class _MockDataStore:
    def __init__(self, base_dir):
        self.base_dir = base_dir

    def load_json(self, key, default=None):
        return default


@pytest.fixture
def mock_store(tmp_path):
    return _MockDataStore(base_dir=str(tmp_path))


@pytest.fixture
def obs(mock_store):
    return ObservabilityService(data_store=mock_store)


# ═══════════════════════════════════════════════════════════
# classify_error
# ═══════════════════════════════════════════════════════════


class TestClassifyError:
    def test_classify_blocking_keywords(self, obs):
        """包含 crash/fatal/traceback 等关键字应返回 BLOCKING。"""
        assert obs.classify_error(None, "crash detected") == SEVERITY_BLOCKING
        assert obs.classify_error(None, "FATAL error") == SEVERITY_BLOCKING
        assert obs.classify_error(RuntimeError("traceback"), "") == SEVERITY_BLOCKING
        assert obs.classify_error(None, "启动失败") == SEVERITY_BLOCKING

    def test_classify_degraded_keywords(self, obs):
        """包含 timeout/fallback 等关键字应返回 DEGRADED。"""
        assert obs.classify_error(None, "request timeout") == SEVERITY_DEGRADED
        assert obs.classify_error(None, "system 降级") == SEVERITY_DEGRADED
        assert obs.classify_error(None, "sync 同步失败") == SEVERITY_DEGRADED
        assert obs.classify_error(None, "network error") == SEVERITY_DEGRADED

    def test_classify_info_default(self, obs):
        """未知关键字应返回 INFO。"""
        assert obs.classify_error(None, "something happened") == SEVERITY_INFO
        assert obs.classify_error(None, "just a log") == SEVERITY_INFO
        assert obs.classify_error(None, "") == SEVERITY_INFO


# ═══════════════════════════════════════════════════════════
# track_event / track_exception
# ═══════════════════════════════════════════════════════════


class TestEventTracking:
    def test_track_event_creates_file(self, obs, tmp_path):
        """track_event 应在 data_store.base_dir 下创建事件文件。"""
        obs.track_event("test.event", severity=SEVERITY_INFO, payload={"key": "value"})
        event_file = os.path.join(tmp_path, obs.EVENT_FILE_NAME)
        assert os.path.isfile(event_file)

    def test_track_event_validates_severity(self, obs):
        """无效 severity 应回退到 INFO。"""
        obs.track_event("test.event", severity="invalid_level")
        events = obs._load_recent_events(limit=10)
        assert len(events) >= 1
        assert events[-1]["severity"] == SEVERITY_INFO

    def test_track_event_empty_name(self, obs):
        """空事件名应为 'unknown'。"""
        obs.track_event("", severity=SEVERITY_INFO)
        events = obs._load_recent_events(limit=10)
        assert events[-1]["event"] == "unknown"

    def test_track_event_reads_back(self, obs):
        """写入的事件可通过 _load_recent_events 读取。"""
        obs.track_event("my.event", severity=SEVERITY_BLOCKING, payload={"count": 42})
        events = obs._load_recent_events(limit=100)
        matching = [e for e in events if e["event"] == "my.event"]
        assert len(matching) >= 1
        assert matching[-1]["severity"] == SEVERITY_BLOCKING
        assert matching[-1]["payload"]["count"] == 42

    def test_track_exception_includes_error_info(self, obs):
        """track_exception 应包含错误类型、消息和回溯。"""
        try:
            raise ValueError("测试异常")
        except ValueError:
            obs.track_exception("my.error", ValueError("测试异常"), payload={"context": "test"})

        events = obs._load_recent_events(limit=100)
        matching = [e for e in events if e["event"] == "my.error"]
        assert len(matching) >= 1
        event = matching[-1]
        assert event["payload"]["error_type"] == "ValueError"
        assert "测试异常" in event["payload"]["error_message"]
        assert "traceback" in event["payload"]

    def test_track_multiple_events_accumulate(self, obs):
        """多次 track_event 应按顺序累加。"""
        for i in range(5):
            obs.track_event(f"event.{i}", payload={"idx": i})
        events = obs._load_recent_events(limit=100)
        assert len(events) >= 5


# ═══════════════════════════════════════════════════════════
# _sanitize / 脱敏
# ═══════════════════════════════════════════════════════════


class TestSanitize:
    def test_sanitize_redacts_sensitive_keys(self, obs):
        """敏感字段（api_key, token, secret）应被脱敏。"""
        data = {
            "name": "test",
            "api_key": "sk-1234567890abcdef",
            "config": {"token": "eyJhbGci", "url": "https://example.com"},
        }
        sanitized = obs._sanitize(data)
        assert sanitized["name"] == "test"
        assert sanitized["api_key"] == obs.REDACT_HINT
        assert sanitized["config"]["token"] == obs.REDACT_HINT
        assert sanitized["config"]["url"] == "https://example.com"

    def test_sanitize_text_redacts_patterns(self, obs):
        """文本中的 api_key= 等模式应被脱敏。"""
        text = 'api_key=sk-1234567890abcdef'
        result = obs._sanitize_text(text)
        assert obs.REDACT_HINT in result
        assert "sk-1234567890abcdef" not in result


# ═══════════════════════════════════════════════════════════
# export_repro_bundle
# ═══════════════════════════════════════════════════════════


class TestExportReproBundle:
    def test_export_repro_bundle_creates_zip(self, obs, tmp_path):
        """export_repro_bundle 应生成包含事件和设置的 zip 文件。"""
        obs.track_event("test.event", payload={"msg": "hello"})
        zip_path = os.path.join(str(tmp_path), "repro.zip")

        result = obs.export_repro_bundle(output_zip_path=zip_path)

        assert os.path.isfile(zip_path)
        assert result["event_count"] >= 1
        assert result["output_zip"] == zip_path

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "metadata.json" in names
            assert "events.recent.json" in names
            assert "settings.redacted.json" in names

    def test_export_repro_bundle_without_events(self, obs, tmp_path):
        """无事件时仍应生成有效的 zip 文件。"""
        zip_path = os.path.join(str(tmp_path), "empty_repro.zip")
        result = obs.export_repro_bundle(output_zip_path=zip_path)
        assert os.path.isfile(zip_path)
        assert result["event_count"] == 0


# ═══════════════════════════════════════════════════════════
# _load_recent_events
# ═══════════════════════════════════════════════════════════


class TestLoadRecentEvents:
    def test_load_recent_events_empty_file(self, obs, tmp_path):
        """事件文件不存在时应返回空列表。"""
        events = obs._load_recent_events(limit=10)
        assert events == []

    def test_load_recent_events_skips_corrupt_lines(self, obs):
        """损坏的 JSON 行应被跳过。"""
        event_file = obs.event_file_path
        os.makedirs(os.path.dirname(event_file), exist_ok=True)
        with open(event_file, "w", encoding="utf-8") as f:
            f.write('{"valid": true}\n')
            f.write("corrupt json line\n")
            f.write('{"valid": false}\n')

        events = obs._load_recent_events(limit=10)
        assert len(events) == 2
        assert all(isinstance(e, dict) for e in events)
