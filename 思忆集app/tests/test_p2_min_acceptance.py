from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from services.data_protection_service import DataProtectionService
from data.local_storage import LocalDataStore
from core.observability import SEVERITY_INFO, ObservabilityService


def _load_module_from_path(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_observability_track_event_writes_jsonl_and_severity(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    service = ObservabilityService(data_store)

    service.track_event(
        "startup.begin",
        severity=SEVERITY_INFO,
        payload={"launch_source": "pytest", "token": "should-not-leak"},
    )

    event_file = tmp_path / service.EVENT_FILE_NAME
    assert event_file.exists()

    lines = event_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])

    assert event["event"] == "startup.begin"
    assert event["severity"] == SEVERITY_INFO
    assert event["payload"]["launch_source"] == "pytest"
    assert event["payload"]["token"] == service.REDACT_HINT


def test_observability_repro_bundle_redacts_sensitive_fields(tmp_path, monkeypatch):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    data_store.save_json(
        "settings",
        {
            "llm_api_key": "sk-secret-raw",
            "automation_hub_api_token": "token-raw",
            "normal_key": "normal-value",
        },
    )

    service = ObservabilityService(data_store)
    service.track_event(
        "sync.failed",
        payload={
            "message": "authorization: bearer abcdef",
            "password": "123456",
            "retry": True,
        },
    )

    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-env-secret")
    monkeypatch.setenv("SAFE_VAR", "safe")

    output_zip = tmp_path / "repro.zip"
    result = service.export_repro_bundle(str(output_zip))
    assert Path(result["output_zip"]).exists()

    with zipfile.ZipFile(output_zip, "r") as archive:
        settings_payload = json.loads(archive.read("settings.redacted.json").decode("utf-8"))
        env_payload = json.loads(archive.read("env.redacted.json").decode("utf-8"))
        events_payload = json.loads(archive.read("events.recent.json").decode("utf-8"))

    assert settings_payload["llm_api_key"] == service.REDACT_HINT
    assert settings_payload["automation_hub_api_token"] == service.REDACT_HINT
    assert settings_payload["normal_key"] == "normal-value"

    assert env_payload["OPENAI_API_KEY"] == service.REDACT_HINT
    assert env_payload["SAFE_VAR"] == "safe"

    assert len(events_payload) >= 1
    last_payload = events_payload[-1]["payload"]
    assert last_payload["password"] == service.REDACT_HINT
    assert service.REDACT_HINT in last_payload["message"].lower() or service.REDACT_HINT in last_payload["message"]


def test_data_protection_restore_namespace_supports_namespaced_and_legacy_snapshots(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    service = DataProtectionService(data_store)

    data_store.save_json("tasks", [{"id": 1, "name": "A"}])
    data_store.save_json("settings", {"theme": "dark"})

    backup_result = service.backup_now()
    snapshot_dir = Path(backup_result["snapshot_dir"])
    assert snapshot_dir.exists()

    data_store.save_json("tasks", [{"id": 2, "name": "B"}])
    data_store.save_json("settings", {"theme": "light"})

    restored = service.restore_namespace("tasks")
    assert restored["restored"] >= 1
    assert data_store.load_json("tasks", []) == [{"id": 1, "name": "A"}]
    assert data_store.load_json("settings", {}) == {"theme": "light"}

    # 验证旧版平铺蹇収鍏煎读取
    legacy_snapshot = Path(service._backup_root()) / "snapshot_legacy"
    legacy_snapshot.mkdir(parents=True, exist_ok=True)
    legacy_tasks = legacy_snapshot / Path(data_store.paths["tasks"]).name
    legacy_tasks.write_text(json.dumps([{"id": 3, "name": "legacy"}], ensure_ascii=False), encoding="utf-8")

    latest_marker = Path(service._backup_root()) / "latest_snapshot.txt"
    latest_marker.write_text(str(legacy_snapshot), encoding="utf-8")

    data_store.save_json("tasks", [{"id": 99, "name": "live"}])
    restored_legacy = service.restore_namespace("tasks")
    assert restored_legacy["restored"] >= 1
    assert data_store.load_json("tasks", []) == [{"id": 3, "name": "legacy"}]


def test_release_report_contains_required_sections(tmp_path, monkeypatch):
    script_path = PROJECT_ROOT / "scripts" / "generate_release_report.py"
    module = _load_module_from_path("generate_release_report_test", script_path)

    output_file = tmp_path / "release_report.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_release_report.py",
            "--channel",
            "dev",
            "--build",
            "2",
            "--output",
            str(output_file),
        ],
    )

    exit_code = module.main()
    assert exit_code == 0
    assert output_file.exists()

    content = output_file.read_text(encoding="utf-8")
    assert "## 功能变化" in content
    assert "## 已知问题" in content
    assert "## 回滚步骤" in content


def test_desktop_quality_gate_wires_smoke_and_report_commands(monkeypatch):
    script_path = PROJECT_ROOT / "scripts" / "desktop_quality_gate.py"
    module = _load_module_from_path("desktop_quality_gate_test", script_path)

    recorded = []

    def _fake_run(command, cwd=None, check=False):
        recorded.append({"command": list(command), "cwd": cwd, "check": bool(check)})

        class _Completed:
            returncode = 0

        return _Completed()

    monkeypatch.setattr(module.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "desktop_quality_gate.py",
            "--suite",
            "p2",
            "--channel",
            "dev",
            "--build",
            "2",
            "--report-output",
            str(PROJECT_ROOT / "release_report.test.md"),
        ],
    )

    exit_code = module.main()
    assert exit_code == 0
    assert len(recorded) == 2
    assert any("smoke_check.py" in " ".join(step["command"]) for step in recorded)
    assert any("generate_release_report.py" in " ".join(step["command"]) for step in recorded)
