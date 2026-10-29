"""OpenAI 兼容 LLM 客户端（无需额外依赖）。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests
import hashlib
from pathlib import Path


logger = logging.getLogger("automation_hub.agent.llm_client")

_RESILIENCE_ROOT = Path(__file__).resolve().parents[2]
if str(_RESILIENCE_ROOT) not in sys.path:
    sys.path.append(str(_RESILIENCE_ROOT))

from automation_hub.llm_resilience import (  # noqa: E402
    RetryPolicy,
    build_observability_tags,
    classify_error,
    compute_retry_delay_seconds,
    extract_error_text_from_response,
    parse_retry_after_seconds,
)


class LLMServiceError(RuntimeError):
    """LLM 调用失败，附带上层可判定的瞬态信息。"""

    def __init__(
        self,
        message: str,
        *,
        transient: bool = False,
        status: int | None = None,
        retry_after_seconds: float | None = None,
        error_kind: str | None = None,
    ):
        super().__init__(message)
        self.transient = bool(transient)
        self.status = status
        self.retry_after_seconds = retry_after_seconds
        self.error_kind = error_kind or "unknown_error"


@dataclass
class OpenAICompatibleClient:
    """OpenAI 兼容接口客户端。

    环境变量:
    - OPENAI_API_KEY
    - OPENAI_BASE_URL (默认 https://api.openai.com/v1)
    - OPENAI_MODEL (默认 gpt-4o-mini)
    """

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    timeout: int = 60

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = (self.base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.model = self.model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        if not self.api_key:
            raise ValueError("OPENAI_API_KEY 未设置")

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        response_format: Optional[Dict[str, Any]] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """发起 chat completion 请求（返回原始 JSON 响应）。"""

        payload: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        policy = RetryPolicy(
            attempts=4,
            min_delay_seconds=1.0,
            max_delay_seconds=8.0,
            jitter_ratio=0.1,
            retry_after_cap_seconds=30.0,
        )

        def _do_request() -> Dict[str, Any]:
            url = f"{self.base_url}/chat/completions"
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()

        for attempt in range(1, policy.attempts + 1):
            classification = None
            retry_after_seconds = None
            try:
                return await asyncio.to_thread(_do_request)

            except requests.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                error_text = extract_error_text_from_response(getattr(exc, "response", None))
                retry_after_seconds = parse_retry_after_seconds(
                    getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After") if getattr(exc, "response", None) is not None else None,
                    error_text,
                )
                classification = classify_error(status, error_text, retry_after_seconds)
                tags = build_observability_tags(
                    provider="openai_compatible",
                    model=model or self.model,
                    status=classification.status,
                    transient=classification.transient,
                    retry_after_seconds=classification.retry_after_seconds,
                    fallback_stage="primary",
                    cache_hit=False,
                    error_kind=classification.kind,
                )
                logger.warning(
                    "LLM HTTP 错误: tags=%s attempt=%d/%d error=%s",
                    tags,
                    attempt,
                    policy.attempts,
                    (classification.message or str(exc))[:200],
                )
                if not classification.transient or attempt == policy.attempts:
                    raise LLMServiceError(
                        f"LLM 调用失败: {exc}",
                        transient=classification.transient,
                        status=classification.status,
                        retry_after_seconds=classification.retry_after_seconds,
                        error_kind=classification.kind,
                    ) from exc

            except requests.RequestException as exc:
                classification = classify_error(None, str(exc), None)
                tags = build_observability_tags(
                    provider="openai_compatible",
                    model=model or self.model,
                    status=classification.status,
                    transient=classification.transient,
                    retry_after_seconds=classification.retry_after_seconds,
                    fallback_stage="primary",
                    cache_hit=False,
                    error_kind=classification.kind,
                )
                logger.warning(
                    "LLM 网络错误: tags=%s attempt=%d/%d error=%s",
                    tags,
                    attempt,
                    policy.attempts,
                    str(exc)[:200],
                )
                if attempt == policy.attempts:
                    raise LLMServiceError(
                        f"LLM 调用失败: {exc}",
                        transient=classification.transient,
                        status=classification.status,
                        retry_after_seconds=classification.retry_after_seconds,
                        error_kind=classification.kind,
                    ) from exc

            # 等待下一次重试（Retry-After 优先级高于本地退避）
            sleep_for = compute_retry_delay_seconds(
                attempt=attempt,
                policy=policy,
                retry_after_seconds=classification.retry_after_seconds if classification else retry_after_seconds,
            )
            await asyncio.sleep(sleep_for)

    async def test_connection(self) -> Dict[str, str]:
        """轻量的连接自检，返回简短信息供 UI 显示。"""
        reply = await self.chat_completion(
            messages=[
                {"role": "system", "content": "你是一个连接测试助手。只返回 OK。"},
                {"role": "user", "content": "请只回复 OK"},
            ],
            model=self.model,
            temperature=0,
            max_tokens=20,
        )
        try:
            text = reply["choices"][0]["message"]["content"]
        except Exception:
            text = str(reply)
        return {"model": self.model, "reply": text, "base_url": self.base_url}


def extract_content(response: Any) -> str:
    """从不同响应结构中提取文本内容。"""
    if isinstance(response, str):
        return response
    if isinstance(response, bytes):
        return response.decode("utf-8", errors="ignore")

    # OpenAI 风格 dict
    if isinstance(response, dict):
        try:
            return response["choices"][0]["message"]["content"]
        except Exception:
            return json.dumps(response, ensure_ascii=False)

    # OpenAI SDK 风格对象
    if hasattr(response, "choices"):
        try:
            return response.choices[0].message.content
        except Exception:
            pass

    return str(response)


class FallbackOpenAIClient:
    """提供主服务 -> 本地 LLM -> 本地缓存的回退策略，适配 async chat_completion。"""

    def __init__(self, primary: OpenAICompatibleClient, local: Optional[OpenAICompatibleClient] = None, cache_path: Optional[str] = None):
        self.primary = primary
        self.local = local
        self.cache_path = Path(cache_path) if cache_path else Path(__file__).parent.joinpath("..", "data", "agent_ai_cache.json")
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache = None

    def _load_cache(self) -> dict:
        if self._cache is None:
            try:
                if self.cache_path.exists():
                    with open(self.cache_path, "r", encoding="utf-8") as f:
                        self._cache = json.load(f)
                else:
                    self._cache = {}
            except Exception:
                self._cache = {}
        return self._cache

    def _save_cache(self) -> None:
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self._load_cache(), f, ensure_ascii=False, indent=2)
        except Exception:
            # 不阻塞主流程
            pass

    @staticmethod
    def _messages_key(messages: List[Dict[str, str]]) -> str:
        joined = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    def _get_cached(self, key: str) -> Optional[Dict[str, str]]:
        cache = self._load_cache()
        return cache.get(key)

    def _store_cached(self, key: str, text: str) -> None:
        cache = self._load_cache()
        cache[key] = {"text": text}
        self._save_cache()

    async def chat_completion(self, messages: List[Dict[str, str]], model: Optional[str] = None, temperature: float = 0.2, response_format: Optional[Dict[str, Any]] = None, max_tokens: Optional[int] = None) -> Dict[str, Any]:
        key = self._messages_key(messages)
        last_transient = False
        last_status = None
        last_retry_after_seconds = None

        # 1) 主服务
        try:
            resp = await self.primary.chat_completion(messages=messages, model=model, temperature=temperature, response_format=response_format, max_tokens=max_tokens)
            try:
                text = resp["choices"][0]["message"]["content"]
                if isinstance(text, str) and text.strip():
                    self._store_cached(key, text.strip())
            except Exception:
                pass
            return resp
        except LLMServiceError as exc:
            last_transient = getattr(exc, "transient", False)
            last_status = getattr(exc, "status", None)
            last_retry_after_seconds = getattr(exc, "retry_after_seconds", None)
            logger.warning(
                "主 LLM 失败，尝试回退: tags=%s error=%s",
                build_observability_tags(
                    provider="openai_compatible",
                    model=getattr(self.primary, "model", None),
                    status=last_status,
                    transient=last_transient,
                    retry_after_seconds=last_retry_after_seconds,
                    fallback_stage="primary",
                    cache_hit=False,
                    error_kind="primary_failed",
                ),
                exc,
            )
        except Exception as exc:
            logger.warning(
                "主 LLM 失败（非结构化异常），尝试回退: tags=%s error=%s",
                build_observability_tags(
                    provider="openai_compatible",
                    model=getattr(self.primary, "model", None),
                    status=None,
                    transient=False,
                    retry_after_seconds=None,
                    fallback_stage="primary",
                    cache_hit=False,
                    error_kind="primary_failed_unstructured",
                ),
                exc,
            )

        # 2) 本地
        if self.local is not None:
            try:
                resp = await self.local.chat_completion(messages=messages, model=model, temperature=temperature, response_format=response_format, max_tokens=max_tokens)
                try:
                    text = resp["choices"][0]["message"]["content"]
                    if isinstance(text, str) and text.strip():
                        self._store_cached(key, text.strip())
                except Exception:
                    pass
                return resp
            except LLMServiceError as exc:
                last_transient = last_transient or getattr(exc, "transient", False)
                last_status = last_status or getattr(exc, "status", None)
                if last_retry_after_seconds is None:
                    last_retry_after_seconds = getattr(exc, "retry_after_seconds", None)
                logger.warning(
                    "本地 LLM 回退失败: tags=%s error=%s",
                    build_observability_tags(
                        provider="local_openai_compatible",
                        model=getattr(self.local, "model", None),
                        status=getattr(exc, "status", None),
                        transient=getattr(exc, "transient", False),
                        retry_after_seconds=getattr(exc, "retry_after_seconds", None),
                        fallback_stage="local",
                        cache_hit=False,
                        error_kind="local_failed",
                    ),
                    exc,
                )
            except Exception as exc:
                logger.warning(
                    "本地 LLM 回退失败（非结构化异常）: tags=%s error=%s",
                    build_observability_tags(
                        provider="local_openai_compatible",
                        model=getattr(self.local, "model", None),
                        status=None,
                        transient=False,
                        retry_after_seconds=None,
                        fallback_stage="local",
                        cache_hit=False,
                        error_kind="local_failed_unstructured",
                    ),
                    exc,
                )

        # 3) 缓存
        cached = self._get_cached(key)
        if cached and isinstance(cached, dict) and cached.get("text"):
            logger.info(
                "使用 LLM 缓存响应: tags=%s key=%s",
                build_observability_tags(
                    provider="cache",
                    model=getattr(self.primary, "model", None),
                    status=last_status,
                    transient=last_transient,
                    retry_after_seconds=last_retry_after_seconds,
                    fallback_stage="cache",
                    cache_hit=True,
                    error_kind="cache_hit",
                ),
                key[:12],
            )
            return {"choices": [{"message": {"content": cached["text"]}}]}

        logger.warning(
            "LLM 回退链路失败: tags=%s key=%s",
            build_observability_tags(
                provider="fallback_chain",
                model=getattr(self.primary, "model", None),
                status=last_status,
                transient=bool(last_transient),
                retry_after_seconds=last_retry_after_seconds,
                fallback_stage="cache",
                cache_hit=False,
                error_kind="fallback_exhausted",
            ),
            key[:12],
        )
        raise LLMServiceError(
            "AI 服务不可用，且未命中本地回退",
            transient=bool(last_transient),
            status=last_status,
            retry_after_seconds=last_retry_after_seconds,
            error_kind="fallback_exhausted",
        )

    async def test_connection(self) -> Dict[str, str]:
        """代理到主服务或本地的轻量连接检测，供 UI 使用。"""
        last_exc: Exception | None = None
        try:
            return await self.primary.test_connection()
        except Exception as exc:  # pragma: no cover - best-effort UI helper
            last_exc = exc
            logger.debug("primary test_connection failed: %s", exc)

        if self.local is not None:
            try:
                return await self.local.test_connection()
            except Exception as exc:  # pragma: no cover - best-effort UI helper
                last_exc = exc
                logger.debug("local test_connection failed: %s", exc)

        # 若都失败，抛出最后的异常以供上层处理
        if last_exc is not None:
            raise last_exc
        raise LLMServiceError("未配置可用的 LLM 客户端用于连接测试")


def create_fallback_client(local_base_url: Optional[str] = None, local_api_key: Optional[str] = None, cache_path: Optional[str] = None) -> FallbackOpenAIClient:
    primary = OpenAICompatibleClient()
    local = None
    if local_base_url:
        local = OpenAICompatibleClient(api_key=local_api_key or os.getenv("OPENAI_API_KEY"), base_url=local_base_url)
    return FallbackOpenAIClient(primary=primary, local=local, cache_path=cache_path)
