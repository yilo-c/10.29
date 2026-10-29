"""ToolBridge — 本地 AI 工具调用桥接层（已废弃）。

将 Planner 声明的能力目录转换成 LLM 可理解的 function_calling 格式。
⚠️ 此模块已被 CapabilityRouter 取代，不再被任何生产代码引用。
    保留仅用于测试参考，后续版本将删除。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from services.react_tool_catalog import to_step_risk

logger = logging.getLogger("siyi.assistant.tool_bridge")


@dataclass(frozen=True)
class ToolCall:
    """A parsed function_call from the LLM response."""

    tool_name: str
    arguments: dict[str, Any]
    raw_text: str = ""


@dataclass(frozen=True)
class ToolResult:
    """Result of dispatching a tool call."""

    success: bool
    tool_name: str
    output: str = ""
    error: str = ""
    data: Any = None


@dataclass(frozen=True)
class BridgeResponse:
    """Final response from ToolBridge — either text or tool result."""

    type: str
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


class ToolBridge:
    """Bridges Planner's capability catalog into LLM tool calling format.

    Usage:
        bridge = ToolBridge(shell_exec, action_dispatcher, security_layer)
        response = bridge.process(capability_catalog, llm_response_text)
    """

    # <function_call>name(args)</function_call>
    _FC_PATTERN_STANDARD = re.compile(
        r"<function_call>\s*(\w+(?:\.\w+)*)\s*\((.*?)\)\s*</function_call>",
        re.DOTALL,
    )
    # "function_call": {"name": "..."}
    _FC_PATTERN_JSON = re.compile(
        r'"function_call":\s*\{\s*"name":\s*"(\w+(?:\.\w+)*)"',
        re.DOTALL,
    )
    # {"tool": "..."}
    _FC_PATTERN_OPENAI = re.compile(
        r'\{\s*"tool":\s*"(\w+(?:\.\w+)*)"',
        re.DOTALL,
    )

    def __init__(
        self,
        shell_execution=None,
        action_dispatcher=None,
        security_layer=None,
    ):
        self._shell_exec = shell_execution
        self._action_dispatcher = action_dispatcher
        self._security_layer = security_layer

    def build_tools_declaration(self, catalog: list[dict[str, Any]]) -> str:
        """Convert Planner capability catalog into a tools section for LLM prompt.

        Returns a string that can be injected into the system prompt.
        """
        lines = ["\n## Available Tools (function_call)"]
        lines.append(
            "You can call tools by outputting: <function_call>tool_name(argument JSON)</function_call>"
        )
        lines.append("")
        for cap in catalog:
            aid = cap.get("action_id", "")
            desc = cap.get("description", "")
            risk = to_step_risk(cap.get("risk", "low"))
            schema = cap.get("payload_schema", {})
            lines.append(f"- **{aid}** ({risk}): {desc}")
            if schema:
                args_desc = "; ".join(f"{k}: {v}" for k, v in schema.items())
                lines.append(f"  Arguments: {args_desc}")
        lines.append("")
        lines.append('Use the format: <function_call>tool_name({"arg": "value"})</function_call>')
        lines.append("After calling a tool, wait for the result before continuing.")
        return "\n".join(lines)

    def process(
        self, catalog: list[dict[str, Any]], llm_response: str
    ) -> BridgeResponse:
        """Process an LLM response: detect and dispatch function calls.

        Steps:
        1. Parse the response for function_call patterns
        2. If none found, return as plain text
        3. If found, dispatch each call and collect results
        4. Return tool results for re-injection into the LLM
        """
        tool_calls = self._parse_function_calls(llm_response)
        if not tool_calls:
            return BridgeResponse(type="text", content=llm_response)

        results = []
        for call in tool_calls:
            results.append(self._dispatch(call, catalog))

        summary_lines = ["Tool call results:"]
        for r in results:
            status = "✓" if r.success else "✗"
            summary_lines.append(f"  {status} {r.tool_name}: {r.output or r.error}")
        summary = "\n".join(summary_lines)

        return BridgeResponse(
            type="tool_results",
            content=summary,
            tool_calls=tool_calls,
            tool_results=results,
        )

    def _parse_function_calls(self, text: str) -> list[ToolCall]:
        """Parse all function_call patterns from the LLM response."""
        calls: list[ToolCall] = []

        # 标准格式：<function_call>name(args)</function_call>
        for match in self._FC_PATTERN_STANDARD.finditer(text):
            name = match.group(1).strip()
            args_text = match.group(2).strip()
            args = self._parse_args(args_text)
            calls.append(
                ToolCall(tool_name=name, arguments=args, raw_text=match.group(0))
            )

        # 回退 1："function_call": {"name": "..."}
        if not calls:
            for match in self._FC_PATTERN_JSON.finditer(text):
                name = match.group(1).strip()
                calls.append(
                    ToolCall(tool_name=name, arguments={}, raw_text=match.group(0))
                )

        # 回退 2：{"tool": "..."}
        if not calls:
            for match in self._FC_PATTERN_OPENAI.finditer(text):
                name = match.group(1).strip()
                calls.append(
                    ToolCall(tool_name=name, arguments={}, raw_text=match.group(0))
                )

        return calls

    @staticmethod
    def _parse_args(args_text: str) -> dict[str, Any]:
        """Parse argument text as JSON or fallback to empty dict."""
        if not args_text:
            return {}
        try:
            return json.loads(args_text)
        except (json.JSONDecodeError, ValueError):
            logger.debug("Failed to parse args as JSON, trying key=value fallback", exc_info=True)
            result = {}
            for part in args_text.split(","):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    result[k.strip()] = v.strip().strip('"').strip("'")
            return result

    def _dispatch(self, call: ToolCall, catalog: list[dict[str, Any]]) -> ToolResult:
        """Dispatch a single tool call to the appropriate executor."""
        cap = self._find_capability(call.tool_name, catalog)
        if cap is None:
            return ToolResult(
                success=False,
                tool_name=call.tool_name,
                error=f"Unknown tool: {call.tool_name}",
            )

        # 目录 risk 已统一到 registry 词表（审计波次② C-1 步骤 4），而下面
        # mock_step.risk_level 交给 security_layer 判定，那里仍按 high/medium 比较
        # → 必须回落，否则 destructive 动作不再触发强确认。
        risk = to_step_risk(cap.get("risk", "medium"))
        if self._security_layer:
            from .models import PlannedStep

            mock_step = PlannedStep(
                step_id="toolbridge_auto",
                action_id=call.tool_name,
                payload=call.arguments,
                risk_level=risk,
            )
            auth = self._security_layer.authorize_step(mock_step, source="toolbridge")
            if not auth.get("allowed", True):
                reason = auth.get("reason", "denied")
                return ToolResult(
                    success=False,
                    tool_name=call.tool_name,
                    error=f"Security block: {reason}",
                )

        if call.tool_name.startswith("shell.") or call.tool_name == "shell.run":
            return self._dispatch_shell(call)
        if call.tool_name.startswith("file."):
            return self._dispatch_shell(call)
        if call.tool_name.startswith("web."):
            return self._dispatch_shell(call)
        if self._action_dispatcher:
            return self._dispatch_capability(call)

        return self._dispatch_shell(call)

    def _dispatch_shell(self, call: ToolCall) -> ToolResult:
        """Dispatch via ShellExecution model."""
        if not self._shell_exec:
            return ToolResult(
                success=False,
                tool_name=call.tool_name,
                error="Shell execution not configured",
            )
        command = call.arguments.get("command") or call.arguments.get("path", "")
        if not command:
            # 没有显式命令时按工具名映射
            command = self._tool_to_shell_command(call.tool_name, call.arguments)

        try:
            result = self._shell_exec.execute(
                command=command,
                args=call.arguments.get("args"),
                timeout=call.arguments.get("timeout", 30),
            )
            return ToolResult(
                success=result.get("success", False),
                tool_name=call.tool_name,
                output=result.get("stdout", ""),
                error=result.get("stderr", ""),
                data=result,
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                tool_name=call.tool_name,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _dispatch_capability(self, call: ToolCall) -> ToolResult:
        """Dispatch via ActionDispatcher."""
        try:
            result = self._action_dispatcher.execute_capability(
                call.tool_name, call.arguments
            )
            return ToolResult(
                success=True,
                tool_name=call.tool_name,
                output=str(result),
                data=result,
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                tool_name=call.tool_name,
                error=f"{type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _find_capability(
        name: str, catalog: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        for cap in catalog:
            if cap.get("action_id") == name:
                return cap
        for cap in catalog:
            if name.startswith(cap.get("action_id", "").split(".")[0]):
                return cap
        return None

    @staticmethod
    def _tool_to_shell_command(tool_name: str, args: dict[str, Any]) -> str:
        """Map tool name to a shell command for basic operations."""
        mapping = {
            "file.read": f"cat {args.get('path', '')}",
            "file.write": f"echo '{args.get('content', '')}' > {args.get('path', '')}",
            "web.search": f"echo 'web search: {args.get('query', '')}'",
            "web.fetch": f"echo 'web fetch: {args.get('url', '')}'",
            "shell.run": args.get("command", ""),
        }
        return mapping.get(tool_name, f"echo 'No mapping for {tool_name}'")
