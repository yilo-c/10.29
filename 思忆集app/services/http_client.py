"""统一出域 HTTP 客户端工厂（M1.6a 第一步：只建不迁）。

背景：仓库内 ``aiohttp.ClientSession(`` 散点实测 42 处 / 32 文件（Python 侧），
其中生产代码 ~24 处。共性形态收敛为三种：

  1. ``async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=N))``
     —— 有超时（_info_proxy / provider_config / rss_subscriber / _system_settings）
  2. ``async with aiohttp.ClientSession()`` —— **完全无超时**（platform_publisher /
     image_generator / kimi_sync / cloud_push / _cloud_sync_crypto），挂起即卡死
  3. ``self._session = aiohttp.ClientSession(headers=...)`` 长连接持有
     （sync/transport、sync/cloud_sync_client）

本工厂把「超时兜底 + UA + 可插拔出域日志钩子」一次性解决，签名兼容上述三类的迁移：

  - ``new_session(purpose=...)``      → 直接返回 ClientSession（对应形态 3）
  - ``async with egress_session(...)``→ 一次性请求（对应形态 1 / 2）

签名要点（与散点共性一一对应）：
  - ``purpose``     必填的出域用途标签（如 ``"rss.fetch"``），进审计/日志，用于归类
  - ``total`` / ``connect`` / ``sock_read``  细粒度超时，缺省用模块级默认值
  - ``headers`` / ``user_agent``  合并 UA（调用方显式 UA 优先，覆盖浏览器 UA 场景）
  - ``trust_env``  默认 True（沿用现有较严谨散点；代理语义显式可控）
  - ``**kwargs``   透传给 ClientSession（connector / base_url / json_serialize 等）

出域日志钩子（可插拔）：
  ``register_egress_hook(fn)`` 注册 ``fn(event: dict) -> None``；事件在**每次请求
  结束**（含异常）与 session 开/关时发出，含 method / target(host[:port]，已脱敏，
  绝不含 path/query) / status / elapsed_ms / purpose。内置 ``logging_egress_hook``
  与 ``make_audit_hook(writer)``（写入既有 integration_audit 表，不新建表）。

本模块**不迁移任何散点**，迁移清单见 M1 返回报告与 ``scripts/egress_ratchet.py``。
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlsplit

import aiohttp

logger = logging.getLogger("siyi.services.http_client")

# ── 默认值（可用环境变量覆盖，便于线上调参不改代码）──
DEFAULT_TIMEOUT_TOTAL = float(os.environ.get("SIYI_HTTP_TIMEOUT_TOTAL", "30"))
DEFAULT_TIMEOUT_CONNECT = float(os.environ.get("SIYI_HTTP_TIMEOUT_CONNECT", "10"))
DEFAULT_USER_AGENT = os.environ.get("SIYI_HTTP_USER_AGENT", "siyi-collection/1.0 (+local-desktop)")

# ── 出域事件常量 ──
KIND_REQUEST = "request"
KIND_SESSION_OPEN = "session_open"
KIND_SESSION_CLOSE = "session_close"

# 钩子签名：``fn(event: dict) -> None``
EgressHook = Callable[[dict[str, Any]], None]

EGRESS_HOOKS: list[EgressHook] = []


def make_timeout(
    total: float | None = DEFAULT_TIMEOUT_TOTAL,
    connect: float | None = DEFAULT_TIMEOUT_CONNECT,
    sock_read: float | None = None,
    sock_connect: float | None = None,
) -> aiohttp.ClientTimeout:
    """构造超时配置。传 ``0`` 表示关闭该项限制（aiohttp 语义）。"""
    return aiohttp.ClientTimeout(
        total=total,
        connect=connect,
        sock_read=sock_read,
        sock_connect=sock_connect,
    )


def default_headers(
    headers: dict[str, str] | None = None,
    *,
    user_agent: str | None = None,
) -> dict[str, str]:
    """合并请求头：注入 UA，但**绝不覆盖**调用方已显式给出的 UA。"""
    merged = {str(k): str(v) for k, v in (headers or {}).items()}
    has_ua = any(k.lower() == "user-agent" for k in merged)
    if not has_ua:
        merged["User-Agent"] = user_agent if user_agent is not None else DEFAULT_USER_AGENT
    return merged


def egress_target(url: Any) -> str:
    """把 URL 归约为 ``host[:port]`` 出域目标（脱敏：不含 path/query/凭证）。"""
    text = str(url or "")
    try:
        parts = urlsplit(text)
    except ValueError:
        return "<unparseable>"
    if not parts.netloc:
        return text[:80]
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    return f"{host}:{port}" if port else host or text[:80]


# ── 可插拔钩子 ──


def register_egress_hook(hook: EgressHook) -> EgressHook:
    """注册出域事件钩子（幂等：同一可调用对象只注册一次）。返回该钩子便于解注册。"""
    if not callable(hook):
        raise TypeError("egress hook must be callable")
    if hook not in EGRESS_HOOKS:
        EGRESS_HOOKS.append(hook)
    return hook


def unregister_egress_hook(hook: EgressHook) -> bool:
    """移除钩子，返回是否确实移除。"""
    try:
        EGRESS_HOOKS.remove(hook)
        return True
    except ValueError:
        return False


def emit_egress(event: dict[str, Any]) -> None:
    """向所有已注册钩子广播出域事件；单钩子异常只告警，绝不影响出域主流程。"""
    for hook in tuple(EGRESS_HOOKS):
        try:
            hook(dict(event))
        except Exception:  # noqa: BLE001
            logger.debug("egress hook %r failed", hook, exc_info=True)


def logging_egress_hook(event: dict[str, Any]) -> None:
    """内置钩子：以 INFO 记录出域事件（仅 host 级信息，可安全常开）。"""
    logger.info(
        "egress kind=%s purpose=%s target=%s method=%s status=%s elapsed_ms=%s error=%s",
        event.get("kind"),
        event.get("purpose"),
        event.get("target"),
        event.get("method"),
        event.get("status"),
        event.get("elapsed_ms"),
        event.get("error"),
    )


def make_audit_hook(writer: Any, *, provider: str = "egress") -> EgressHook:
    """把出域事件写入既有审计通道（``AuditWriter.submit_integration``，不新建表）。

    ``writer`` 只需鸭子类型具备 ``submit_integration(provider, event, status, payload)``。
    """

    def _hook(event: dict[str, Any]) -> None:
        kind = str(event.get("kind") or "")
        status = "error" if event.get("error") else "ok"
        writer.submit_integration(
            provider,
            kind or "unknown",
            status,
            {
                "source": "egress_factory",
                "kind": kind,
                "purpose": event.get("purpose"),
                "target": event.get("target"),
                "method": event.get("method"),
                "status": event.get("status"),
                "elapsed_ms": event.get("elapsed_ms"),
                "error": event.get("error"),
            },
        )

    return _hook


@dataclass
class _SessionState:
    """每个 session 一份：purpose + 请求计时表（id(trace_ctx) → 起始单调时钟）。"""

    purpose: str
    trust_env: bool
    timeout_total: float | None
    started: dict[int, float] = field(default_factory=dict)
    opened_at: float = field(default_factory=time.monotonic)


def _build_trace_config(state: _SessionState) -> aiohttp.TraceConfig:
    """装配 TraceConfig：请求级出域事件（成功 / 异常）都带 purpose 与耗时。"""
    trace = aiohttp.TraceConfig()

    async def _on_request_start(_session, trace_ctx, params) -> None:
        state.started[id(trace_ctx)] = time.monotonic()

    def _elapsed_ms(trace_ctx) -> float | None:
        t0 = state.started.pop(id(trace_ctx), None)
        return None if t0 is None else round((time.monotonic() - t0) * 1000.0, 2)

    async def _on_request_end(_session, trace_ctx, params) -> None:
        response = getattr(params, "response", None)
        emit_egress(
            {
                "kind": KIND_REQUEST,
                "purpose": state.purpose,
                "method": str(getattr(params, "method", "") or ""),
                "target": egress_target(getattr(params, "url", "")),
                "status": getattr(response, "status", None),
                "elapsed_ms": _elapsed_ms(trace_ctx),
                "error": None,
                "trust_env": state.trust_env,
                "timeout_total": state.timeout_total,
            }
        )

    async def _on_request_exception(_session, trace_ctx, params) -> None:
        exc = getattr(params, "exception", None)
        emit_egress(
            {
                "kind": KIND_REQUEST,
                "purpose": state.purpose,
                "method": str(getattr(params, "method", "") or ""),
                "target": egress_target(getattr(params, "url", "")),
                "status": None,
                "elapsed_ms": _elapsed_ms(trace_ctx),
                "error": exc.__class__.__name__ if exc is not None else "unknown",
                "trust_env": state.trust_env,
                "timeout_total": state.timeout_total,
            }
        )

    trace.on_request_start.append(_on_request_start)
    trace.on_request_end.append(_on_request_end)
    trace.on_request_exception.append(_on_request_exception)
    return trace


def new_session(
    purpose: str,
    *,
    total: float | None = DEFAULT_TIMEOUT_TOTAL,
    connect: float | None = DEFAULT_TIMEOUT_CONNECT,
    sock_read: float | None = None,
    timeout: aiohttp.ClientTimeout | None = None,
    headers: dict[str, str] | None = None,
    user_agent: str | None = None,
    trust_env: bool = True,
    **kwargs: Any,
) -> aiohttp.ClientSession:
    """创建带超时兜底 / UA / 出域追踪的 ClientSession（调用方负责 close）。

    ``timeout`` 显式传入时完全优先（用于调用方已有精细超时的场景）。
    """
    resolved = timeout or make_timeout(total=total, connect=connect, sock_read=sock_read)
    state = _SessionState(
        purpose=str(purpose or "unspecified"),
        trust_env=bool(trust_env),
        timeout_total=getattr(resolved, "total", None),
    )
    emit_egress(
        {
            "kind": KIND_SESSION_OPEN,
            "purpose": state.purpose,
            "target": "",
            "method": "",
            "status": None,
            "elapsed_ms": None,
            "error": None,
            "trust_env": state.trust_env,
            "timeout_total": state.timeout_total,
        }
    )
    session = aiohttp.ClientSession(
        timeout=resolved,
        headers=default_headers(headers, user_agent=user_agent),
        trust_env=trust_env,
        trace_configs=[_build_trace_config(state)],
        **kwargs,
    )
    session._siyi_egress_state = state  # type: ignore[attr-defined]
    return session


@contextlib.asynccontextmanager
async def egress_session(
    purpose: str,
    **kwargs: Any,
) -> AsyncIterator[aiohttp.ClientSession]:
    """``async with`` 版工厂：出域结束后保证关闭 session。

    用法::

        async with egress_session("rss.fetch", total=15) as session:
            async with session.get(url) as resp:
                text = await resp.text()
    """
    session = new_session(purpose, **kwargs)
    state: _SessionState | None = getattr(session, "_siyi_egress_state", None)
    try:
        yield session
    finally:
        try:
            elapsed = None
            if state is not None:
                elapsed = round((time.monotonic() - state.opened_at) * 1000.0, 2)
            emit_egress(
                {
                    "kind": KIND_SESSION_CLOSE,
                    "purpose": state.purpose if state else str(purpose),
                    "target": "",
                    "method": "",
                    "status": None,
                    "elapsed_ms": elapsed,
                    "error": None,
                    "trust_env": state.trust_env if state else None,
                    "timeout_total": state.timeout_total if state else None,
                }
            )
        finally:
            await session.close()


__all__ = [
    "DEFAULT_TIMEOUT_CONNECT",
    "DEFAULT_TIMEOUT_TOTAL",
    "DEFAULT_USER_AGENT",
    "KIND_REQUEST",
    "KIND_SESSION_CLOSE",
    "KIND_SESSION_OPEN",
    "default_headers",
    "egress_session",
    "egress_target",
    "emit_egress",
    "logging_egress_hook",
    "make_audit_hook",
    "make_timeout",
    "new_session",
    "register_egress_hook",
    "unregister_egress_hook",
]
