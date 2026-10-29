"""Manifest -> Tools Registry 迁移脚本。

支持两种 manifest 格式：
1) automation-hub/scripts/manifest.json: {"scripts": [ {id,name,description,command,parameters: []} ]}
2) 根目录 scripts/manifest.json: {"tool_id": {description, parameters{...}}}

用法：
python automation-hub/scripts/migrate_manifest_to_tools.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _bootstrap_imports() -> Path:
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "automation-hub"))
    return root


def _json_load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _map_param_type(t: str | None) -> str:
    if not t:
        return "string"
    t = t.lower()
    if t in {"int", "integer"}:
        return "integer"
    if t in {"number", "float", "double"}:
        return "number"
    if t in {"bool", "boolean"}:
        return "boolean"
    return "string"


def _build_args_schema_from_list(params: list[dict]) -> dict:
    properties: dict = {}
    required: list[str] = []
    for p in params or []:
        name = p.get("name")
        if not name:
            continue
        prop: dict = {
            "type": _map_param_type(p.get("type")),
            "description": p.get("description", ""),
        }
        if "default" in p:
            prop["default"] = p.get("default")
        if p.get("pattern"):
            prop["pattern"] = p.get("pattern")
        properties[name] = prop
        if p.get("required") is True:
            required.append(name)
    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _build_args_schema_from_dict(params: dict) -> dict:
    properties: dict = {}
    required: list[str] = []
    for name, p in (params or {}).items():
        prop: dict = {
            "type": _map_param_type(p.get("type")),
            "description": p.get("description", ""),
        }
        if "default" in p:
            prop["default"] = p.get("default")
        if p.get("pattern"):
            prop["pattern"] = p.get("pattern")
        properties[name] = prop
        if "default" not in p:
            required.append(name)
    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _resolve_script_command(root: Path, key: str) -> list[str] | None:
    candidates = [
        root / "automation-hub" / "scripts" / f"{key}.py",
        root / "scripts" / f"{key}.py",
    ]
    for c in candidates:
        if c.exists():
            rel = c.relative_to(root)
            return ["python", str(rel).replace("\\", "/")]
    return None


def _infer_risk_level(name: str, command: list[str]) -> str:
    hay = " ".join([name] + (command or [])).lower()
    if any(k in hay for k in ["backup", "cleanup", "delete", "remove", "apply", "patch", "format", "migrate", "seed"]):
        return "write"
    if any(k in hay for k in ["rg", "ripgrep", "diff", "list", "search", "show", "status"]):
        return "read"
    return "exec_high"


def _infer_allowed_paths(params: list[str] | dict) -> list[str]:
    keys = []
    if isinstance(params, list):
        keys = [p.get("name") for p in params if isinstance(p, dict)]
        patterns = [p.get("pattern") for p in params if isinstance(p, dict)]
    else:
        keys = list(params.keys())
        patterns = [v.get("pattern") for v in params.values() if isinstance(v, dict)]

    for pat in patterns:
        if isinstance(pat, str) and pat.startswith("^/workspace"):
            return ["/workspace"]
        if isinstance(pat, str) and pat.startswith("^/data"):
            return ["/data"]

    if any(k and k.lower() in {"path", "file", "files", "dir", "directory", "source", "destination", "target"} for k in keys):
        return ["."]

    return ["."]


def _migrate_manifest_list(data: dict, root: Path) -> list[dict]:
    items: list[dict] = []
    for s in data.get("scripts", []) or []:
        tool_id = s.get("id") or s.get("name")
        if not tool_id:
            continue
        command = s.get("command")
        if not command:
            continue
        params = s.get("parameters") or []
        args_schema = _build_args_schema_from_list(params)
        risk_level = _infer_risk_level(tool_id, command)
        allowed_paths = _infer_allowed_paths(params)
        items.append(
            {
                "id": tool_id,
                "name": s.get("name") or tool_id,
                "description": s.get("description", ""),
                "risk_level": risk_level,
                "executor": "host",
                "command": command,
                "args_schema": args_schema,
                "timeout_sec": 120,
                "allowed_paths": allowed_paths,
                "is_enabled": True,
            }
        )
    return items


def _migrate_manifest_dict(data: dict, root: Path) -> list[dict]:
    items: list[dict] = []
    for key, spec in (data or {}).items():
        command = _resolve_script_command(root, key)
        if not command:
            continue
        params = spec.get("parameters") or {}
        args_schema = _build_args_schema_from_dict(params)
        risk_level = _infer_risk_level(key, command)
        allowed_paths = _infer_allowed_paths(params)
        items.append(
            {
                "id": key,
                "name": key,
                "description": spec.get("description", ""),
                "risk_level": risk_level,
                "executor": "host",
                "command": command,
                "args_schema": args_schema,
                "timeout_sec": 120,
                "allowed_paths": allowed_paths,
                "is_enabled": True,
            }
        )
    return items


def main() -> int:
    root = _bootstrap_imports()

    from api.config import settings
    from api import db
    from api.tools.registry import upsert_tool

    parser = argparse.ArgumentParser(description="migrate manifest.json to tools registry")
    parser.add_argument("--manifest", action="append", default=[], help="manifest path (can repeat)")
    args = parser.parse_args()

    settings.ensure_dirs()
    db.init_db()

    default_manifests = [
        root / "automation-hub" / "scripts" / "manifest.json",
        root / "scripts" / "manifest.json",
    ]

    manifest_paths = [Path(p) for p in args.manifest] if args.manifest else default_manifests

    total = 0
    for mp in manifest_paths:
        if not mp.exists():
            print(f"skip: {mp} (not found)")
            continue
        data = _json_load(mp)
        if isinstance(data, dict) and "scripts" in data:
            items = _migrate_manifest_list(data, root)
        elif isinstance(data, dict):
            items = _migrate_manifest_dict(data, root)
        else:
            items = []

        for t in items:
            upsert_tool(t)
            print(f"ok: {t['id']}")
            total += 1

    print(f"done: {total} tools registered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
