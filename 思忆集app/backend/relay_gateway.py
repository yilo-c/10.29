"""对外 LLM 中继网关（OpenAI + Anthropic 双协议）。

给思忆集 aiohttp 后端加一层 HTTP 壳，复用 ``FallbackOpenAIClient`` 的
primary → fallback → local 回退链与用量统计，**不注入**"小问"persona。

端点：
- ``POST /v1/chat/completions``  OpenAI 格式（Authorization: Bearer 鉴权）
- ``POST /v1/messages``           Anthropic 格式（x-api-key / Bearer 兼容）

模型映射（对标 new-api 的别名/重定向表）：
- ``auto`` / ``default`` / 空 → 走默认回退链（``FallbackOpenAIClient.chat_completion*``）。
- 已知 provider 名 → ``get_client_by_provider(provider, None, settings)`` 直选。
- 命中 ``relay_model_map`` 别名表（值形如 ``provider/model`` 或 ``provider``）→ 精确路由。
- 其余未知名 → 回落默认链 + WARNING（不报错）。

关键设计：
- handler **自产各自协议的错误体**（OpenAI ``{"error": {...}}`` / Anthropic
  ``{"type": "error", ...}``），不依赖 aiohttp 全局 JSON-RPC 错误中间件
  （该中间件输出 JSON-RPC 形状，对 OpenAI/Anthropic 调用方是错误格式）。
- 底层同步（httpx）调用经 ``asyncio.to_thread`` 抬到线程，流式经后台线程包装，
  绝不在事件循环里直接跑同步生成器。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from aiohttp import web

from ai.types import AIServiceError

logger = logging.getLogger("siyi.backend.relay")

# 非流式默认 max_tokens —— 与 FallbackOpenAIClient.chat_completion_stream 默认一致
_DEFAULT_MAX_TOKENS_NONSTREAM = 900
# Anthropic 协议要求显式 max_tokens，缺省给一个较大的兜底值
_DEFAULT_MAX_TOKENS_ANTHROPIC = 4000

# 走默认自动路由的模型名
_AUTO_MODELS = frozenset({"", "auto", "default"})

_SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


class RelayGateway:
    """双协议对外中继网关，包装一个 FallbackOpenAIClient 复用其回退链/用量统计。"""

    def __init__(
        self,
        fallback: Any,
        relay_key: str | None,
        model_map: dict[str, str] | None = None,
        *,
        settings_repository: Any = None,
        debug: bool = False,
    ) -> None:
        self._fallback = fallback
        self._relay_key = relay_key or None
        self._model_map: dict[str, str] = dict(model_map or {})
        self._settings_repository = settings_repository
        self._debug = debug

    # ── 鉴权 ──────────────────────────────────────────────

    def _has_key(self) -> bool:
        return bool(self._relay_key)

    def _authorize(self, request: web.Request, *, header: str, prefix: str = "") -> bool:
        if not self._has_key():
            return False
        value = request.headers.get(header, "")
        if prefix and value.startswith(prefix):
            value = value[len(prefix) :].strip()
        if not value:
            return False
        return hmac.compare_digest(value, self._relay_key or "")

    def _authorize_openai(self, request: web.Request) -> bool:
        if self._authorize(request, header="Authorization", prefix="Bearer "):
            return True
        return self._authorize(request, header="x-api-key") or self._authorize(request, header="X-Api-Key")

    def _authorize_anthropic(self, request: web.Request) -> bool:
        if self._authorize(request, header="x-api-key"):
            return True
        if self._authorize(request, header="X-Api-Key"):
            return True
        return self._authorize(request, header="Authorization", prefix="Bearer ")

    # ── 错误体（各自协议，不依赖全局 JSON-RPC 中间件）────────────

    def _error(
        self,
        status: int,
        message: str,
        *,
        openai_type: str = "invalid_request_error",
        code: str | None = None,
    ) -> web.Response:
        return web.json_response(
            {"error": {"message": message, "type": openai_type, "param": None, "code": code}},
            status=status,
        )

    def _error_anthropic(self, status: int, message: str, error_type: str = "invalid_request_error") -> web.Response:
        return web.json_response(
            {"type": "error", "error": {"type": error_type, "message": message}},
            status=status,
        )

    # ── 模型解析（new-api 风格别名/重定向表）────────────────

    def _get_client_by_provider(self, provider: str, model: str | None) -> Any:
        try:
            return self._fallback.get_client_by_provider(provider, model, self._settings_repository)
        except Exception as exc:  # noqa: BLE001
            logger.warning("relay: get_client_by_provider(%s, %s) failed: %s", provider, model, exc)
            return None

    def _client_from_map_value(self, value: str) -> Any:
        """解析别名表映射值：空 → 默认链；provider/model → 精确；provider → 直选。"""
        val = value.strip()
        if not val:
            return None
        if "/" in val:
            provider, _, model = val.partition("/")
            client = self._get_client_by_provider(provider.strip(), model.strip() or None)
        else:
            client = self._get_client_by_provider(val.strip(), None)
        if client is None:
            logger.warning("relay: mapping %r -> provider not resolvable, falling back to default chain", val)
            return None
        return client

    def _resolve_model(self, model: Any) -> Any:
        """解析请求里的 model：返回具体客户端或 None（None = 走默认回退链）。"""
        key = (str(model) if model is not None else "").strip()
        if key in _AUTO_MODELS:
            return None
        if key in self._model_map:
            return self._client_from_map_value(str(self._model_map[key]))
        # 未命中别名表 → 尝试当作 provider 名直选；解析不到则回落默认链
        client = self._get_client_by_provider(key, None)
        if client is not None:
            return client
        logger.warning("relay: unknown model %r, falling back to default chain", key)
        return None

    # ── 请求参数归一化 ─────────────────────────────────────

    @staticmethod
    def _cc_kwargs(temperature: Any, max_tokens: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if temperature is not None:
            try:
                kwargs["temperature"] = float(temperature)
            except (TypeError, ValueError):
                pass
        if max_tokens is not None:
            try:
                kwargs["max_tokens"] = int(max_tokens)
            except (TypeError, ValueError):
                pass
        return kwargs

    @staticmethod
    def _extract_text(resp: dict[str, Any]) -> str:
        try:
            return str(resp["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError):
            return ""

    @staticmethod
    def _chunk_text(chunk: Any) -> str | None:
        """从流式 chunk 提取文本增量，兼容 str 与 dict-with-choices 两态。"""
        if isinstance(chunk, dict):
            try:
                return chunk["choices"][0]["delta"].get("content")
            except (KeyError, IndexError, TypeError):
                return None
        return chunk if isinstance(chunk, str) else None

    # ── 内部调用（同步 → 线程池）───────────────────────────

    async def _call_text(self, client: Any, messages: list[dict[str, str]], temperature: Any, max_tokens: Any) -> dict[str, Any]:
        fn = self._fallback.chat_completion if client is None else client.chat_completion
        kwargs = self._cc_kwargs(temperature, max_tokens)
        resp = await asyncio.to_thread(fn, messages, **kwargs)
        return resp if isinstance(resp, dict) else {"choices": [{"message": {"content": str(resp)}}]}

    async def _raw_chunks(self, client: Any, messages: list[dict[str, str]], temperature: Any, max_tokens: Any) -> AsyncIterator[str]:
        """逐块产出归一化后的纯文本增量（统一 str/dict 两态），兼容默认链与 provider 直选。"""
        kwargs = dict(messages=messages, **self._cc_kwargs(temperature, max_tokens))
        if client is None:
            source = self._fallback.achat_completion_stream(**kwargs)
        else:
            # provider 直选命中的裸 OpenAICompatibleClient：同步生成器 → 后台线程包装
            from ai.client.fallback import _async_iter_from_sync

            source = _async_iter_from_sync(lambda: client.chat_completion_stream(**kwargs))
        async for chunk in source:
            text = self._chunk_text(chunk)
            if text is None:
                continue
            yield text

    # ── OpenAI /v1/chat/completions ────────────────────────

    async def handle_openai_chat(self, request: web.Request) -> web.StreamResponse:
        if not self._authorize_openai(request):
            return self._error(401, "Invalid API key", openai_type="authentication_error", code="invalid_api_key")
        try:
            body = await request.json()
        except Exception:
            return self._error(400, "invalid JSON body: expected an application/json object")
        if not isinstance(body, dict):
            return self._error(400, "request body must be a JSON object")
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._error(400, "`messages` is required and must be a non-empty array")
        model = body.get("model")
        temperature = body.get("temperature")
        max_tokens = body.get("max_tokens", _DEFAULT_MAX_TOKENS_NONSTREAM)
        stream = bool(body.get("stream", False))

        client = self._resolve_model(model)
        try:
            if stream:
                return await self._openai_stream(request, client, messages, temperature, max_tokens)
            resp = await self._call_text(client, messages, temperature, max_tokens)
            return self._openai_json_response(model, resp)
        except AIServiceError as exc:
            return self._error(502, f"AI 服务不可用或全部回退失败: {exc}", openai_type="server_error", code="ai_unavailable")
        except Exception as exc:  # noqa: BLE001
            logger.warning("relay openai handler error: %s", exc, exc_info=True)
            return self._error(500, f"内部错误: {exc}", openai_type="internal_error")

    def _openai_json_response(self, model: Any, resp: dict[str, Any]) -> web.Response:
        text = self._extract_text(resp)
        usage = resp.get("usage") if isinstance(resp, dict) else None
        payload = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": str(model) or "auto",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": usage if isinstance(usage, dict) else {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        return web.json_response(payload)

    async def _openai_stream(
        self, request: web.Request, client: Any, messages: list[dict[str, str]], temperature: Any, max_tokens: Any
    ) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, headers=_SSE_HEADERS)
        await resp.prepare(request)
        try:
            async for text in self._raw_chunks(client, messages, temperature, max_tokens):
                payload = {"choices": [{"delta": {"content": text, "role": "assistant"}, "index": 0}]}
                await resp.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        except AIServiceError as exc:
            payload = {"error": {"message": str(exc), "type": "server_error"}}
            await resp.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("relay openai stream error: %s", exc, exc_info=True)
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp

    # ── Anthropic /v1/messages ────────────────────────────

    async def handle_anthropic_messages(self, request: web.Request) -> web.StreamResponse:
        if not self._authorize_anthropic(request):
            return self._error_anthropic(401, "Invalid API key", "authentication_error")
        try:
            body = await request.json()
        except Exception:
            return self._error_anthropic(400, "invalid JSON body: expected an application/json object")
        if not isinstance(body, dict):
            return self._error_anthropic(400, "request body must be a JSON object")
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._error_anthropic(400, "`messages` is required and must be a non-empty array")
        max_tokens = body.get("max_tokens", _DEFAULT_MAX_TOKENS_ANTHROPIC)
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            return self._error_anthropic(400, "`max_tokens` must be an integer")
        temperature = body.get("temperature")
        stream = bool(body.get("stream", False))
        model = body.get("model")

        converted = self._build_openai_messages(body.get("system"), messages)
        client = self._resolve_model(model)
        try:
            if stream:
                return await self._anthropic_stream(request, client, converted, temperature, max_tokens)
            resp = await self._call_text(client, converted, temperature, max_tokens)
            return self._anthropic_json_response(model, resp)
        except AIServiceError as exc:
            return self._error_anthropic(502, f"AI 服务不可用或全部回退失败: {exc}", "api_error")
        except Exception as exc:  # noqa: BLE001
            logger.warning("relay anthropic handler error: %s", exc, exc_info=True)
            return self._error_anthropic(500, f"内部错误: {exc}", "api_error")

    @staticmethod
    def _build_openai_messages(system: Any, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        if system:
            if isinstance(system, str) and system:
                out.append({"role": "system", "content": system})
            elif isinstance(system, list):
                parts = [str(blk.get("text", "")) for blk in system if isinstance(blk, dict) and blk.get("type") == "text"]
                if parts:
                    out.append({"role": "system", "content": "".join(parts)})
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "user")
            if role not in ("system", "user", "assistant"):
                role = "user"
            content = msg.get("content")
            if isinstance(content, list):
                text_parts = [str(blk.get("text", "")) for blk in content if isinstance(blk, dict) and blk.get("type") == "text"]
                content = "".join(text_parts) if text_parts else ""
            out.append({"role": role, "content": str(content if content is not None else "")})
        return out

    def _anthropic_json_response(self, model: Any, resp: dict[str, Any]) -> web.Response:
        text = self._extract_text(resp)
        usage = resp.get("usage") if isinstance(resp, dict) else {}
        input_tokens = int(usage.get("prompt_tokens") or 0) if isinstance(usage, dict) else 0
        output_tokens = int(usage.get("completion_tokens") or 0) if isinstance(usage, dict) else 0
        payload = {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "model": str(model) or "auto",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
        return web.json_response(payload)

    async def _anthropic_stream(
        self, request: web.Request, client: Any, messages: list[dict[str, str]], temperature: Any, max_tokens: Any
    ) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, headers=_SSE_HEADERS)
        await resp.prepare(request)
        message_id = f"msg_{uuid.uuid4().hex[:24]}"
        prefix = (
            "event: message_start\n"
            + f"data: {json.dumps({'type': 'message_start', 'message': {'id': message_id, 'type': 'message', 'role': 'assistant', 'model': 'auto', 'content': [], 'usage': {'input_tokens': 0, 'output_tokens': 0}}}, ensure_ascii=False)}\n\n"
            + "event: content_block_start\n"
            + f"data: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}, ensure_ascii=False)}\n\n"
        )
        await resp.write(prefix.encode("utf-8"))
        try:
            async for text in self._raw_chunks(client, messages, temperature, max_tokens):
                delta = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}
                await resp.write(f"event: content_block_delta\ndata: {json.dumps(delta, ensure_ascii=False)}\n\n".encode("utf-8"))
        except AIServiceError as exc:
            await resp.write(
                (
                    "event: error\n"
                    + f"data: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': str(exc)}}, ensure_ascii=False)}\n\n"
                ).encode("utf-8")
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("relay anthropic stream error: %s", exc, exc_info=True)
        suffix = (
            "event: content_block_stop\n"
            + f"data: {json.dumps({'type': 'content_block_stop', 'index': 0}, ensure_ascii=False)}\n\n"
            + "event: message_delta\n"
            + f"data: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 0}}, ensure_ascii=False)}\n\n"
            + "event: message_stop\n"
            + f"data: {json.dumps({'type': 'message_stop'}, ensure_ascii=False)}\n\n"
        )
        await resp.write(suffix.encode("utf-8"))
        await resp.write_eof()
        return resp
