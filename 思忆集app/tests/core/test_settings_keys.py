"""settings_keys 常量定义测试：确保所有常量、默认值、类型集合的一致性。"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.settings_keys import (
    KEY_YOUDAO_APP_KEY,
    KEY_YOUDAO_APP_SECRET,
    KEY_LLM_API_KEY,
    KEY_LLM_API_BASE,
    KEY_WEATHER_CITY,
    KEY_UI_THEME,
    SETTINGS_SCHEMA_VERSION,
    SETTINGS_SCHEMA_VERSION_KEY,
    SETTINGS_DEFAULTS,
    BOOL_KEYS,
    INT_KEYS,
    STRING_KEYS,
    LIST_KEYS,
    DICT_KEYS,
    DEFAULT_MCP_SERVER_CONFIGS,
)


class TestKeyConstants:
    def test_key_constants_are_strings(self):
        """所有 KEY_* 常量应为非空字符串。"""
        import core.settings_keys as sk

        key_attrs = [attr for attr in dir(sk) if attr.startswith("KEY_")]
        for attr in key_attrs:
            value = getattr(sk, attr)
            assert isinstance(value, str), f"{attr} 应为字符串，实际为 {type(value)}"
            assert value, f"{attr} 不应为空"

    def test_youdao_constants(self):
        assert isinstance(KEY_YOUDAO_APP_KEY, str)
        assert KEY_YOUDAO_APP_KEY == "youdao_app_key"
        assert KEY_YOUDAO_APP_SECRET == "youdao_app_secret"

    def test_llm_constants(self):
        assert KEY_LLM_API_BASE == "llm_api_base"
        assert KEY_LLM_API_KEY == "llm_api_key"

    def test_schema_version_constants(self):
        assert SETTINGS_SCHEMA_VERSION_KEY == "settings_schema_version"
        assert isinstance(SETTINGS_SCHEMA_VERSION, int)
        assert SETTINGS_SCHEMA_VERSION >= 1


class TestSettingsDefaults:
    def test_defaults_contain_all_keys(self):
        """SETTINGS_DEFAULTS 应包含所有 KEY_* 常量作为键。"""
        import core.settings_keys as sk

        key_attrs = [attr for attr in dir(sk) if attr.startswith("KEY_")]
        for attr in key_attrs:
            key_value = getattr(sk, attr)
            assert key_value in SETTINGS_DEFAULTS, (
                f"{attr}('{key_value}') 未在 SETTINGS_DEFAULTS 中找到"
            )

    def test_weather_default(self):
        assert SETTINGS_DEFAULTS[KEY_WEATHER_CITY] == "Shanghai"
        assert SETTINGS_DEFAULTS.get("weather_city_label") == "上海"

    def test_ui_theme_default(self):
        assert SETTINGS_DEFAULTS[KEY_UI_THEME] == "light"

    def test_defaults_value_types(self):
        """默认值的类型应与用途匹配。"""
        assert isinstance(SETTINGS_DEFAULTS.get("llm_timeout"), int)
        assert isinstance(SETTINGS_DEFAULTS.get("assistant_autonomy_enabled"), bool)
        assert isinstance(SETTINGS_DEFAULTS.get("assistant_focus_topics"), list)
        assert isinstance(SETTINGS_DEFAULTS.get("assistant_daily_brief_time"), str)


class TestTypeSets:
    def test_bool_keys_all_bool_defaults(self):
        """BOOL_KEYS 中每个键的默认值应为 bool 类型。"""
        for key in BOOL_KEYS:
            assert key in SETTINGS_DEFAULTS, f"{key} 缺失于 SETTINGS_DEFAULTS"
            assert isinstance(SETTINGS_DEFAULTS[key], bool), (
                f"{key} 的默认值应为 bool，实际为 {type(SETTINGS_DEFAULTS[key]).__name__}"
            )

    def test_int_keys_all_int_defaults(self):
        """INT_KEYS 中每个键的默认值应为 int 类型。"""
        for key in INT_KEYS:
            assert key in SETTINGS_DEFAULTS, f"{key} 缺失于 SETTINGS_DEFAULTS"
            assert isinstance(SETTINGS_DEFAULTS[key], int), (
                f"{key} 的默认值应为 int，实际为 {type(SETTINGS_DEFAULTS[key]).__name__}"
            )

    def test_string_keys_all_str_defaults(self):
        """STRING_KEYS 中每个键的默认值应为 str 类型。"""
        for key in STRING_KEYS:
            assert key in SETTINGS_DEFAULTS, f"{key} 缺失于 SETTINGS_DEFAULTS"
            assert isinstance(SETTINGS_DEFAULTS[key], str), (
                f"{key} 的默认值应为 str，实际为 {type(SETTINGS_DEFAULTS[key]).__name__}"
            )

    def test_list_keys_all_list_defaults(self):
        """LIST_KEYS 中每个键的默认值应为 list 类型。"""
        for key in LIST_KEYS:
            assert key in SETTINGS_DEFAULTS, f"{key} 缺失于 SETTINGS_DEFAULTS"
            assert isinstance(SETTINGS_DEFAULTS[key], list), (
                f"{key} 的默认值应为 list，实际为 {type(SETTINGS_DEFAULTS[key]).__name__}"
            )

    def test_dict_keys_all_dict_defaults(self):
        """DICT_KEYS 中每个键的默认值应为 dict 类型。"""
        for key in DICT_KEYS:
            assert key in SETTINGS_DEFAULTS, f"{key} 缺失于 SETTINGS_DEFAULTS"
            assert isinstance(SETTINGS_DEFAULTS[key], dict), (
                f"{key} 的默认值应为 dict，实际为 {type(SETTINGS_DEFAULTS[key]).__name__}"
            )

    def test_sets_are_disjoint(self):
        """BOOL_KEYS / INT_KEYS / STRING_KEYS / LIST_KEYS / DICT_KEYS 应互不重叠。"""
        assert BOOL_KEYS.isdisjoint(INT_KEYS)
        assert BOOL_KEYS.isdisjoint(STRING_KEYS)
        assert BOOL_KEYS.isdisjoint(LIST_KEYS)
        assert BOOL_KEYS.isdisjoint(DICT_KEYS)
        assert INT_KEYS.isdisjoint(STRING_KEYS)
        assert INT_KEYS.isdisjoint(LIST_KEYS)
        assert INT_KEYS.isdisjoint(DICT_KEYS)
        assert STRING_KEYS.isdisjoint(LIST_KEYS)
        assert STRING_KEYS.isdisjoint(DICT_KEYS)
        assert LIST_KEYS.isdisjoint(DICT_KEYS)


class TestMCPDefaults:
    def test_default_mcp_configs_structure(self):
        """DEFAULT_MCP_SERVER_CONFIGS 中每条应有必需字段。"""
        for config in DEFAULT_MCP_SERVER_CONFIGS:
            assert "name" in config
            assert "description" in config
            assert "command" in config
            assert "enabled" in config
            assert "timeout_seconds" in config
            assert isinstance(config["enabled"], bool)
            assert isinstance(config["timeout_seconds"], int)

    def test_default_mcp_configs_count(self):
        """默认应有 5 个 MCP Server 配置。"""
        assert len(DEFAULT_MCP_SERVER_CONFIGS) == 5

    def test_default_mcp_all_disabled(self):
        """默认所有 MCP Server 应为禁用状态。"""
        for config in DEFAULT_MCP_SERVER_CONFIGS:
            assert config["enabled"] is False
