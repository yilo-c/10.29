"""P0-2 / P0-3 攻击链守卫回归测试（B-14 补回，原 test_batch1_attack_chain.py 从未入库）。

P0-2：tool.register / tool.management.register 的统一 CLI 白名单校验
      `_validate_rpc_tool_spec` 必须拒绝 cli_path / health_check_command 指向
      白名单外可执行名（cmd.exe / powershell.exe / reset_machine.exe 等 RCE 载体）。
P0-3：risk / confirmation 大小写归一（"High"→"high"、"Every_TIME"→"every_time"），
      杜绝大小写绕过 dispatch 审批判断。
纯单元测试，无后端 / 文件系统依赖。
"""
from __future__ import annotations

from services.tooling.tool_spec import (
    ALLOWED_CLI_BASENAMES,
    ToolCommand,
    _validate_rpc_tool_spec,
)


# ── P0-2：CLI 白名单双校验 ──
class TestP0_2CliWhitelist:
    def test_cli_path_cmd_exe_rejected(self):
        res = _validate_rpc_tool_spec({"cli_path": "cmd.exe"})
        assert res["valid"] is False
        assert any("whitelist" in e.lower() for e in res["errors"])

    def test_cli_path_powershell_exe_rejected(self):
        res = _validate_rpc_tool_spec(
            {"cli_path": "C:\\Windows\\System32\\powershell.exe"}
        )
        assert res["valid"] is False

    def test_cli_path_reset_machine_exe_rejected(self):
        res = _validate_rpc_tool_spec({"cli_path": "reset_machine.exe"})
        assert res["valid"] is False

    def test_health_check_command_cmd_exe_rejected(self):
        res = _validate_rpc_tool_spec(
            {"cli_path": "git", "health_check_command": "cmd.exe /c echo hi"}
        )
        assert res["valid"] is False
        assert any("health_check_command" in e.lower() for e in res["errors"])

    def test_allowed_cli_passes(self):
        res = _validate_rpc_tool_spec({"cli_path": "git"})
        assert res["valid"] is True
        assert res["errors"] == []

    def test_whitelist_does_not_include_rce_carriers(self):
        # 回归守卫：若白名单误纳入高危可执行名，本断言立即红
        assert "cmd.exe" not in ALLOWED_CLI_BASENAMES
        assert "powershell.exe" not in ALLOWED_CLI_BASENAMES
        assert "reset_machine.exe" not in ALLOWED_CLI_BASENAMES


# ── P0-3：risk / confirmation 大小写归一 ──
class TestP0_3RiskCaseNormalization:
    def test_high_normalized_to_lowercase(self):
        cmd = ToolCommand.from_dict("c", {"risk": "High"})
        assert cmd.risk == "high"

    def test_every_time_normalized_to_lowercase(self):
        cmd = ToolCommand.from_dict("c", {"confirmation": "Every_TIME"})
        assert cmd.confirmation == "every_time"

    def test_mixed_case_both_normalized(self):
        cmd = ToolCommand.from_dict(
            "c", {"risk": "MEDIUM", "confirmation": "FIRST_Time"}
        )
        assert cmd.risk == "medium"
        assert cmd.confirmation == "first_time"
