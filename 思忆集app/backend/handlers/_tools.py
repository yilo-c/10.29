# -*- coding: utf-8 -*-
"""动态工具管理 JSON-RPC handlers。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.tools")

# P0-12 跨层防线：校验函数已下沉到 services/tooling/tool_spec.py（稳定区，
# backend→services.tooling 为合法方向，符合内核分层 Zone）。
from services.tooling.tool_spec import _validate_spec, _validate_rpc_tool_spec  # noqa: E402


def register_tools(registry, context) -> None:
    """注册动态工具管理相关 JSON-RPC 方法。"""

    def _get_router():
        # S0-5（八轮实测 AJ-1）：旧链读 context.autonomy_layer / context.autonomy——
        # 两属性全仓从未挂载（facade 白名单只有 autonomous_assistant_manager），
        # 导致 tool.* 全族恒 "tool registry not available"。真实挂载点与
        # assistant/agents/butler_agent.py:_resolve_capability_router 一致：
        # autonomous_assistant_manager.router（AssistantCapabilityRouter，
        # 其上挂 tool_registry，见 assistant/capability_router.py:240-252）。
        manager = getattr(context, "autonomous_assistant_manager", None)
        router = getattr(manager, "router", None) if manager is not None else None
        if router is not None and hasattr(router, "tool_registry"):
            return router
        # 兼容兜底：context.capability_router（部分测试 harness 直挂）
        cap = getattr(context, "capability_router", None)
        if cap is not None and hasattr(cap, "tool_registry"):
            return cap
        return None

    def _get_tool_registry():
        router = _get_router()
        if router is None:
            return None
        return getattr(router, "tool_registry", None)

    async def tool_register(
        tool_id: str = "",
        display_name: str = "",
        cli_path: str = "",
        description: str = "",
        commands: "dict[str, Any] | None" = None,
        enabled: bool = True,
        env_keys: "list[str] | None" = None,
        health_check_command: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        if not tool_id:
            return {"success": False, "error": "tool_id required"}
        if not commands:
            return {"success": False, "error": "commands required"}
        from services.tooling.tool_spec import ToolSpec

        spec_data = {
            "tool_id": tool_id,
            "display_name": display_name or tool_id,
            "cli_path": cli_path,
            "description": description,
            "enabled": enabled,
            "commands": commands,
            "env_keys": env_keys or [],
            "health_check_command": health_check_command,
        }
        validation = _validate_spec(spec_data)
        if not validation["valid"]:
            return {
                "success": False,
                "error": "schema validation failed",
                "details": validation,
            }
        # P0-2 RCE 封堵：执行面白名单校验（cli_path / health_check_command / command)
        # 必须在允许的 CLI basename 内，防止经 tool.register RPC 注册任意命令。
        sec = _validate_rpc_tool_spec(spec_data)
        if not sec["valid"]:
            return {
                "success": False,
                "error": "security validation failed",
                "details": sec,
            }
        spec = ToolSpec.from_dict(spec_data)
        tool_registry = _get_tool_registry()
        if tool_registry is None:
            return {"success": False, "error": "tool registry not available"}
        tool_registry.register(spec)
        router = _get_router()
        if router and hasattr(router, "dynamic_tool_domain"):
            refresh_result = router.dynamic_tool_domain.refresh()
            action_ids = spec.get_all_action_ids()
            logger.info("Registered tool '%s' with actions: %s", tool_id, action_ids)
            return {
                "success": True,
                "tool_id": tool_id,
                "action_ids": action_ids,
                "refresh": refresh_result,
            }
        return {
            "success": True,
            "tool_id": tool_id,
            "action_ids": spec.get_all_action_ids(),
            "note": "tool registered but router refresh skipped",
        }

    async def tool_list(**kwargs: Any) -> "dict[str, Any]":
        tool_registry = _get_tool_registry()
        if tool_registry is None:
            return {"success": False, "error": "tool registry not available"}
        tools = []
        for spec in tool_registry.list_specs():
            cli_resolved = spec.resolve_cli()
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
                    "health_check_command": spec.health_check_command,
                    "commands": {
                        name: {
                            "description": cmd.description,
                            "risk": cmd.risk,
                            "confirmation": cmd.confirmation,
                            "action_id": spec.get_action_id(name),
                            "params": {
                                p.name: {
                                    "type": p.type,
                                    "required": p.required,
                                    "description": p.description,
                                    "default": p.default,
                                }
                                for p in cmd.params
                            },
                            "stdin_template": cmd.stdin_template,
                            "env_keys": list(cmd.env_keys),
                            "health_check_command": cmd.health_check_command,
                            "output_regex": cmd.output_regex,
                            "output_fields": dict(cmd.output_fields),
                            "detach": cmd.detach,
                            "timeout": cmd.timeout,
                        }
                        for name, cmd in spec.commands.items()
                    },
                    "action_ids": spec.get_all_action_ids(),
                }
            )
        return {"success": True, "tools": tools, "total": len(tools)}

    async def tool_unregister(
        tool_id: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        if not tool_id:
            return {"success": False, "error": "tool_id required"}
        tool_registry = _get_tool_registry()
        if tool_registry is None:
            return {"success": False, "error": "tool registry not available"}
        removed = tool_registry.unregister(tool_id)
        router = _get_router()
        if router and hasattr(router, "dynamic_tool_domain"):
            router.dynamic_tool_domain.refresh()
        return {"success": removed, "tool_id": tool_id}

    async def tool_refresh(**kwargs: Any) -> "dict[str, Any]":
        router = _get_router()
        if router is None or not hasattr(router, "dynamic_tool_domain"):
            return {"success": False, "error": "dynamic tool domain not available"}
        result = router.dynamic_tool_domain.refresh()
        return {
            "success": True,
            "new_actions": result.get("new_actions", []),
            "total_actions": result.get("total_actions", 0),
        }

    async def tool_health_check(
        tool_id: str = "",
        command_name: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        if not tool_id:
            return {"success": False, "error": "tool_id required"}
        tool_registry = _get_tool_registry()
        if tool_registry is None:
            return {"success": False, "error": "tool registry not available"}
        spec = tool_registry.get(tool_id)
        if spec is None:
            return {"success": False, "error": f"tool '{tool_id}' not registered"}
        cli_resolved = spec.resolve_cli()
        cli_available = bool(cli_resolved)
        health_cmd = spec.get_health_check_command(command_name or None)
        health_result = {"executed": False, "returncode": None, "stdout": "", "stderr": ""}
        if health_cmd and cli_available:
            import asyncio as _asyncio
            import shlex as _shlex
            from services.tooling.tool_spec import (
                _normalize_cli_basename,
                ALLOWED_CLI_BASENAMES,
            )

            # P0-2 RCE 纵深防御：即使 tool 已注册（无论经由哪条路径），
            # 健康检查执行前仍强制可执行名在白名单内，杜绝执行任意命令。
            try:
                hc_tokens = _shlex.split(health_cmd)
            except ValueError:
                hc_tokens = health_cmd.split()
            hc_base = _normalize_cli_basename(hc_tokens[0]) if hc_tokens else ""
            if not hc_tokens or hc_base.lower() not in ALLOWED_CLI_BASENAMES:
                logger.warning(
                    "Blocked tool.health_check %s: executable '%s' not in CLI whitelist",
                    tool_id,
                    hc_tokens[0] if hc_tokens else "",
                )
                health_result = {
                    "executed": False,
                    "returncode": None,
                    "stdout": "",
                    "stderr": (f"executable '{hc_tokens[0] if hc_tokens else ''}' not in allowed CLI whitelist"),
                }
            else:
                cmd_args = _shlex.split(health_cmd)
                proc = await _asyncio.create_subprocess_exec(
                    *cmd_args,
                    stdout=_asyncio.subprocess.PIPE,
                    stderr=_asyncio.subprocess.PIPE,
                )
                try:
                    stdout_b, stderr_b = await _asyncio.wait_for(proc.communicate(), 15)
                    health_result = {
                        "executed": True,
                        "returncode": proc.returncode,
                        "stdout": stdout_b.decode("utf-8", "replace").strip(),
                        "stderr": stderr_b.decode("utf-8", "replace").strip(),
                    }
                except _asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    health_result = {
                        "executed": True,
                        "returncode": None,
                        "stdout": "",
                        "stderr": "health check timeout>15s",
                    }
                except Exception as exc:
                    logger.exception("tools.health 执行健康检查命令时发生异常 (tool=%s)", tool_id)
                    health_result = {
                        "executed": True,
                        "returncode": None,
                        "stdout": "",
                        "stderr": f"{type(exc).__name__}: {exc}",
                    }
        from services.tooling.generic_tool_bridge import GenericToolBridge

        missing_secrets = []
        if command_name:
            keys_to_check = spec.get_env_keys_for(command_name)
        else:
            keys_to_check = list(spec.env_keys)
            for cmd in spec.commands.values():
                keys_to_check.extend(cmd.env_keys)
            keys_to_check = list(set(keys_to_check))
        resolver = GenericToolBridge._default_secret_resolver
        for key in keys_to_check:
            if not resolver(key):
                missing_secrets.append(key)
        healthy = cli_available and (not health_result["executed"] or health_result["returncode"] == 0) and not missing_secrets
        return {
            "success": True,
            "tool_id": tool_id,
            "healthy": healthy,
            "cli_available": cli_available,
            "cli_resolved": cli_resolved,
            "health_check_command": health_cmd,
            "health_result": health_result,
            "missing_secrets": missing_secrets,
            "configured_secrets": [k for k in keys_to_check if k not in missing_secrets],
        }

    async def tool_samples(**kwargs: Any) -> "dict[str, Any]":
        def _load_samples() -> "dict[str, Any]":
            import os as _os
            import json as _json
            from pathlib import Path as _Path

            candidates = []
            try:
                from services.tooling import tool_spec as _ts_mod

                candidates.append(_Path(_ts_mod.__file__).parent.parent / "data" / "tool_specs_samples")
            except Exception:
                logger.exception("tools.samples 加载 tool_specs_samples 候选路径失败")
                pass
            candidates.append(_Path(_os.path.dirname(__file__)).parent.parent / "data" / "tool_specs_samples")
            samples = []
            samples_dir = None
            for cand in candidates:
                if cand.exists() and cand.is_dir():
                    samples_dir = cand
                    break
            if samples_dir is None:
                return {
                    "success": True,
                    "samples": [],
                    "total": 0,
                    "note": "samples directory not found; create data/tool_specs_samples/ to add samples",
                }
            for json_file in sorted(samples_dir.glob("*.json")):
                try:
                    content = _json.loads(json_file.read_text("utf-8"))
                    if isinstance(content, dict) and "tools" in content:
                        for tool_data in content["tools"]:
                            samples.append(
                                {
                                    "file": json_file.name,
                                    "tool_id": tool_data.get("tool_id", ""),
                                    "display_name": tool_data.get("display_name", ""),
                                    "description": tool_data.get("description", ""),
                                    "spec": tool_data,
                                }
                            )
                    elif isinstance(content, dict) and "tool_id" in content:
                        samples.append(
                            {
                                "file": json_file.name,
                                "tool_id": content.get("tool_id", ""),
                                "display_name": content.get("display_name", ""),
                                "description": content.get("description", ""),
                                "spec": content,
                            }
                        )
                except Exception as exc:
                    logger.warning("Failed to parse sample %s: %s", json_file, exc)
            return {
                "success": True,
                "samples_dir": str(samples_dir),
                "samples": samples,
                "total": len(samples),
            }

        return await asyncio.to_thread(_load_samples)

    async def tool_validate(
        spec: "dict[str, Any] | None" = None,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        if spec is None:
            spec = {k: v for k, v in kwargs.items() if k != "spec"}
        if not spec:
            return {"success": False, "error": "spec required"}
        result = _validate_spec(spec)
        return {
            "success": True,
            "valid": result["valid"],
            "errors": result["errors"],
            "warnings": result["warnings"],
        }

    registry.register("tool.register", tool_register)
    registry.register("tool.list", tool_list)
    registry.register("tool.unregister", tool_unregister)
    registry.register("tool.refresh", tool_refresh)
    registry.register("tool.health_check", tool_health_check)
    registry.register("tool.samples", tool_samples)
    registry.register("tool.validate", tool_validate)
    logger.info("Dynamic tool management RPC methods registered (7 methods)")
