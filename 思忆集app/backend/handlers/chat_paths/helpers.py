"""chat.send helper functions: complexity check, message building, aggregation dispatch.

本模块同时承载 chat 流式的两项公共设施（QUA-13 / V1-3）：

- 在途流取消控制（``ChatStreamControl`` + 注册表）：``chat.cancel`` RPC 据此把取消
  意图真正传播到 provider 流任务，而不是只给前端一个确认。
- 流式异常分型（``grade_stream_exception``）：复用 ``ai.llm_resilience.classify_error``
  既有分级，补齐超时/内容审核两类，产出「用户友好文案 + 技术细节」双字段载荷。
"""

from __future__ import annotations
import asyncio
import itertools
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("siyi.chat_paths.helpers")

__all__ = [
    "_is_complex_task",
    "strip_polite_prefix",
    "build_messages",
    "_chat_send_aggregate",
    "ChatStreamControl",
    "register_chat_stream",
    "unregister_chat_stream",
    "active_chat_streams",
    "cancel_chat_streams",
    "grade_stream_exception",
    "emit_graded_error",
    "emit_cancelled_notice",
]

# Action verbs that indicate the user wants the AI to DO something (not just chat)
# QUA-12 规则收紧（2026-09-12，改代码不激活——chat_route 缺省仍 legacy）：
# 旧规则 _MIN_ACTION_LENGTH=4 + 动词表含单字动词（找/读/写/做）与礼貌前缀
# （帮我/帮忙）→ 中文语境近乎**全判复杂**，auto 路由一旦点火会把绝大多数
# 闲聊推进 9~15s 内核慢路径，反向击穿快档优先目标。收紧口径：
#   1) 阈值 4 → 12（剥离礼貌前缀后计长，短请求一律简单）；
#   2) 动词表删除单字动词与礼貌前缀词，只保留明确的双字/多字动作词；
#   3) 判定前先剥离句首礼貌前缀（帮我/请/帮忙…），前缀本身不再构成动作信号；
#   4) 拿不准一律判简单（快档优先：错判简单=用户可再要求转内核，
#      错判复杂=每次白等 10 秒，两类错误代价不对称）。
_ACTION_VERBS = [
    # English（≥4 字符的明确动作词；单字符过泛的 do/get 已删）
    "search",
    "create",
    "delete",
    "open",
    "check",
    "list",
    "show",
    "fetch",
    "download",
    "analyze",
    "fix",
    "build",
    "generate",
    "translate",
    "summarize",
    "execute",
    # Chinese（双字及以上明确动作词；单字"找/读/写/做"与礼貌词"帮我/帮忙"已删）
    "搜索",
    "查找",
    "读取",
    "阅读",
    "写入",
    "创建",
    "删除",
    "打开",
    "运行",
    "检查",
    "查看",
    "列出",
    "显示",
    "获取",
    "下载",
    "分析",
    "修复",
    "执行",
    "构建",
    "生成",
    "翻译",
    "总结",
    "清理",
    "整理",
    "移动",
    "复制",
    "粘贴",
    "保存",
    "导出",
    "导入",
    "扫描",
    "监控",
    "管理",
    "配置",
    "设置",
    "安装",
    "卸载",
]
_MIN_ACTION_LENGTH = 12

# 句首礼貌前缀：剥离后再判定（只剥一层，避免"请帮我"剥成空串后语义漂移）
_POLITE_PREFIXES = (
    "请帮我",
    "麻烦帮我",
    "帮我",
    "帮忙",
    "请",
    "麻烦",
    "帮我忙",
    "please",
    "could you",
    "can you",
)


