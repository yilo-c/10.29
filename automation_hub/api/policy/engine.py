from __future__ import annotations
from dataclasses import dataclass
import json

@dataclass
class Decision:
    allowed: bool
    requires_approval: bool
    reason: str = ""


def decide_execute(scopes: list[str], tool: dict, args: dict) -> Decision:
    if "tool:execute" not in scopes:
        return Decision(False, False, "missing scope tool:execute")
    if int(tool.get("is_enabled", 1)) != 1:
        return Decision(False, False, "tool disabled")

    risk = tool.get("risk_level", "exec_low")
    if risk in ("exec_high", "write"):
        return Decision(True, True, f"risk={risk} requires approval")

    # 简单 args schema 判断（可扩展为 jsonschema）
    try:
        schema = json.loads(tool.get("args_schema_json") or "{}")
        # TODO: use jsonschema.validate for full validation
    except Exception:
        schema = {}

    # allowed by default
    return Decision(True, False, "ok")
