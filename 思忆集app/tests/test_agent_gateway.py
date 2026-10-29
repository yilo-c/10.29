"""AgentGateway（M1，执行内核可替换端口）单元测试。

覆盖：工厂路由 / 配置解析合并 / 各适配器行为（subprocess mock，不打真实 CLI）。
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from services.agent_gateway import (
    AGENT_GATEWAY_ADAPTERS,
    DEFAULT_AGENT_KERNEL_CONFIG,
    AgentRun,
    ClaudeCodeGateway,
    CodexGateway,
    DshGateway,
    NoneGateway,
    build_gateway,
    build_gateway_from_repository,
    build_gateway_from_settings_value,
    parse_agent_kernel_value,
)
from services.agent_gateway import adapters as adapters_mod
from services.agent_gateway.adapters import adapter_kwargs


class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ── 注册表与配置 ───────────────────────────────────────────────


def test_registry_covers_all_kernel_types():
    assert set(AGENT_GATEWAY_ADAPTERS) == {"none", "dsh", "claude_code", "codex"}


def test_default_kernel_is_dsh():
    assert DEFAULT_AGENT_KERNEL_CONFIG["type"] == "dsh"


def test_parse_empty_value_yields_defaults():
    cfg = parse_agent_kernel_value("")
    assert cfg["type"] == "dsh"
    assert cfg["dsh"]["profile"] == "headless"


def test_parse_dict_config():
    cfg = parse_agent_kernel_value({"type": "claude_code", "claude_code": {"max_turns": 3}})
    assert cfg["type"] == "claude_code"
    assert cfg["claude_code"]["max_turns"] == 3
    # 未提供的嵌套键保留默认
    assert cfg["claude_code"]["timeout_s"] == 240


def test_parse_json_string_config():
    raw = json.dumps({"type": "codex", "codex": {"model": "gpt-5"}})
    cfg = parse_agent_kernel_value(raw)
    assert cfg["type"] == "codex"
    assert cfg["codex"]["model"] == "gpt-5"


def test_parse_invalid_json_falls_back_to_defaults():
    cfg = parse_agent_kernel_value("{not json")
    assert cfg["type"] == "dsh"


def test_parse_non_object_json_falls_back_to_defaults():
    cfg = parse_agent_kernel_value("[1, 2]")
    assert cfg["type"] == "dsh"


def test_build_gateway_routes_each_type():
    assert isinstance(build_gateway({"type": "none"}), NoneGateway)
    assert isinstance(build_gateway({"type": "dsh"}), DshGateway)
    assert isinstance(build_gateway({"type": "claude_code"}), ClaudeCodeGateway)
    assert isinstance(build_gateway({"type": "codex"}), CodexGateway)


def test_build_gateway_unknown_type_degrades_to_none():
    assert isinstance(build_gateway({"type": "nonexistent_kernel"}), NoneGateway)


def test_build_gateway_from_settings_value_json_string():
    gw = build_gateway_from_settings_value(json.dumps({"type": "none"}))
    assert isinstance(gw, NoneGateway)


def test_dsh_adapter_kwargs_applied():
    gw = build_gateway({"type": "dsh", "dsh": {"profile": "siyi", "timeout_s": 60, "unknown_key": 1}})
    assert isinstance(gw, DshGateway)
    assert gw.profile == "siyi"
    assert gw.timeout_s == 60


def test_adapter_kwargs_filters_unknown_keys():
    assert adapter_kwargs(DshGateway, {"profile": "x", "bogus": 1}) == {"profile": "x"}


# ── NoneGateway ────────────────────────────────────────────────


def test_none_gateway_submit_unavailable():
    run = NoneGateway().submit("hello")
    assert isinstance(run, AgentRun)
    assert run.status == "unavailable"
    assert not run.ok


# ── DshGateway（mock subprocess）─────────────────────────────


def test_dsh_health_missing_cli(monkeypatch):
    monkeypatch.setattr(adapters_mod.shutil, "which", lambda _name: None)
    health = DshGateway().health()
    assert health["kernel"] == "dsh"
    assert health["available"] is False


def test_dsh_submit_missing_cli(monkeypatch):
    monkeypatch.setattr(adapters_mod.shutil, "which", lambda _name: None)
    run = DshGateway().submit("hello")
    assert run.status == "unavailable"


def test_dsh_submit_success(monkeypatch, tmp_path):
    fake_exe = tmp_path / "dsh.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(adapters_mod.shutil, "which", lambda _name: str(fake_exe))
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompleted(stdout="final answer\n")

    monkeypatch.setattr(adapters_mod.subprocess, "run", fake_run)
    run = DshGateway(profile="siyi").submit("say hi")
    assert run.ok
    assert run.result == "final answer"
    assert captured["cmd"][:3] == [str(fake_exe), "--profile", "siyi"]


def test_dsh_submit_failure_reports_stderr(monkeypatch, tmp_path):
    fake_exe = tmp_path / "dsh.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(adapters_mod.shutil, "which", lambda _name: str(fake_exe))
    monkeypatch.setattr(
        adapters_mod.subprocess, "run",
        lambda cmd, **kw: FakeCompleted(returncode=1, stdout="", stderr="boom"),
    )
    run = DshGateway().submit("hello")
    assert run.status == "failed"
    assert "boom" in (run.error or "")


def test_dsh_submit_timeout(monkeypatch, tmp_path):
    fake_exe = tmp_path / "dsh.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(adapters_mod.shutil, "which", lambda _name: str(fake_exe))

    def raise_timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 5)

    monkeypatch.setattr(adapters_mod.subprocess, "run", raise_timeout)
    run = DshGateway(timeout_s=5).submit("hello")
    assert run.status == "failed"
    assert "timeout" in (run.error or "")


# ── ClaudeCodeGateway（mock subprocess）─────────────────────


def test_claude_submit_parses_json_result(monkeypatch):
    monkeypatch.setattr(adapters_mod.shutil, "which", lambda _name: "C:/fake/claude.exe")
    payload = json.dumps({"result": "claude says hi", "is_error": False})
    monkeypatch.setattr(
        adapters_mod.subprocess, "run",
        lambda cmd, **kw: FakeCompleted(stdout=payload),
    )
    run = ClaudeCodeGateway().submit("hi")
    assert run.ok
    assert run.result == "claude says hi"


def test_claude_submit_bad_json_fails(monkeypatch):
    monkeypatch.setattr(adapters_mod.shutil, "which", lambda _name: "C:/fake/claude.exe")
    monkeypatch.setattr(
        adapters_mod.subprocess, "run",
        lambda cmd, **kw: FakeCompleted(stdout="not-json", stderr="auth error"),
    )
    run = ClaudeCodeGateway().submit("hi")
    assert run.status == "failed"
    assert "auth error" in (run.error or "")


# ── CodexGateway（mock subprocess）──────────────────────────


def test_codex_submit_takes_last_message_event(monkeypatch):
    monkeypatch.setattr(adapters_mod.shutil, "which", lambda _name: "C:/fake/codex.exe")
    stream = "\n".join([
        json.dumps({"type": "session.started"}),
        json.dumps({"type": "agent_message", "message": "first"}),
        json.dumps({"type": "agent_message", "message": "final answer"}),
    ])
    monkeypatch.setattr(
        adapters_mod.subprocess, "run",
        lambda cmd, **kw: FakeCompleted(stdout=stream),
    )
    run = CodexGateway().submit("hi")
    assert run.ok
    assert run.result == "final answer"


def test_health_shapes():
    for gw in (NoneGateway(), DshGateway(), ClaudeCodeGateway(), CodexGateway()):
        health = gw.health()
        assert isinstance(health, dict)
        assert health["kernel"] == gw.name
        assert "available" in health


def test_dsh_submit_profile_override(monkeypatch, tmp_path):
    # M4：submit 支持运行时 profile 覆盖（params.kernel 映射）；默认用 self.profile
    fake_exe = tmp_path / "dsh.exe"
    fake_exe.write_text("", encoding="utf-8")
    captured: dict = {}
    monkeypatch.setattr(adapters_mod.shutil, "which", lambda _name: str(fake_exe))

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return FakeCompleted(returncode=0, stdout="answer\n")

    monkeypatch.setattr(adapters_mod.subprocess, "run", fake_run)

    run = DshGateway(profile="headless").submit("say hi")
    assert run.ok
    assert captured["cmd"][:3] == [str(fake_exe), "--profile", "headless"]

    run2 = DshGateway(profile="headless").submit("say hi", profile="siyi-deep")
    assert run2.ok
    assert captured["cmd"][:3] == [str(fake_exe), "--profile", "siyi-deep"]


# ── DshGateway 方案 B（provider 注入：--patch overlay + provider_env）──


def test_dsh_submit_model_patch_writes_overlay_and_cleans(monkeypatch, tmp_path):
    """model_patch 非空时 spawn 挂 --patch overlay，内容正确且用后即删。"""
    fake_exe = tmp_path / "dsh.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(adapters_mod.shutil, "which", lambda _name: str(fake_exe))
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        idx = cmd.index("--patch")
        captured["patch_path"] = cmd[idx + 1]
        captured["patch_content"] = open(cmd[idx + 1], encoding="utf-8").read()
        return FakeCompleted(returncode=0, stdout="answer\n")

    monkeypatch.setattr(adapters_mod.subprocess, "run", fake_run)

    run = DshGateway(model_patch="deepseek-official/deepseek-chat").submit("say hi")
    assert run.ok
    assert "--patch" in captured["cmd"]
    assert captured["cmd"].index("--patch") < captured["cmd"].index("say hi")
    assert "agent-default-model" in captured["patch_content"]
    assert "deepseek-official" in captured["patch_content"]
    assert "deepseek-chat" in captured["patch_content"]
    assert not os.path.exists(captured["patch_path"]), "临时补丁应随进程结束删除"


def test_dsh_submit_no_patch_by_default(monkeypatch, tmp_path):
    """默认构造（无 model_patch）时命令行不含 --patch，行为与注入机制前一致。"""
    fake_exe = tmp_path / "dsh.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(adapters_mod.shutil, "which", lambda _name: str(fake_exe))
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return FakeCompleted(returncode=0, stdout="answer\n")

    monkeypatch.setattr(adapters_mod.subprocess, "run", fake_run)

    run = DshGateway().submit("say hi")
    assert run.ok
    assert "--patch" not in captured["cmd"]


def test_dsh_submit_invalid_model_patch_skipped(monkeypatch, tmp_path):
    """model_patch 不是 provider/model 形态时跳过注入（不生成补丁）。"""
    fake_exe = tmp_path / "dsh.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(adapters_mod.shutil, "which", lambda _name: str(fake_exe))
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return FakeCompleted(returncode=0, stdout="answer\n")

    monkeypatch.setattr(adapters_mod.subprocess, "run", fake_run)

    run = DshGateway(model_patch="just-a-model-name").submit("say hi")
    assert run.ok
    assert "--patch" not in captured["cmd"]


def test_dsh_submit_timeout_still_cleans_patch(monkeypatch, tmp_path):
    """超时路径（TimeoutExpired）也必须清理临时补丁文件。"""
    fake_exe = tmp_path / "dsh.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(adapters_mod.shutil, "which", lambda _name: str(fake_exe))
    captured: dict = {}

    def raise_timeout(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        raise subprocess.TimeoutExpired(cmd, 5)

    monkeypatch.setattr(adapters_mod.subprocess, "run", raise_timeout)

    run = DshGateway(model_patch="deepseek-official/deepseek-chat").submit("hello")
    assert run.status == "failed"
    assert "timeout" in (run.error or "")
    patch_path = captured["cmd"][captured["cmd"].index("--patch") + 1]
    assert not os.path.exists(patch_path), "超时后补丁文件也应被 finally 清理"


def test_dsh_submit_provider_env_injected_and_node_options_cleaned(monkeypatch, tmp_path):
    """provider_env 注入子进程环境，且 NODE_OPTIONS 清理语义保留。"""
    fake_exe = tmp_path / "dsh.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(adapters_mod.shutil, "which", lambda _name: str(fake_exe))
    monkeypatch.setenv("NODE_OPTIONS", "--bad-shim")
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env") or {}
        return FakeCompleted(returncode=0, stdout="answer\n")

    monkeypatch.setattr(adapters_mod.subprocess, "run", fake_run)

    gw = DshGateway(provider_env={"DEEPSEEK_API_KEY": "sk-test"})
    run = gw.submit("say hi")
    assert run.ok
    assert captured["env"]["DEEPSEEK_API_KEY"] == "sk-test"
    assert "NODE_OPTIONS" not in captured["env"]


class FakeSettingsRepo:
    """build_gateway_from_repository 引用解析测试用的最小 settings 仓储。"""

    def __init__(self, settings=None):
        self._settings = dict(settings or {})

    def get_setting(self, key, default=""):
        return self._settings.get(key, default)

    def set_setting(self, key, value):
        self._settings[key] = value


def test_factory_resolves_provider_env_refs():
    """factory 从仓储构建时解析 provider_env 三种引用（flat:/provider:/字面量）。"""
    from core.settings_schema import KEY_PROVIDER_CONFIGS

    agent_kernel = json.dumps({
        "type": "dsh",
        "dsh": {
            "profile": "siyi",
            "provider_env": {
                "DEEPSEEK_API_KEY": "provider:DeepSeek",
                "DSH_FLAT_KEY": "flat:test_flat_key",
                "LITERAL_KEY": "lit-value",
            },
        },
    })
    repo = FakeSettingsRepo({
        "agent_kernel": agent_kernel,
        "test_flat_key": "sk-flat",
        KEY_PROVIDER_CONFIGS: [{"name": "DeepSeek", "api_key": "sk-plain"}],
    })

    gw = build_gateway_from_repository(repo)

    assert isinstance(gw, DshGateway)
    assert gw.profile == "siyi"
    assert gw.provider_env == {
        "DEEPSEEK_API_KEY": "sk-plain",
        "DSH_FLAT_KEY": "sk-flat",
        "LITERAL_KEY": "lit-value",
    }


def test_factory_provider_env_absent_by_default():
    """无 provider_env 配置时构建结果保持 None（向后兼容，行为零变化）。"""
    repo = FakeSettingsRepo({"agent_kernel": json.dumps({"type": "dsh"})})
    gw = build_gateway_from_repository(repo)
    assert isinstance(gw, DshGateway)
    assert gw.provider_env is None
    assert gw.model_patch == ""
