from __future__ import annotations

import html
import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

# HTML 净化：优先使用 nh3（Rust 实现，性能更好且活跃维护），
# 回退到 bleach（已 deprecated 但仍可用）。
try:
    import nh3  # type: ignore

    _HAVE_NH3 = True
except ImportError:
    _HAVE_NH3 = False
    try:
        import bleach  # type: ignore
    except ImportError:
        bleach = None  # type: ignore
        _HAVE_NH3 = False
from core.error_grading import (
    ANCHOR_BRIDGE_START,
    ANCHOR_BRIDGE_START_OK,
    ANCHOR_BRIDGE_START_FAILED,
    ANCHOR_BRIDGE_STOP,
    ANCHOR_BRIDGE_REQUEST_FAILED,
    bridge_anchor,
)
from core.observability import SEVERITY_DEGRADED, SEVERITY_INFO
from core.settings_schema import (
    KEY_ASSISTANT_UPGRADE_CHANNEL,
    KEY_BROWSER_ASSISTANT_BRIDGE_ENABLED,
    KEY_BROWSER_ASSISTANT_BRIDGE_PORT,
    KEY_BROWSER_ASSISTANT_TRUSTED_EXTENSION_IDS,
    SETTINGS_DEFAULTS,
)
from .bridge_content import BrowserBridgeContentMixin
from domain.models.behavior import BehaviorEvent, CATEGORY_BROWSER, SOURCE_BROWSER_BRIDGE
from services.url_learning_service import UrlLearningError

logger = logging.getLogger("siyi.browser_bridge")

# --- 安全常量 ---
_ALLOWED_ORIGINS: frozenset[str] = frozenset(
    {
        "chrome-extension://",  # Chrome 扩展前缀，运行时按实际 ID 校验
        "moz-extension://",  # Firefox 扩展前缀
    }
)
_MAX_REQUEST_BODY_BYTES = 512 * 1024  # 512 KB，防止大 payload 攻击
_MAX_ALLOWED_EXTENSION_IDS = 16
_EXTENSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9@._-]{1,128}$")

_SAFE_HTML_TAGS = frozenset({"p", "br", "b", "i", "em", "strong", "ul", "ol", "li", "a", "code", "pre", "h1", "h2", "h3", "h4", "blockquote"})
_SAFE_HTML_ATTRIBUTES = {"a": ["href", "title"]}
_SAFE_HTML_PROTOCOLS = frozenset({"http", "https", "mailto"})
_HTML_SANITIZER_LOCAL = threading.local()


def _get_html_sanitizer():
    """获取 HTML 净化器（线程局部缓存）。

    优先使用 nh3（Rust 实现），回退到 bleach。
    返回值是一个 callable，接受 HTML 字符串返回净化后的字符串。
    """
    cleaner = getattr(_HTML_SANITIZER_LOCAL, "cleaner", None)
    if cleaner is not None:
        return cleaner

    if _HAVE_NH3:
        # nh3.clean 返回净化后的字符串，参数与 bleach 类似但 API 不同
        # nh3 的 attributes 参数是 dict[str, set[str]] 格式
        nh3_attributes = {tag: set(attrs) for tag, attrs in _SAFE_HTML_ATTRIBUTES.items()}

        def _nh3_cleaner(raw: str) -> str:
            return nh3.clean(
                raw,
                tags=set(_SAFE_HTML_TAGS),
                attributes=nh3_attributes,
                url_schemes=_SAFE_HTML_PROTOCOLS,
                strip_comments=True,
            )

        _HTML_SANITIZER_LOCAL.cleaner = _nh3_cleaner
    elif bleach is not None:
        _HTML_SANITIZER_LOCAL.cleaner = bleach.Cleaner(
            tags=list(_SAFE_HTML_TAGS),
            attributes=_SAFE_HTML_ATTRIBUTES,
            protocols=list(_SAFE_HTML_PROTOCOLS),
            strip=True,
            strip_comments=True,
        )
    else:
        # 两个库都不可用 — 使用最小化转义（仅去标签）
        logger.warning("nh3 和 bleach 均不可用，HTML 净化降级为纯文本转义")

        def _fallback_cleaner(raw: str) -> str:
            import re as _re

            return _re.sub(r"<[^>]+>", "", raw)

        _HTML_SANITIZER_LOCAL.cleaner = _fallback_cleaner

    return _HTML_SANITIZER_LOCAL.cleaner


