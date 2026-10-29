"""工具管理能力 — 让 AI 在 chat 中自行发现/注册工具。

注册三个 capability，让 LLM 在 ReAct 循环中能：
- tool.management.list    — 列出已注册工具（让 AI 知道有哪些工具可用）
- tool.management.samples — 列出工具样例库（让 AI 参考 ToolSpec 模板）
- tool.management.register — 注册新工具（含 LLM 来源安全校验）

设计要点：
- 三个 capability 的 handler 内部调用底层 ToolSpecRegistry，与
  backend/handlers/_tools.py 的 RPC 实现共享同一份数据源
- register handler 加 LLM 来源安全校验层（cli_path 白名单 + risk 下限
  + confirmation 限制 + 工具总数上限），防止 LLM 注册恶意命令
- 注册成功后调用 dynamic_tool_domain.refresh()，让新工具立即可被
  下一轮 ReAct 循环看到（配合 stream_tools_path 的每轮重建逻辑）
"""

from __future__ import annotations

import logging
import os
from typing import Any

from services.capability_types import CapabilityGroup, CapabilitySpec

logger = logging.getLogger("siyi.assistant.capability.tool_management")

__all__ = [
    "register_tool_management_capabilities",
    "_validate_llm_tool_spec",
    "_LLM_ALLOWED_CLI_BASENAMES",
    "_MAX_LLM_REGISTERED_TOOLS",
]


#: 允许注册的 CLI basename 白名单（与 services/tooling/tool_spec.ALLOWED_CLI_BASENAMES
#: 收敛为同一来源，消除两处漂移——后端 RPC 与 AI 注册共享同一安全集）。
from services.tooling.tool_spec import ALLOWED_CLI_BASENAMES as _SHARED_CLI_BASENAMES  # noqa: E402

_LLM_ALLOWED_CLI_BASENAMES = _SHARED_CLI_BASENAMES

# 最多允许注册的工具总数
_MAX_LLM_REGISTERED_TOOLS = 50

# AI 注册时每条 command 允许的 risk 等级（low 被禁止，避免绕过沙箱审批）
_LLM_ALLOWED_RISK_LEVELS = frozenset({"medium", "high"})

# AI 注册时每条 command 允许的 confirmation 等级（auto 被禁止）
_LLM_ALLOWED_CONFIRMATIONS = frozenset({"first_time", "every_time"})


