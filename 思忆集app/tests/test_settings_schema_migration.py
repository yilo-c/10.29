from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore
from repositories import LocalSettingsRepository
from core.settings_schema import (
    SETTINGS_DEFAULTS,
    SETTINGS_SCHEMA_VERSION,
    SETTINGS_SCHEMA_VERSION_KEY,
    migrate_settings_repository,
)


def _make_store(tmp_path, initial_settings=None):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)
    if initial_settings is not None:
        data_store.save_json("settings", initial_settings)
    return data_store, settings_repo


def test_settings_schema_migration_injects_defaults_and_version(tmp_path):
    data_store, settings_repo = _make_store(
        tmp_path,
        {
            "llm_api_base": "https://api.example.com/v1",
            "news_auto_sync_enabled": "true",
            "assistant_docker_stack_port": "8012",
            "main_workbench_template": "daily",
        },
    )

    result = migrate_settings_repository(settings_repo)
    migrated = data_store.load_json("settings", {})

    assert result["version"] == SETTINGS_SCHEMA_VERSION
    assert migrated["settings_schema_version"] == SETTINGS_SCHEMA_VERSION
    assert migrated["youdao_app_key"] == ""
    assert migrated["news_auto_sync_enabled"] is True
    assert migrated["assistant_docker_stack_port"] == 8012
    assert isinstance(migrated["assistant_action_whitelist"], list)
    assert "capture" in migrated["assistant_action_whitelist"]
    assert migrated["assistant_action_risk_warn_only"] is False  # P0-4: 安全默认改为 False
    assert migrated["assistant_action_guard_profile"] == "balanced"
    assert migrated["assistant_action_risk_enforcement"] == "auto"
    assert migrated["assistant_codex_verify_commands"] == []
    assert migrated["startup_guide_completed"] is False
    assert migrated["startup_guide_last_version"] == ""
    assert migrated["assistant_focus_topics"] == ["AI", "自动化", "软件工程"]
    assert "launch_nana_pet" in migrated["assistant_command_whitelist"]
    assert "start_docker_stack" in migrated["assistant_command_whitelist"]


def test_settings_schema_migration_creates_workbench_config(tmp_path):
    data_store, settings_repo = _make_store(
        tmp_path,
        {
            "main_workbench_template": "focus",
            "main_workbench_density": "compact",
            "main_workbench_card_order": ["tasks", "news"],
        },
    )

    migrate_settings_repository(settings_repo)
    migrated = data_store.load_json("settings", {})

    config = migrated.get("main_workbench_layout_config")
    assert isinstance(config, dict)
    assert config.get("template") in {"balanced", "focus", "daily", "custom"}
    assert config.get("density") in {"compact", "standard", "comfortable"}
    assert isinstance(config.get("card_order"), list)


def test_migration_empty_settings_fills_all_defaults(tmp_path):
    """空配缃枃件迁移后，所鏈"SETTINGS_DEFAULTS 閿兘必须存在。"""
    data_store, settings_repo = _make_store(tmp_path, {})

    result = migrate_settings_repository(settings_repo)
    migrated = data_store.load_json("settings", {})

    assert result["changed"] is True
    assert migrated[SETTINGS_SCHEMA_VERSION_KEY] == SETTINGS_SCHEMA_VERSION
    for key, default_value in SETTINGS_DEFAULTS.items():
        assert key in migrated, f"missing key: {key}"


def test_migration_preserves_user_overrides(tmp_path):
    """用户宸茶值不搴旇榛樿鍊艰鐩栥€"""
    data_store, settings_repo = _make_store(
        tmp_path,
        {
            "llm_api_base": "https://custom.api.example.com/v1",
            "llm_model": "gpt-4o",
            "weather_city": "上海",
        },
    )

    migrate_settings_repository(settings_repo)
    migrated = data_store.load_json("settings", {})

    assert migrated["llm_api_base"] == "https://custom.api.example.com/v1"
    assert migrated["llm_model"] == "gpt-4o"
    assert migrated["weather_city"] == "上海"


def test_migration_coerces_bool_string_to_bool(tmp_path):
    """瀛楃一"'true'/'false' 搴旇强转一"bool。"""
    data_store, settings_repo = _make_store(
        tmp_path,
        {
            "news_auto_sync_enabled": "false",
            "notes_auto_sync_enabled": "true",
            "assistant_autonomy_enabled": "yes",
        },
    )

    migrate_settings_repository(settings_repo)
    migrated = data_store.load_json("settings", {})

    assert migrated["news_auto_sync_enabled"] is False
    assert migrated["notes_auto_sync_enabled"] is True
    assert migrated["assistant_autonomy_enabled"] is True


def test_migration_coerces_int_string_to_int(tmp_path):
    """瀛楃串数字应琚己杞负 int。"""
    data_store, settings_repo = _make_store(
        tmp_path,
        {
            "llm_timeout": "45",
            "assistant_docker_stack_port": "9090",
            "browser_assistant_bridge_port": "53681",
        },
    )

    migrate_settings_repository(settings_repo)
    migrated = data_store.load_json("settings", {})

    assert migrated["llm_timeout"] == 45
    assert migrated["assistant_docker_stack_port"] == 9090
    assert migrated["browser_assistant_bridge_port"] == 53681


