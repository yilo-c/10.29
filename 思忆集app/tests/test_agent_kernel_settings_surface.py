# -*- coding: utf-8 -*-
"""P0 生产接线测试：agent_kernel 两个一等设置键 + DSH→mcp_server --kernel 传参链。

覆盖：
1. 配置面：SETTINGS_DEFAULTS / DICT_KEYS 注册 + validate_agent_kernel_settings 校验
   + LocalSettingsRepository.validate_setting 集成 + 迁移持久化默认值
2. 工具策略：tool_policy.resolve_kernel_identity（--kernel 回退）
3. MCP Server：_load_mcp_tool_policy_from_store（语义默认 → None）与
   _load_kernel_from_store（agent_kernel.type → 内核身份）回退链

红线：开关显式关闭（per_request_selection=False）/ 无 kernel 时行为必须与现状一致
（policy=None、kernel=""）。F 默认自 A1① 起放开为 True。
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.settings_keys import (  # noqa: E402
    AGENT_KERNEL_MCP_TOOL_POLICY,
    AGENT_KERNEL_PER_REQUEST_SELECTION,
    DICT_KEYS,
    KEY_AGENT_KERNEL,
    SETTINGS_DEFAULTS,
    validate_agent_kernel_settings,
)
from core.settings_schema import migrate_settings_repository  # noqa: E402
from data.local_storage import LocalDataStore  # noqa: E402
from repositories import LocalSettingsRepository  # noqa: E402
from services.tooling import tool_policy  # noqa: E402
from mcp_server.server import (  # noqa: E402
    _load_kernel_from_store,
    _load_mcp_tool_policy_from_store,
)
from mcp_server.store import SiyiStore  # noqa: E402


# ── 配置面：默认值注册 ──────────────────────────────────────────


def test_agent_kernel_defaults_registered_in_schema():
    assert KEY_AGENT_KERNEL == "agent_kernel"
    assert AGENT_KERNEL_PER_REQUEST_SELECTION == "per_request_selection"
    assert AGENT_KERNEL_MCP_TOOL_POLICY == "mcp_tool_policy"
    # 默认值已注册：F=true（A1① 放开，per-request 内核选择默认生效），H=allow_all（现状）
    default = SETTINGS_DEFAULTS[KEY_AGENT_KERNEL]
    assert default == {
        "per_request_selection": True,
        "mcp_tool_policy": {"mode": "allow_all"},
    }
    # DICT_KEYS 注册：迁移/校验按 dict 处理
    assert KEY_AGENT_KERNEL in DICT_KEYS


# ── 配置面：validate_agent_kernel_settings 校验 ─────────────────


def test_validate_agent_kernel_accepts_valid_config():
    ok, err = validate_agent_kernel_settings(
        {
            "type": "dsh",
            "dsh": {"profile": "headless"},
            "per_request_selection": False,
            "mcp_tool_policy": {
                "mode": "restrict",
                "allow": ["siyi_health"],
                "deny": ["siyi_notes_delete"],
                "per_kernel": {"claude_code": {"allow": ["siyi_notes_search"]}},
            },
        }
    )
    assert ok, err
    assert err is None


def test_validate_agent_kernel_accepts_bool_like():
    ok, err = validate_agent_kernel_settings(
        {
            "per_request_selection": "true",
            "mcp_tool_policy": '{"mode": "allow_all"}',
        }
    )
    assert ok, err


def test_validate_agent_kernel_rejects_invalid_bool():
    ok, err = validate_agent_kernel_settings({"per_request_selection": "not-a-bool"})
    assert ok is False
    assert "per_request_selection" in (err or "")


def test_validate_agent_kernel_rejects_invalid_policy_mode():
    ok, err = validate_agent_kernel_settings({"mcp_tool_policy": {"mode": "bogus"}})
    assert ok is False
    assert "mcp_tool_policy.mode" in (err or "")


def test_validate_agent_kernel_rejects_non_dict():
    ok, err = validate_agent_kernel_settings([1, 2])
    assert ok is False
    assert "dict" in (err or "")


def test_validate_agent_kernel_accepts_none():
    assert validate_agent_kernel_settings(None) == (True, None)


# ── 配置面：LocalSettingsRepository.validate_setting 集成 ─────────


def test_repository_validate_agent_kernel():
    data_store = LocalDataStore(base_dir=str(__import__("tempfile").mkdtemp()))
    repo = LocalSettingsRepository(data_store)

    ok, err = repo.validate_setting("agent_kernel", {"per_request_selection": False})
    assert ok, err

    ok, err = repo.validate_setting("agent_kernel", {"per_request_selection": "nope"})
    assert ok is False

    ok, err = repo.validate_setting("agent_kernel", {"mcp_tool_policy": {"mode": "restrict"}})
    assert ok, err


# ── 配置面：迁移持久化默认值 ─────────────────────────────────────


def test_migration_persists_agent_kernel_default(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalSettingsRepository(data_store)
    migrate_settings_repository(repo)
    migrated = data_store.load_json("settings", {})
    assert migrated[KEY_AGENT_KERNEL] == {
        "per_request_selection": True,
        "mcp_tool_policy": {"mode": "allow_all"},
    }


def test_migration_preserves_existing_agent_kernel(tmp_path):
    existing = {"type": "dsh", "dsh": {"profile": "siyi-fast"}, "per_request_selection": True}
    data_store = LocalDataStore(base_dir=str(tmp_path))
    data_store.save_json("settings", {KEY_AGENT_KERNEL: existing})
    repo = LocalSettingsRepository(data_store)
    migrate_settings_repository(repo)
    migrated = data_store.load_json("settings", {})
    # 用户已配置的 agent_kernel 不被默认值覆盖
    assert migrated[KEY_AGENT_KERNEL] == existing


# ── 工具策略：resolve_kernel_identity（--kernel 回退）───────────


def test_resolve_kernel_identity_concrete():
    assert tool_policy.resolve_kernel_identity({"type": "dsh"}) == "dsh"
    assert tool_policy.resolve_kernel_identity({"type": "claude_code"}) == "claude_code"
    assert tool_policy.resolve_kernel_identity({"type": "codex"}) == "codex"
    assert tool_policy.resolve_kernel_identity('{"type": "codex"}') == "codex"
    assert tool_policy.resolve_kernel_identity({"type": "DSH"}) == "dsh"  # 大小写不敏感


def test_resolve_kernel_identity_default_empty():
    # none / 缺失 / auto / 坏值 → ""（per-kernel 策略不生效，保持现状）
    assert tool_policy.resolve_kernel_identity({"type": "none"}) == ""
    assert tool_policy.resolve_kernel_identity({}) == ""
    assert tool_policy.resolve_kernel_identity({"type": "auto"}) == ""
    assert tool_policy.resolve_kernel_identity(None) == ""
    assert tool_policy.resolve_kernel_identity("not-json") == ""
    assert tool_policy.resolve_kernel_identity([]) == ""


# ── MCP Server：策略/内核回退链（无 context 的独立进程）──────────


def _make_store(tmp_path, agent_kernel=None) -> SiyiStore:
    store = SiyiStore(data_dir=str(tmp_path))
    settings: dict = {}
    if agent_kernel is not None:
        settings[KEY_AGENT_KERNEL] = agent_kernel
    store.store.save_json("settings", settings)
    return store


def test_server_load_mcp_tool_policy_absent_is_none(tmp_path):
    store = _make_store(tmp_path, agent_kernel=None)
    assert _load_mcp_tool_policy_from_store(store) is None  # 现状：无门控


def test_server_load_mcp_tool_policy_semantic_default_is_none(tmp_path):
    # 迁移后默认（F=True 开启、H={"mode":"allow_all"}）中 H 与缺省等价 → None（行为逐字节一致）
    store = _make_store(
        tmp_path,
        agent_kernel={
            "per_request_selection": True,
            "mcp_tool_policy": {"mode": "allow_all"},
        },
    )
    assert _load_mcp_tool_policy_from_store(store) is None


def test_server_load_mcp_tool_policy_explicit_restrict(tmp_path):
    store = _make_store(
        tmp_path,
        agent_kernel={
            "mcp_tool_policy": {"mode": "restrict", "allow": ["siyi_health"]},
        },
    )
    policy = _load_mcp_tool_policy_from_store(store)
    assert policy is not None
    assert policy["mode"] == "restrict"
    assert "siyi_health" in policy["allow"]


def test_server_load_kernel_from_store(tmp_path):
    assert _load_kernel_from_store(_make_store(tmp_path, {"type": "dsh"})) == "dsh"
    assert _load_kernel_from_store(_make_store(tmp_path, {"type": "codex"})) == "codex"
    # 无内核 → 空串（现状默认）
    assert _load_kernel_from_store(_make_store(tmp_path, {"type": "none"})) == ""
    assert _load_kernel_from_store(_make_store(tmp_path, agent_kernel=None)) == ""
