"""执行器基类。

定义所有执行器的统一接口。
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class Executor(ABC):
    """工具执行器基类。
    
    所有执行器（host、docker、k8s_job）都应继承此类并实现 run 方法。
    """
    
    @abstractmethod
    def run(
        self,
        command: list[str],
        cwd: str | None,
        env: dict[str, str],
        timeout: int,
        stdout_path: str,
        stderr_path: str
    ) -> int:
        """执行命令。
        
        Args:
            command: 命令列表，如 ["python", "script.py", "arg1"]
            cwd: 工作目录
            env: 环境变量字典
            timeout: 超时时间（秒）
            stdout_path: 标准输出文件路径
            stderr_path: 标准错误文件路径
            
        Returns:
            进程退出码（0 表示成功）
            
        Raises:
            TimeoutError: 执行超时时
            Exception: 执行失败时
        """
        ...
