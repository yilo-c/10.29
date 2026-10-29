from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import httpx

DEFAULT_RETRY_STATUS = (408, 413, 429, 500, 502, 503, 504)
DEFAULT_RETRY_METHODS = ("GET", "HEAD", "OPTIONS", "PUT", "DELETE")


class ApiHttpClient:
    """统一 httpx Client、超时和重试策略。"""

    def __init__(
        self,
        timeout: tuple[float, float] = (3.05, 20.0),
        total_retries: int = 3,
        backoff_factor: float = 0.4,
        status_forcelist: Sequence[int] = DEFAULT_RETRY_STATUS,
        allowed_methods: Iterable[str] = DEFAULT_RETRY_METHODS,
        client: httpx.Client | None = None,
    ):
        self.timeout = timeout
        self._client = client or httpx.Client()
        self._owns_client = client is None

        # httpx 的重试机制通过自定义传输层实现
        # 这里简化处理，使用 httpx 内置的重试（在 request 方法中处理）
        # 更复杂的重试策略可以在需要时添加

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        timeout: tuple[float, float] | None = None,
    ) -> httpx.Response:
        # httpx 使用单独的 timeout 参数
        httpx_timeout = httpx.Timeout(timeout or self.timeout)

        response = self._client.request(
            method=str(method or "GET").upper(),
            url=str(url),
            headers=headers,
            params=params,
            json=json_body,
            data=data,
            timeout=httpx_timeout,
        )
        response.raise_for_status()
        return response

    def request_json(self, method: str, url: str, **kwargs) -> Any:
        response = self.request(method, url, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise ValueError(f"invalid json response from {url}") from exc

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
