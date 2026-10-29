"""OpenAI 兼容 API 客户端封装。

从 ai_service.py 提取，职责：
- OpenAICompatibleClient 类（chat / stream / explain / summarize）
- 同步接口基于 httpx.Client，异步接口基于 httpx.AsyncClient
- JSON 解析、文本规整辅助函数
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx

from ai.types import AIServiceError

from ai._llm_path import PROJECT_ROOT  # noqa: F401 — ensures project root on sys.path

from core.isolation import is_offline_mode

from ai.llm_resilience import (
    RetryPolicy,
    classify_error,
    compute_retry_delay_seconds,
    extract_error_text_from_response,
    parse_retry_after_seconds,
)

_default_retry_policy = RetryPolicy()

logger = logging.getLogger("siyi.ai.client")

# P1-5: 重试 sleep 上限 —— 同步客户端的 time.sleep 在被异步代码直接调用时会
# 阻塞事件循环。限制重试延迟上限为 10 秒，降低误用时的阻塞影响。
_RETRY_SLEEP_MAX = 10.0

# 连接池配置：复用底层 TCP 连接，减少 LLM 调用握手开销。
_HTTPX_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)


# ── 离线闸门（PD-ISO-b 治本，2026-09-18）──────────────────────────
#
# 旧病：隔离后端带着离线开关启动，但开关只被 headless_main 的 RSS/autonomy
# 两处消费，本客户端完全无视 → AUTOSTART 集成测试每次真实联网调 DeepSeek
# 烧配额（实测 knowledge.brief 20.3s 成功）。
#
# 治本点选在**传输层**而不是各个请求方法里补 `if`：本类有 6 条出网路径
# （_request/_arequest/stream_chat/astream_chat/stream_chat_with_tools/
# astream_chat_with_tools），逐条加守卫必然漏（下次新增方法再漏一次）。
# 装成 httpx 传输层后，任何走 `_get_sync_client`/`_get_async_client` 的请求
# ——包括未来新增的——结构上都不可能绕过。
#
# 为什么用自定义异常而非 httpx.RequestError：后者会被 stream_with_tools 的
# 重试分支接住并 sleep 2/4/8s 重试，离线时白等 14s；自定义异常直接穿透。


class OfflineRequestBlocked(AIServiceError):
    """离线模式（显式声明）下拒绝出网请求。

    继承 ``AIServiceError`` 是为了让既有调用方（把 AI 失败当降级信号处理）
    无需改动即可优雅降级，而不是抛出一个没人认识的异常类型。
    """


def _offline_message(url: object) -> str:
    return f"离线模式（显式声明）：拒绝出网请求 {url}。如需真实联网请显式关闭离线开关（见 core.isolation）。"


_NO_THINK_SUFFIX = "/no_think"


def _with_no_think(messages: list[Any]) -> list[Any]:
    """副本形式给最后一条 user 消息追加 ``/no_think``（Qwen3 关思考约定）。

    为什么需要：Qwen3 系默认开思考，实测同一 prompt 开/关思考耗时 231.9s vs
    11.2s（2026-10-15，5k tokens 输入）。本地兜底必须关思考才能落在 chat.send
    的 60s idle 窗口内。已含后缀则不重复追加；找不到 user 消息则原样返回。
    """
    if not messages:
        return messages
    out = [dict(m) if isinstance(m, dict) else m for m in messages]
    for msg in reversed(out):
        if isinstance(msg, dict) and str(msg.get("role") or "") == "user":
            content = str(msg.get("content") or "")
            if _NO_THINK_SUFFIX in content:
                return out
            msg["content"] = f"{content}\n{_NO_THINK_SUFFIX}" if content.strip() else _NO_THINK_SUFFIX
            return out
    return out


class _OfflineTransport(httpx.BaseTransport):
    """同步传输层：任何请求直接判失败，不建立任何连接。"""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise OfflineRequestBlocked(_offline_message(request.url), status=0)

    def close(self) -> None:
        return None


class _AsyncOfflineTransport(httpx.AsyncBaseTransport):
    """异步传输层：与同步版同语义（异步腿不得成为绕过口）。"""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise OfflineRequestBlocked(_offline_message(request.url), status=0)

    async def aclose(self) -> None:
        return None


def _offline_transport_kwargs() -> dict[str, Any]:
    """离线时返回 `transport=` 参数（否则空 dict，保持 httpx 默认行为）。

    只在离线时注入：非离线路径继续走 httpx 默认传输层，`trust_env=True` 的
    系统代理（Clash 等）行为完全不变——不引入代理相关回归。
    判定在**首次建连时**读取，因此子进程启动后再设开关同样生效。
    """
    if is_offline_mode():
        logger.warning("AI 客户端处于离线模式：已装拒绝出网的传输层，LLM 调用将直接失败（不联网）")
        return {"transport": _OfflineTransport()}
    return {}


def _async_offline_transport_kwargs() -> dict[str, Any]:
    if is_offline_mode():
        return {"transport": _AsyncOfflineTransport()}
    return {}


def _retry_sleep(seconds: float) -> None:
    """重试时的 sleep，上限 _RETRY_SLEEP_MAX 秒。"""
    time.sleep(min(seconds, _RETRY_SLEEP_MAX))


async def _async_retry_sleep(seconds: float) -> None:
    """异步重试时的 sleep，上限 _RETRY_SLEEP_MAX 秒。"""
    await asyncio.sleep(min(seconds, _RETRY_SLEEP_MAX))


# ── 辅助工具 ──────────────────────────────────────────


def _parse_json_object(text: str) -> dict[str, Any]:
    """从 AI 回复文本中提取 JSON 对象。

    支持纯 JSON 以及包裹在 ```json ... ``` 或 ``` ... ``` 标记中的 JSON。
    """
    for marker in ("```json\n", "```\n"):
        if marker in text:
            start = text.index(marker) + len(marker)
            end = text.index("```", start) if "```" in text[start:] else len(text)
            text = text[start:end].strip()
            break

    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)


def _normalize_text(text: str) -> str:
    """保留首个换行前的内容。"""
    idx = text.find("\n")
    return text[:idx].strip() if idx != -1 else text


def _normalize_text_list(text: str) -> list[str]:
    """将 AI 回复变为条目列表（按序号/换行分割）。"""
    import re as _re

    text = text.strip().removeprefix("```").removesuffix("```").strip()
    lines = _re.split(r"\n\s*(?:\d+[\.\)、]\s*)?", text)
    return [l.strip() for l in lines if l.strip()]


def _httpx_timeout(connect: float, read: float) -> httpx.Timeout:
    """构造 httpx 超时对象（分离连接/读取超时）。"""
    # httpx.Timeout 必须提供默认 timeout，或显式设置全部四个参数；
    # 这里以 read 作为默认超时，并单独指定 connect/read。
    return httpx.Timeout(read, connect=connect, read=read)


# ── OpenAICompatibleClient ──────────────────────────────


class _ReasoningTextBuffer:
    """思考型模型的**流式**兜底（与非流式 ``_normalize_reasoning_response`` 同口径）。

    背景（2026-10-16）：4 个流式循环只处理 ``delta.content``，思考型模型
    （qwen3 thinking / deepseek-v4-*）的 ``delta.reasoning_content`` 被静默
    丢弃——推理阶段耗尽 max_tokens 时 content 恒空（简报探针实测 1087 字
    截断同族），上层收到零文本 → idle 超时 / 误判失败。

    用法：每次流式尝试开始时新建实例；每个 delta 喂 ``feed()``，返回值非空
    即为应发出的正文增量；流结束后调 ``fallback_text()``，非空则补发一条
    正文（全文 = 累积的推理文本）。

    ⚠️ 必须保持在模块级：插入类体内部会截断 OpenAICompatibleClient 的
    方法归属（本批实测教训——首版插在 _normalize_reasoning_response 之后，
    后续两个 stream_with_tools 方法被吞进本类，AttributeError 被测试当场
    抓出）。
    """

    def __init__(self) -> None:
        self.saw_content = False
        self.reasoning_parts: list[str] = []

    def feed(self, delta: dict[str, Any]) -> str | None:
        """喂入一个 delta；返回应作为正文发出的增量（纯 reasoning 增量返回 None）。"""
        content = delta.get("content", "")
        if content:
            self.saw_content = True
            return str(content)
        rc = delta.get("reasoning_content") or ""
        if isinstance(rc, str) and rc:
            self.reasoning_parts.append(rc)
        return None

    def fallback_text(self) -> str:
        """全程无 content 时返回累积推理文本；否则返回空串（不补发）。"""
        if self.saw_content:
            return ""
        return "".join(self.reasoning_parts).strip()


class OpenAICompatibleClient:
    """OpenAI 兼容 API 的轻量客户端（httpx 实现，同步+异步）。"""

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str = "gpt-4o",
        timeout: int = 300,
        max_retries: int = 3,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        if not self.api_key:
            logger.warning("API key is empty — requests will likely fail with 401")

        self._sync_client: httpx.Client | None = None
        self._async_client: httpx.AsyncClient | None = None
        self._client_lock = threading.Lock()
        self._async_client_lock: asyncio.Lock | None = None

    # ── 客户端生命周期 ───────────────────────────────────────

    def _timeout(self, timeout: int | None = None) -> httpx.Timeout:
        effective_timeout = timeout if timeout is not None else self.timeout
        connect_timeout = min(10, effective_timeout)
        return _httpx_timeout(connect_timeout, effective_timeout)

    def _is_local_endpoint(self) -> bool:
        """api_base 是否为本机地址（Ollama / Kiro 等本地服务）。

        本地地址必须绕过系统代理（Clash 等）——trust_env=True 时
        httpx 会读取 Windows 系统代理设置，本机请求被代理劫持后
        返回 502，导致本地 AI 服务整体不可用。云端地址保留代理能力。
        """
        try:
            from urllib.parse import urlparse

            host = (urlparse(self.api_base).hostname or "").lower()
            return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}
        except Exception:
            return False

    def _get_sync_client(self) -> httpx.Client:
        with self._client_lock:
            if self._sync_client is None or self._sync_client.is_closed:
                self._sync_client = httpx.Client(
                    timeout=self._timeout(),
                    limits=_HTTPX_LIMITS,
                    trust_env=not self._is_local_endpoint(),
                    **_offline_transport_kwargs(),
                )
            return self._sync_client

    async def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client_lock is None:
            self._async_client_lock = asyncio.Lock()
        async with self._async_client_lock:
            if self._async_client is None or self._async_client.is_closed:
                self._async_client = httpx.AsyncClient(
                    timeout=self._timeout(),
                    limits=_HTTPX_LIMITS,
                    trust_env=not self._is_local_endpoint(),
                    **_async_offline_transport_kwargs(),
                )
            return self._async_client

    def close(self) -> None:
        """关闭同步客户端（释放连接池）。"""
        if self._sync_client is not None and not self._sync_client.is_closed:
            self._sync_client.close()

    async def aclose(self) -> None:
        """关闭异步客户端（释放连接池）。"""
        if self._async_client is not None and not self._async_client.is_closed:
            await self._async_client.aclose()

    def __enter__(self) -> "OpenAICompatibleClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    async def __aenter__(self) -> "OpenAICompatibleClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    # ── 内部请求 ────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        # 空 api_key（如本地 Ollama 无鉴权）时不要发 "Authorization: Bearer "，
        # 否则 httpx 会抛 "Illegal header value b'Bearer '" 导致本地回退失败
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _is_local_qwen3(self) -> bool:
        """是否为「本机端点 + Qwen3 系模型」（决定是否追加 /no_think）。"""
        base = str(getattr(self, "api_base", "") or "").lower()
        model = str(getattr(self, "model", "") or "").lower()
        local_ep = any(h in base for h in ("localhost", "127.0.0.1", "0.0.0.0"))
        return local_ep and "qwen3" in model

    def _payload(self, messages: list[dict[str, str]], **overrides: Any) -> dict[str, Any]:
        payload = dict(overrides)
        payload.setdefault("model", self.model)
        payload.setdefault("messages", messages)
        payload.setdefault("temperature", 0.7)
        payload.setdefault("max_tokens", 4096)
        # DeepSeek v4 要求 temperature ∈ [0,2]（>2 会 400 "Invalid temperature value"）；
        # 对 None / bool / 越界值做 clamp 兜底，避免上游异常值导致整条请求 400
        temp = payload.get("temperature")
        if not isinstance(temp, (int, float)) or isinstance(temp, bool):
            temp = 0.7
        payload["temperature"] = max(0.0, min(2.0, float(temp)))
        # 本地 Qwen3 系：默认开思考（thinking），实测同一 5k-token prompt
        # 关思考 11.2s vs 开思考 231.9s（**20 倍**，2026-10-15 实测）——不关必然
        # 超过 chat.send 60s idle 超时，使本地兜底在云端余额耗尽时形同虚设
        # （U1 实测：chat.send -32006 idle timed out）。`/no_think` 是 Qwen3 的
        # prompt 约定；仅在「本机端点 + qwen3 模型」时追加，云端 provider 不受影响。
        # 注：Ollama 的 OpenAI 兼容端点**不支持** `think: false` 参数（实测无效）。
        if self._is_local_qwen3():
            payload["messages"] = _with_no_think(payload.get("messages") or messages)
        return payload

    def _request(self, payload: dict[str, Any], *, timeout: int | None = None) -> dict[str, Any]:
        """发送请求，支持可选的 per-call timeout 覆盖。

        P1-3: ``timeout`` 参数允许路由类轻量调用（如 Judge 路由决策）使用
        比默认 300s 更短的超时，避免 DeepSeek 卡住时阻塞整个对话链路。
        """
        last_error: Exception | None = None
        client = self._get_sync_client()
        effective_timeout = timeout if timeout is not None else self.timeout
        connect_timeout = min(10, effective_timeout)
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = client.post(
                    f"{self.api_base}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=_httpx_timeout(connect_timeout, effective_timeout),
                )
                if resp.status_code == 200:
                    return resp.json()
                error_text = extract_error_text_from_response(resp)
                retry_after = parse_retry_after_seconds(resp.headers.get("Retry-After"), error_text)
                retry_delay = compute_retry_delay_seconds(
                    attempt=attempt,
                    policy=_default_retry_policy,
                    retry_after_seconds=retry_after,
                )
                err_cls = classify_error(resp.status_code, error_text)
                if err_cls.transient and attempt < self.max_retries:
                    logger.warning(
                        "attempt %d/%d retryable error %d: %s; waiting %.1fs", attempt, self.max_retries, resp.status_code, error_text, retry_delay
                    )
                    _retry_sleep(retry_delay)
                    last_error = AIServiceError(error_text, status=resp.status_code)
                    continue
                raise AIServiceError(error_text, status=resp.status_code)
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    logger.warning("attempt %d/%d timeout, retrying...", attempt, self.max_retries)
                    _retry_sleep(2**attempt)
                    last_error = exc
                    continue
                raise AIServiceError(f"Request timed out after {self.max_retries} retries", status=408) from exc
            except httpx.RequestError as exc:
                raise AIServiceError(str(exc), status=0) from exc
        if last_error is not None:
            raise AIServiceError(f"All {self.max_retries} retries exhausted") from last_error
        # last_error 为 None 仅在 max_retries <= 0（循环体未执行）时发生
        raise AIServiceError(f"All {self.max_retries} retries exhausted (no attempts made)", status=0)

    async def _arequest(self, payload: dict[str, Any], *, timeout: int | None = None) -> dict[str, Any]:
        """异步发送请求，支持可选的 per-call timeout 覆盖。"""
        last_error: Exception | None = None
        client = await self._get_async_client()
        effective_timeout = timeout if timeout is not None else self.timeout
        connect_timeout = min(10, effective_timeout)
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await client.post(
                    f"{self.api_base}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=_httpx_timeout(connect_timeout, effective_timeout),
                )
                if resp.status_code == 200:
                    return resp.json()
                error_text = extract_error_text_from_response(resp)
                retry_after = parse_retry_after_seconds(resp.headers.get("Retry-After"), error_text)
                retry_delay = compute_retry_delay_seconds(
                    attempt=attempt,
                    policy=_default_retry_policy,
                    retry_after_seconds=retry_after,
                )
                err_cls = classify_error(resp.status_code, error_text)
                if err_cls.transient and attempt < self.max_retries:
                    logger.warning(
                        "async attempt %d/%d retryable error %d: %s; waiting %.1fs",
                        attempt,
                        self.max_retries,
                        resp.status_code,
                        error_text,
                        retry_delay,
                    )
                    await _async_retry_sleep(retry_delay)
                    last_error = AIServiceError(error_text, status=resp.status_code)
                    continue
                raise AIServiceError(error_text, status=resp.status_code)
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    logger.warning("async attempt %d/%d timeout, retrying...", attempt, self.max_retries)
                    await _async_retry_sleep(2**attempt)
                    last_error = exc
                    continue
                raise AIServiceError(f"Request timed out after {self.max_retries} retries", status=408) from exc
            except httpx.RequestError as exc:
                raise AIServiceError(str(exc), status=0) from exc
        if last_error is not None:
            raise AIServiceError(f"All {self.max_retries} retries exhausted") from last_error
        raise AIServiceError(f"All {self.max_retries} retries exhausted (no attempts made)", status=0)

    # ── 对话 ────────────────────────────────────────────────

    def chat(
        self,
        messages: list[dict[str, str]],
        **overrides: Any,
    ) -> tuple[str, dict[str, Any]]:
        """发送对话，返回 (内容, 原始响应)。"""
        # P1-3: 从 overrides 中提取 timeout（不进入 JSON payload）
        timeout = overrides.pop("timeout", None)
        payload = self._payload(messages, **overrides)
        data = self._request(payload, timeout=timeout)
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return content, data

    async def achat(
        self,
        messages: list[dict[str, str]],
        **overrides: Any,
    ) -> tuple[str, dict[str, Any]]:
        """异步发送对话，返回 (内容, 原始响应)。"""
        timeout = overrides.pop("timeout", None)
        payload = self._payload(messages, **overrides)
        data = await self._arequest(payload, timeout=timeout)
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return content, data

    def chat_completion_vision(
        self,
        text: str,
        image_base64: str,
        image_format: str = "png",
        system_prompt: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = 900,
    ) -> dict[str, Any]:
        """带图片的 chat completion（OpenAI 兼容 vision 格式）。

        适配 Ollama /v1、DeepSeek 兼容端、OpenAI 等支持 image_url 的服务。
        返回 dict：同时携带 content/text 与原始响应字段（含 usage）。
        """
        fmt = (image_format or "png").lower().lstrip(".")
        if fmt == "jpg":
            fmt = "jpeg"
        data_url = f"data:image/{fmt};base64,{image_base64}"
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        )
        overrides: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            overrides["max_tokens"] = max_tokens
        data = self._request(self._payload(messages, **overrides))
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return {"content": content, "text": content, **data}

    def stream_chat(
        self,
        messages: list[dict[str, str]],
        **overrides: Any,
    ) -> Iterator[str]:
        """SSE 流式对话。连接阶段有重试，开始传输后不再重试。"""
        payload = self._payload(messages, stream=True, **overrides)
        client = self._get_sync_client()
        connect_timeout = min(15, self.timeout)
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with client.stream(
                    "POST",
                    f"{self.api_base}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=_httpx_timeout(connect_timeout, self.timeout),
                ) as resp:
                    if resp.status_code == 200:
                        rbuf = _ReasoningTextBuffer()
                        for line in resp.iter_lines():
                            if not line:
                                continue
                            decoded = line
                            if decoded.startswith("data: "):
                                chunk_data = decoded.removeprefix("data: ").strip()
                                if chunk_data == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(chunk_data)
                                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                                    piece = rbuf.feed(delta)
                                    if piece:
                                        yield piece
                                except json.JSONDecodeError:
                                    continue
                        # 思考型兜底：全程无 content 时补发推理文本（免零文本误判失败）
                        fallback = rbuf.fallback_text()
                        if fallback:
                            yield fallback
                        return
                    error_text = extract_error_text_from_response(resp)
                    retry_after = parse_retry_after_seconds(resp.headers.get("Retry-After"), error_text)
                    retry_delay = compute_retry_delay_seconds(
                        attempt=attempt,
                        policy=_default_retry_policy,
                        retry_after_seconds=retry_after,
                    )
                    err_cls = classify_error(resp.status_code, error_text)
                    if err_cls.transient and attempt < self.max_retries:
                        logger.warning(
                            "stream attempt %d/%d retryable error %d: %s; waiting %.1fs",
                            attempt,
                            self.max_retries,
                            resp.status_code,
                            error_text,
                            retry_delay,
                        )
                        _retry_sleep(retry_delay)
                        last_error = AIServiceError(error_text, status=resp.status_code)
                        continue
                    raise AIServiceError(error_text, status=resp.status_code)
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    logger.warning("stream attempt %d/%d timeout, retrying...", attempt, self.max_retries)
                    _retry_sleep(2**attempt)
                    last_error = exc
                    continue
                raise AIServiceError(f"Stream timed out after {self.max_retries} retries", status=408) from exc
            except httpx.RequestError as exc:
                if attempt < self.max_retries:
                    logger.warning("stream attempt %d/%d connection error: %s, retrying...", attempt, self.max_retries, exc)
                    _retry_sleep(2**attempt)
                    last_error = exc
                    continue
                raise AIServiceError(str(exc), status=0) from exc

        # 防御：max_retries<=0 时 for 循环未执行；或所有重试耗尽
        if last_error is not None:
            raise AIServiceError(
                f"stream_chat: all {self.max_retries} attempts exhausted",
                status=0,
            ) from last_error
        raise AIServiceError(
            f"stream_chat: no attempts made (max_retries={self.max_retries})",
            status=0,
        )

    async def astream_chat(
        self,
        messages: list[dict[str, str]],
        **overrides: Any,
    ) -> AsyncIterator[str]:
        """异步 SSE 流式对话。连接阶段有重试，开始传输后不再重试。"""
        payload = self._payload(messages, stream=True, **overrides)
        client = await self._get_async_client()
        connect_timeout = min(15, self.timeout)
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                async with client.stream(
                    "POST",
                    f"{self.api_base}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=_httpx_timeout(connect_timeout, self.timeout),
                ) as resp:
                    if resp.status_code == 200:
                        rbuf = _ReasoningTextBuffer()
                        async for line in resp.aiter_lines():
                            if not line:
                                continue
                            decoded = line
                            if decoded.startswith("data: "):
                                chunk_data = decoded.removeprefix("data: ").strip()
                                if chunk_data == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(chunk_data)
                                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                                    piece = rbuf.feed(delta)
                                    if piece:
                                        yield piece
                                except json.JSONDecodeError:
                                    continue
                        # 思考型兜底：全程无 content 时补发推理文本（免零文本误判失败）
                        fallback = rbuf.fallback_text()
                        if fallback:
                            yield fallback
                        return
                    error_text = extract_error_text_from_response(resp)
                    retry_after = parse_retry_after_seconds(resp.headers.get("Retry-After"), error_text)
                    retry_delay = compute_retry_delay_seconds(
                        attempt=attempt,
                        policy=_default_retry_policy,
                        retry_after_seconds=retry_after,
                    )
                    err_cls = classify_error(resp.status_code, error_text)
                    if err_cls.transient and attempt < self.max_retries:
                        logger.warning(
                            "async stream attempt %d/%d retryable error %d: %s; waiting %.1fs",
                            attempt,
                            self.max_retries,
                            resp.status_code,
                            error_text,
                            retry_delay,
                        )
                        await _async_retry_sleep(retry_delay)
                        last_error = AIServiceError(error_text, status=resp.status_code)
                        continue
                    raise AIServiceError(error_text, status=resp.status_code)
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    logger.warning("async stream attempt %d/%d timeout, retrying...", attempt, self.max_retries)
                    await _async_retry_sleep(2**attempt)
                    last_error = exc
                    continue
                raise AIServiceError(f"Stream timed out after {self.max_retries} retries", status=408) from exc
            except httpx.RequestError as exc:
                if attempt < self.max_retries:
                    logger.warning("async stream attempt %d/%d connection error: %s, retrying...", attempt, self.max_retries, exc)
                    await _async_retry_sleep(2**attempt)
                    last_error = exc
                    continue
                raise AIServiceError(str(exc), status=0) from exc

        if last_error is not None:
            raise AIServiceError(
                f"astream_chat: all {self.max_retries} attempts exhausted",
                status=0,
            ) from last_error
        raise AIServiceError(
            f"astream_chat: no attempts made (max_retries={self.max_retries})",
            status=0,
        )

    # ── 结构化输出 ──────────────────────────────────────────

    def structured_chat(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        """请求 JSON 格式的回复。"""
        payload = self._payload(messages, response_format={"type": "json_object"}, **overrides)
        data = self._request(payload)
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "{}")
        return json.loads(content)

    async def astructured_chat(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        """异步请求 JSON 格式的回复。"""
        payload = self._payload(messages, response_format={"type": "json_object"}, **overrides)
        data = await self._arequest(payload)
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "{}")
        return json.loads(content)

    # ── 快捷方法 ────────────────────────────────────────────

    def explain(self, text: str, system_prompt: str = "请用中文简要解释以下内容") -> str:
        content, _ = self.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ]
        )
        return content

    async def aexplain(self, text: str, system_prompt: str = "请用中文简要解释以下内容") -> str:
        content, _ = await self.achat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ]
        )
        return content

    def summarize(self, text: str, max_words: int = 100) -> str:
        content, _ = self.chat(
            [
                {"role": "system", "content": f"请用中文总结核心要点，不超过 {max_words} 字。"},
                {"role": "user", "content": text},
            ]
        )
        return content

    async def asummarize(self, text: str, max_words: int = 100) -> str:
        content, _ = await self.achat(
            [
                {"role": "system", "content": f"请用中文总结核心要点，不超过 {max_words} 字。"},
                {"role": "user", "content": text},
            ]
        )
        return content

    # ── B1: 用量追踪辅助 ──────────────────────────────────

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> tuple[int, int, int] | None:
        """从 OpenAI 兼容响应里提取 usage。

        成功时返回 (prompt_tokens, completion_tokens, total_tokens)；
        无 usage 字段或字段非法时返回 None。
        """
        if not isinstance(data, dict):
            return None
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return None
        try:
            prompt = int(usage.get("prompt_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
            total = int(usage.get("total_tokens") or (prompt + completion))
            return prompt, completion, total
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_reasoning_response(data: dict[str, Any], content: str) -> dict[str, Any]:
        """归一化思考型模型（如 DeepSeek deepseek-v4-*）的空 content 响应。

        思考型模型在推理过程耗尽了 max_tokens 预算时，会把结果放在
        ``message.reasoning_content`` 而 ``message.content`` 为空，最终导致上层
        （简报生成、简历画像提取等）取到空文本而失败。这里把 reasoning_content
        作为兜底回填到顶层 ``content`` 与 ``choices[0].message.content``，
        让所有读取 message.content 的调用方都能拿到非空文本。
        """
        if content or not isinstance(data, dict):
            return {**data, "content": content or ""}
        choices = data.get("choices") or []
        if not choices:
            return {**data, "content": ""}
        try:
            msg = choices[0].get("message") or {}
            rc = msg.get("reasoning_content") or ""
            if isinstance(rc, str) and rc.strip():
                out = {**data, "content": rc}
                try:
                    out["choices"][0]["message"]["content"] = rc
                except Exception:
                    pass
                return out
            # 流式输出（choices[0].message 可能缺失）时也检查顶层 reasoning
            rc = data.get("reasoning_content") or ""
            if isinstance(rc, str) and rc.strip():
                return {**data, "content": rc}
        except Exception:
            pass
        return {**data, "content": ""}

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """粗略估算文本的 token 数。

        - CJK 字符约 2 字符/token
        - 英文约 4 字符/token
        取折中值；空串返回 0。
        """
        if not text:
            return 0
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        non_cjk = len(text) - cjk
        return max(1, int(cjk / 2 + non_cjk / 4))

    def _record_usage(
        self,
        data: dict[str, Any] | None,
        messages: list[dict[str, str]],
        content: str,
        success: bool = True,
    ) -> None:
        """记录一次 chat 调用的用量到全局 UsageTracker。

        - 优先从 response ``usage`` 字段读取；
        - 缺失时按消息长度估算 prompt tokens，按 content 长度估算 completion tokens。
        - 全程 try/except：用量追踪失败不得影响 AI 主路径。
        """
        try:
            from ai.usage_tracker import (
                extract_provider_name,
                get_usage_tracker,
            )

            tracker = get_usage_tracker()
            if tracker is None:
                return
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0
            usage_found = False
            if isinstance(data, dict):
                usage = self._extract_usage(data)
                if usage is not None:
                    prompt_tokens, completion_tokens, total_tokens = usage
                    usage_found = True
            if not usage_found:
                # 估算：拼接 messages 内容
                prompt_text = " ".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))
                prompt_tokens = self._estimate_tokens(prompt_text)
                completion_tokens = self._estimate_tokens(content or "")
                total_tokens = prompt_tokens + completion_tokens
            tracker.record(
                provider=extract_provider_name(self.api_base),
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                success=success,
            )
        except Exception as exc:  # noqa: BLE001 — 用量追踪不得阻塞主路径
            logger.debug("record usage failed: %s", exc)

    # ── FallbackClient 兼容接口 ──────────────────────────────

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        timeout: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        overrides: dict[str, Any] = dict(temperature=temperature, max_tokens=max_tokens)
        if tools is not None:
            overrides["tools"] = tools
        if tool_choice is not None:
            overrides["tool_choice"] = tool_choice
        # P1-3: 透传 per-call timeout（Judge 路由决策使用短超时）
        if timeout is not None:
            overrides["timeout"] = timeout
        # E3: 透传 response_format（JSON 稳健模式）。本地端点不支持时静默剥离，
        # 由调用方的提示词约束 / JSON 提取兜底，避免端点因未知字段直接报错。
        if response_format is not None:
            if self.supports_response_format:
                overrides["response_format"] = response_format
            else:
                logger.info("端点不支持 response_format，已剥离: api_base=%s", self.api_base)
        try:
            content, data = self.chat(messages, **overrides)
        except Exception:
            # 失败也记录一笔（success=False），便于追踪失败率
            self._record_usage(None, messages, "", success=False)
            raise
        self._record_usage(data, messages, content, success=True)
        return self._normalize_reasoning_response(data, content)

    async def achat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        overrides: dict[str, Any] = dict(temperature=temperature, max_tokens=max_tokens)
        if tools is not None:
            overrides["tools"] = tools
        if tool_choice is not None:
            overrides["tool_choice"] = tool_choice
        if timeout is not None:
            overrides["timeout"] = timeout
        try:
            content, data = await self.achat(messages, **overrides)
        except Exception:
            self._record_usage(None, messages, "", success=False)
            raise
        self._record_usage(data, messages, content, success=True)
        return self._normalize_reasoning_response(data, content)

    def chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ):
        overrides: dict[str, Any] = dict(temperature=temperature, max_tokens=max_tokens)
        if tools is not None:
            overrides["tools"] = tools
        if tool_choice is not None:
            overrides["tool_choice"] = tool_choice

        # B1: 包装生成器，在流结束后记录用量（按消息/输出长度估算）
        def _tracked_stream() -> Iterator[str]:
            accumulated: list[str] = []
            success = True
            try:
                for chunk in self.stream_chat(messages, **overrides):
                    accumulated.append(chunk)
                    yield chunk
            except Exception:
                success = False
                self._record_usage(None, messages, "", success=False)
                raise
            finally:
                if success:
                    self._record_usage(
                        None,
                        messages,
                        "".join(accumulated),
                        success=True,
                    )

        return _tracked_stream()

    async def achat_completion_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        overrides: dict[str, Any] = dict(temperature=temperature, max_tokens=max_tokens)
        if tools is not None:
            overrides["tools"] = tools
        if tool_choice is not None:
            overrides["tool_choice"] = tool_choice

        # B1: 包装异步生成器，在流结束后记录用量
        accumulated: list[str] = []
        success = True
        try:
            async for chunk in self.astream_chat(messages, **overrides):
                accumulated.append(chunk)
                yield chunk
        except Exception:
            success = False
            self._record_usage(None, messages, "", success=False)
            raise
        finally:
            if success:
                self._record_usage(
                    None,
                    messages,
                    "".join(accumulated),
                    success=True,
                )

    def stream_chat_with_tools(
        self,
        messages: list[dict[Any, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """流式对话 + 工具调用（同时支持 streaming 和 tool calling）。

        与 stream_chat 不同，此方法 yield 结构化事件 dict，而非纯文本字符串：
        - {"type": "text", "data": "..."}           → 文本内容增量
        - {"type": "tool_call", "data": {...}}       → 工具调用增量（累积）
        - {"type": "tool_call_done", "data": {...}}  → 工具调用完成（完整）
        - {"type": "done"}                           → 流结束

        tool_call_done.data 格式:
            {"index": 0, "id": "call_xxx",
             "function": {"name": "web.search", "arguments": '{"query":"..."}'}}

        此方法仅负责解析流，不执行工具。工具执行由调用方（如 chat.send）负责。
        """
        payload = self._payload(messages, stream=True, temperature=temperature)
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        payload["stream_options"] = {"include_usage": True}

        client = self._get_sync_client()
        connect_timeout = min(15, self.timeout)
        current_tool_calls: dict[int, dict[str, Any]] = {}
        last_error: Exception | None = None
        success = False
        for attempt in range(1, self.max_retries + 1):
            try:
                with client.stream(
                    "POST",
                    f"{self.api_base}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=_httpx_timeout(connect_timeout, self.timeout),
                ) as resp:
                    if resp.status_code == 200:
                        current_tool_calls = {}
                        rbuf = _ReasoningTextBuffer()
                        for line in resp.iter_lines():
                            if not line:
                                continue
                            decoded = line
                            if not decoded.startswith("data: "):
                                continue
                            chunk_data = decoded.removeprefix("data: ").strip()
                            if chunk_data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(chunk_data)
                            except json.JSONDecodeError:
                                continue

                            choices = chunk.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {})

                            # 文本内容（含思考型 reasoning_content 兜底）
                            piece = rbuf.feed(delta)
                            if piece:
                                yield {"type": "text", "data": piece}

                            # 工具调用（增量）
                            if "tool_calls" in delta:
                                for tc in delta["tool_calls"]:
                                    idx = tc.get("index", 0)
                                    if idx not in current_tool_calls:
                                        current_tool_calls[idx] = {
                                            "id": tc.get("id", ""),
                                            "function": {"name": "", "arguments": ""},
                                        }
                                    entry = current_tool_calls[idx]
                                    if "id" in tc:
                                        entry["id"] = tc["id"]
                                    func = tc.get("function", {})
                                    if "name" in func:
                                        entry["function"]["name"] += func["name"]
                                    if "arguments" in func:
                                        entry["function"]["arguments"] += func["arguments"]
                                    # 实时推送增量（累积值，调用方可用于显示进度）
                                    yield {
                                        "type": "tool_call",
                                        "data": {
                                            "index": idx,
                                            "id": entry["id"],
                                            "name": entry["function"]["name"],
                                            "arguments": entry["function"]["arguments"],
                                        },
                                    }
                        # 思考型兜底：全程无 content 时补发推理文本（免零文本误判失败）
                        tool_fallback = rbuf.fallback_text()
                        if tool_fallback:
                            yield {"type": "text", "data": tool_fallback}
                        success = True
                        break
                    error_text = extract_error_text_from_response(resp)
                    retry_after = parse_retry_after_seconds(resp.headers.get("Retry-After"), error_text)
                    retry_delay = compute_retry_delay_seconds(
                        attempt=attempt,
                        policy=_default_retry_policy,
                        retry_after_seconds=retry_after,
                    )
                    err_cls = classify_error(resp.status_code, error_text)
                    if err_cls.transient and attempt < self.max_retries:
                        logger.warning(
                            "stream_with_tools attempt %d/%d retryable %d: %s; wait %.1fs",
                            attempt,
                            self.max_retries,
                            resp.status_code,
                            error_text,
                            retry_delay,
                        )
                        _retry_sleep(retry_delay)
                        last_error = AIServiceError(error_text, status=resp.status_code)
                        continue
                    raise AIServiceError(error_text, status=resp.status_code)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if attempt < self.max_retries:
                    _retry_sleep(2**attempt)
                    last_error = exc
                    continue
                raise AIServiceError(str(exc), status=0) from exc

        if not success:
            if last_error is not None:
                raise AIServiceError(
                    "stream_with_tools: no response after retries",
                    status=0,
                ) from last_error
            raise AIServiceError("stream_with_tools: no response after retries", status=0)

        # 流结束后，推送完整的工具调用
        for idx in sorted(current_tool_calls.keys()):
            tc = current_tool_calls[idx]
            yield {
                "type": "tool_call_done",
                "data": {
                    "index": idx,
                    "id": tc["id"],
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    },
                },
            }

        yield {"type": "done"}

    async def astream_chat_with_tools(
        self,
        messages: list[dict[Any, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """异步流式对话 + 工具调用。"""
        payload = self._payload(messages, stream=True, temperature=temperature)
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        payload["stream_options"] = {"include_usage": True}

        client = await self._get_async_client()
        connect_timeout = min(15, self.timeout)
        current_tool_calls: dict[int, dict[str, Any]] = {}
        last_error: Exception | None = None
        success = False
        for attempt in range(1, self.max_retries + 1):
            try:
                async with client.stream(
                    "POST",
                    f"{self.api_base}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=_httpx_timeout(connect_timeout, self.timeout),
                ) as resp:
                    if resp.status_code == 200:
                        current_tool_calls = {}
                        rbuf = _ReasoningTextBuffer()
                        async for line in resp.aiter_lines():
                            if not line:
                                continue
                            decoded = line
                            if not decoded.startswith("data: "):
                                continue
                            chunk_data = decoded.removeprefix("data: ").strip()
                            if chunk_data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(chunk_data)
                            except json.JSONDecodeError:
                                continue

                            choices = chunk.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {})

                            piece = rbuf.feed(delta)
                            if piece:
                                yield {"type": "text", "data": piece}

                            if "tool_calls" in delta:
                                for tc in delta["tool_calls"]:
                                    idx = tc.get("index", 0)
                                    if idx not in current_tool_calls:
                                        current_tool_calls[idx] = {
                                            "id": tc.get("id", ""),
                                            "function": {"name": "", "arguments": ""},
                                        }
                                    entry = current_tool_calls[idx]
                                    if "id" in tc:
                                        entry["id"] = tc["id"]
                                    func = tc.get("function", {})
                                    if "name" in func:
                                        entry["function"]["name"] += func["name"]
                                    if "arguments" in func:
                                        entry["function"]["arguments"] += func["arguments"]
                                    yield {
                                        "type": "tool_call",
                                        "data": {
                                            "index": idx,
                                            "id": entry["id"],
                                            "name": entry["function"]["name"],
                                            "arguments": entry["function"]["arguments"],
                                        },
                                    }
                        # 思考型兜底：全程无 content 时补发推理文本（免零文本误判失败）
                        async_tool_fallback = rbuf.fallback_text()
                        if async_tool_fallback:
                            yield {"type": "text", "data": async_tool_fallback}
                        success = True
                        break
                    error_text = extract_error_text_from_response(resp)
                    retry_after = parse_retry_after_seconds(resp.headers.get("Retry-After"), error_text)
                    retry_delay = compute_retry_delay_seconds(
                        attempt=attempt,
                        policy=_default_retry_policy,
                        retry_after_seconds=retry_after,
                    )
                    err_cls = classify_error(resp.status_code, error_text)
                    if err_cls.transient and attempt < self.max_retries:
                        logger.warning(
                            "async stream_with_tools attempt %d/%d retryable %d: %s; wait %.1fs",
                            attempt,
                            self.max_retries,
                            resp.status_code,
                            error_text,
                            retry_delay,
                        )
                        await _async_retry_sleep(retry_delay)
                        last_error = AIServiceError(error_text, status=resp.status_code)
                        continue
                    raise AIServiceError(error_text, status=resp.status_code)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if attempt < self.max_retries:
                    await _async_retry_sleep(2**attempt)
                    last_error = exc
                    continue
                raise AIServiceError(str(exc), status=0) from exc

        if not success:
            if last_error is not None:
                raise AIServiceError(
                    "astream_with_tools: no response after retries",
                    status=0,
                ) from last_error
            raise AIServiceError("astream_with_tools: no response after retries", status=0)

        for idx in sorted(current_tool_calls.keys()):
            tc = current_tool_calls[idx]
            yield {
                "type": "tool_call_done",
                "data": {
                    "index": idx,
                    "id": tc["id"],
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    },
                },
            }

        yield {"type": "done"}

    def test_connection(self) -> dict[str, str]:
        """发送测试消息验证连接是否正常。"""
        reply = self.chat_completion([{"role": "user", "content": "Hi"}])
        text = reply.get("content", "") if isinstance(reply, dict) else str(reply)
        return {"base_url": self.api_base, "model": self.model, "reply": text[:200]}

    async def atest_connection(self) -> dict[str, str]:
        """异步发送测试消息验证连接是否正常。"""
        reply = await self.achat_completion([{"role": "user", "content": "Hi"}])
        text = reply.get("content", "") if isinstance(reply, dict) else str(reply)
        return {"base_url": self.api_base, "model": self.model, "reply": text[:200]}

    def is_configured(self) -> bool:
        return bool(self.api_key) and bool(self.api_base)

    @property
    def supports_response_format(self) -> bool:
        """E3 能力标记：该端点是否支持 OpenAI 的 ``response_format`` 强约束。

        本地推理服务（Ollama / Kiro 等本机端点）对 ``response_format`` 的支持
        参差不齐，收到不认识的字段可能直接 400，因此视为不支持；上层
        （services/ai_text_service）据此降级为提示词约束，本类在真正发请求前
        也会再剥一次（防御纵深，避免 Judge 动态路由到本地腿时误传）。
        """
        return not self._is_local_endpoint()