def _sanitize_html(raw: str) -> str:
    """使用 sanitizer 按 HTML 白名单清洗。"""
    if not raw:
        return raw
    normalized = html.unescape(raw)
    cleaner = _get_html_sanitizer()
    # bleach.Cleaner 用 .clean()，nh3 wrapper 是 callable
    if hasattr(cleaner, "clean"):
        return cleaner.clean(normalized)
    return cleaner(normalized)


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class BrowserAssistantBridgeService(BrowserBridgeContentMixin):
    """Bridge between browser extension and assistant."""

    TOKEN_TTL_SECONDS = 3600

    def __init__(
        self,
        *,
        settings_repository=None,
        observability_service=None,
        workbench_service=None,
        inbox_capture_service=None,
        content_organizer_service=None,
        subagent_manager=None,
        memory_index_service=None,
        note_repository=None,
        task_repository=None,
        news_repository=None,
        inbox_repository=None,
        proposal_repository=None,
        translation_repository=None,
        ai_service=None,
        behavior_logger=None,
        memory_extractor=None,
        chat_session_repository=None,
        vocabulary_repository=None,
    ):
        self.settings_repository = settings_repository
        self.observability_service = observability_service
        self._behavior_logger = behavior_logger
        self.workbench_service = workbench_service
        self.inbox_capture_service = inbox_capture_service
        self.content_organizer_service = content_organizer_service
        self.subagent_manager = subagent_manager
        self.memory_index_service = memory_index_service
        self.note_repository = note_repository
        self.task_repository = task_repository
        self.news_repository = news_repository
        self.inbox_repository = inbox_repository
        self.proposal_repository = proposal_repository
        self.translation_repository = translation_repository
        self.ai_service = ai_service
        self.memory_extractor = memory_extractor
        self.chat_session_repository = chat_session_repository
        self.vocabulary_repository = vocabulary_repository
        self._server: _ReusableThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._assistant_events: list[dict[str, Any]] = []
        self._session_token = ""
        self._token_issued_monotonic = 0.0
        self._token_expires_at = ""
        self._allowed_extension_ids: set[str] = set()  # 运行时注册的扩展 ID
        self._issue_session_token_locked()
        self._status = {
            "enabled": True,
            "running": False,
            "host": "127.0.0.1",
            "port": self._resolve_port(),
            "base_url": f"http://127.0.0.1:{self._resolve_port()}",
            "token_expires_at": self._token_expires_at,
            "last_error": "",
        }

    def _resolve_enabled(self) -> bool:
        if self.settings_repository is None:
            return True
        return bool(self.settings_repository.get_setting(KEY_BROWSER_ASSISTANT_BRIDGE_ENABLED, True))

    def _configured_extension_ids(self) -> set[str]:
        if self.settings_repository is None:
            return set()
        raw = self.settings_repository.get_setting(KEY_BROWSER_ASSISTANT_TRUSTED_EXTENSION_IDS, [])
        if isinstance(raw, str):
            values = [item.strip() for item in raw.splitlines() if item.strip()]
        elif isinstance(raw, list):
            values = [str(item).strip() for item in raw if str(item).strip()]
        else:
            values = []
        return {item for item in values if _EXTENSION_ID_PATTERN.fullmatch(item)}

    def _resolve_release_channel(self) -> str:
        if self.settings_repository is None:
            return "stable"
        return str(self.settings_repository.get_setting(KEY_ASSISTANT_UPGRADE_CHANNEL, "stable") or "stable").strip().lower() or "stable"

    def _requires_configured_extension_whitelist(self) -> bool:
        return self._resolve_release_channel() == "stable"

    def _trusted_extension_ids(self) -> set[str]:
        configured = self._configured_extension_ids()
        if configured:
            return configured
        # 无配置白名单时，使用已注册的扩展 ID（首次握手自动注册）
        return set(self._allowed_extension_ids)

    def _resolve_port(self) -> int:
        _default = SETTINGS_DEFAULTS[KEY_BROWSER_ASSISTANT_BRIDGE_PORT]
        if self.settings_repository is None:
            return _default
        try:
            value = int(self.settings_repository.get_setting(KEY_BROWSER_ASSISTANT_BRIDGE_PORT, _default) or _default)
        except (TypeError, ValueError):
            value = _default
        return max(1025, min(value, 65535))

    def start(self, avoid_port: int | None = None) -> dict[str, Any]:
        with self._lock:
            port = self._resolve_port()
            # 自动避开 WebSocket 端口，避免两个服务器绑定同一端口导致请求路由混乱
            if avoid_port is not None and port == avoid_port:
                port = avoid_port + 1
                logger.info("browser assistant bridge: port %d conflicts with WS port, using %d", avoid_port, port)
            self._status.update(
                {
                    "enabled": self._resolve_enabled(),
                    "port": port,
                    "base_url": f"http://127.0.0.1:{port}",
                }
            )
            if not self._status["enabled"]:
                self._status.update({"running": False, "last_error": "disabled"})
                return dict(self._status)
            if self._server is not None:
                return dict(self._status)
            try:
                handler_cls = self._build_handler()
                bridge_anchor(SEVERITY_INFO, ANCHOR_BRIDGE_START, "starting bridge server", port=port)
                self._server = _ReusableThreadingHTTPServer(("127.0.0.1", port), handler_cls)
                self._server_thread = threading.Thread(
                    target=self._server.serve_forever,
                    name="siyi-browser-assistant-bridge",
                    daemon=True,
                )
                self._server_thread.start()
                self._status.update({"running": True, "last_error": ""})
                logger.info("browser assistant bridge started: %s", self._status["base_url"])
                bridge_anchor(SEVERITY_INFO, ANCHOR_BRIDGE_START_OK, "bridge started", port=port)
                self._track_event("browser.bridge.started", {"port": port})
            except OSError as exc:
                self._server = None
                self._server_thread = None
                self._status.update({"running": False, "last_error": str(exc)})
                logger.warning("browser assistant bridge start failed: %s", exc)
                bridge_anchor(SEVERITY_DEGRADED, ANCHOR_BRIDGE_START_FAILED, "bridge start failed", port=port, error=str(exc))
                self._track_event("browser.bridge.start_failed", {"port": port, "error": str(exc)})
            return dict(self._status)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            server = self._server
            thread = self._server_thread
            self._server = None
            self._server_thread = None
            if server is not None:
                try:
                    server.shutdown()
                    server.server_close()
                except Exception as exc:
                    logger.warning("browser assistant bridge shutdown failed: %s", exc, exc_info=True)
                if thread is not None:
                    thread.join(timeout=2.0)
            self._status["running"] = False
            logger.info("browser assistant bridge stopped")
            bridge_anchor(SEVERITY_INFO, ANCHOR_BRIDGE_STOP, "bridge stopped", port=self._status.get("port"))
            self._track_event("browser.bridge.stopped", {"port": self._status.get("port")})
            return dict(self._status)

    def get_status_snapshot(self) -> dict[str, Any]:
        snapshot = dict(self._status)
        snapshot["token_expires_at"] = self._token_expires_at
        snapshot["token_ttl_seconds"] = self.TOKEN_TTL_SECONDS
        snapshot["trusted_extension_count"] = len(self._trusted_extension_ids())
        snapshot["configured_trusted_extension_count"] = len(self._configured_extension_ids())
        snapshot["extension_whitelist_required"] = self._requires_configured_extension_whitelist()
        snapshot["release_channel"] = self._resolve_release_channel()
        return snapshot

    def get_session_token(self) -> str:
        """返回当前 session token（供扩展桥握手时获取）。"""
        with self._lock:
            return self._session_token

    def _issue_session_token_locked(self) -> str:
        self._session_token = secrets.token_urlsafe(32)
        self._token_issued_monotonic = time.monotonic()
        self._token_expires_at = (datetime.now(timezone.utc) + timedelta(seconds=self.TOKEN_TTL_SECONDS)).replace(microsecond=0).isoformat()
        return self._session_token

    def rotate_session_token(self) -> dict[str, Any]:
        with self._lock:
            token = self._issue_session_token_locked()
            self._status["token_expires_at"] = self._token_expires_at
            return {
                "ok": True,
                "sessionToken": token,
                "expiresAt": self._token_expires_at,
                "expiresInSeconds": self.TOKEN_TTL_SECONDS,
                "bridgeVersion": "1.2.0",
            }

    def _session_token_valid_locked(self) -> bool:
        """当前 session token 是否仍在有效期内（调用方须持锁）。"""
        return bool(self._session_token) and (time.monotonic() - self._token_issued_monotonic) < self.TOKEN_TTL_SECONDS

    def _session_token_state(self, token: str) -> str | None:
        with self._lock:
            if not token or token != self._session_token:
                return "invalid_session_token"
            if not self._session_token_valid_locked():
                return "token_expired"
            return None

    @staticmethod
    def _extract_extension_id_from_origin(origin: str) -> str:
        for prefix in _ALLOWED_ORIGINS:
            if origin.startswith(prefix):
                return origin[len(prefix) :].strip()
        return ""

    def register_extension_id(self, extension_id: str) -> None:
        """注册受信任的浏览器扩展 ID。"""
        cleaned = str(extension_id or "").strip()
        if not cleaned:
            return
        if not _EXTENSION_ID_PATTERN.fullmatch(cleaned):
            raise ValueError("invalid_extension_id")
        configured = self._configured_extension_ids()
        # stable 频道强制要求先配置白名单，无白名单时拒绝注册
        if not configured and self._requires_configured_extension_whitelist():
            raise ValueError("extension_whitelist_required_in_stable")
        # 有白名单时严格校验；无白名单时允许首次握手自动注册（信任首个扩展）
        if configured and cleaned not in configured:
            raise ValueError("extension_not_whitelisted")
        if cleaned in self._allowed_extension_ids:
            return
        if len(self._allowed_extension_ids) >= _MAX_ALLOWED_EXTENSION_IDS:
            raise ValueError("too_many_registered_extensions")
        self._allowed_extension_ids.add(cleaned)

    def _handle_handshake(self, body: dict[str, Any]) -> dict[str, Any]:
        """扩展桥握手：校验扩展身份并返回 session token 供后续请求使用。

        白名单未配置（stable）或扩展不在白名单时抛 ValueError，由
        ``_dispatch_request`` 映射为 403 + 明确 error code（不再兜成 500）。

        token 复用：仅在当前 token 缺失/过期时轮换。此前每次握手都无条件轮换，
        多扩展（或同一扩展多窗口）会互相吊销 token，表现为随机 401 抖动。
        """
        extension_id = str(body.get("extensionId") or "").strip()
        with self._lock:
            if extension_id:
                self.register_extension_id(extension_id)
            if not self._session_token_valid_locked():
                self._issue_session_token_locked()
            self._status["token_expires_at"] = self._token_expires_at
            token_payload = {
                "ok": True,
                "sessionToken": self._session_token,
                "expiresAt": self._token_expires_at,
                "expiresInSeconds": self.TOKEN_TTL_SECONDS,
                "bridgeVersion": "1.2.0",
            }
        self._track_event("browser.bridge.handshake", {"extension_id": extension_id})
        return token_payload

    def _handle_refresh_token(self, body: dict[str, Any]) -> dict[str, Any]:
        self._track_event("browser.bridge.refresh_token", {"requested_at": datetime.now(timezone.utc).isoformat()})
        return self.rotate_session_token()

    @staticmethod
    def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """递归清洗 payload 中所有字符串值，移除不安全 HTML 标签。"""
        if not isinstance(payload, dict):
            return payload
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, str):
                cleaned[key] = _sanitize_html(value)
            elif isinstance(value, dict):
                cleaned[key] = BrowserAssistantBridgeService._sanitize_payload(value)
            elif isinstance(value, list):
                cleaned[key] = [
                    BrowserAssistantBridgeService._sanitize_payload(item)
                    if isinstance(item, dict)
                    else (_sanitize_html(item) if isinstance(item, str) else item)
                    for item in value
                ]
            else:
                cleaned[key] = value
        return cleaned

    def drain_assistant_events(self) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._assistant_events)
            self._assistant_events.clear()
        return events

    def _build_handler(self):
        service = self

        class BrowserAssistantHandler(BaseHTTPRequestHandler):
            def _check_origin(self, body: dict[str, Any] | None = None) -> bool:
                """校验 Origin 是否为受信任的浏览器扩展。"""
                origin = self.headers.get("Origin") or ""
                route = urlparse(self.path).path.rstrip("/") or "/"
                origin_extension_id = service._extract_extension_id_from_origin(origin)
                if not origin:
                    # 无 Origin 的本地请求仅允许健康检查路径。
                    return self.command in ("GET", "OPTIONS") and route in {"/health", "/api/browser-assistant/health"}
                # 握手阶段允许浏览器扩展前缀，供首次注册 extensionId
                if route == "/api/browser-assistant/handshake":
                    expected_extension_id = str((body or {}).get("extensionId") or "").strip()
                    configured_extension_ids = service._configured_extension_ids()
                    if (
                        origin_extension_id
                        and expected_extension_id
                        and origin_extension_id == expected_extension_id
                        and _EXTENSION_ID_PATTERN.fullmatch(expected_extension_id)
                        and (not configured_extension_ids or expected_extension_id in configured_extension_ids)
                    ):
                        return True
                    logger.warning("browser bridge: rejected handshake origin=%s", origin)
                    return False
                # 受信扩展白名单/注册表是唯一的 Origin 闸门。
                # （此前"无白名单配置即放行任意 chrome-extension Origin"的分支与
                #  register_extension_id 的 stable 白名单闸门自相矛盾：不填白名单时
                #  任何本机进程都能自封为扩展并拿到 token，白名单形同虚设。）
                if origin_extension_id and origin_extension_id in service._trusted_extension_ids():
                    return True
                logger.warning("browser bridge: rejected origin=%s", origin)
                return False

            def _check_session_token(self) -> str | None:
                """校验请求携带的 session token（健康检查和 handshake 豁免）。"""
                route = urlparse(self.path).path.rstrip("/") or "/"
                # 健康检查和握手端点豁免 token
                if route in {"/health", "/api/browser-assistant/health", "/api/browser-assistant/handshake"}:
                    return None
                token = self.headers.get("X-Bridge-Token") or ""
                error = service._session_token_state(token)
                if error is None:
                    return None
                logger.warning("browser bridge: %s for %s", error, route)
                return error

            def _resolved_cors_origin(self) -> str:
                """返回经过校验的 Origin（不再使用通配符 *）。"""
                origin = self.headers.get("Origin") or ""
                if origin:
                    return origin
                return "http://127.0.0.1"

            def do_OPTIONS(self):
                if not self._check_origin():
                    self._send_json(403, {"ok": False, "error": "origin_rejected"})
                    return
                self._send_json(204, {})

            def do_GET(self):
                if not self._check_origin():
                    self._send_json(403, {"ok": False, "error": "origin_rejected"})
                    return
                token_error = self._check_session_token()
                if token_error is not None:
                    self._send_json(401, {"ok": False, "error": token_error})
                    return
                status, payload = service._dispatch_request("GET", self.path, None)
                self._send_json(status, payload)

            def do_POST(self):
                content_length = int(self.headers.get("Content-Length") or 0)
                if content_length > _MAX_REQUEST_BODY_BYTES:
                    self._send_json(413, {"ok": False, "error": "payload_too_large"})
                    return
                raw_body = self.rfile.read(content_length) if content_length > 0 else b""
                payload = {}
                if raw_body:
                    try:
                        payload = json.loads(raw_body.decode("utf-8"))
                    except ValueError:
                        self._send_json(400, {"ok": False, "error": "invalid_json"})
                        return
                if not self._check_origin(payload):
                    self._send_json(403, {"ok": False, "error": "origin_rejected"})
                    return
                token_error = self._check_session_token()
                if token_error is not None:
                    self._send_json(401, {"ok": False, "error": token_error})
                    return
                status, response_payload = service._dispatch_request("POST", self.path, payload)
                self._send_json(status, response_payload)

            def log_message(self, format, *args):
                logger.debug("browser bridge http: " + format, *args)

            def _send_json(self, status_code: int, payload: dict[str, Any]):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                cors_origin = self._resolved_cors_origin()
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Bridge-Token")
                self.send_header("Vary", "Origin")
                self.end_headers()
                if self.command != "HEAD" and body:
                    self.wfile.write(body)

        return BrowserAssistantHandler

    def _record_behavior(self, action: str, detail: dict[str, Any] | None = None) -> None:
        if self._behavior_logger is None:
            return
        try:
            if hasattr(self._behavior_logger, "record"):
                self._behavior_logger.record(
                    BehaviorEvent(
                        category=CATEGORY_BROWSER,
                        action=action,
                        source=SOURCE_BROWSER_BRIDGE,
                        privacy="local",
                        detail=detail or {},
                    )
                )
        except Exception:
            logger.debug("failed to record behavior event", exc_info=True)

    def _dispatch_request(self, method: str, path: str, payload: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        route = urlparse(path).path.rstrip("/") or "/"
        body = payload or {}
        body = self._sanitize_payload(body)
        try:
            if method == "GET" and route in {"/health", "/api/browser-assistant/health"}:
                return 200, {"ok": True, "service": "browser_assistant_bridge", **self.get_status_snapshot()}
            if method == "GET" and route == "/api/browser-assistant/status":
                return 200, {"ok": True, "status": self.get_status_snapshot()}
            if method == "GET" and route == "/api/browser-assistant/diagnostics":
                return 200, self.get_diagnostics_snapshot()
            if method == "POST" and route == "/api/browser-assistant/handshake":
                try:
                    return 200, self._handle_handshake(body)
                except ValueError as exc:
                    # 白名单未配置（stable）或扩展未受信：返回明确的 403 + error code。
                    # 修复前该异常被下方通用 except 兜成 500，插件侧只能显示"请求失败"。
                    return 403, {"ok": False, "error": str(exc)}
            if method == "POST" and route == "/api/browser-assistant/refresh-token":
                return 200, self._handle_refresh_token(body)
            if method == "POST" and route == "/api/browser-assistant/summarize":
                self._record_behavior("page_summarized", {"url": body.get("url", "")})
                return 200, self.summarize_page(body)
            if method == "POST" and route == "/api/browser-assistant/summarize-with-transcript":
                self._record_behavior("page_summarized_with_transcript", {"url": body.get("url", "")})
                return 200, self.summarize_with_transcript(body)
            if method == "POST" and route == "/api/browser-assistant/transcribe-subtitle":
                self._record_behavior("subtitle_transcribed")
                return 200, self.transcribe_subtitle(body)
            if method == "POST" and route == "/api/browser-assistant/capture":
                self._record_behavior("page_visited", {"url": body.get("url", ""), "title": body.get("title", "")[:200]})
                return 200, self.capture_page_context(body)
            if method == "POST" and route == "/api/browser-assistant/save-to-reading":
                self._record_behavior("reading_saved", {"url": body.get("url", ""), "title": body.get("title", "")[:200]})
                return 200, self.save_page_to_reading(body)
            if method == "POST" and route == "/api/browser-assistant/create-note":
                self._record_behavior("note_created_from_page", {"url": body.get("url", "")})
                return 200, self.create_note_from_page(body)
            if method == "POST" and route == "/api/browser-assistant/create-task":
                self._record_behavior("task_created_from_page", {"url": body.get("url", "")})
                return 200, self.create_task_from_page(body)
            if method == "POST" and route == "/api/browser-assistant/add-word":
                # 划词入库：适配到真实能力 word.add（见 bridge_content.add_word_from_page）
                self._record_behavior("word_added_from_page", {"url": body.get("url", "")})
                result = self.add_word_from_page(body)
                if result.get("ok"):
                    return 200, result
                return (503 if result.get("error") == "vocabulary_unavailable" else 400), result
            if method == "POST" and route == "/api/browser-assistant/import-conversations":
                self._record_behavior("conversations_imported", {"count": len(body.get("conversations", []))})
                return 200, self.import_conversations(body)
            if method == "POST" and route == "/api/browser-assistant/explain":
                self._record_behavior("page_explained", {"url": body.get("url", "")})
                return 200, self.explain_question(body)
        except UrlLearningError as exc:
            logger.warning("browser summarize failed: %s", exc, exc_info=True)
            return 400, {"ok": False, "error": str(exc)}
        except Exception as exc:
            logger.exception("browser bridge request failed: %s", exc)
            bridge_anchor(SEVERITY_DEGRADED, ANCHOR_BRIDGE_REQUEST_FAILED, "bridge request failed", error=str(exc)[:200])
            return 500, {"ok": False, "error": str(exc)}
        return 404, {"ok": False, "error": "not_found", "route": route}

    # 以下内容处理方法已提取至 browser_bridge_content.py (BrowserBridgeContentMixin)