def _validate_llm_tool_spec(spec_data: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    """LLM 来源 ToolSpec 安全校验。

    Returns:
        (valid, errors, warnings)
    """
    errors: list[str] = []
    warnings: list[str] = []

    cli_path = spec_data.get("cli_path", "")
    if not cli_path:
        errors.append("cli_path is required")
    else:
        cli_basename = os.path.basename(cli_path)
        if cli_basename.lower().endswith(".exe"):
            cli_basename = cli_basename[:-4]
        if cli_basename not in _LLM_ALLOWED_CLI_BASENAMES:
            errors.append(f"cli_path '{cli_basename}' is not in the allowed list for AI registration. Allowed: {sorted(_LLM_ALLOWED_CLI_BASENAMES)}")

    commands = spec_data.get("commands", {})
    if not commands:
        errors.append("commands must not be empty")
    else:
        for cmd_name, cmd_spec in commands.items():
            if not isinstance(cmd_spec, dict):
                errors.append(f"command '{cmd_name}' must be a dict")
                continue
            risk = cmd_spec.get("risk", "medium")
            if risk not in _LLM_ALLOWED_RISK_LEVELS:
                errors.append(
                    f"command '{cmd_name}' risk='{risk}' not allowed for AI registration; "
                    f"must be one of {sorted(_LLM_ALLOWED_RISK_LEVELS)}"
                    f" (low is forbidden to prevent bypassing sandbox approval)"
                )
            confirmation = cmd_spec.get("confirmation", "auto")
            if confirmation not in _LLM_ALLOWED_CONFIRMATIONS:
                errors.append(
                    f"command '{cmd_name}' confirmation='{confirmation}' not allowed for AI registration; "
                    f"must be one of {sorted(_LLM_ALLOWED_CONFIRMATIONS)}"
                    f" (auto is forbidden)"
                )

    tool_id = spec_data.get("tool_id", "")
    if not tool_id:
        errors.append("tool_id is required")
    elif not all(c.isalnum() or c in "_-" for c in tool_id):
        errors.append("tool_id must only contain alphanumeric/underscore/hyphen")

    # T1.3 安全修复：health_check_command 必须受 CLI 白名单约束。
    # 此前该校验缺失——AI 注册工具时 cli_path 走白名单，但可声明任意
    # health_check_command（如 "rm -rf /"），在 tool_health_check 时被
    # create_subprocess_exec 直接执行（无 shell 但命令本身危险）。
    # 现要求 health_check_command 首个 token（可执行名）也须在白名单内。
    health_cmd = spec_data.get("health_check_command", "")
    if health_cmd:
        import shlex as _shlex

        try:
            tokens = _shlex.split(health_cmd)
        except ValueError:
            tokens = health_cmd.split()
        if not tokens:
            errors.append("health_check_command is empty after parsing")
        else:
            hc_basename = os.path.basename(tokens[0])
            if hc_basename.lower().endswith(".exe"):
                hc_basename = hc_basename[:-4]
            if hc_basename not in _LLM_ALLOWED_CLI_BASENAMES:
                errors.append(
                    f"health_check_command executable '{hc_basename}' is not in the allowed "
                    f"CLI list for AI registration. Allowed: {sorted(_LLM_ALLOWED_CLI_BASENAMES)}"
                )

    return len(errors) == 0, errors, warnings


def _get_tool_registry(router):
    return getattr(router, "tool_registry", None)


def _list_tools_handler(router):
    """tool.management.list handler — 列出已注册工具。

    返回精简版（不含 commands 全量字段），避免 prompt 过长。
    """

    def _handler(args: dict[str, Any]) -> dict[str, Any]:
        tool_registry = _get_tool_registry(router)
        if tool_registry is None:
            return {"success": False, "error": "tool registry not available"}

        tools = []
        for spec in tool_registry.list_specs():
            cli_resolved = spec.resolve_cli()
            commands_summary = []
            for name, cmd in spec.commands.items():
                commands_summary.append(
                    {
                        "name": name,
                        "description": cmd.description,
                        "risk": cmd.risk,
                        "action_id": spec.get_action_id(name),
                    }
                )
            tools.append(
                {
                    "tool_id": spec.tool_id,
                    "display_name": spec.display_name,
                    "cli_path": spec.cli_path,
                    "cli_resolved": cli_resolved,
                    "available": bool(cli_resolved),
                    "enabled": spec.enabled,
                    "description": spec.description,
                    "env_keys": list(spec.env_keys),
                    "commands": commands_summary,
                    "action_ids": spec.get_all_action_ids(),
                }
            )

        return {
            "success": True,
            "tools": tools,
            "total": len(tools),
        }

    return _handler


def _list_samples_handler(router):
    """tool.management.samples handler — 列出工具样例库。"""

    def _handler(args: dict[str, Any]) -> dict[str, Any]:
        from pathlib import Path
        import json

        samples_dir = Path(__file__).resolve().parent.parent.parent / "data" / "tool_specs_samples"
        if not samples_dir.exists():
            return {
                "success": True,
                "samples": [],
                "total": 0,
                "note": "samples dir not found",
            }

        samples = []
        for json_file in sorted(samples_dir.glob("*.json")):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    spec = json.load(f)
            except Exception as exc:
                logger.warning("Failed to load sample %s: %s", json_file, exc)
                continue
            samples.append(
                {
                    "file": json_file.name,
                    "tool_id": spec.get("tool_id", ""),
                    "display_name": spec.get("display_name", ""),
                    "description": spec.get("description", ""),
                    "spec": spec,
                }
            )

        return {
            "success": True,
            "samples": samples,
            "total": len(samples),
            "samples_dir": str(samples_dir),
        }

    return _handler


def _register_tool_handler(router):
    """tool.management.register handler — 注册新工具（含 LLM 安全校验）。

    校验层（在原 schema 校验之上额外加）：
    1. cli_path basename 白名单
    2. 每条 command risk 最低 medium
    3. 每条 command confirmation 不能是 auto
    4. tool_id 字符集白名单
    5. 当前工具总数 < 50

    注册成功后调用 dynamic_tool_domain.refresh()，让新工具立即可被
    下一轮 ReAct 循环看到（stream_tools_path 每轮重建 openai_tools）。
    """

    def _handler(args: dict[str, Any]) -> dict[str, Any]:
        from services.tooling.tool_spec import ToolSpec

        # 兼容 {"spec": {...}} 与平铺两种入参形态
        if "spec" in args and isinstance(args["spec"], dict):
            spec_data = dict(args["spec"])
        else:
            spec_data = dict(args)

        spec_data.pop("_source", None)

        tool_id = spec_data.get("tool_id", "")
        if not tool_id:
            return {"success": False, "error": "tool_id is required"}

        valid, errors, warnings = _validate_llm_tool_spec(spec_data)
        if not valid:
            return {
                "success": False,
                "error": "AI registration validation failed",
                "details": {"valid": False, "errors": errors, "warnings": warnings},
            }

        tool_registry = _get_tool_registry(router)
        if tool_registry is None:
            return {"success": False, "error": "tool registry not available"}

        try:
            current_count = len(list(tool_registry.list_specs()))
        except Exception:
            current_count = 0
        if current_count >= _MAX_LLM_REGISTERED_TOOLS:
            return {
                "success": False,
                "error": (f"maximum number of tools reached ({_MAX_LLM_REGISTERED_TOOLS}); unregister some tools before registering new ones"),
            }

        try:
            # P0-12 跨层防线：校验函数已下沉到 services/tooling/tool_spec.py
            # （原版延迟导入 backend.handlers._tools，已禁止）
            from services.tooling.tool_spec import _validate_spec
        except ImportError:
            _validate_spec = lambda d: {"valid": True, "errors": [], "warnings": []}

        validation = _validate_spec(spec_data)
        if not validation["valid"]:
            return {
                "success": False,
                "error": "schema validation failed",
                "details": validation,
            }

        try:
            spec = ToolSpec.from_dict(spec_data)
        except Exception as exc:
            logger.exception("Failed to construct ToolSpec from AI registration")
            return {"success": False, "error": f"ToolSpec construction failed: {exc}"}

        try:
            tool_registry.register(spec)
        except Exception as exc:
            logger.exception("Failed to register tool from AI")
            return {"success": False, "error": f"registration failed: {exc}"}

        dynamic_domain = getattr(router, "dynamic_tool_domain", None)
        refresh_result = None
        if dynamic_domain is not None:
            try:
                refresh_result = dynamic_domain.refresh()
            except Exception as exc:
                logger.warning("refresh failed after AI registration: %s", exc)
                refresh_result = {"new_actions": [], "error": str(exc)}

        action_ids = spec.get_all_action_ids()
        logger.info(
            "AI registered tool '%s' with actions: %s (refresh added %s)",
            tool_id,
            action_ids,
            refresh_result.get("new_actions", []) if refresh_result else "N/A",
        )

        return {
            "success": True,
            "tool_id": tool_id,
            "action_ids": action_ids,
            "new_actions": refresh_result.get("new_actions", []) if refresh_result else [],
            "warnings": warnings,
        }

    return _handler


def register_tool_management_capabilities(router) -> None:
    """把工具管理能力注册到 CapabilityRegistry 和 router._handlers。

    在 CapabilityRouter._register_builtin_capabilities() 末尾调用。
    """
    registry = router.capability_registry
    if registry is None:
        return

    group_id = "tool_management"
    if registry.get_group(group_id) is None:
        registry.register_group(
            CapabilityGroup(
                group_id=group_id,
                name="工具管理",
                description="让 AI 查询/注册外部 CLI 工具的能力",
            )
        )

    capabilities = [
        CapabilitySpec(
            capability_id="tool.management.list",
            display_name="列出已注册工具",
            description=(
                "列出当前已注册的所有外部 CLI 工具及其命令。用于在不确定有哪些工具可用时先查询工具列表。"
                "场景示例：用户说'现在能调用哪些工具'/'我想用某个 CLI 但不知道注册了没有'。"
                "返回精简版（每条命令含 name/description/risk/action_id）。"
            ),
            group=group_id,
            source="tool_management",
            enabled=True,
            payload_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
            risk_level="read",
            confirmation_level="auto",
            timeout_default=10,
        ),
        CapabilitySpec(
            capability_id="tool.management.samples",
            display_name="列出工具样例库",
            description=(
                "列出 data/tool_specs_samples/ 目录下的工具样例（含完整 ToolSpec JSON）。"
                "用于在用户要注册新工具但不知道 ToolSpec 格式时参考。"
                "场景示例：用户说'我想接入 ripgrep 但不会写配置'，AI 可先调此工具看是否有 ripgrep 样例，有则直接复用样例 spec。"
            ),
            group=group_id,
            source="tool_management",
            enabled=True,
            payload_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
            risk_level="read",
            confirmation_level="auto",
            timeout_default=10,
        ),
        CapabilitySpec(
            capability_id="tool.management.register",
            display_name="注册新工具",
            description=(
                "注册一个新的外部 CLI 工具，注册后立即可在后续对话中调用。"
                "用于用户说'我想用 XX 工具'但该工具尚未注册时。"
                "场景示例：用户说'帮我接入 ripgrep 搜代码'，AI 可调此工具注册 rg 命令。"
                "安全约束（AI 注册时强制）：(1) cli_path 必须在白名单（gh/claude/git/rg/python/node/cargo 等常用开发工具）；"
                "(2) 每条命令 risk 必须 >= medium（low 会被拒，避免绕过审批）；"
                "(3) 每条命令 confirmation 不能是 auto（必须 first_time 或 every_time）；"
                "(4) 工具总数上限 50。"
                "payload_schema 与 tool.register RPC 一致：tool_id/display_name/cli_path/description/commands/env_keys/health_check_command/enabled。"
            ),
            group=group_id,
            source="tool_management",
            enabled=True,
            payload_schema={
                "type": "object",
                "properties": {
                    "tool_id": {"type": "string", "description": "工具唯一标识，如 'ripgrep'"},
                    "display_name": {"type": "string", "description": "显示名称"},
                    "cli_path": {"type": "string", "description": "CLI 可执行文件名（如 'rg'）"},
                    "description": {"type": "string", "description": "工具描述"},
                    "commands": {
                        "type": "object",
                        "description": "命令字典：name → {template, params, risk, confirmation, ...}",
                    },
                    "env_keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "工具级环境变量名列表（从 SecureKeyStorage 加密读取）",
                    },
                    "health_check_command": {"type": "string", "description": "健康检查命令"},
                    "enabled": {"type": "boolean", "description": "是否启用"},
                },
                "required": ["tool_id", "cli_path", "commands"],
            },
            risk_level="write",
            confirmation_level="first_time",
            timeout_default=15,
        ),
    ]

    for spec in capabilities:
        registry.register_capability(spec)

    router._handlers["tool.management.list"] = _list_tools_handler(router)
    router._handlers["tool.management.samples"] = _list_samples_handler(router)
    router._handlers["tool.management.register"] = _register_tool_handler(router)

    logger.info(
        "Registered tool management capabilities: %s",
        [c.capability_id for c in capabilities],
    )
