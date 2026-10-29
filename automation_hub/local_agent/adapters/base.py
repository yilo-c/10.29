"""local_agent.adapters.base — 适配器抽象基类。"""

from __future__ import annotations

import abc
from typing import Any

from local_agent.stream import StreamBuffer


class AdapterBase(abc.ABC):
    """所有本地执行适配器的基类。"""

    name: str = "base"

    @abc.abstractmethod
    async def execute(self, args: dict[str, Any], stream: StreamBuffer | None = None) -> Any:
        """执行操作并返回结果。

        Args:
            args: 工具参数。
            stream: 可选的流式输出缓冲。
        """

    @abc.abstractmethod
    async def validate(self, args: dict[str, Any]) -> tuple[bool, str]:
        """验证参数是否合法。

        Returns:
            (通过, 原因)
        """

    async def cleanup(self) -> None:
        """释放资源（可选覆盖）。"""
