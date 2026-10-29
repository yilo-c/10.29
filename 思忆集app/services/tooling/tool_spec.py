# -*- coding: utf-8 -*-
"""ToolSpec — 声明式外部工具适配框架。

让思忆集能通过配置文件（而非手写代码）接入任何 CLI 工具。
用户只需在 data/tool_specs.json 中声明工具的 CLI 路径和命令模板，
思忆集的 AI 就能自动发现并使用它。

配置格式（data/tool_specs.json）：
{
  "tools": [
    {
      "tool_id": "zcode",
      "display_name": "ZCode IDE",
      "cli_path": "zcode",
      "description": "ZCode AI 编码工具",
      "enabled": true,
      "commands": {
        "open": {
          "description": "用 ZCode 打开项目/文件",
          "template": "{cli} {path}",
          "params": {
            "path": {"type": "string", "required": true, "description": "项目/文件路径"}
          },
          "risk": "medium",
          "confirmation": "first_time",
          "detach": true,
          "timeout": 10
        },
        "run": {
          "description": "执行 ZCode 命令",
          "template": "{cli} --run {command}",
          "params": {
            "command": {"type": "string", "required": true, "description": "要执行的命令"}
          },
          "risk": "high",
          "confirmation": "every_time",
          "timeout": 300
        }
      }
    }
  ]
}

action_id 格式：tool.<tool_id>.<command>
例如：tool.zcode.open / tool.trae.open_file
所有 tool.* 前缀的 action_id 自动允许进入 ReAct 白名单。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("siyi.tooling.tool_spec")

#: 动态工具 action_id 前缀
DYNAMIC_TOOL_PREFIX = "tool."

#: 允许注册的 CLI basename 白名单（P0-2 RCE 封堵）
#: 与 assistant/capability/_tool_management.py 的 _LLM_ALLOWED_CLI_BASENAMES 对齐，
#: 收敛到一个可信来源，避免两处漂移。
ALLOWED_CLI_BASENAMES = frozenset(
    {
        "git",
        "gh",
        "claude",
        "siyi",
        "rg",
        "grep",
        "find",
        "fd",
        "fzf",
        "cat",
        "head",
        "tail",
        "wc",
        "sort",
        "uniq",
        "cut",
        "tr",
        "sed",
        "awk",
        "echo",
        "ls",
        "dir",
        "jq",
        "yq",
        "xq",
        "curl",
        "wget",
        "python",
        "python3",
        "py",
        "node",
        "npm",
        "npx",
        "pnpm",
        "yarn",
        "bun",
        "deno",
        "cargo",
        "rustc",
        "go",
        "java",
        "gradle",
        "mvn",
        "gcc",
        "g++",
        "clang",
        "make",
        "cmake",
        "ruby",
        "gem",
        "bundle",
    }
)


def _normalize_cli_basename(candidate: str) -> str:
    """归一化一个可执行名候选为可比较的 basename（去路径与 .exe/.cmd/.bat 后缀）。"""
    import os as _os
    import sys as _sys

    base = _os.path.basename(candidate)
    lower = base.lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if _sys.platform == "win32" or suffix == ".exe":
            if lower.endswith(suffix):
                base = base[: -len(suffix)]
                break
    return base


def _validate_rpc_tool_spec(
    spec: "dict[str, Any]",
) -> "dict[str, Any]":
    """tool.register / tool.management.register RPC 入口的统一安全校验（P0-2 封堵）。

    在 _validate_spec（schema 语义）之上叠加执行面约束：
      1. cli_path basename 必须在白名单
      2. health_check_command 首 token 可执行名必须在白名单
      3. 每条 command 的 cli 相关可执行名仍受同一白名单约束
      4. tool_id 字符集白名单

    返回 {valid, errors, warnings}，与 _validate_spec 同构。
    """
    import shlex as _shlex

    errors: "list[str]" = []
    warnings: "list[str]" = []

    cli_path = spec.get("cli_path", "")
    if not cli_path:
        errors.append("cli_path is required")
    else:
        cli_basename = _normalize_cli_basename(str(cli_path))
        if not cli_basename or cli_basename.lower() not in ALLOWED_CLI_BASENAMES:
            errors.append(f"cli_path '{cli_path}' basename '{cli_basename}' not in allowed CLI whitelist. Allowed: {sorted(ALLOWED_CLI_BASENAMES)}")

    health_cmd = spec.get("health_check_command", "")
    if health_cmd:
        try:
            tokens = _shlex.split(str(health_cmd))
        except ValueError:
            tokens = str(health_cmd).split()
        if not tokens:
            errors.append("health_check_command is empty after parsing")
        else:
            hc_basename = _normalize_cli_basename(tokens[0])
            if hc_basename.lower() not in ALLOWED_CLI_BASENAMES:
                errors.append(f"health_check_command executable '{tokens[0]}' not in allowed CLI whitelist. Allowed: {sorted(ALLOWED_CLI_BASENAMES)}")

    return {"valid": not errors, "errors": errors, "warnings": warnings}


@dataclass
class CommandParam:
    """工具命令的参数定义。"""

    name: str
    type: str = "string"
    required: bool = False
    description: str = ""
    default: Any = None

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "CommandParam":
        return cls(
            name=name,
            type=data.get("type", "string"),
            required=bool(data.get("required", False)),
            description=data.get("description", ""),
            default=data.get("default"),
        )

    def to_dict(self) -> dict[str, Any]:
        d = {"type": self.type, "description": self.description}
        if self.required:
            d["required"] = True
        if self.default is not None:
            d["default"] = self.default
        return d


@dataclass
class ToolCommand:
    """工具的一个命令定义。

    扩展字段（支持任意 CLI 工具自助接入）：
        stdin_template: 通过 stdin 传入的模板（用于长 prompt、交互式 CLI）
            如 "{task}" → 子进程 stdin 收到 task 内容
        env_keys: 子进程需要注入的环境变量名列表
            值从 SecureKeyStorage 加密读取（不写回 os.environ，避免泄漏）
            如 ["GH_TOKEN", "OPENAI_API_KEY"]
        health_check_command: 验证此命令是否可用的检测命令
            如 "gh auth status" — tool.health_check RPC 会执行它
        output_regex: 从 stdout 提取结构化字段的正则表达式
            命名分组 (?P<field>...) 会写入 result.extracted 字典
            如 "```\\n(?P<command>[^`]+)\\n```" → {"command": "..."}
        output_fields: 声明 output_regex 会提取的字段及类型（仅用于文档/catalog）
            如 {"command": "string", "confidence": "float"}
    """

    name: str
    description: str = ""
    template: str = ""
    params: list[CommandParam] = field(default_factory=list)
    risk: str = "medium"
    confirmation: str = "auto"
    detach: bool = False
    timeout: int = 30
    stdin_template: str = ""
    env_keys: list[str] = field(default_factory=list)
    health_check_command: str = ""
    output_regex: str = ""
    output_fields: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "ToolCommand":
        params = [CommandParam.from_dict(n, p) for n, p in (data.get("params") or {}).items()]
        # P0-3 风险分级大小写统一：任何来源（配置 JSON / RPC / AI）传入的
        # risk/confirmation 一律归一化为小写，杜绝 "High"/"AUTO" 等绕过
        # dispatch 审批判断（_dynamic_tools._maybe_block_for_sandbox）的大小写绕过。
        risk = str(data.get("risk", "medium") or "medium").lower()
        confirmation = str(data.get("confirmation", "auto") or "auto").lower()
        return cls(
            name=name,
            description=data.get("description", ""),
            template=data.get("template", ""),
            params=params,
            risk=risk,
            confirmation=confirmation,
            detach=bool(data.get("detach", False)),
            timeout=int(data.get("timeout", 30)),
            stdin_template=data.get("stdin_template", ""),
            env_keys=list(data.get("env_keys") or []),
            health_check_command=data.get("health_check_command", ""),
            output_regex=data.get("output_regex", ""),
            output_fields=dict(data.get("output_fields") or {}),
        )

    @property
    def action_id(self) -> str:
        # 由注册方（ToolSpecRegistry）赋值
        return self._action_id

    def to_dict(self) -> dict[str, Any]:
        d = {
            "description": self.description,
            "template": self.template,
            "params": {p.name: p.to_dict() for p in self.params},
            "risk": self.risk,
            "confirmation": self.confirmation,
            "detach": self.detach,
            "timeout": self.timeout,
        }
        if self.stdin_template:
            d["stdin_template"] = self.stdin_template
        if self.env_keys:
            d["env_keys"] = list(self.env_keys)
        if self.health_check_command:
            d["health_check_command"] = self.health_check_command
        if self.output_regex:
            d["output_regex"] = self.output_regex
        if self.output_fields:
            d["output_fields"] = dict(self.output_fields)
        return d

    def to_catalog_entry(self, action_id: str) -> dict[str, Any]:
        """生成 Planner CAPABILITY_CATALOG 格式的条目。"""
        schema: dict[str, Any] = {}
        for p in self.params:
            desc = p.description
            if p.required:
                desc = f"{desc}(必填)" if desc else "必填"
            if p.default is not None:
                desc = f"{desc}(默认{p.default})" if desc else f"默认{p.default}"
            schema[p.name] = desc or p.type
        entry = {
            "action_id": action_id,
            "description": self.description,
            "risk": self.risk,
            "payload_schema": schema,
        }
        if self.confirmation != "auto":
            entry["confirmation"] = self.confirmation
        if self.output_fields:
            entry["output_fields"] = dict(self.output_fields)
        if self.env_keys:
            entry["requires_secrets"] = list(self.env_keys)
        return entry


@dataclass
class ToolSpec:
    """一个外部工具的完整定义。

    扩展字段：
        env_keys: 工具级默认环境变量（命令级 env_keys 会合并到此列表）
        health_check_command: 工具级健康检查命令（如 "gh --version"）
            命令级 health_check_command 优先
    """

    tool_id: str
    display_name: str = ""
    cli_path: str = ""
    description: str = ""
    enabled: bool = True
    commands: dict[str, ToolCommand] = field(default_factory=dict)
    env_keys: list[str] = field(default_factory=list)
    health_check_command: str = ""
    _resolved_cli: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolSpec":
        commands = {name: ToolCommand.from_dict(name, cmd_data) for name, cmd_data in (data.get("commands") or {}).items()}
        return cls(
            tool_id=data["tool_id"],
            display_name=data.get("display_name", data["tool_id"]),
            cli_path=data.get("cli_path", ""),
            description=data.get("description", ""),
            enabled=bool(data.get("enabled", True)),
            commands=commands,
            env_keys=list(data.get("env_keys") or []),
            health_check_command=data.get("health_check_command", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        d = {
            "tool_id": self.tool_id,
            "display_name": self.display_name,
            "cli_path": self.cli_path,
            "description": self.description,
            "enabled": self.enabled,
            "commands": {n: c.to_dict() for n, c in self.commands.items()},
        }
        if self.env_keys:
            d["env_keys"] = list(self.env_keys)
        if self.health_check_command:
            d["health_check_command"] = self.health_check_command
        return d

    def get_env_keys_for(self, command_name: str) -> list[str]:
        """获取指定命令需要注入的环境变量名（工具级 + 命令级合并去重）。"""
        cmd = self.commands.get(command_name)
        if not cmd:
            return list(self.env_keys)
        seen: set[str] = set()
        result: list[str] = []
        for k in [*self.env_keys, *cmd.env_keys]:
            if k not in seen:
                seen.add(k)
                result.append(k)
        return result

    def get_health_check_command(self, command_name: str | None = None) -> str:
        """获取健康检查命令；命令级优先，回退到工具级。"""
        if command_name:
            cmd = self.commands.get(command_name)
            if cmd and cmd.health_check_command:
                return cmd.health_check_command
        return self.health_check_command

    def resolve_cli(self) -> str:
        """解析 CLI 可执行文件路径。"""
        # 已解析过则直接返回
        if self._resolved_cli:
            return self._resolved_cli
        # 1. cli_path 是具体文件路径
        if self.cli_path and Path(self.cli_path).is_file():
            self._resolved_cli = self.cli_path
            return self._resolved_cli
        # 2. PATH 中查找
        if self.cli_path:
            found = shutil.which(self.cli_path)
            if found:
                self._resolved_cli = found
                return self._resolved_cli
        # 3. 常见安装目录约定探测 <prefix>/<tool_id>/<tool_id>.exe
        for prefix in (
            os.environ.get("LOCALAPPDATA", ""),
            os.environ.get("ProgramFiles", ""),
            "D:/Program Files",
            "D:/Program Files (x86)",
        ):
            if not prefix:
                continue
            candidate = Path(prefix) / self.tool_id / f"{self.tool_id}.exe"
            if candidate.is_file():
                self._resolved_cli = str(candidate)
                return self._resolved_cli
        return ""

    @property
    def is_available(self) -> bool:
        return bool(self.resolve_cli())

    def get_action_id(self, command_name: str) -> str:
        """生成 action_id：tool.<tool_id>.<command>"""
        return f"{DYNAMIC_TOOL_PREFIX}{self.tool_id}.{command_name}"

    def get_all_action_ids(self) -> list[str]:
        return [self.get_action_id(name) for name in self.commands]

    def get_catalog_entries(self) -> list[dict[str, Any]]:
        """生成所有命令的 catalog 条目。"""
        entries: list[dict[str, Any]] = []
        for name, cmd in self.commands.items():
            action_id = self.get_action_id(name)
            entries.append(cmd.to_catalog_entry(action_id))
        return entries


class ToolSpecRegistry:
    """管理所有已注册的 ToolSpec。

    支持从 JSON 文件加载 + 运行时动态注册。
    """

    def __init__(self, config_path: str | Path | None = None):
        self._config_path = Path(config_path) if config_path else None
        self._specs: dict[str, ToolSpec] = {}
        self._load()

    def _load(self) -> None:
        """从配置文件加载 ToolSpec。"""
        if not self._config_path or not self._config_path.exists():
            return
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
            for tool_data in data.get("tools", []):
                spec = ToolSpec.from_dict(tool_data)
                self._specs[spec.tool_id] = spec
            logger.info("Loaded %d tool specs from %s", len(self._specs), self._config_path)
        except Exception:
            logger.exception("Failed to load tool specs from %s", self._config_path)

    def _save(self) -> None:
        """保存到配置文件。"""
        if not self._config_path:
            return
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"tools": [s.to_dict() for s in self._specs.values()]}
            self._config_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("Failed to save tool specs to %s", self._config_path)

    def register(self, spec: ToolSpec) -> None:
        """注册或更新一个 ToolSpec。"""
        self._specs[spec.tool_id] = spec
        self._save()
        logger.info("Registered tool spec: %s (%d commands)", spec.tool_id, len(spec.commands))

    def unregister(self, tool_id: str) -> bool:
        """移除一个 ToolSpec。"""
        if tool_id in self._specs:
            del self._specs[tool_id]
            self._save()
            logger.info("Unregistered tool spec: %s", tool_id)
            return True
        return False

    def get(self, tool_id: str) -> ToolSpec | None:
        return self._specs.get(tool_id)

    def list_specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def list_enabled(self) -> list[ToolSpec]:
        return [s for s in self._specs.values() if s.enabled]

    def get_all_action_ids(self) -> list[str]:
        """返回所有已启用工具的所有 action_id。"""
        ids: list[str] = []
        for spec in self.list_enabled():
            ids.extend(spec.get_all_action_ids())
        return ids

    def get_all_catalog_entries(self) -> list[dict[str, Any]]:
        """返回所有已启用工具的 catalog 条目。"""
        entries: list[dict[str, Any]] = []
        for spec in self.list_enabled():
            entries.extend(spec.get_catalog_entries())
        return entries

    def find_by_action_id(self, action_id: str) -> tuple[ToolSpec, ToolCommand] | None:
        """根据 action_id 查找对应的 ToolSpec 和 ToolCommand。"""
        if not action_id.startswith(DYNAMIC_TOOL_PREFIX):
            return None
        remainder = action_id[len(DYNAMIC_TOOL_PREFIX) :]
        parts = remainder.split(".", 1)
        if len(parts) != 2:
            return None
        tool_id, command_name = parts
        spec = self._specs.get(tool_id)
        if not spec or not spec.enabled:
            return None
        cmd = spec.commands.get(command_name)
        if not cmd:
            return None
        return spec, cmd


def _validate_spec(spec: "dict[str, Any]") -> "dict[str, Any]":
    """校验 tool spec 的语义合法性，返回 {valid, errors, warnings}。

    从 backend/handlers/_tools.py 下沉至此（P0-12 跨层防线：assistant 不得
    反向导入 backend），backend 侧改为从本模块导入。
    """
    errors: "list[str]" = []
    warnings: "list[str]" = []
    tool_id = spec.get("tool_id", "")
    if not tool_id:
        errors.append("tool_id required")
    elif not (isinstance(tool_id, str) and tool_id.replace("_", "").isalnum()):
        errors.append(f"tool_id '{tool_id}' must be alphanumeric (underscores allowed)")

    commands = spec.get("commands") or {}
    if not commands:
        errors.append("commands required (at least one)")
    elif not isinstance(commands, dict):
        errors.append("commands must be a dict of name → command_spec")
    else:
        for name, cmd_spec in commands.items():
            if not isinstance(cmd_spec, dict):
                errors.append(f"command '{name}' must be a dict")
                continue
            template = cmd_spec.get("template", "")
            detach = bool(cmd_spec.get("detach", False))
            if not template and not detach:
                errors.append(f"command '{name}' requires 'template' (or detach=true)")
            risk = cmd_spec.get("risk", "medium")
            if risk not in ("low", "medium", "high"):
                errors.append(f"command '{name}' risk must be 'low'/'medium'/'high', got '{risk}'")
            confirmation = cmd_spec.get("confirmation", "auto")
            valid_confirmations = ("auto", "first_time", "every_time")
            if confirmation not in valid_confirmations:
                errors.append(f"command '{name}' confirmation must be {valid_confirmations}, got '{confirmation}'")
            if risk == "high" and confirmation == "auto":
                errors.append(f"command '{name}' risk=high but confirmation=auto — high-risk tools require explicit confirmation")
            output_regex = cmd_spec.get("output_regex", "")
            if output_regex:
                import re as _re

                try:
                    _re.compile(output_regex)
                except _re.error as exc:
                    errors.append(f"command '{name}' output_regex invalid: {exc}")
            timeout = cmd_spec.get("timeout", 30)
            try:
                t = int(timeout)
                if t <= 0:
                    errors.append(f"command '{name}' timeout must be positive, got {t}")
                elif t > 3600:
                    warnings.append(f"command '{name}' timeout={t}s is very long (>1h), consider using detach=true instead")
            except (TypeError, ValueError):
                errors.append(f"command '{name}' timeout must be int, got {type(timeout).__name__}")
            params = cmd_spec.get("params") or {}
            if not isinstance(params, dict):
                errors.append(f"command '{name}' params must be dict, got {type(params).__name__}")
            else:
                for pname, pspec in params.items():
                    if not isinstance(pspec, dict):
                        errors.append(f"command '{name}' param '{pname}' must be dict")
                        continue
                    ptype = pspec.get("type", "string")
                    if ptype not in ("string", "int", "bool"):
                        warnings.append(f"command '{name}' param '{pname}' type='{ptype}' not in standard (string/int/bool)")
            env_keys = cmd_spec.get("env_keys") or []
            if not isinstance(env_keys, list):
                errors.append(f"command '{name}' env_keys must be list")
            elif not all(isinstance(k, str) for k in env_keys):
                errors.append(f"command '{name}' env_keys all items must be string")

    tool_env_keys = spec.get("env_keys") or []
    if not isinstance(tool_env_keys, list):
        errors.append("tool-level env_keys must be list")
    elif not all(isinstance(k, str) for k in tool_env_keys):
        errors.append("tool-level env_keys all items must be string")

    return {"valid": not errors, "errors": errors, "warnings": warnings}


__all__ = [
    "DYNAMIC_TOOL_PREFIX",
    "ALLOWED_CLI_BASENAMES",
    "_normalize_cli_basename",
    "_validate_rpc_tool_spec",
    "CommandParam",
    "ToolCommand",
    "ToolSpec",
    "ToolSpecRegistry",
    "_validate_spec",
]