def strip_polite_prefix(message: str) -> str:
    """剥离句首礼貌前缀（QUA-12）。纯函数：只去前缀与其后空白，不改其余内容。

    前缀按长度降序尝试，保证"请帮我"优先于"帮我"/"请"命中（只剥一层）。
    """
    text = (message or "").strip()
    lowered = text.lower()
    for prefix in sorted(_POLITE_PREFIXES, key=len, reverse=True):
        if not lowered.startswith(prefix):
            continue
        rest = text[len(prefix) :]
        if not rest or rest[0].isspace():
            return rest.strip()
        # 中文前缀常直接连动词（"帮我打开…"）；英文需词边界，避免误剥
        if prefix.isascii() and rest[0].isalpha():
            continue
        return rest.strip()
    return text


def _is_complex_task(message: str) -> bool:
    """Detect if user wants AI to perform actions (not just chat).

    QUA-12 收紧版（保持纯函数、可测、无副作用）：
    1. 先剥离句首礼貌前缀（帮我/请/帮忙…不再独立触发复杂判定）；
    2. 剥离后长度 < _MIN_ACTION_LENGTH(12) → 简单（快档优先）；
    3. 命中明确动作动词表 → 复杂；否则 → 简单（拿不准一律判简单）。

    注意：本函数在 ``chat_route == "auto"`` 分层时才被调用；缺省路由
    仍是 legacy（不激活），激活归 M4a。
    """
    residual = strip_polite_prefix(message or "")
    if len(residual) < _MIN_ACTION_LENGTH:
        return False
    residual_lower = residual.lower()
    return any(kw in residual_lower for kw in _ACTION_VERBS)


async def build_messages(context, message, params):
    """Build the final message list for the LLM with optional enrichments."""
    msgs = [{"role": "user", "content": message}]
    # Web search enrichment
    if params.get("web_search"):
        try:
            web = getattr(context, "web_search_service", None)
            if web:
                results = await web.search(message, limit=3)
                if results:
                    search_text = "\n\n[Web Search Results]\n" + "\n".join(
                        f"- {r.get('title', '')}: {r.get('snippet', '')[:300]}" for r in results[:3]
                    )
                    msgs[-1]["content"] += search_text
        except Exception:
            logger.debug("web_search failed", exc_info=True)
    # History
    history = params.get("context_messages", [])
    if history:
        msgs = history + msgs
    context_limit = params.get("context_limit", 20)
    if len(msgs) > context_limit:
        msgs = msgs[-context_limit:]
    # Memory anchor
    memory = getattr(context, "memory_index_service", None)
    if memory:
        try:
            anchor = await memory.search(message, limit=3)
            if anchor:
                anchor_text = "\n\n[Related Memories]\n" + "\n".join(f"- {a.get('content', '')[:200]}" for a in anchor)
                msgs[0]["content"] = anchor_text + "\n" + msgs[0]["content"]
        except Exception:
            logger.debug("memory anchor failed", exc_info=True)
    return msgs


async def _chat_send_aggregate(context, message, params, aggregation, *, preferred_provider=None, preferred_model=None, send_chunk=None, stream=True):
    """Multi-provider aggregation path — delegates to aggregate_path."""
    from .aggregate_path import run_aggregate_path

    async for chunk in run_aggregate_path(
        context,
        message,
        params,
        aggregation,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        send_chunk=send_chunk,
        stream=stream,
    ):
        yield chunk


# ── 在途 chat 流取消控制（QUA-13：chat.cancel 真取消）──────────────────────
#
# 选型：协作式标志位（threading.Event）为主 + asyncio.Task.cancel 兜底。
# 理由（对现有架构侵入最小）：
# 1) chat.send 的执行链是「异步生成器 →（可选）后台线程里的 provider 同步生成器」
#    （见 simple_stream_path）。单靠 asyncio 取消穿不透那个后台线程，单靠标志位又
#    无法打断远端长时间 stall。故：标志位保证线程侧 ``gen.close()``（真正断开 HTTP
#    流、停止烧 token），Task.cancel 只作为宽限期后的硬兜底。
# 2) 不改 ws_server 的流任务表：控制对象由 handler 自己在 ``chat_send`` 里用
#    ``asyncio.current_task()`` 登记，chat.cancel RPC 从注册表取用。
# 3) 前端 ``chat.cancel`` 目前不带任何参数（stopChatStream 里 call('chat.cancel', {})），
#    因此缺省语义 = 取消该进程内全部在途 chat 流；给了 session_id 就只定向取消。

