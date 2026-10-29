"""Settings persistence smoke tests: save, load, close/reopen preserves data."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore
from repositories._settings import LocalSettingsRepository


def test_settings_set_and_get(tmp_path):
    """Set a setting, read it back from same instance."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalSettingsRepository(store)

    repo.set_setting("test_key", "test_value")
    assert repo.get_setting("test_key", "") == "test_value"


def test_settings_close_reopen_preserves_data(tmp_path):
    """Create setting, close/reopen — setting survives."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalSettingsRepository(store)

    repo.set_setting("persist_key", "persist_value")

    # Simulate close
    del repo
    del store

    # Reopen
    store2 = LocalDataStore(base_dir=str(tmp_path))
    repo2 = LocalSettingsRepository(store2)

    assert repo2.get_setting("persist_key", "") == "persist_value"


def test_settings_overwrite(tmp_path):
    """Overwrite existing setting."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalSettingsRepository(store)

    repo.set_setting("key", "old_value")
    repo.set_setting("key", "new_value")
    assert repo.get_setting("key", "") == "new_value"


def test_settings_default_returned_when_missing(tmp_path):
    """Missing key returns default."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalSettingsRepository(store)

    assert repo.get_setting("nonexistent", "default_val") == "default_val"


def test_settings_multiple_keys(tmp_path):
    """Multiple keys saved independently."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalSettingsRepository(store)

    repo.set_setting("key_a", "value_a")
    repo.set_setting("key_b", "value_b")
    repo.set_setting("key_c", "value_c")

    assert repo.get_setting("key_a", "") == "value_a"
    assert repo.get_setting("key_b", "") == "value_b"
    assert repo.get_setting("key_c", "") == "value_c"


def test_settings_none_value(tmp_path):
    """Setting value to None (null)."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalSettingsRepository(store)

    repo.set_setting("key", None)
    assert repo.get_setting("key", "default") is None


def test_settings_dict_value(tmp_path):
    """Setting with dict value."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalSettingsRepository(store)

    dict_val = {"nested": "value", "number": 42}
    repo.set_setting("config", dict_val)
    loaded = repo.get_setting("config", {})
    assert loaded == dict_val


def test_settings_list_value(tmp_path):
    """Setting with list value."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalSettingsRepository(store)

    list_val = ["item1", "item2", "item3"]
    repo.set_setting("items", list_val)
    loaded = repo.get_setting("items", [])
    assert loaded == list_val


def test_settings_file_is_json(tmp_path):
    """Saved settings file is valid JSON."""
    import json

    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalSettingsRepository(store)

    repo.set_setting("key", "value")

    settings_file = Path(str(tmp_path)) / "settings.json"
    assert settings_file.exists()

    with open(settings_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("key") == "value"


def test_settings_reload_clears_cache(tmp_path):
    """reload() clears cache, next get reads from disk."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalSettingsRepository(store)

    repo.set_setting("key", "original")

    # Reload and verify from disk
    repo.reload()
    assert repo.get_setting("key", "") == "original"


def test_settings_cache_not_stale_after_save(tmp_path):
    """After set_setting, get_setting returns latest value (not stale cache)."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalSettingsRepository(store)

    repo.set_setting("key", "first")
    repo.set_setting("key", "second")
    # Should return "second", not "first"
    assert repo.get_setting("key", "") == "second"


def test_settings_bulk_save_load(tmp_path):
    """Simulate the real save_settings flow: many keys at once."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalSettingsRepository(store)

    # Simulate what save_settings does
    settings = {
        "youdao_app_key": "test_key",
        "youdao_app_secret": "test_secret",
        "automation_hub_api_base": "http://test:8000",
        "automation_hub_api_token": "test_token",
        "assistant_codex_verify_commands": ["cmd1", "cmd2"],
        "assistant_focus_topics": ["topic1", "topic2"],
        "weather_city": "Beijing",
        "weather_cache": {},
        "cron_enabled": True,
        "mcp_enabled": False,
        "proactive_enabled": True,
        "ui_theme": "light",
    }
    for k, v in settings.items():
        repo.set_setting(k, v)

    # Verify all from same repo
    for k, v in settings.items():
        loaded = repo.get_setting(k, None)
        assert loaded == v, f"Mismatch for {k}: expected {v!r}, got {loaded!r}"

    # Verify all from fresh repo (close/reopen)
    store2 = LocalDataStore(base_dir=str(tmp_path))
    repo2 = LocalSettingsRepository(store2)
    for k, v in settings.items():
        loaded = repo2.get_setting(k, None)
        assert loaded == v, f"Persist mismatch for {k}: expected {v!r}, got {loaded!r}"
