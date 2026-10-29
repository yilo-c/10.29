"""模块说明：脚本任务执行。"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

# 修正导入路径
from automation_hub.api.config import settings
from automation_hub.api.db import update_run_status


def now_iso() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def execute_script_job(run_id: str, script_name: str, parameters: Dict[str, Any]) -> None:
    """
    执行脚本任务的函数
    :param run_id: 运行ID
    :param script_name: 脚本名称
    :param parameters: 参数字典
    """
    try:
        # 获取脚本配置
        manifest = load_scripts_manifest()
        # 使用 id 而不是 name 来查找脚本
        script = next((s for s in manifest.get("scripts", []) if s["id"] == script_name), None)
        if not script:
            raise ValueError(f"Script not found in manifest: {script_name}")

        # 更新状态为正在运行
        update_run_status(run_id, "running", started=True)

        # 准备运行目录和输出文件
        run_dir = Path(settings.RUNS_DIR) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = run_dir / "stdout.txt"
        stderr_path = run_dir / "stderr.txt"

        # 构建命令 - 修正命令路径，确保使用正确的路径
        cmd = list(script["command"])

        # 如果命令是相对路径，确保它基于正确的工作目录
        arg_style = script.get("arg_style", "flags")

        if arg_style == "flags":
            for k, v in parameters.items():
                cmd.extend([f"--{k.replace('_', '-')}", "true" if v is True else "false" if v is False else str(v)])
        elif arg_style == "positional":
            for key in script.get("positional_args", []):
                if key in parameters:
                    cmd.append(str(parameters[key]))

        # 设置环境变量
        env = os.environ.copy()
        env["WORKSPACE"] = env.get("WORKSPACE", "/workspace")

        # 执行脚本，使用根目录作为工作目录
        timeout = int(script.get("timeout_sec", 300))

        with open(stdout_path, "w", encoding="utf-8") as stdout_file, \
             open(stderr_path, "w", encoding="utf-8") as stderr_file:
            result = subprocess.run(
                cmd,
            cwd=str(settings.SCRIPTS_DIR.parent),  # 使用项目根目录作为工作目录
                env=env,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                timeout=timeout,
                check=False
            )

        # 读取标准输出内容
        with open(stdout_path, "r", encoding="utf-8") as f:
            stdout_content = f.read()

        # 检查执行结果
        if result.returncode == 0:
            # 使用 completed=True
            update_run_status(run_id, "completed", completed=True, result=stdout_content)
        else:
            with open(stderr_path, "r", encoding="utf-8") as f:
                error_msg = f.read().strip()
            error_msg = error_msg[-500:] if len(error_msg) > 500 else error_msg  # Truncate long errors
            # 使用 completed=True
            update_run_status(run_id, "failed", completed=True, error_msg=error_msg)

    except subprocess.TimeoutExpired:
        error_msg = f"Script execution timed out after {timeout} seconds"
        # 使用 completed=True
        update_run_status(run_id, "failed", completed=True, error_msg=error_msg)
    except Exception as e:
        # 使用 completed=True
        update_run_status(run_id, "failed", completed=True, error_msg=str(e))


# 修正 load_scripts_manifest 函数的导入
def load_scripts_manifest():
    """加载脚本清单"""
    import json
    from pathlib import Path
    
    manifest_path = Path(settings.SCRIPTS_DIR) / "manifest.json"
    if not manifest_path.exists():
        return {"scripts": []}
    
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"scripts": []}