_CANCEL_GRACE_SECONDS = 2.0
_STREAM_TTL_SECONDS = 1800.0
_stream_seq = itertools.count(1)

_STATUS_STREAMING = "streaming"
_STATUS_CANCELLED = "cancelled"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"


@dataclass
class ChatStreamControl:
    """一次 chat.send / chat.resume 流式运行的取消与观测句柄。"""

    stream_id: str
    session_id: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)
    started_at: float = field(default_factory=time.monotonic)
    task: Any = None
    loop: Any = None
    finished: Any = None
    status: str = _STATUS_STREAMING
    deltas: int = 0
    chars: int = 0
    error_kind: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def request_cancel(self, reason: str = "user_cancelled") -> None:
        """置位取消标志（线程安全，可从任意上下文调用）。"""
        with self._lock:
            if self.cancel_event.is_set():
                return
            self.cancel_event.set()
            self.status = _STATUS_CANCELLED
        logger.info("chat stream cancel requested: id=%s session=%r reason=%s", self.stream_id, self.session_id, reason)

    def note_delta(self, text: str) -> None:
        """记账：本流已产出的 delta 数与字符数（取消后不再增长）。"""
        if not text:
            return
        with self._lock:
            self.deltas += 1
            self.chars += len(text)

    def summary(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "session_id": self.session_id,
            "status": self.status,
            "cancelled": self.cancelled,
            "deltas": self.deltas,
            "chars": self.chars,
            "error_kind": self.error_kind,
        }


_CHAT_STREAMS: dict[str, ChatStreamControl] = {}
_CHAT_STREAMS_LOCK = threading.Lock()
_GRACE_WATCHERS: set[Any] = set()


def _prune_stale_streams_locked() -> None:
    """回收超时未注销的陈旧条目（进程异常/测试遗留），避免注册表泄漏。"""
    now = time.monotonic()
    stale = [sid for sid, ctl in _CHAT_STREAMS.items() if now - ctl.started_at > _STREAM_TTL_SECONDS]
    for sid in stale:
        _CHAT_STREAMS.pop(sid, None)


def register_chat_stream(session_id: Any = "") -> ChatStreamControl:
    """登记一条在途 chat 流；必须在事件循环内（handler 协程）调用。"""
    control = ChatStreamControl(
        stream_id=f"chat-{next(_stream_seq):06d}",
        session_id=str(session_id or ""),
        task=asyncio.current_task(),
        loop=asyncio.get_running_loop(),
        finished=asyncio.Event(),
    )
    with _CHAT_STREAMS_LOCK:
        _prune_stale_streams_locked()
        _CHAT_STREAMS[control.stream_id] = control
    return control


def unregister_chat_stream(control: ChatStreamControl | None) -> None:
    """注销在途流；未取消时把状态落为 completed（取消则保持 cancelled）。"""
    if control is None:
        return
    with _CHAT_STREAMS_LOCK:
        _CHAT_STREAMS.pop(control.stream_id, None)
    with control._lock:
        if control.status == _STATUS_STREAMING:
            control.status = _STATUS_CANCELLED if control.cancel_event.is_set() else _STATUS_COMPLETED
    try:
        if control.loop is not None and control.finished is not None:
            control.loop.call_soon_threadsafe(control.finished.set)
    except Exception:  # noqa: BLE001 — 收尾信号不得影响主路径
        logger.debug("chat stream finish signal failed", exc_info=True)


def active_chat_streams() -> list[ChatStreamControl]:
    with _CHAT_STREAMS_LOCK:
        return list(_CHAT_STREAMS.values())


