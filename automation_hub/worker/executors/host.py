"""主机执行器。

直接在当前主机上执行命令（适用于低风险操作）。
"""

from __future__ import annotations
import subprocess
from .base import Executor


class HostExecutor(Executor):
    """在主机上直接执行命令的执行器。
    
    适用于：
    - 低风险的读操作
    - 信任的工具
    - 不需要隔离的场景
    
    安全提示：应谨慎使用，高风险操作建议使用 DockerExecutor。
    """
    
    def run(
        self,
        command: list[str],
        cwd: str | None,
        env: dict[str, str],
        timeout: int,
        stdout_path: str,
        stderr_path: str
    ) -> int:
        """在主机上执行命令。"""
        with open(stdout_path, "w", encoding="utf-8") as out_f, \
             open(stderr_path, "w", encoding="utf-8") as err_f:
            
            try:
                proc = subprocess.run(
                    command,
                    cwd=cwd,
                    env=env,
                    timeout=timeout,
                    stdout=out_f,
                    stderr=err_f,
                    text=True,
                    check=False
                )
                return proc.returncode
            
            except subprocess.TimeoutExpired:
                err_f.write(f"\nERROR: Command timed out after {timeout} seconds\n")
                return 124  # 超时退出码
            
            except Exception as e:
                err_f.write(f"\nERROR: {str(e)}\n")
                return 1
