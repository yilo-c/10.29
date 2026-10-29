# -*- coding: utf-8 -*-
"""带超时和重试机制的 HTTP 客户端"""
import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger("siyi.http")

class RetryableHttpClient:
    """支持超时和指数退避重试的 HTTP 客户端"""
    
    def __init__(
        self,
        timeout: float = 10.0,
        max_retries: int = 3,
        base_delay: float = 0.5,
        backoff_factor: float = 2.0,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.backoff_factor = backoff_factor
        self._client: Optional[httpx.AsyncClient] = None
    
    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()
    
    async def request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """发送 HTTP 请求，自动重试"""
        last_exception = None
        
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(method, url, **kwargs)
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exception = e
                if attempt < self.max_retries:
                    delay = self.base_delay * (self.backoff_factor ** attempt)
                    logger.warning(
                        f"请求失败 (尝试 {attempt + 1}/{self.max_retries + 1}): "
                        f"{method} {url} - {e}. 等待 {delay:.1f}s 后重试..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"请求最终失败: {method} {url} - {e}")
                    raise
            except httpx.HTTPStatusError as e:
                # 对于 HTTP 错误状态码，不重试
                logger.error(f"HTTP错误: {method} {url} - {e}")
                raise