def _hard_cancel_after_grace(control: ChatStreamControl) -> None:
    """宽限期内流未自行退出 → 硬取消承载它的 asyncio 任务。

    协作式标志只在「下一个 chunk 边界」生效；provider 远端 stall 时永远等不到边界，
    此时必须取消任务：CancelledError 会沿异步生成器链上抛，``agent.run_stream_events``
    的 ``async with`` 与 provider 生成器的 finally 因此关闭底层 HTTP 流。
    """
    loop = control.loop
    if loop is None or not loop.is_running():
        return

    async def _watch() -> None:
        try:
            await asyncio.wait_for(control.finished.wait(), timeout=_CANCEL_GRACE_SECONDS)
        except asyncio.TimeoutError:
            task = control.task
            if task is not None and not task.done():
                task.cancel()
                logger.info("chat stream hard-cancelled after grace: id=%s", control.stream_id)
        except (asyncio.CancelledError, RuntimeError):
            raise
        except Exception:  # noqa: BLE001
            logger.debug("chat cancel grace watcher failed", exc_info=True)

    try:
        if asyncio.get_running_loop() is loop:
            watcher = loop.create_task(_watch())
        else:
            watcher = None
    except RuntimeError:
        watcher = None
    if watcher is None:
        # 不在同一循环（如同步测试上下文）：只依赖协作式标志，不做硬取消。
        return
    _GRACE_WATCHERS.add(watcher)
    watcher.add_done_callback(_GRACE_WATCHERS.discard)


def cancel_chat_streams(
    stream_id: str | None = None,
    session_id: str | None = None,
    reason: str = "user_cancelled",
) -> list[dict[str, Any]]:
    """取消在途 chat 流：定向（stream_id / session_id）或全部（前端 stop 按钮不带参数）。"""
    with _CHAT_STREAMS_LOCK:
        targets = [
            ctl
            for ctl in _CHAT_STREAMS.values()
            if (stream_id and ctl.stream_id == stream_id) or (session_id and ctl.session_id == str(session_id)) or (not stream_id and not session_id)
        ]
    for ctl in targets:
        ctl.request_cancel(reason)
    # 标志置位后再排程硬兜底，给协作式退出留出优先窗口。
    for ctl in targets:
        _hard_cancel_after_grace(ctl)
    return [ctl.summary() for ctl in targets]


async def emit_cancelled_notice(send_chunk: Any, control: ChatStreamControl | None) -> dict[str, Any] | None:
    """流被取消时发一行 notice（含已产出量）；不发 error，由调用方正常收尾 done。"""
    if control is None:
        return None
    payload = control.summary()
    payload.update({"event": "chat_cancelled", "reason": "user_cancelled"})
    if send_chunk is not None:
        try:
            await _safe_send_chunk(send_chunk, json.dumps(payload, ensure_ascii=False), "notice")
        except Exception:  # noqa: BLE001 — 取消收尾不得反噬主路径
            logger.debug("cancelled notice emit failed", exc_info=True)
    return payload


# ── 流式异常分型（V1-3 任务二：错误透传）─────────────────────────────────

_KNOWN_KINDS = frozenset(
    {
        "rate_limit",
        "billing_error",
        "auth_error",
        "bad_request",
        "server_error",
        "network_error",
        "timeout",
        "content_moderation",
        "not_configured",
        "budget_exhausted",
        "unknown_error",
    }
)

