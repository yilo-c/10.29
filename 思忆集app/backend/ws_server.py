"""aiohttp WebSocket JSON-RPC 服务器。

提供：
- request/response 模式（标准 JSON-RPC）
- streaming 模式（AI chat SSE-like via WebSocket）
- server-push 事件（通过 EventBridge）
- 连接池管理（通过 ConnectionManager）
- 可选的 Token 认证
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

from backend.connection_manager import ConnectionManager
from backend.event_bridge import EventBridge
from backend.method_registry import MethodNotFound, MethodRegistry
from services.sandbox_manager import reset_request_connection, set_request_connection
from core.errors import (
    ClientUpgradeRequiredError,
    ConnectionLimitExceededError,
    ErrorCode,
    InvalidParamsError,
    RateLimitedError,
    RPCTimeoutError,
    create_error_middleware,
    make_jsonrpc_error,
)
from core.health_check import get_health_checker
from core.metrics import get_metrics_collector, metrics_handler
from core.rate_limiter import RateLimiter, TokenBucketLimiter

logger = logging.getLogger("siyi.backend.ws")

# ── JSON-RPC 常量 ──
JSONRPC_VERSION = "2.0"

# ── 出站体积守卫（P0-2026-09-16）──
# 背景：aiohttp 的 SIYI_WS_MAX_MSG_SIZE 只约束**入站**消息（WebSocketResponse
# max_msg_size），出站 send_str 无任何限制——实测 inbox.list 单帧 2,321,996 字节
# （≈2.3MB）照发不误，客户端帧上限被打爆后同连接后续调用全灭，且服务端日志一片
# 安静（典型的"静默死连接"）。
# 守卫语义：超限**不下发**，改为回一条带方法名与真实体积的可读错误（code=-32007），
# 并落 ERROR 日志。0 表示关闭守卫（应急逃生开关）。
DEFAULT_OUTBOUND_MAX_BYTES = 4 * 1024 * 1024  # 4 MiB
OUTBOUND_MAX_BYTES = int(os.environ.get("SIYI_WS_MAX_OUTBOUND_BYTES", str(DEFAULT_OUTBOUND_MAX_BYTES)))


def outbound_oversize(payload: str) -> int:
    """返回 payload 的 UTF-8 字节数；未超限时返回 0。0 阈值表示守卫关闭。"""
    if OUTBOUND_MAX_BYTES <= 0:
        return 0
    size = len(payload.encode("utf-8"))
    return size if size > OUTBOUND_MAX_BYTES else 0


def _too_large_detail(method: str, size: int) -> dict[str, Any]:
    return {
        "code": ErrorCode.RESPONSE_TOO_LARGE,
        "message": (f"响应过大已被服务端拦截：method={method} bytes={size} limit={OUTBOUND_MAX_BYTES}（请缩小 limit/offset 分页参数后重试）"),
        "data": {
            "method": method,
            "bytes": size,
            "limit": OUTBOUND_MAX_BYTES,
            "hint": "inbox.list/notes.list 等列表方法请传 limit（默认 50，上限 200）",
        },
    }


def enforce_outbound_limit(payload: str, *, method: str, request_id: Any) -> str:
    """request/response 出站守卫：超限则把响应替换成 -32007 错误体 + ERROR 日志。"""
    size = outbound_oversize(payload)
    if not size:
        return payload
    logger.error(
        "WS_TX_BLOCKED method=%s bytes=%d limit=%d request_id=%s —— 响应过大已拦截，未下发",
        method,
        size,
        OUTBOUND_MAX_BYTES,
        request_id,
    )
    return json.dumps(
        _make_response(request_id, error=_too_large_detail(method, size)),
        ensure_ascii=False,
    )


def enforce_outbound_limit_stream(raw: str, *, method: str, request_id: Any, chunk_type: str) -> str:
    """流式 chunk 出站守卫。

    流通道的帧形状是 ``result: {"type": ..., "data": ...}``，客户端只认 result
    （B-1：流错误必须走 error chunk 通道，回 JSON-RPC error 会被静默丢弃）。
    因此超限时不改帧形状，而是把 data 换成可读报错并降级为 ``type="error"``。
    """
    size = outbound_oversize(raw)
    if not size or chunk_type == "error":
        return raw
    logger.error(
        "WS_TX_BLOCKED(stream) method=%s bytes=%d limit=%d request_id=%s —— chunk 过大已降级为 error 帧",
        method,
        size,
        OUTBOUND_MAX_BYTES,
        request_id,
    )
    detail = _too_large_detail(method, size)
    return json.dumps(
        {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "result": {"type": "error", "data": detail["message"]},
        },
        ensure_ascii=False,
    )


# V2.1 批次1#2：允许的来源白名单（精确 host+port 匹配，杜绝 startswith 前缀绕过）
# 仅放行 Electron 本地开发端口与 file://（生产打包）。无 Origin（非浏览器）走 token 校验。
ALLOWED_ORIGINS = frozenset(
    {
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://localhost:5190",
        "http://127.0.0.1:5190",
    }
)

# ── RQ-1：匿名 loopback 审计位（M1 后端A，2026-09-12）──
# 实测结论：本机 127.0.0.1 且 Origin 通过白名单的 WS 连接在 _handle_websocket 处
# 短路放行，完全不进 _verify_auth —— 即"无 token 的 loopback 调用可执行任意 RPC"。
# 本次**只加观测、不改放行语义**（个人自用项目，先观测后收紧）。审计出口复用
# core.audit_writer 既有 integration_audit 表（provider/event/status/payload_json），
# source=loopback_anon 作为 payload tag 落库，不新建表。
LOOPBACK_ANON_SOURCE_TAG = "loopback_anon"
LOOPBACK_ANON_AUDIT_PROVIDER = "backend.auth"
LOOPBACK_ANON_AUDIT_EVENT = "auth.loopback_anon"

_AUDIT_WRITER_UNRESOLVED = object()


@web.middleware
async def create_cors_middleware(request: web.Request, handler):
    """P1-4: 跨域反射 Origin（不置 Access-Control-Allow-Credentials）。

    供手机端跨设备配对 / 调用后端。仅反射请求自带 Origin，不共享 cookie
    （SameSite=Lax 亦不跨域发送），不引入 CSRF 新向量；手机端使用 Bearer /
    ``?token=`` 持有者凭证，无需 cookie。无 Origin（原生客户端）不加任何头。
    """
    resp = await handler(request)
    origin = request.headers.get("Origin", "")
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
    return resp


def _make_response(request_id: Any, result: Any = None, error: Any = None) -> dict:
    resp: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": request_id}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    return resp


class WebSocketServer:
    """aiohttp WebSocket JSON-RPC 服务器。"""

    def __init__(
        self,
        registry: MethodRegistry,
        event_bridge: EventBridge,
        *,
        host: str = "127.0.0.1",
        port: int = 18765,
        max_connections: int = 100,
        enable_heartbeat: bool = True,
        debug: bool = False,
        auth_token: str | None = None,
        auth_enabled: bool = False,
        auth_token_store: Any = None,
        relay: Any | None = None,
    ) -> None:
        self._registry = registry
        self._bridge = event_bridge
        self._host = host
        self._port = port
        self._debug = debug
        self._max_connections = max_connections
        self._enable_heartbeat = enable_heartbeat

        # 认证配置
        self._auth_enabled = auth_enabled or bool(auth_token)
        self._auth_token = auth_token or self._generate_auth_token()
        self._auth_sessions: dict[str, dict[str, Any]] = {}  # session_id -> {created, last_used}
        # V2.1 批次1#1：token 持久化存储（支持 rotate/revoke 时落盘）
        self._auth_token_store = auth_token_store
        # 对外 LLM 中继网关（可选注入；None 时 /v1 端点返回 404，不崩）
        self._relay = relay

        # S1 安全加固：/auth 端点速率限制，防止暴力破解 token
        self._auth_rate_limiter = RateLimiter(max_requests=10, window=60.0)
        # B-05：/pair action=request 无速率限制（对照 /auth 有 10/min）→ LAN 恶意
        # 客户端可高频 POST /pair 污染 _pairing_requests 内存 + pairing.pending 列表。
        # 独立 5/min/IP 桶（不与 /auth 共享，避免配对尝试与登录互相挤占）。
        self._pair_rate_limiter = RateLimiter(max_requests=5, window=60.0)

        # V2.1 批次1#2 #3：WS 消息按连接分桶限流，防止单连接打爆 RPC 面
        # P1 修复：滑动窗口(120/60s≈2条/s)把桌面端"启动瞬时并发初始化 RPC"误判为
        # flooding（violations 累积到 20 连断）。改为令牌桶，容忍突发（一次启动风暴
        # 可全吃下 capacity=300），稳态限速 40/s 仍能防滥用。均可用环境变量覆盖。
        self._ws_msg_limiter = TokenBucketLimiter(
            rate=float(os.environ.get("SIYI_WS_MSG_RATE_RATE", "40")),
            capacity=int(os.environ.get("SIYI_WS_MSG_RATE_CAPACITY", "300")),
        )
        # A-2：per-conn 限流连续违规计数（超限软失败时累计，达到阈值才断开）
        self._conn_rate_violations: dict[str, int] = {}
        # M2.1 R4 兼容·连接级最小识别：conn_id -> 客户端声明的 client_version
        # （握手 query 参数或首帧字段，任一命中即视为新客户端；从未声明 =
        # legacy_client，其 R4 调用回 -32010 显式错误而非 pending）。
        self._conn_client_version: dict[str, str] = {}
        # M2.1 G1：conn_id -> 是否为可信 UI 持有者（经 Origin+鉴权握手放行的连接）。
        # sandbox.approve 据此 + 请求方连接指纹，拒绝匿名/注入帧的同连接自我批准。
        self._conn_trusted_ui: dict[str, bool] = {}

        # 消息处理任务强引用集合（防止 asyncio.create_task 被 GC 回收）
        self._pending_msg_tasks: set[asyncio.Task] = set()

        # 连接管理器
        self._conn_manager = ConnectionManager(
            max_connections=max_connections,
            heartbeat_interval=30.0,
            idle_timeout=900.0,
        )

        # 创建 aiohttp 应用，添加错误处理中间件
        self._app = web.Application(
            client_max_size=20 * 1024 * 1024,  # 20MB for file/transfer payloads
            middlewares=[create_cors_middleware, create_error_middleware(debug=debug)],
        )
        self._app.router.add_get("/ws", self._handle_websocket)
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/stats", self._handle_stats)  # 新增统计端点
        self._app.router.add_get("/metrics", self._handle_metrics)  # #3: /metrics 需认证
        self._app.router.add_post("/rpc", self._handle_rpc_http)
        self._app.router.add_route("OPTIONS", "/rpc", self._handle_rpc_preflight)
        self._app.router.add_post("/auth", self._handle_auth)  # 认证端点
        self._app.router.add_get("/discover", self._handle_discover)  # 免鉴权：配对发现
        self._app.router.add_post("/pair", self._handle_pair)  # 免鉴权：配对请求/状态轮询
        self._app.router.add_route("OPTIONS", "/discover", self._handle_preflight_public)
        self._app.router.add_route("OPTIONS", "/pair", self._handle_preflight_public)
        # ── 对外 LLM 中继网关（OpenAI / Anthropic 兼容 HTTP 端点）──
        if self._relay is not None:
            self._app.router.add_post("/v1/chat/completions", self._handle_relay_openai)
            self._app.router.add_post("/v1/messages", self._handle_relay_anthropic)
        # T7（2026-08-30）：托管 desk-notes PWA 构建产物 web_dist（index.html /
        # assets/ / sw.js 等 369 文件）。此前浏览器/手机端无法经 HTTP 访问前端
        # （候选路径全 404，tests/test_web_dist_serving.py 登记）。目录缺失时
        # 优雅跳过（瘦打包 / 未同步 dist 场景不阻断启动）。
        self._web_dist_dir = Path(__file__).resolve().parents[1] / "web_dist"
        self._mount_web_dist(self._web_dist_dir)
        self._runner: web.AppRunner | None = None

        # P1-4: 配对请求状态（待桌面批准 → 下发主 token）。内存态，5 分钟过期。
        self._pairing_requests: dict[str, dict[str, Any]] = {}

        # RQ-1：匿名 loopback 审计位。计数器供 RPC/自检读取，writer 延迟解析
        # （仅在拿到显式数据目录时才落盘，避免单测污染真实 data/）。
        self._loopback_anon_count = 0
        self._audit_writer: Any = _AUDIT_WRITER_UNRESOLVED

    @property
    def loopback_anon_count(self) -> int:
        """本进程内被放行的匿名 loopback 调用次数（RQ-1 观测）。"""
        return self._loopback_anon_count

    @property
    def port(self) -> int:
        return self._port

    @property
    def connection_manager(self) -> ConnectionManager:
        """获取连接管理器。"""
        return self._conn_manager

    @property
    def auth_enabled(self) -> bool:
        """是否启用认证。"""
        return self._auth_enabled

    def _generate_auth_token(self) -> str:
        """生成随机认证 token。"""
        return secrets.token_urlsafe(32)

    async def _handle_relay_openai(self, request: web.Request) -> web.StreamResponse:
        """对外中继网关 OpenAI 端点：未注入 relay 时返回 404，不崩。"""
        if self._relay is None:
            return web.json_response({"error": {"message": "relay gateway not enabled"}}, status=404)
        return await self._relay.handle_openai_chat(request)

    async def _handle_relay_anthropic(self, request: web.Request) -> web.StreamResponse:
        """对外中继网关 Anthropic 端点：未注入 relay 时返回 404，不崩。"""
        if self._relay is None:
            return web.json_response({"error": {"message": "relay gateway not enabled"}}, status=404)
        return await self._relay.handle_anthropic_messages(request)

    def get_auth_token(self) -> str:
        """获取当前认证 token（用于本地客户端自动获取）。"""
        return self._auth_token

    def rotate_auth_token(self, *, length: int = 32) -> str:
        """V2.1 批次1#1：轮换主 token 并落盘。

        调用方（RPC auth.rotate）负责随后踢出活跃连接。
        返回新 token。
        """
        new_token = self._generate_auth_token()
        if self._auth_token_store is not None:
            # auth_token_store.rotate(data_dir, token) 语义：
            # 传入 {data_dir, token} 二元组由调用方构造；此处走统一持久化。
            try:
                import backend.auth_token_store as _ats

                _ats.persist(self._auth_token_store, new_token)
            except Exception as exc:
                logger.warning("auth token rotate persist failed: %s", exc)
        self._auth_token = new_token
        # 轮换后：清除所有会话（旧 session 基于旧 token 签发）
        self._auth_sessions.clear()
        logger.info("Auth token rotated, all sessions invalidated")
        return new_token

    def revoke_all(self, *, rotate: bool = False) -> None:
        """V2.1 批次1#1：吊销所有会话（可选同时轮换 token）。

        配合 ConnectionManager.close_all(1001) 可踢出全部活跃连接。
        """
        self._auth_sessions.clear()
        if rotate:
            self.rotate_auth_token()
        logger.info("All auth sessions revoked%s", " and token rotated" if rotate else "")

    def _verify_auth(self, request: web.Request) -> bool:
        """验证请求认证。

        认证方式：
        1. URL 参数: ?token=xxx
        2. Header: Authorization: Bearer xxx
        3. Cookie: siyi_auth=xxx

        S1 安全加固：对于非 localhost 来源，即使 auth_enabled=False 也强制要求认证。
        注意：使用 transport peername 而非 request.remote，避免被反向代理或
        X-Forwarded-For 头欺骗。

        本机（loopback）信任不在本方法内做：它属于 WS 连接层（_handle_websocket），
        且必须同时通过 Origin 白名单才放行——否则本机浏览器里的恶意网页（transport
        同为 loopback）会绕过认证（CSWSH）。HTTP 端点一律走严格的 token/session 校验。
        """
        if not self._auth_enabled:
            # S1 修复：未启用认证时，仅允许真实 localhost 连接
            peer = request.remote or ""
            transport_peer = request.transport.get_extra_info("peername") if request.transport else None
            transport_ip = transport_peer[0] if isinstance(transport_peer, (tuple, list)) and transport_peer else ""
            localhost_addrs = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}
            # P1-16: 移除空字符串 —— peer/transport_ip 为空意味着来源未知
            # （如反向代理未设置 remote），不应被误判为 localhost 放行。
            if peer in localhost_addrs and transport_ip in localhost_addrs:
                return True
            # 非 localhost 连接但未启用认证 → 拒绝
            logger.warning(
                "Non-localhost connection rejected (auth not enabled): remote=%s peer=%s",
                peer,
                transport_ip,
            )
            return False

        # 检查 URL 参数（?token= 已从 V3-01 迁移期门控转为正式通道，2026-08-25 决策）：
        # 浏览器/WebView 的 WS 无法带 Authorization header 或 cookie（跨域 cookie 不发送），
        # ?token= 是手机端（远端）与 undici relay（本机）连 WS 的唯一可行通道。
        # - 有效主 token → 放行（持有者凭证与 Bearer 等价；远端手机必须已通过配对取得）。
        #   落日志风险在此接受——个人 LAN 无端到端 TLS 时无法规避，与 Bearer 同级。
        # - loopback 例外：本机 undici WS 无法携带 cookie，允许以 session_id 走 URL
        #   （按 session 校验，非主 token），作为合规兜底。
        # 历史：SIYI_WS_ALLOW_URL_TOKEN 门控（V3-01）已删除——其语义被"有效主 token
        # 无条件放行"分支完全覆盖，保留只会制造"门控生效"的错觉。
        url_token = request.query.get("token")
        if url_token:
            if self._verify_token(url_token):
                return True
            if self._is_loopback(request) and self._verify_session(url_token):
                return True

        # 检查 Authorization header
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if self._verify_token(token):
                return True

        # 检查 Cookie（P1-6: cookie 现在存储 session_id 而非主 token）
        cookie_session = request.cookies.get("siyi_auth")
        if cookie_session and self._verify_session(cookie_session):
            return True

        return False

    def _is_loopback(self, request: web.Request) -> bool:
        """基于 transport peername 判断来源是否为本机 loopback（不可被 XFF/头伪造）。

        仅当 request.remote 与 transport peername 均为 loopback 地址时才认作本机，
        # P1-16: 任一为空（来源未知，如经反向代理未透传）一律判非本机。
        """
        localhost_addrs = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}
        peer = request.remote or ""
        transport_peer = request.transport.get_extra_info("peername") if request.transport else None
        transport_ip = transport_peer[0] if isinstance(transport_peer, (tuple, list)) and transport_peer else ""
        return peer in localhost_addrs and transport_ip in localhost_addrs

    def _verify_token(self, token: str) -> bool:
        """验证 token 有效性。"""
        # 使用常量时间比较防止时序攻击
        return hmac.compare_digest(token, self._auth_token)

    def _has_explicit_token(self, request: web.Request) -> bool:
        """P1-4: 请求是否自带有效主 token（Bearer header 或 ?token=）。

        持有者凭证即 CSRF 防御——带有效 token 的请求无需再受 Origin 白名单约束
        （恶意站点无法读取该 token 来伪造请求）。用于放行手机端跨域 WS / RPC。
        """
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return self._verify_token(auth_header[7:])
        url_token = request.query.get("token")
        if url_token:
            return self._verify_token(url_token)
        return False

    def _verify_session(self, session_id: str) -> bool:
        """P1-6: 验证 session_id 有效性并更新 last_used。

        session_id 由 /auth 端点签发，存储在 cookie 中。与主 token 不同，
        session_id 泄露后只能用于 WebSocket/RPC 访问，无法获取主 token。
        """
        session = self._auth_sessions.get(session_id)
        if session is None:
            return False
        # 检查过期（24 小时）
        if time.time() - session["created"] > 86400:
            del self._auth_sessions[session_id]
            return False
        session["last_used"] = time.time()
        return True

    def _presents_valid_credential(self, request: web.Request) -> bool:
        """RQ-1 审计位：请求是否自带**可用**凭证（有效主 token 或有效 session）。

        仅用于给"匿名 loopback 放行"打标记，绝不参与放行决定（放行语义维持现状）。
        """
        try:
            if self._has_explicit_token(request):
                return True
            session_id = request.cookies.get("siyi_auth") or request.query.get("session_id") or request.query.get("token")
            if session_id and self._verify_session(str(session_id)):
                return True
        except Exception:  # noqa: BLE001 - 审计位不得影响主流程
            logger.debug("loopback_anon credential probe failed", exc_info=True)
        return False

    def _resolve_audit_writer(self) -> Any | None:
        """延迟解析审计写入器（RQ-1 出口）。

        只有拿到**显式数据目录**（auth_token_store 或 SIYI_DATA_DIR）时才建 writer：
        否则退回 None（仅日志 + 计数），避免单测裸构造时污染/迁移真实 data/ 目录。
        """
        if self._audit_writer is not _AUDIT_WRITER_UNRESOLVED:
            return self._audit_writer
        data_dir = str(self._auth_token_store or os.environ.get("SIYI_DATA_DIR", "") or "").strip()
        if not data_dir:
            self._audit_writer = None
            return None
        try:
            from core.audit_writer import get_audit_writer

            self._audit_writer = get_audit_writer(data_dir=data_dir)
        except Exception:  # noqa: BLE001
            logger.debug("audit writer unavailable (loopback_anon audit degrades to log-only)", exc_info=True)
            self._audit_writer = None
        return self._audit_writer

    def _mark_loopback_anon(self, request: web.Request, *, channel: str) -> None:
        """给一次"匿名 loopback 被放行执行"打 ``source=loopback_anon`` 审计位。

        双出口：① 既有审计通道 integration_audit（payload 带 source tag，不新建表）；
        ② logger.warning 实时告警。任何审计失败都不影响已放行的请求。
        """
        self._loopback_anon_count += 1
        peer = request.remote or ""
        path = ""
        try:
            path = request.path
        except Exception:  # noqa: BLE001
            path = ""
        payload: dict[str, Any] = {
            "source": LOOPBACK_ANON_SOURCE_TAG,
            "channel": channel,
            "peer": peer,
            "path": path,
            "origin": request.headers.get("Origin", ""),
            "user_agent": request.headers.get("User-Agent", "")[:120],
            "auth_enabled": self._auth_enabled,
        }
        logger.warning(
            "RQ-1 loopback_anon: 匿名本机 %s 调用无凭证仍被放行执行（peer=%s path=%s count=%d）",
            channel,
            peer,
            path,
            self._loopback_anon_count,
        )
        writer = self._resolve_audit_writer()
        if writer is None:
            return
        try:
            writer.submit_integration(
                LOOPBACK_ANON_AUDIT_PROVIDER,
                LOOPBACK_ANON_AUDIT_EVENT,
                LOOPBACK_ANON_SOURCE_TAG,
                payload,
            )
        except Exception:  # noqa: BLE001
            logger.debug("loopback_anon audit submit failed", exc_info=True)

    def _cleanup_expired_sessions(self) -> None:
        """P1-8: 清理过期的认证 session，避免 _auth_sessions 无限增长。"""
        now = time.time()
        expired = [sid for sid, s in self._auth_sessions.items() if now - s["created"] > 86400]
        for sid in expired:
            del self._auth_sessions[sid]
        if expired:
            logger.debug("Cleaned up %d expired auth sessions", len(expired))

    async def _handle_auth(self, request: web.Request) -> web.Response:
        """处理认证请求。

        POST /auth
        Body: {"token": "xxx"}

        Returns:
            认证成功返回 session_id 和 cookie
        """
        # S1 安全加固：按客户端 IP 限流，防止暴力破解 token
        peer = request.transport.get_extra_info("peername") if request.transport else None
        client_ip = peer[0] if isinstance(peer, (tuple, list)) and peer else (request.remote or "unknown")
        if not await self._auth_rate_limiter.allow(client_ip):
            logger.warning("Auth rate limit exceeded for client: %s", client_ip)
            return web.json_response(
                {"error": "Too many auth attempts, please try again later"},
                status=429,
            )

        try:
            body = await request.json()
        except Exception as exc:
            logger.debug("Invalid JSON in auth request: %s", exc)
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400,
            )

        token = body.get("token", "")
        if not self._verify_token(token):
            # 一律拒绝无效 token（含本机）：loopback 免 token 签发 session 会让
            # 本机浏览器里的恶意网页经 /auth 拿到合法 session 后连 WS 绕过认证
            # （CSWSH）。本机 Electron 走 WS loopback 信任，无需 session 引导。
            logger.warning("Auth rejected (invalid token) from %s", request.remote)
            return web.json_response(
                {"error": "Invalid token"},
                status=401,
            )

        # 创建 session
        session_id = secrets.token_urlsafe(16)
        # P1-8: 创建新 session 时顺便清理过期 session，避免 _auth_sessions 无限增长
        self._cleanup_expired_sessions()
        self._auth_sessions[session_id] = {
            "created": time.time(),
            "last_used": time.time(),
        }

        # 设置 cookie 并返回
        response = web.json_response(
            {
                "success": True,
                "session_id": session_id,
            }
        )
        response.set_cookie(
            "siyi_auth",
            session_id,  # P1-6: 使用 session_id 而非主 token（避免主 token 泄露到 cookie）
            max_age=86400,  # 24 小时
            httponly=True,
            samesite="Lax",
            # V2.1 批次1#2: 无 TLS → 不设 Secure（浏览器明文 HTTP 下 Secure cookie 不发送，
            # 会静默破坏手机/本机认证）。真实 TLS 后由部署层/配置显式开启。
            secure=False,
        )
        return response

    # ── P1-4: 配对发现 / 配对握手（均免鉴权，供未配对手机调用）──

    async def _handle_discover(self, request: web.Request) -> web.Response:
        """免鉴权的配对发现端点：返回桌面标识与是否需要 token。

        手机端输入 IP 后调用，用于确认对端是思忆集桌面端且需要配对。
        """
        return web.json_response(
            {
                "name": os.environ.get("SIYI_DEVICE_NAME", "思忆集桌面端"),
                "requires_token": True,
                "paired": False,
                "version": "1.0.0",
            }
        )

    def _cleanup_expired_pairing(self) -> None:
        """清理超过 5 分钟未批准的配对请求。"""
        now = time.time()
        expired = [rid for rid, e in self._pairing_requests.items() if now - e["created"] > 300]
        for rid in expired:
            del self._pairing_requests[rid]
        if expired:
            logger.debug("Cleaned up %d expired pairing requests", len(expired))

    async def _handle_pair(self, request: web.Request) -> web.Response:
        """免鉴权的配对端点（未配对手机调用）。

        action=request:  创建待批请求，返回 request_id（桌面需批准）
        action=status:   轮询状态；approved 时一并返回主 token
        """
        try:
            body = await request.json()
        except Exception as exc:
            logger.debug("Invalid JSON in pair request: %s", exc)
            return web.json_response({"error": "Invalid JSON"}, status=400)

        action = body.get("action", "")
        if action == "request":
            client_ip = request.remote or "unknown"
            # B-05：配对请求限流（5/min/IP）。无限流时恶意客户端可高频 POST /pair
            # 制造垃圾配对条目（_pairing_requests 内存增长 + pairing.pending 污染）。
            if not await self._pair_rate_limiter.allow(client_ip):
                logger.warning("Pairing rate limit exceeded from %s", client_ip)
                return web.json_response({"error": "rate limited"}, status=429)
            device_name = str(body.get("device_name", "手机"))[:64]
            device_id = str(body.get("device_id", ""))[:128]
            request_id = secrets.token_urlsafe(12)
            self._cleanup_expired_pairing()
            self._pairing_requests[request_id] = {
                "device_name": device_name,
                "device_id": device_id,
                "created": time.time(),
                "state": "pending",
                "token": None,
            }
            logger.info("Pairing request %s from %s (%s)", request_id, request.remote, device_name)
            return web.json_response({"request_id": request_id, "expires_in": 300})

        if action == "status":
            request_id = str(body.get("request_id", ""))
            entry = self._pairing_requests.get(request_id)
            if not entry:
                return web.json_response({"state": "unknown"}, status=404)
            if entry["state"] == "approved":
                return web.json_response({"state": "approved", "token": entry["token"]})
            return web.json_response({"state": entry["state"]})

        return web.json_response({"error": "unknown action"}, status=400)

    async def _handle_health(self, request: web.Request) -> web.Response:
        """健康检查端点，返回详细系统指标。"""
        # ★ 安全修复：要求认证（localhost 自动放行，非 localhost 需 token）
        if not self._verify_auth(request):
            return web.json_response(
                {"status": "unhealthy", "error": "Unauthorized"},
                status=401,
            )
        try:
            checker = get_health_checker()
            # B5 修复：使用公开 API 替代私有属性访问
            stats = self._conn_manager.get_stats()
            checker.set_active_connections(stats["active"])

            report = await checker.get_health_report()

            # 根据状态返回不同 HTTP 状态码
            status_code = 200 if report["status"] == "healthy" else (503 if report["status"] == "unhealthy" else 200)

            return web.json_response(
                report,
                status=status_code,
            )
        except Exception:
            logger.exception("Health check failed")
            return web.json_response(
                {"status": "unhealthy", "error": "Internal error"},
                status=503,
            )

    async def _handle_stats(self, request: web.Request) -> web.Response:
        """获取服务器统计信息。"""
        # ★ 安全修复：要求认证（localhost 自动放行，非 localhost 需 token）
        if not self._verify_auth(request):
            return web.json_response(
                {"error": "Unauthorized"},
                status=401,
            )
        stats = self._conn_manager.get_stats()

        # 添加运行时间
        checker = get_health_checker()
        # B5 修复：使用公开 API 替代私有属性访问
        system_metrics = await checker.get_system_metrics()
        stats["uptime_seconds"] = round(system_metrics.uptime_seconds, 2)
        stats["version"] = "1.0.0"  # 可以从配置读取

        # 添加性能指标
        metrics = checker.get_performance_metrics()
        stats["performance"] = {
            "avg_response_time_ms": round(metrics.avg_response_time_ms, 2),
            "p95_response_time_ms": round(metrics.p95_response_time_ms, 2),
            "p99_response_time_ms": round(metrics.p99_response_time_ms, 2),
            "error_rate": round(
                metrics.error_count / max(1, metrics.request_count) * 100,
                2,
            ),
        }

        return web.json_response(
            stats,
        )

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        """V2.1 批次1#3: /metrics 端点需认证（暴露内存/请求量等运行态指标）。"""
        if not self._verify_auth(request):
            return web.json_response(
                {"error": "Unauthorized"},
                status=401,
            )
        return await metrics_handler(request)

    async def _handle_rpc_http(self, request: web.Request) -> web.Response:
        """HTTP JSON-RPC endpoint for mobile clients that can't use WebSocket.

        Accepts POST with JSON body: {"method": "...", "params": {...}}
        Returns JSON-RPC response: {"jsonrpc": "2.0", "id": 1, "result": ...}
        """
        # ★ V2.1 批次1#2：校验 Origin（浏览器来源必须在白名单内）。持有效主 token
        # 的请求跳过 Origin 约束（CSRF 由 token 承担），放行手机端跨域 /rpc。
        if not self._has_explicit_token(request) and not self._verify_origin(request):
            return web.json_response(
                {"jsonrpc": JSONRPC_VERSION, "id": None, "error": {"code": ErrorCode.UNAUTHORIZED, "message": "Origin not allowed"}},
                status=403,
            )

        # ★ 安全检查：验证认证
        if not self._verify_auth(request):
            return web.json_response(
                {"jsonrpc": JSONRPC_VERSION, "id": None, "error": {"code": ErrorCode.UNAUTHORIZED, "message": "Unauthorized"}},
                status=401,
            )

        # RQ-1：走到这里说明 RPC 即将执行。若来源是本机 loopback 且不持任何可用凭证
        # （auth_enabled=False 时 _verify_auth 的 localhost 分支放行）→ 打审计位。
        # 只观测，不改判定结果。
        if self._is_loopback(request) and not self._presents_valid_credential(request):
            self._mark_loopback_anon(request, channel="http_rpc")

        try:
            body = await request.json()
        except Exception as exc:
            logger.debug("Invalid JSON in RPC request: %s", exc)
            return web.json_response(
                {"jsonrpc": JSONRPC_VERSION, "id": None, "error": {"code": -32700, "message": "Parse error"}},
                status=400,
            )

        method = body.get("method", "")
        params = body.get("params") or {}
        request_id = body.get("id", 1)

        if not method:
            return web.json_response(
                {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": {"code": -32600, "message": "Invalid Request: missing method"}},
                status=400,
            )

        # M2.1 F1：HTTP /rpc 与 WS 同源判定 legacy_client——未在请求体声明
        # client_version 即视为旧客户端，其 R4 调用回显式 -32010 而非静默 pending。
        legacy_client = not str(body.get("client_version", "") or "").strip()

        try:
            _metrics = get_metrics_collector()
            _start_ts = time.time()
            # A-1 修复（HTTP 侧）：按来源 IP 分桶——此前 HTTP /rpc 与 WS 全部落
            # "default" 共享桶，手机端 HTTP 调用会挤占桌面 WS 配额（反之亦然）。
            result = await self._registry.dispatch(method, params, client_id=request.remote or "http", legacy_client=legacy_client)
            _metrics.record_request(method, "ok", (time.time() - _start_ts) * 1000.0)
            return web.json_response(
                {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result},
                status=200,
            )
        except MethodNotFound:
            get_metrics_collector().record_request(method, "not_found", (time.time() - _start_ts) * 1000.0)
            return web.json_response(
                {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}},
                status=404,
            )
        except ClientUpgradeRequiredError as exc:
            # M2.1 F1：HTTP /rpc 通道旧客户端 R4 → 显式 -32010，不回 pending 信封。
            get_metrics_collector().record_request(method, "client_upgrade_required", (time.time() - _start_ts) * 1000.0)
            return web.json_response(
                make_jsonrpc_error(request_id, ErrorCode.CLIENT_UPGRADE_REQUIRED, exc.message, exc.details),
                status=400,
            )
        except InvalidParamsError as exc:
            get_metrics_collector().record_request(method, "invalid_params", (time.time() - _start_ts) * 1000.0)
            return web.json_response(
                make_jsonrpc_error(request_id, ErrorCode.INVALID_PARAMS, exc.message, exc.details),
                status=400,
            )
        except RPCTimeoutError as exc:
            get_metrics_collector().record_request(method, "timeout", (time.time() - _start_ts) * 1000.0)
            return web.json_response(
                make_jsonrpc_error(request_id, ErrorCode.TIMEOUT, exc.message, exc.details),
                status=504,
            )
        except RateLimitedError as exc:
            # P0-2026-08-17: 限流应返回 RATE_LIMITED(-32003) + HTTP 429
            get_metrics_collector().record_request(method, "rate_limited", (time.time() - _start_ts) * 1000.0)
            return web.json_response(
                make_jsonrpc_error(request_id, ErrorCode.RATE_LIMITED, exc.message, exc.details),
                status=429,
            )
        except Exception as exc:
            logger.exception("RPC method %s failed", method)
            get_metrics_collector().record_request(method, "error", (time.time() - _start_ts) * 1000.0)
            # P2-4: 不回传 str(exc) 防止敏感信息泄漏
            error_obj: dict[str, Any] = {
                "code": -32603,
                "message": "Internal error",
            }
            if self._debug:
                error_obj["data"] = {
                    "exception": type(exc).__name__,
                    "detail": (str(exc) or "")[:200],
                }
            return web.json_response(
                {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error_obj},
                status=500,
            )

    async def _handle_rpc_preflight(self, request: web.Request) -> web.Response:
        # P1-4: 反射请求 Origin（不设 credentials——手机用 Bearer 而非 cookie，
        # 不引入 CSRF）。无 Origin 时回退 * 以兼容 null/缺失 Origin 的 WebView。
        origin = request.headers.get("Origin", "")
        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin": origin or "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            },
        )

    async def _handle_preflight_public(self, request: web.Request) -> web.Response:
        """P1-4: 配对/发现端点的 CORS 预检（免鉴权端点，无需 credentials）。"""
        origin = request.headers.get("Origin", "")
        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin": origin or "*",
                "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            },
        )

    # ── T7: web_dist 静态托管（免鉴权：静态前端产物无敏感数据，与 /discover 同级）──

    def _mount_web_dist(self, web_dist_dir: Path) -> None:
        """T7: 挂载 web_dist 静态托管（浏览器/手机端可经 HTTP 访问前端入口）。

        - 免鉴权：仅暴露前端构建产物（HTML/JS/CSS/字体/图标/模型资源），无敏感
          数据；"未认证客户端可达前端入口" 与 /discover（配对发现）语义一致。
        - 显式 index 入口先注册：aiohttp add_static 对目录请求返回 403（无 index
          支持），故 "/", "/app", "/app/", "/web_dist/" 与 "/index.html" 由
          FileResponse 显式回 index.html；aiohttp 按注册顺序匹配，精确路径优先。
        - 前缀静态路由承载 assets/ / sw.js / registerSW.js 等产物文件（index.html
          内资源为 ./assets/... 相对引用，经 /app/ 入口加载时解析到 /app/assets/...）；
          根挂载兜底绝对路径引用（/manifest.webmanifest）。已注册 API 路由
          （/ws /health /stats /metrics /rpc /auth /discover /pair）先于静态资源
          命中，不受影响。
        - 守卫：web_dist / index.html 缺失或挂载异常时仅告警跳过，不炸启动。
        """
        index_html = web_dist_dir / "index.html"
        if not web_dist_dir.is_dir() or not index_html.is_file():
            logger.warning(
                "web_dist not found at %s; static frontend hosting disabled",
                web_dist_dir,
            )
            return
        try:
            router = self._app.router
            # 1) 精确 index 入口（先注册，优先于前缀静态路由）
            router.add_get("/", self._handle_web_index)
            router.add_get("/index.html", self._handle_web_index)
            router.add_get("/app", self._handle_web_index)
            router.add_get("/app/", self._handle_web_index)
            router.add_get("/web_dist/", self._handle_web_index)
            # 2) 前缀静态路由：产物文件
            router.add_static("/app", str(web_dist_dir))
            router.add_static("/web_dist", str(web_dist_dir))
            # 3) 根挂载兜底：/assets/...（"/" 入口的相对引用）与 /manifest.webmanifest
            #    （index.html 内的绝对引用）
            router.add_static("/", str(web_dist_dir))
        except Exception as exc:  # noqa: BLE001
            logger.warning("web_dist static hosting mount failed: %s", exc)
            return
        logger.info(
            "web_dist static hosting enabled: %s (routes: /, /app, /web_dist)",
            web_dist_dir,
        )

    async def _handle_web_index(self, request: web.Request) -> web.StreamResponse:
        """T7: 返回 web_dist/index.html（前端入口，免鉴权）。"""
        index_html = self._web_dist_dir / "index.html"
        if not index_html.is_file():
            return web.json_response({"error": "frontend bundle missing"}, status=404)
        return web.FileResponse(index_html)

    def _is_localhost_origin(self, origin: str) -> bool:
        """V2.1 批次1#2: 精确 Origin 白名单匹配（urlparse host+port，杜绝 startswith 前缀绕过）。

        例如 ``http://localhost.attacker.com`` 不再匹配 ``http://localhost``。
        放行：Electron 开发端口（5175/5190）与 ``file://``（生产打包）。
        """
        if not origin:
            return False
        # file:// origin（Electron 生产）
        if origin.startswith("file://"):
            return True
        try:
            parsed = urlparse(origin)
        except ValueError:
            return False
        # 规范化 host+port 进行精确匹配
        host = (parsed.hostname or "").lower()
        port = parsed.port
        candidate = f"{parsed.scheme}://{host}"
        if port:
            candidate += f":{port}"
        return candidate in ALLOWED_ORIGINS

    def _verify_origin(self, request: web.Request) -> bool:
        """V2.1 批次1#2: 校验请求 Origin。

        - 无 Origin（非浏览器/原生客户端）→ 放行，交由 token 认证把关
        - transport 为 loopback（本机）→ 仅当 Origin host 为 loopback
          （127.0.0.1/localhost/::1）或 ``file://``（Electron 生产打包）时放行。
          注意：不能仅因 transport 是 loopback 就放行任意 Origin——本机浏览器里的
          恶意网页 transport 同为 loopback，其 Origin 是恶意站点，必须拒绝（防 CSWSH）。
        - 有 Origin（浏览器/手机 WebView）→ 必须在白名单内，否则拒绝（防 CSWSH / CSRF）
        """
        origin = request.headers.get("Origin", "")
        if not origin:
            return True
        if self._is_loopback(request):
            if origin.startswith("file://"):
                return True
            try:
                origin_host = (urlparse(origin).hostname or "").lower()
            except ValueError:
                origin_host = ""
            if origin_host in {"127.0.0.1", "localhost", "::1"}:
                return True
        allowed = self._is_localhost_origin(origin)
        if not allowed:
            logger.warning(
                "Rejected request with disallowed Origin: %s (remote=%s)",
                origin,
                request.remote,
            )
        return allowed

    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        # ★ V2.1 批次1#2：先校验 Origin（浏览器来源必须在白名单内，杜绝 CSWSH）。
        # 但持有效主 token（Bearer / ?token=）的请求已自带持有者凭证，CSRF 防御由
        # token 承担，无需再受 Origin 约束——放行手机端跨域 WS。
        if not self._has_explicit_token(request) and not self._verify_origin(request):
            logger.warning(
                "Unauthorized WebSocket connection (bad Origin) from %s",
                request.remote,
            )
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.close(code=1008, message=b"Origin not allowed")
            return ws

        # ★ 安全检查：验证认证。
        # 本机信任：transport 为 loopback（Electron 渲染进程 / Node 主进程 relay）且
        # 已通过上方 Origin 校验（loopback host / file:// / 白名单）→ 直接放行，无需
        # token——这是桌面端渲染进程能连上自身后端的前提。远端（手机/Tailscale）必须
        # 经 _verify_auth 持有效 token/session；本机恶意网页已被 _verify_origin 拦截。
        # RQ-1：短路放行分支只做审计位（loopback_anon），放行语义逐字保持不变。
        loopback_anon = False
        if self._is_loopback(request):
            loopback_anon = not self._presents_valid_credential(request)
        elif not self._verify_auth(request):
            logger.warning("Unauthorized WebSocket connection attempt from %s", request.remote)
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.close(code=1008, message=b"Unauthorized")
            return ws

        max_msg = int(os.environ.get("SIYI_WS_MAX_MSG_SIZE", str(16 * 1024 * 1024)))
        ws = web.WebSocketResponse(max_msg_size=max_msg)
        await ws.prepare(request)

        peer = request.remote

        # 使用连接管理器添加连接
        try:
            conn_id = await self._conn_manager.add_connection(ws, peer)
        except ConnectionLimitExceededError as e:
            logger.warning("Connection rejected from %s: %s", peer, e)
            await ws.close(code=1013, message=b"Connection limit exceeded")
            return ws

        # M2.1：握手 query 参数声明 client_version → 记入连接标记
        _cv = request.query.get("client_version", "")
        if _cv:
            self._conn_client_version[conn_id] = str(_cv)
        # M2.1 G1：能走到此处 = 已过 Origin 校验（远端另过 _verify_auth），
        # 视为可信 UI 持有者连接；其同连接批准代表真实用户点击，予以放行。
        self._conn_trusted_ui[conn_id] = True

        # 同步到事件桥接（保持向后兼容）
        self._bridge.add_socket(ws)
        logger.info("WebSocket client connected: %s (conn_id=%s)", peer, conn_id)
        # RQ-1：匿名 loopback 连接成功建立（后续 RPC 可无凭证执行）→ 打审计位 + 告警
        if loopback_anon:
            self._mark_loopback_anon(request, channel="ws")
        # S14: 记录活跃连接数（gauge）
        get_metrics_collector().inc_connections()

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    # P1-12: 不记录消息内容（可能含敏感数据），仅记录长度
                    logger.debug("WS_RX from %s: len=%d", peer, len(msg.data))
                    # ★ V2.1 批次1#2 #3：按连接分桶限流（拒绝滥用）
                    # A-2 修复：超限不再直接 close(1008)——那会把限流误伤与
                    # Origin/鉴权拒绝混在同一 close code 上，客户端无从分辨，
                    # 且杀连接会触发快速重连惊群。改为回 JSON-RPC -32003
                    # 错误响应；仅对持续洪泛（连续违规）才升级断开。
                    if not await self._ws_msg_limiter.allow(conn_id):
                        self._conn_rate_violations[conn_id] = self._conn_rate_violations.get(conn_id, 0) + 1
                        violations = self._conn_rate_violations[conn_id]
                        logger.warning(
                            "WS rate limit exceeded for conn=%s peer=%s (violations=%d)",
                            conn_id,
                            peer,
                            violations,
                        )
                        if violations >= 20:
                            logger.warning("conn=%s persistent flooding; closing", conn_id)
                            await ws.close(code=1008, message=b"Rate limit exceeded")
                            break
                        request_id = self._extract_request_id(msg.data)
                        try:
                            await ws.send_str(
                                json.dumps(
                                    _make_response(
                                        request_id,
                                        error={
                                            "code": -32003,
                                            "message": "Rate limit exceeded",
                                            "data": {"retry_after": 60},
                                        },
                                    ),
                                    ensure_ascii=False,
                                )
                            )
                        except Exception:
                            pass
                        continue
                    else:
                        self._conn_rate_violations.pop(conn_id, None)
                    # ★ P0 修复：并发处理消息，避免流式请求（chat.send）阻塞后续 RPC 调用
                    # 之前 await self._on_message(...) 会阻塞整个消息循环，
                    # 导致流式 AI 响应期间所有其他 RPC（如 api_keys.list）超时。
                    # ★ 安全修复：保存 Task 强引用，防止被 GC 回收导致消息静默丢失
                    task = asyncio.create_task(self._on_message_safe(ws, msg.data, peer, conn_id=conn_id))
                    self._pending_msg_tasks.add(task)
                    task.add_done_callback(self._pending_msg_tasks.discard)
                elif msg.type == web.WSMsgType.ERROR:
                    logger.warning("WebSocket error from %s: %s", peer, ws.exception())
                elif msg.type == web.WSMsgType.PONG:
                    # 更新心跳时间
                    info = self._conn_manager.find_by_ws(ws)
                    if info:
                        # P2-10: 记录收到 pong 的时间戳（此前存储消息数据字符串，
                        # 但 _heartbeat_loop 从未使用它判断 stale）
                        info.last_pong = time.time()
        except asyncio.CancelledError:
            logger.info("WebSocket handler cancelled for %s", peer)
        except Exception:
            logger.exception("WebSocket handler error for %s", peer)
        finally:
            # ★ S1 修复：统一清理连接资源，避免部分失败导致连接泄漏
            await self._cleanup_connection(ws, peer, conn_id)

        # ★ 修复 "Missing return statement on request handler"：
        # aiohttp 要求 WebSocket handler 返回 WebSocketResponse 对象，
        # 否则会在连接结束后记录 ERROR 级别日志
        return ws

    async def _cleanup_connection(self, ws: web.WebSocketResponse, peer: str | None, conn_id: str) -> None:
        """统一清理连接资源。

        修复 S1: 确保 bridge 和 conn_manager 的清理操作都执行，
        即使其中一个失败也不会导致连接泄漏。
        """
        errors: list[str] = []

        # 1. 从事件桥接移除
        try:
            self._bridge.remove_socket(ws)
        except Exception as exc:
            errors.append(f"bridge.remove_socket: {exc}")

        # 2. 从连接管理器移除
        try:
            await self._conn_manager.remove_by_ws(ws)
        except Exception as exc:
            errors.append(f"conn_manager.remove_by_ws: {exc}")

        # 3. S14: 递减活跃连接数（gauge）
        try:
            get_metrics_collector().dec_connections()
        except Exception as exc:
            errors.append(f"metrics.dec_connections: {exc}")

        # 4. 记录清理结果
        self._conn_rate_violations.pop(conn_id, None)
        self._conn_client_version.pop(conn_id, None)  # M2.1：连接标记随连接销毁回收
        self._conn_trusted_ui.pop(conn_id, None)  # M2.1 G1
        if errors:
            logger.warning(
                "Connection cleanup had errors (peer=%s, conn_id=%s): %s",
                peer,
                conn_id,
                errors,
            )
        else:
            logger.info("WebSocket client disconnected: %s (conn_id=%s)", peer, conn_id)

        return ws

    @staticmethod
    def _extract_request_id(raw: str) -> Any:
        """从 JSON-RPC 请求中提取 id 字段（用于错误响应）。"""
        try:
            data = json.loads(raw)
            return data.get("id")
        except Exception:
            return None

    async def _on_message_safe(self, ws: web.WebSocketResponse, raw: str, peer: str, conn_id: str | None = None) -> None:
        """并发安全的消息处理包装器，确保单条消息异常不会断开连接。"""
        try:
            await self._on_message(ws, raw, conn_id=conn_id)
        except asyncio.CancelledError:
            logger.debug("WebSocket message task cancelled for %s", peer)
        except Exception as exc:
            logger.exception("Unexpected error handling message from %s: %s", peer, exc)
            if not ws.closed:
                try:
                    request_id = self._extract_request_id(raw)
                    # P2-4: debug 模式仅回传异常类名 + 截断消息，不含 traceback / 路径。
                    error_obj: dict[str, Any] = {
                        "code": -32603,
                        "message": "Internal error",
                    }
                    if self._debug:
                        error_obj["data"] = {
                            "exception": type(exc).__name__,
                            "detail": (str(exc) or "")[:200],
                        }
                    await ws.send_str(
                        json.dumps(
                            _make_response(request_id, error=error_obj),
                            ensure_ascii=False,
                        )
                    )
                except Exception:
                    pass

    async def _on_message(self, ws: web.WebSocketResponse, raw: str, conn_id: str | None = None) -> None:
        """处理单条 JSON-RPC 消息（设置连接持有者上下文后委派）。

        M2.1 G1：把当前连接指纹 + 可信 UI 标记写入 contextvar，使 R4 入队记录
        requester_conn、sandbox.approve 得以识别"同连接自我批准"并拒绝。
        """
        _trusted = bool(self._conn_trusted_ui.get(conn_id, False)) if conn_id else False
        tokens = set_request_connection(conn_id, _trusted)
        try:
            await self._dispatch_message(ws, raw, conn_id=conn_id)
        finally:
            reset_request_connection(tokens)

    async def _dispatch_message(self, ws: web.WebSocketResponse, raw: str, conn_id: str | None = None) -> None:
        """处理单条 JSON-RPC 消息。"""
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("invalid JSON from client: %s", exc)
            await ws.send_str(
                json.dumps(
                    _make_response(None, error={"code": -32700, "message": "Parse error"}),
                    ensure_ascii=False,
                )
            )
            return

        request_id = message.get("id")
        method = message.get("method", "")
        params = message.get("params") or {}

        # M2.1 R4 兼容·首帧（或任意帧）声明 client_version → 升级连接标记；
        # 从未声明的连接视为 legacy_client（握手 query 参数已在建连时记录）。
        _declared = message.get("client_version")
        if conn_id is not None and _declared:
            self._conn_client_version[conn_id] = str(_declared)
        legacy_client = conn_id is not None and not self._conn_client_version.get(conn_id)

        if not method:
            await ws.send_str(
                json.dumps(
                    _make_response(request_id, error={"code": -32600, "message": "Invalid Request: missing method"}),
                )
            )
            return

        # ── ping 直答（不进 registry dispatch）──
        # B-3/N3 修复：心跳若走 dispatch 会烧共享限流桶配额，一旦被限流
        # 客户端即误判死连并强杀健康连接，形成"限流→误杀→重连惊群→再限流"
        # 的自激回路。ping 是连接保活语义，与业务 RPC 隔离。
        if method == "ping" and not message.get("stream", False):
            await ws.send_str(
                json.dumps(
                    _make_response(request_id, result="pong"),
                    ensure_ascii=False,
                )
            )
            return

        # ── streaming 模式 ──
        if message.get("stream", False):
            await self._handle_stream(ws, request_id, method, params, conn_id=conn_id, legacy_client=legacy_client)
            return

        # ── 标准 request/response ──
        # S14: 记录请求计数和延迟（按方法、状态码分类）
        _metrics = get_metrics_collector()
        _start_ts = time.time()
        try:
            # A-1 修复：传 client_id=conn_id，使 registry 限流按连接分桶
            # （此前所有连接共享 "default" 桶，N 个前端 = N 倍配额消耗）
            result = await self._registry.dispatch(method, params, client_id=conn_id or "default", legacy_client=legacy_client)
            payload = json.dumps(
                _make_response(request_id, result=result),
                ensure_ascii=False,
                default=str,
            )
            # 出站体积守卫：超限不静默下发（会把客户端连接打死），改回 -32007
            payload = enforce_outbound_limit(payload, method=method, request_id=request_id)
            await ws.send_str(payload)
            _metrics.record_request(method, "ok", (time.time() - _start_ts) * 1000.0)
        except (ConnectionError, ClientConnectionResetError):
            # 客户端先行断开（心跳强杀 / 窗口关闭 / 页面刷新）：
            # 请求本身可能已执行成功，只是回包无处可送。简记一笔，
            # 不再尝试回送错误包（会再次抛异常形成连环 traceback）。
            logger.info("client disconnected while responding to %s", method)
            _metrics.record_request(method, "conn_lost", (time.time() - _start_ts) * 1000.0)
            return
        except MethodNotFound:
            await ws.send_str(
                json.dumps(
                    _make_response(request_id, error={"code": -32601, "message": f"Method not found: {method}"}),
                )
            )
            _metrics.record_request(method, "not_found", (time.time() - _start_ts) * 1000.0)
        except InvalidParamsError as exc:
            await ws.send_str(
                json.dumps(
                    _make_response(
                        request_id,
                        error=exc.to_dict(),
                    ),
                    ensure_ascii=False,
                )
            )
            _metrics.record_request(method, "invalid_params", (time.time() - _start_ts) * 1000.0)
        except RPCTimeoutError as exc:
            await ws.send_str(
                json.dumps(
                    _make_response(request_id, error=exc.to_dict()),
                    ensure_ascii=False,
                )
            )
            _metrics.record_request(method, "timeout", (time.time() - _start_ts) * 1000.0)
        except RateLimitedError as exc:
            # P0-2026-08-17: 限流应返回 RATE_LIMITED(-32003)，而非被吞成 -32603，
            # 否则全方法契约测试会把正常限流误判为内部崩溃（真实 bug）。
            await ws.send_str(
                json.dumps(
                    _make_response(request_id, error=exc.to_dict()),
                    ensure_ascii=False,
                )
            )
            _metrics.record_request(method, "rate_limited", (time.time() - _start_ts) * 1000.0)
        except ClientUpgradeRequiredError as exc:
            # M2.1 R4 兼容：旧客户端（未声明 client_version）调 R4 → 显式 -32010，
            # 不再静默 pending 挂起（数据完整性 P0；P0-3 文案含"请升级客户端"）。
            await ws.send_str(
                json.dumps(
                    _make_response(request_id, error=exc.to_dict()),
                    ensure_ascii=False,
                )
            )
            _metrics.record_request(method, "client_upgrade_required", (time.time() - _start_ts) * 1000.0)
        except Exception as exc:
            logger.exception("method %s failed", method)
            # P2-4: 不再回传 traceback 给客户端（即使 DEBUG 也仅落本地日志），
            # 防止文件路径 / 函数名 / 内部状态等敏感信息泄漏到远端。
            error_obj: dict[str, Any] = {
                "code": -32603,
                "message": "Internal error",
            }
            if self._debug:
                error_obj["data"] = {
                    "exception": type(exc).__name__,
                    "detail": (str(exc) or "")[:200],
                }
            await ws.send_str(
                json.dumps(
                    _make_response(request_id, error=error_obj),
                )
            )
            _metrics.record_request(method, "error", (time.time() - _start_ts) * 1000.0)

    async def _handle_stream(
        self,
        ws: web.WebSocketResponse,
        request_id: Any,
        method: str,
        params: dict[str, Any],
        conn_id: str | None = None,
        legacy_client: bool = False,
    ) -> None:
        """处理 streaming 方法调用（AI chat 场景）。

        修复 S5: 增强流式响应中断处理，确保客户端断开时正确取消生成任务。
        P2-14: 用 asyncio.Event + asyncio.wait 取代 50ms 轮询，断连检测延迟从最高 50ms
        降到几乎零（事件由 send_chunk 在发送失败 / 连接关闭时立即置位）。
        B-1（G4/N2）: 流异常终止信号必须走 error chunk 通道（result.type=error），
        不能回 JSON-RPC error 响应——流请求 id 从不在客户端 _pending 表中，
        error 响应会被静默丢弃，前端生成器永久 await，表现为"思考中"卡死。
        """
        stream_task: asyncio.Task | None = None
        disconnect_event = asyncio.Event()

        async def send_chunk(chunk: str, chunk_type: str = "chunk") -> None:
            """Send a streaming chunk to the client.

            chunk_type: "chunk" for normal AI text deltas, "error" for terminal
            error messages handlers want to surface without polluting the
            assistant message buffer (H9 fix). Clients should not append
            type=error chunks to chat history.
            """
            if ws.closed:
                # 客户端已断开（aiohttp 心跳或对端 close 帧已处理）
                disconnect_event.set()
                return
            try:
                raw = json.dumps(
                    {
                        "jsonrpc": JSONRPC_VERSION,
                        "id": request_id,
                        "result": {"type": chunk_type, "data": chunk},
                    },
                    ensure_ascii=False,
                )
                # 出站体积守卫：流式 chunk 同样不得静默超发（降级为 error 帧而非丢帧）
                raw = enforce_outbound_limit_stream(raw, method=method, request_id=request_id, chunk_type=chunk_type)
                await ws.send_str(raw)
            except Exception:
                # 发送失败（客户端可能已断开），先置位事件让等待端立刻醒来，
                # 再向上抛出由 _handle_stream 统一处理
                disconnect_event.set()
                raise

        async def send_stream_error(message: str) -> None:
            """B-1：流终止错误兜底——优先走 error chunk（客户端可消费）。"""
            try:
                await send_chunk(message, chunk_type="error")
            except Exception:
                logger.debug("stream error chunk delivery failed: method=%s", method)

        try:
            # 创建流式任务，以便在客户端断开时取消
            async def run_stream() -> None:
                async for chunk in self._registry.dispatch_stream(
                    method,
                    params,
                    send_chunk=send_chunk,
                    client_id=conn_id or "default",
                    legacy_client=legacy_client,
                ):
                    # 检查客户端是否已断开
                    if ws.closed:
                        disconnect_event.set()
                        logger.info(
                            "Client disconnected during stream, aborting: method=%s",
                            method,
                        )
                        break

            stream_task = asyncio.create_task(run_stream())
            disconnect_waiter = asyncio.create_task(disconnect_event.wait())

            # P2-14: 兜底 watchdog —— 处理"流静默期"网络 FIN 场景：
            # 如果 dispatch_stream 长时间不 send_chunk（如 AI 在远端思考），
            # 单靠事件不会发现 ws.closed 已被 aiohttp transport 翻转。
            # 用 500ms 周期巡检（比原 50ms 轮询 CPU 占用低 10x），命中即置位事件。
            async def _closed_watchdog() -> None:
                while not disconnect_event.is_set():
                    if ws.closed:
                        disconnect_event.set()
                        return
                    await asyncio.sleep(0.5)

            watchdog_task = asyncio.create_task(_closed_watchdog())

            try:
                # 用 asyncio.wait 同时监听"流完成"与"断连事件"，
                # 任一发生即唤醒，常见路径(send_chunk 失败)零延迟。
                done, _pending = await asyncio.wait(
                    {stream_task, disconnect_waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if disconnect_waiter in done and stream_task not in done:
                    # 客户端断开 → 取消生成任务，等其退出
                    stream_task.cancel()
                    try:
                        await stream_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    logger.info("Stream cancelled due to client disconnect: method=%s", method)
                    return
            finally:
                # 无论哪条分支结束，都把辅助任务收尾掉，避免悬挂
                for t in (disconnect_waiter, watchdog_task):
                    if not t.done():
                        t.cancel()
                        try:
                            await t
                        except (asyncio.CancelledError, Exception):
                            pass

            # 流正常完成
            if not stream_task.cancelled():
                # 检查是否有异常
                exc = stream_task.exception()
                if exc:
                    raise exc

            if not ws.closed:
                await ws.send_str(
                    json.dumps(
                        {
                            "jsonrpc": JSONRPC_VERSION,
                            "id": request_id,
                            "result": {"type": "done"},
                        }
                    )
                )
        except MethodNotFound:
            # B-1：走 error chunk 通道（客户端 _handleStreamChunk type=error 可消费）
            await send_stream_error(f"Method not found: {method}")
        except ClientUpgradeRequiredError as exc:
            # M2.1 F5：流式帧旧客户端 R4 → 显式码透传，不得吞成 Internal error 丢码。
            await send_stream_error(f"[{exc.code}] {exc.message}")
        except InvalidParamsError as exc:
            await send_stream_error(exc.message or "Invalid params")
        except RPCTimeoutError as exc:
            logger.warning("Stream method %s timed out", method)
            await send_stream_error(f"响应超时（{exc.details.get('timeout', 60) if isinstance(exc.details, dict) else 60}s 无输出），请重试")
        except asyncio.CancelledError:
            # 流被取消，静默处理
            logger.info("Stream cancelled: method=%s", method)
        except Exception as exc:
            logger.exception("stream method %s failed", method)
            # P2-4: 不回传 str(exc) 防止敏感信息泄漏
            error_msg = "Internal error"
            if self._debug:
                error_msg = f"Internal error: {type(exc).__name__}: {(str(exc) or '')[:200]}"
            await send_stream_error(error_msg)
        finally:
            # 确保流任务被清理
            if stream_task and not stream_task.done():
                stream_task.cancel()

    async def start(self) -> None:
        """启动服务器。"""
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()

        # 启动心跳检测
        if self._enable_heartbeat:
            await self._conn_manager.start_heartbeat()

        logger.info(
            "WebSocket server started on %s:%d (max_connections=%d)",
            self._host,
            self._port,
            self._max_connections,
        )

    async def stop(self) -> None:
        """停止服务器，包含优雅关闭。

        关闭流程：
        1. 停止接受新连接
        2. 通知所有客户端即将关闭
        3. 等待活跃请求完成（最多 5 秒）
        4. 强制关闭所有连接
        5. 清理资源
        """
        logger.info("WebSocket server shutting down...")

        shutdown_msg = b"Server shutting down"

        # 1. 给活跃连接一些时间完成（优雅期）
        active_count = len(self._conn_manager._connections)
        if active_count > 0:
            logger.info("Waiting for %d active connections to close...", active_count)

            # 发送关闭提示给所有客户端（close_all 内部会停止心跳）
            await self._conn_manager.close_all(code=1001, message=shutdown_msg)

            # 等待一小段时间让客户端正常关闭
            try:
                await asyncio.wait_for(
                    self._wait_for_connections_drain(),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for connections to drain, forcing close")

        # 2. 强制关闭事件桥接
        await self._bridge.close_all(code=1001, message=shutdown_msg)

        # 3. 清理 aiohttp 资源
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

        logger.info("WebSocket server stopped gracefully")

    async def _wait_for_connections_drain(self) -> None:
        """等待所有连接自然关闭。"""
        while self._conn_manager._connections:
            await asyncio.sleep(0.1)
