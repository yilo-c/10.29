"""注册 MVP 最小工具集到 Tools Registry。

包含：
- code_search (rg)
- git_diff
- git_apply_patch (需审批)
- run_tests (pytest)

用法（在仓库根目录运行）：
python automation-hub/scripts/register_mvp_tools.py

注意：
- 该脚本会通过 api.tools.registry.upsert_tool 写入 SQLite。
- 运行前请确保已初始化数据库（FastAPI 启动时也会 init_db）。
"""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_imports() -> None:
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "automation-hub"))


def main() -> int:
    _bootstrap_imports()

    from api.config import settings
    from api import db
    from api.tools.registry import upsert_tool

    settings.ensure_dirs()
    db.init_db()

    tools = [
        {
            "id": "code_search",
            "name": "代码搜索(rg)",
            "description": "使用 ripgrep 在仓库中搜索文本模式",
            "risk_level": "read",
            "executor": "host",
            "command": ["python", "automation-hub/tool_scripts/code_search_rg.py"],
            "args_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "搜索模式(正则/文本)"},
                    "path": {"type": "string", "description": "搜索路径", "default": "."},
                    "file_type": {"type": "string", "description": "rg 文件类型 (py/js/ts...)"},
                    "ignore_case": {"type": "boolean", "default": True},
                },
                "required": ["pattern"],
            },
            "timeout_sec": 30,
            "allowed_paths": ["."],
            "is_enabled": True,
        },
        {
            "id": "git_diff",
            "name": "Git Diff",
            "description": "查看工作区 diff（可选 cached/file）",
            "risk_level": "read",
            "executor": "host",
            "command": ["python", "automation-hub/tool_scripts/git_diff.py"],
            "args_schema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "文件路径(可选)"},
                    "cached": {"type": "boolean", "default": False},
                },
            },
            "timeout_sec": 20,
            "allowed_paths": ["."],
            "is_enabled": True,
        },
        {
            "id": "git_apply_patch",
            "name": "Git Apply Patch",
            "description": "应用 unified diff（会先 git apply --check，再 apply）",
            "risk_level": "write",
            "executor": "host",
            "command": ["python", "automation-hub/tool_scripts/git_apply_patch.py"],
            "args_schema": {
                "type": "object",
                "properties": {
                    "patch_text": {"type": "string", "description": "unified diff 文本"},
                    "check_only": {"type": "boolean", "default": False},
                },
                "required": ["patch_text"],
            },
            "timeout_sec": 30,
            "allowed_paths": ["."],
            "is_enabled": True,
        },
        {
            "id": "run_tests",
            "name": "运行测试(pytest)",
            "description": "运行 pytest（默认 tests）",
            "risk_level": "exec_low",
            "executor": "host",
            "command": ["python", "automation-hub/tool_scripts/run_tests.py"],
            "args_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "tests", "description": "测试路径"},
                    "markers": {"type": "string", "description": "pytest -m markers"},
                },
            },
            "timeout_sec": 600,
            "allowed_paths": ["."],
            "is_enabled": True,
        },
    ]

    for t in tools:
        upsert_tool(t)
        print(f"ok: {t['id']}")

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