# classify_error 未覆盖的两类：内容审核 / 显式超时（后者会被它并入 network_error）
_MODERATION_MARKERS = (
    "content_filter",
    "content filter",
    "content_policy",
    "content policy",
    "flagged",
    "prohibited",
    "moderation",
    "safety",
    "sensitive content",
    "违规",
    "审核",
    "敏感",
    "不当内容",
    "风控",
)
_TIMEOUT_MARKERS = ("timeout", "timed out", "超时", "deadline exceeded")
_AUTH_MARKERS = (
    "invalid api key",
    "invalid_api_key",
    "incorrect api key",
    "unauthorized",
    "authentication",
    "authentication_error",
    "expired api key",
    "forbidden",
    "权限不足",
    "密钥无效",
    "鉴权",
    "无权限",
)
_NOT_CONFIGURED_MARKERS = (
    "not configured",
    "no api key",
    "client not available",
    "未配置",
    "尚未配置",
    # e2e 登记项 4 补录（2026-09-12）：一个 provider 都没配时，simple_stream 的
    # 显式文案（"AI client not available — no provider configured"）与设置面
    # 提示语都含此串；耗尽类文案（"所有提供方均失败"）另见弱信号表。
    "no provider configured",
)

# 回退链**耗尽**类文案（fallback.py：「AI 服务不可用，且未命中本地回退」/
# 「流式对话在所有提供方均失败」/「无可用 AI 提供方」）。属**弱信号**：链耗尽的
# 根因也可能是欠费/鉴权（那时 last_status 带真实 HTTP 状态），故只在拿不到 status
# 且 classify_error 也给不出更具体分类时才升格为 not_configured（见分型链尾）。
_FALLBACK_EXHAUSTED_MARKERS = (
    "ai 服务不可用",
    "提供方均失败",
    "无可用 ai 提供方",
    "fallback exhausted",
    "all providers failed",
    "no available provider",
)