def test_migration_idempotent_no_change_on_second_run(tmp_path):
    """已迁移过的配缃啀次迁移，changed 应为 False。"""
    data_store, settings_repo = _make_store(tmp_path, {})

    first = migrate_settings_repository(settings_repo)
    assert first["changed"] is True

    second = migrate_settings_repository(settings_repo)
    assert second["changed"] is False
    assert second["version"] == SETTINGS_SCHEMA_VERSION


def test_migration_normalizes_invalid_guard_profile(tmp_path):
    """无效鐨"guard profile 应回閫€到默璁ゅ€笺€"""
    data_store, settings_repo = _make_store(
        tmp_path,
        {
            "assistant_action_guard_profile": "nonexistent_profile",
        },
    )

    migrate_settings_repository(settings_repo)
    migrated = data_store.load_json("settings", {})

    assert migrated["assistant_action_guard_profile"] in {"strict", "balanced", "permissive"}


def test_migration_normalizes_invalid_enforcement(tmp_path):
    """无效鐨"enforcement 应回閫€到默璁ゅ€笺€"""
    data_store, settings_repo = _make_store(
        tmp_path,
        {
            "assistant_action_risk_enforcement": "garbage",
        },
    )

    migrate_settings_repository(settings_repo)
    migrated = data_store.load_json("settings", {})

    assert migrated["assistant_action_risk_enforcement"] in {"auto", "manual", "off"}


def test_migration_injects_required_actions_into_whitelist(tmp_path):
    """迁移后白名单应包鍚墍有必闇€动作。"""
    data_store, settings_repo = _make_store(
        tmp_path,
        {
            "assistant_action_whitelist": ["capture"],
        },
    )

    migrate_settings_repository(settings_repo)
    migrated = data_store.load_json("settings", {})

    whitelist = migrated["assistant_action_whitelist"]
    for required in ("news.lookup", "web.search", "memory.recall", "file.read", "shell.run"):
        assert required in whitelist, f"missing required action: {required}"


def test_migration_truncates_focus_topics_to_max_12(tmp_path):
    """focus_topics 超过 12 涓簲琚埅鏂€"""
    topics = [f"topic-{i}" for i in range(20)]
    data_store, settings_repo = _make_store(
        tmp_path,
        {
            "assistant_focus_topics": topics,
        },
    )

    migrate_settings_repository(settings_repo)
    migrated = data_store.load_json("settings", {})

    assert len(migrated["assistant_focus_topics"]) <= 12


def test_migration_handles_non_dict_settings_gracefully(tmp_path):
    """如果 settings 文件不是 dict，迁移应安全地重寤恒€"""
    data_store, settings_repo = _make_store(tmp_path)
    data_store.save_json("settings", "not-a-dict")

    result = migrate_settings_repository(settings_repo)

    assert result["changed"] is True
    migrated = data_store.load_json("settings", {})
    assert migrated[SETTINGS_SCHEMA_VERSION_KEY] == SETTINGS_SCHEMA_VERSION


def test_migration_no_data_store_returns_unchanged(tmp_path):
    """settings_repository 没有 data_store 灞炴€褎椂直接返回 unchanged。"""

    class _BareRepo:
        pass

    result = migrate_settings_repository(_BareRepo())

    assert result["changed"] is False
    assert result["version"] == SETTINGS_SCHEMA_VERSION


def test_migration_backfills_per_request_selection_for_legacy_agent_kernel(tmp_path):
    """A1① 存量补键：老配置 agent_kernel 不含 per_request_selection 时显式补 True。"""
    data_store, settings_repo = _make_store(
        tmp_path,
        {
            "settings_schema_version": SETTINGS_SCHEMA_VERSION,
            "agent_kernel": {"type": "dsh", "dsh": {"profile": "headless"}},
        },
    )

    migrate_settings_repository(settings_repo)
    migrated = data_store.load_json("settings", {})

    assert migrated["agent_kernel"]["per_request_selection"] is True
    assert migrated["agent_kernel"]["type"] == "dsh"
    assert migrated["agent_kernel"]["dsh"] == {"profile": "headless"}


def test_migration_keeps_explicit_false_per_request_selection(tmp_path):
    """A1① 存量补键：用户显式保存过 False 的不得被覆盖。"""
    data_store, settings_repo = _make_store(
        tmp_path,
        {
            "settings_schema_version": SETTINGS_SCHEMA_VERSION,
            "agent_kernel": {"per_request_selection": False},
        },
    )

    migrate_settings_repository(settings_repo)
    migrated = data_store.load_json("settings", {})

    assert migrated["agent_kernel"]["per_request_selection"] is False


def test_migration_backfill_idempotent(tmp_path):
    """A1① 存量补键幂等：二次运行不再改动。"""
    data_store, settings_repo = _make_store(
        tmp_path,
        {
            "settings_schema_version": SETTINGS_SCHEMA_VERSION,
            "agent_kernel": {"type": "dsh"},
        },
    )

    first = migrate_settings_repository(settings_repo)
    assert first["changed"] is True

    second = migrate_settings_repository(settings_repo)
    assert second["changed"] is False
    migrated = data_store.load_json("settings", {})
    assert migrated["agent_kernel"]["per_request_selection"] is True