# 双字段中的「用户友好文案」：给终端看；technical 给日志/开发者面板。
_FRIENDLY_BY_KIND = {
    "rate_limit": "模型服务请求过于频繁（限流），请稍候片刻再试",
    "billing_error": "模型账户额度或余额不足，请检查服务商计费设置后重试",
    "auth_error": "模型服务鉴权失败，请检查 API Key 是否有效或已过期",
    "bad_request": "请求参数有误，模型无法处理本次对话，请调整后重试",
    "server_error": "模型服务暂时不可用，请稍后重试",
    "network_error": "网络连接异常，无法访问模型服务，请检查网络后重试",
    "timeout": "模型响应超时，请稍后重试或缩短本次提问",
    "content_moderation": "内容未通过安全审核，请调整表述后重试",
    "not_configured": "AI 服务尚未配置，请先在设置中填写模型 API Key",
    "budget_exhausted": "任务步数预算已用完，管家已中止本次执行；请拆小任务后重试",
    "unknown_error": "生成失败，请重试",
}


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def grade_stream_exception(exc: BaseException | None) -> dict[str, Any]:
    """把流式异常分型为 {friendly, technical, error_kind, ...} 双字段载荷。

    复用 ``ai.llm_resilience.classify_error`` 的既有分级（billing/auth/bad_request/
    rate_limit/server/network/unknown），在其之上补三类它未覆盖的判定：
    ``timeout``、``content_moderation``、``not_configured``。
    技术细节只含异常类名 + 截断消息，绝不含 traceback（沿用 P2-4 约定）。
    """
    if exc is None:
        exc = RuntimeError("unknown stream failure")

    text = str(exc) or type(exc).__name__
    lowered = text.lower()
    status = getattr(exc, "status", None)
    if not isinstance(status, int):
        status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = None
    retry_after = getattr(exc, "retry_after_seconds", None)

    kind = str(getattr(exc, "error_kind", "") or "")
    transient = getattr(exc, "transient", None)

    if kind not in _KNOWN_KINDS:
        kind = ""
        # 1) 显式异常类型 / 文本特征优先（比 HTTP 状态更能说明问题）
        # U0 实测（2026-09-19）：butler 12 步预算耗尽抛 UsageLimitExceeded，
        # 曾落 unknown_error → 用户只见「生成失败，请重试」（病灶二）。
        if type(exc).__name__ == "UsageLimitExceeded" or "request_limit" in lowered:
            kind = "budget_exhausted"
        elif isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or _contains_any(lowered, _TIMEOUT_MARKERS):
            kind = "timeout"
        elif _contains_any(lowered, _MODERATION_MARKERS):
            kind = "content_moderation"
        elif _contains_any(lowered, _AUTH_MARKERS):
            kind = "auth_error"
        elif _contains_any(lowered, _NOT_CONFIGURED_MARKERS) or "未配置" in text:
            kind = "not_configured"
        else:
            # 2) 落到 ai.llm_resilience 既有分级
            kind = classify_error_safe(status, lowered, retry_after)
            # classify_error 把 timeout 归进 network_error，这里细分回来
            if kind == "network_error" and _contains_any(lowered, _TIMEOUT_MARKERS):
                kind = "timeout"
            # OSError 族（含 socket.timeout / 连接重置）→ 网络
            elif kind == "unknown_error" and isinstance(exc, OSError):
                kind = "network_error"
            # 3) e2e 登记项 4：回退链耗尽但**无任何 HTTP 状态**（一个 provider
            #    都没配时的典型形态）→ 升格为 not_configured。带状态（欠费 402 /
            #    鉴权 401 等导致的链耗尽）时上面已给出更准确的分类，此处不覆盖。
            elif kind == "unknown_error" and status is None and _contains_any(lowered, _FALLBACK_EXHAUSTED_MARKERS):
                kind = "not_configured"
        transient = None
    elif kind == "unknown_error":
        # e2e 登记项 4 的另一半：ai.types.AIServiceError 未显式传 error_kind 时，
        # 属性默认就是占位值 "unknown_error"（"流式对话在所有提供方均失败" 即如此）。
        # 占位值不代表 provider 的结论，不能像真结论那样短路文本判定；但为避免
        # 改写 provider 给出的其它有效分型，这里只补判 not_configured 一类。
        if status is None and (_contains_any(lowered, _NOT_CONFIGURED_MARKERS) or _contains_any(lowered, _FALLBACK_EXHAUSTED_MARKERS)):
            kind = "not_configured"
        # 其余情况原样保留 unknown_error 与 provider 声明的 transient，
        # 不改写既有行为（本分支只负责"占位值不得吞掉未配置信号"）。

    if kind == "timeout":
        transient = True
    elif kind == "content_moderation":
        transient = False
    elif kind == "network_error":
        transient = True

    if transient is None:
        transient = kind in ("rate_limit", "server_error", "network_error", "timeout")

    technical = f"{type(exc).__name__}: {(text or '')[:300]}"
    if status is not None:
        technical = f"{technical} [http_status={status}]"
    if retry_after:
        technical = f"{technical} [retry_after={retry_after}]"

    return {
        "event": "chat_error",
        "error_kind": kind,
        "friendly": _FRIENDLY_BY_KIND.get(kind, _FRIENDLY_BY_KIND["unknown_error"]),
        "technical": technical,
        "status": status,
        "transient": bool(transient),
        "retry_after_seconds": retry_after,
    }


def classify_error_safe(status: int | None, error_text: str, retry_after: Any = None) -> str:
    """调用既有 classify_error；任何导入/调用失败都退化为 unknown_error。"""
    try:
        from ai.llm_resilience import classify_error

        return classify_error(status, error_text or "", retry_after).kind
    except Exception:  # noqa: BLE001 — 分型失败不得打断错误上报
        logger.debug("classify_error unavailable, falling back to unknown_error", exc_info=True)
        return "unknown_error"


async def _safe_send_chunk(send_chunk: Any, text: str, chunk_type: str) -> None:
    """兼容新旧 send_chunk 签名（部分调用点为位置参数 only）。"""
    if send_chunk is None:
        return None
    try:
        await send_chunk(text, chunk_type=chunk_type)
    except TypeError:
        await send_chunk(text)
    return None


async def preflight_ai_or_emit(context, send_chunk: Any, control: Any = None) -> bool:
    """V1② 首反馈 fail-fast（2026-09-13）：AI 完全未配置时立即发分型错误。

    「完全未配置」= 无任何云端可用 key（provider_configs / flat llm_api_key /
    env SIYI_LLM_API_KEY·OPENAI_API_KEY·DEEPSEEK_API_KEY 皆空）**且**用户从未
    显式配置本地推理（llm_local_api_base 为空）。此时任何执行路径都注定失败
    （实测 8s 零帧后才吐分型错误）——在进路径前 ~0.1s 直接发
    not_configured 分型（notice+error），满足「首反馈 ≤1s + 错误分型可见」。

    返回 True = 有可用配置，继续正常路由；False = 已发错误帧，调用方直接返回。
    """
    import os as _os

    from ai.types import AIServiceError
    from core.provider_config import load_providers
    from core.settings_keys import (
        KEY_LLM_API_KEY,
        KEY_LLM_LOCAL_API_BASE,
    )

    try:
        settings_repo = getattr(context, "settings", None) or getattr(context, "settings_repository", None)
        cloud_key = bool(_os.environ.get("SIYI_LLM_API_KEY") or _os.environ.get("OPENAI_API_KEY") or _os.environ.get("DEEPSEEK_API_KEY"))
        if settings_repo is not None:
            if not cloud_key:
                cloud_key = bool(str(settings_repo.get_setting(KEY_LLM_API_KEY, "") or "").strip())
            if not cloud_key:
                for prov in load_providers(settings_repo):
                    if getattr(prov, "enabled", True) and str(getattr(prov, "api_key", "") or "").strip():
                        cloud_key = True
                        break
        local_configured = bool(str(settings_repo.get_setting(KEY_LLM_LOCAL_API_BASE, "") or "").strip()) if settings_repo is not None else False
        if cloud_key or local_configured:
            return True
        await emit_graded_error(
            send_chunk,
            AIServiceError("AI 服务尚未配置：未检测到任何可用的 API Key 或本地模型配置"),
            control=control,
        )
        return False
    except Exception:  # noqa: BLE001 —— 预检自身故障绝不阻塞正常对话
        logger.debug("chat preflight failed; continue normal routing", exc_info=True)
        return True


async def emit_graded_error(
    send_chunk: Any,
    exc: BaseException | None,
    *,
    control: ChatStreamControl | None = None,
    extra_technical: str = "",
    emit_chunks: bool = True,
) -> dict[str, Any]:
    """按分型上送错误：先 notice(JSON 双字段) 后 error(用户友好文案)。

    返回分型载荷供调用方记账/断言。error chunk 的 data 仍是纯字符串，保持既有
    ws_server / 前端契约（H9：type=error 不进消息正文缓冲）不被破坏。

    ``emit_chunks=False`` 只分型 + 落 control/日志不发 chunk —— 供降级链使用：
    管家路径在尚无任何输出时失败要留给 simple_stream 兜底，此时提前报错会误导用户。
    """
    payload = grade_stream_exception(exc)
    if extra_technical:
        payload["technical"] = f"{payload['technical']} | {extra_technical}"
    if not emit_chunks:
        # 只分型不落状态：降级链后续可能成功，此刻下结论会误报终态。
        logger.warning("chat stream error (graded, not emitted): %s", payload["technical"])
        return payload
    if control is not None:
        with control._lock:
            control.error_kind = payload["error_kind"]
            if not control.cancelled:
                control.status = _STATUS_FAILED
    if send_chunk is not None:
        try:
            await _safe_send_chunk(send_chunk, json.dumps(payload, ensure_ascii=False), "notice")
            await _safe_send_chunk(send_chunk, payload["friendly"], "error")
        except Exception:  # noqa: BLE001 — 错误上报自身失败只能落日志
            logger.debug("graded error emit failed", exc_info=True)
    else:
        logger.warning("chat stream error (no send_chunk): %s", payload["technical"])
    return payload
