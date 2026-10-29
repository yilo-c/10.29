"""JSON-RPC 方法注册中心。

用法:
    registry = MethodRegistry()
    registry.register("chat.send", my_chat_handler)

    # 带 JSON Schema 参数校验的注册
    registry.register_with_schema(
        "notes.create",
        notes_create,
        schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
            },
        },
    )

    # 调用
    result = await registry.dispatch("chat.send", {"message": "hello"})
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import jsonschema
from jsonschema import ValidationError as JsonSchemaValidationError

from core.errors import ClientUpgradeRequiredError, InvalidParamsError, RPCTimeoutError

logger = logging.getLogger("siyi.backend.methods")

Handler = Callable[..., Awaitable[Any]]
Schema = dict[str, Any]

# ── 风险分级（V2.1 批次2：dispatch 层 R1-R4 审批闸门）────────────
# R1 只读 / R2 创建类 / R3 中等（未标注默认，保守缺省）/ R4 破坏性（强制 sandbox 审批）
RISK_R1 = "R1"
RISK_R2 = "R2"
RISK_R3 = "R3"
RISK_R4 = "R4"
DEFAULT_RISK = RISK_R3
VALID_RISKS = frozenset({RISK_R1, RISK_R2, RISK_R3, RISK_R4})

# ── N-E 范围棘轮（2026-09-17）：分页参数通用数值校验 ──────────────────
# 背景：376 个静态注册方法仅约 5% 带 JSON Schema（scripts/schema_coverage_ratchet.py
# 口径），无 schema 方法对 limit="abc"/limit=10**9 完全无感 → handler 层
# int() 抛 ValueError → -32603（八轮实测 D-2 横切根因）。此处对**约定命名**的
# 分页参数 limit/offset 在 dispatch 层统一棘轮，覆盖全部方法：
#   - 纯数值字符串 / 整值 float → 归一为 int 回写（前端 JSON 传 "50" 合法化）；
#   - bool / 非数值字符串 / 小数 float / 其他类型 → InvalidParamsError(-32602)；
#   - 越界（limit ∉ [0, PAGE_LIMIT_MAX]、offset ∉ [0, PAGE_OFFSET_MAX]）→ -32602；
#   - limit=0 放行（存量 handler「0=默认」语义，如 _notes.py limit if limit else）；
#   - 显式 None 放行（部分 handler 以 None 表达「未指定」）。
# 与 handler 局部 clamp（_inbox_crud._coerce_page 等）不冲突：棘轮只拦非法值
# 与荒谬值，安全区间内的取值仍由各 handler 自己的默认/夹取策略决定。
PAGE_LIMIT_MAX = 10_000
PAGE_OFFSET_MAX = 1_000_000
PAGE_RANGES: dict[str, tuple[int, int]] = {
    "limit": (0, PAGE_LIMIT_MAX),
    "offset": (0, PAGE_OFFSET_MAX),
}

# ── D6（2026-09-17）：R4 审批重放标记 ────────────────────────────────
# 背景：file.write 这类「RPC 已入 R4 审批、但 handler 内部还会再经过
# ActionBroker/ActionGuard 高危拦截（llm_source_high_risk）」的动作，批准重放时
# 需要把**真实发生过的**人工授权如实传下去，否则批准后依然被拦（写面整体坏死）。
# 该标记只在 skip_approval=True 的重放路径置位 —— 即 sandbox.approve 批准后由
# _handle_sandbox_approve 重放时；默认 False，直接/内部调用拿不到，因而仍会被
# 高危拦截要求审批。这**不是**免审：拿不到审批就永远走不到该标记。
_R4_APPROVED_REPLAY: contextvars.ContextVar[bool] = contextvars.ContextVar("siyi_r4_approved_replay", default=False)


def is_r4_approved_replay() -> bool:
    """当前调用是否处于「R4 审批通过后的重放执行」上下文（见 _R4_APPROVED_REPLAY）。"""
    return _R4_APPROVED_REPLAY.get()


class MethodNotFound(Exception):
    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(f"Method not found: {method}")


def _format_validation_error(exc: JsonSchemaValidationError) -> str:
    """将 jsonschema 校验错误格式化为简短可读的英文消息。"""
    path = ".".join(str(p) for p in exc.absolute_path) or "<root>"
    validator = exc.validator
    message = exc.message or "validation failed"
    if validator == "required":
        missing = list(getattr(exc, "validator_value", []) or [])
        return f"Missing required field(s): {', '.join(missing)}"
    if validator == "type":
        expected = exc.validator_value
        return f"Field '{path}' must be of type {expected}"
    if validator == "additionalProperties":
        return f"Unexpected additional field at '{path}'"
    return f"Field '{path}': {message}"


class MethodRegistry:
    """线程安全的 JSON-RPC 方法注册中心。

    B10 修复：使用 threading.RLock 保护 _handlers 字典，
    避免并发注册/注销/分发时触发 "dictionary changed size during iteration"。

    可选频率限制：设置 rate_limiter 后，dispatch() 会先检查 client_id 的请求频率。

    新增：支持为 handler 注册 JSON Schema，dispatch() 会在调用前校验 params。
    """

    def __init__(self, rate_limiter=None, timeout: float | None = 60.0) -> None:
        self._handlers: dict[str, Handler] = {}
        self._schemas: dict[str, Schema] = {}
        # V2.1 批次2：方法风险分级（未标注默认 R3）+ 动态风险判定器
        self._risks: dict[str, str] = {}
        # risk_resolver(method, params) -> risk：用于按调用参数动态判定风险
        # （如 settings.set 对敏感键返回 R4、普通键返回 R3）
        self._risk_resolvers: dict[str, Callable[[str, dict[str, Any]], str]] = {}
        # 裁决 2（2026-09-17 F-P2-①）：R4 入队前静态预检回调表。
        # precheck(method, params) -> 拒绝原因 str | None（None=通过，可抛异常
        # 由 dispatch 侧按「预检不可用→照常入队」降级处理）。
        self._prechecks: dict[str, Callable[[str, dict[str, Any]], "str | None"]] = {}
        self._lock = threading.RLock()
        self._rate_limiter = rate_limiter
        self._timeout = timeout
        # 按方法的超时覆盖（秒）：耗时方法（如 ai_chat.sync_deepseek 的 LLM 抽取
        # 链路）需要比默认 60s 更长的执行窗口。
        self._method_timeouts: dict[str, float] = {}

    def register(
        self,
        name: str,
        handler: Handler,
        *,
        risk: str = DEFAULT_RISK,
        risk_resolver: Callable[[str, dict[str, Any]], str] | None = None,
    ) -> None:
        """注册一个异步方法处理器。

        Args:
            name: 方法名
            handler: 异步处理函数
            risk: 风险分级（R1 只读 / R2 创建 / R3 中等默认 / R4 破坏性强制审批）。
                  R4 方法经 dispatch 调用时必须先通过 sandbox 审批（批次2 闸门）。
            risk_resolver: 可选动态风险判定器 (method, params) -> risk；提供时优先于
                  risk 静态分级（用于 settings.set 等按参数键判级的场景）。
        """
        if risk not in VALID_RISKS:
            raise ValueError(f"invalid risk level: {risk!r} (valid: {sorted(VALID_RISKS)})")
        with self._lock:
            if name in self._handlers:
                logger.warning("method %s already registered, overwriting", name)
            self._handlers[name] = handler
            self._risks[name] = risk
            if risk_resolver is not None:
                self._risk_resolvers[name] = risk_resolver
            else:
                self._risk_resolvers.pop(name, None)

    def register_with_schema(
        self,
        name: str,
        handler: Handler,
        schema: Schema,
        *,
        risk: str = DEFAULT_RISK,
        risk_resolver: Callable[[str, dict[str, Any]], str] | None = None,
    ) -> None:
        """注册一个带 JSON Schema 参数校验的异步方法处理器。

        Args:
            name: 方法名
            handler: 异步处理函数
            schema: JSON Schema 字典，用于校验 params
            risk: 风险分级（同 register）
            risk_resolver: 动态风险判定器（同 register）
        """
        self.register(name, handler, risk=risk, risk_resolver=risk_resolver)
        with self._lock:
            self._schemas[name] = schema

    def set_timeout(self, name: str, seconds: float) -> None:
        """为指定方法设置调用超时覆盖（秒）。

        适用于默认 60s 不足以完成的耗时方法。未设置的方法沿用全局默认。
        """
        with self._lock:
            self._method_timeouts[name] = float(seconds)

    def unregister(self, name: str) -> None:
        with self._lock:
            self._handlers.pop(name, None)
            self._schemas.pop(name, None)

    def has_method(self, name: str) -> bool:
        with self._lock:
            return name in self._handlers

    def list_methods(self) -> list[str]:
        with self._lock:
            return list(self._handlers.keys())

    def get_schema(self, name: str) -> Schema | None:
        """获取指定方法的 JSON Schema（如果存在）。"""
        with self._lock:
            return self._schemas.get(name)

    def get_risk(self, name: str) -> str:
        """返回指定方法的风险分级；未标注默认 R3（保守缺省）。"""
        with self._lock:
            return self._risks.get(name, DEFAULT_RISK)

    def set_risk(self, name: str, risk: str) -> None:
        """事后补充/覆盖方法风险分级（供 register_all 集中标注 R4 清单使用）。"""
        if risk not in VALID_RISKS:
            raise ValueError(f"invalid risk level: {risk!r} (valid: {sorted(VALID_RISKS)})")
        with self._lock:
            self._risks[name] = risk

    def set_risk_resolver(self, name: str, resolver: Callable[[str, dict[str, Any]], str]) -> None:
        """事后设置动态风险判定器（供 settings.set 等按参数键判级的方法使用）。"""
        with self._lock:
            self._risk_resolvers[name] = resolver

    def register_precheck(self, name: str, precheck: Callable[[str, dict[str, Any]], "str | None"]) -> None:
        """注册 R4 入队前静态预检（裁决 2，2026-09-17）。

        签名 ``(method, params) -> 拒绝原因 | None``：

        - 返回 ``None``：静态不可判 / 参数合法 → 照常走 R4 审批入队（既有行为）；
        - 返回原因字符串：参数**注定被执行层守卫拒绝**（如 file.write 路径越界）
          → dispatch 层直接以 InvalidParamsError(-32602) 拒绝，**不入队、不发
          pending 信封、不发 sandbox.pending_added 事件**（消除审批面板垃圾条目 +
          ``_MAX_PENDING=200`` 满队驱逐最旧合法条目的 DoS 面）。

        钩子做成注册方法可选回调（与 risk_resolver 同构），不在 dispatch 层散落
        ``if method == "file.write"`` 硬编码；precheck 自身抛异常按「不可判」降级
        （照常入队），绝不让预检把正常审批流打断。
        """
        with self._lock:
            self._prechecks[name] = precheck

    def _run_precheck(self, method: str, params: dict[str, Any]) -> "str | None":
        """执行入队前预检；异常降级为通过（保持既有入队行为，预检是增益不是闸门）。"""
        with self._lock:
            precheck = self._prechecks.get(method)
        if precheck is None:
            return None
        try:
            verdict = precheck(method, params)
        except Exception:  # noqa: BLE001
            logger.debug("precheck for %s raised; enqueue proceeds normally", method, exc_info=True)
            return None
        return str(verdict) if verdict else None

    @staticmethod
    def _precheck_rejection(method: str, params: dict[str, Any], reason: str) -> InvalidParamsError:
        """预检拒绝 → 与非 R4 静态拒绝（N-E 分页棘轮）**同款**的 -32602。

        形态一致性（裁决 2「探测面与正常拒绝同形」）：details 结构
        ``{method, param, value}`` 与 _ratchet_paging 的越界错误同构；原因短语
        复用执行层守卫原语（path_outside_allowed_root 等），不泄露「入队前被拦」
        与「重放时被拦」的差别。
        """
        offending = next((k for k in ("path", "dest", "source") if k in params), "")
        return InvalidParamsError(
            message=f"Field '{offending or 'params'}': {reason}",
            details={"method": method, "param": offending, "value": params.get(offending) if offending else None, "reason": reason},
        )

    def resolve_risk(self, method: str, params: dict[str, Any]) -> str:
        """解析方法的实际风险分级：有 risk_resolver 时优先按参数动态判定。

        用于 settings.set 等"按参数键判级"的方法（敏感键 R4、普通键 R3）。
        """
        with self._lock:
            resolver = self._risk_resolvers.get(method)
        if resolver is not None:
            try:
                resolved = resolver(method, params)
                if resolved in VALID_RISKS:
                    return resolved
                logger.warning("risk_resolver for %s returned invalid risk: %r", method, resolved)
            except Exception:
                logger.debug("risk_resolver for %s raised; falling back to static risk", method, exc_info=True)
        return self.get_risk(method)

    def is_r4(self, name: str, params: dict[str, Any] | None = None) -> bool:
        """是否为 R4 破坏性方法（dispatch 前必须经 sandbox 审批）。

        有 risk_resolver 时按 params 动态判定；否则用静态分级。
        """
        return self.resolve_risk(name, params or {}) == RISK_R4

    async def _enqueue_r4_approval(self, method: str, params: dict[str, Any], *, legacy_client: bool = False) -> dict[str, Any]:
        """R4 破坏性 RPC：入 sandbox 审批队列，返回 pending 响应（不执行）。

        与 react_executor 的工具破坏性拦截共用 SandboxManager 队列，
        前端经 sandbox.pending_added 事件弹出审批，approve 后由
        _handle_sandbox_approve 以 skip_approval=True 重放执行。

        M2.1 R4 向后兼容（P0-3）：未声明 client_version 的旧客户端连接
        （legacy_client=True）不再收 ``{pending:true}`` 静默挂起，改抛
        显式 JSON-RPC 错误 -32010 CLIENT_UPGRADE_REQUIRED（旧手机端=冻结
        历史包，D21 单机优先：审批引导文案降级为登记项）。

        裁决 2（2026-09-17 F-P2-①）：入队前先跑注册级静态预检
        （register_precheck）。预检判定「参数注定被执行层守卫拒绝」（如
        file.write 路径越界）→ 抛 -32602 InvalidParamsError，**不入队**
        （sandbox.add 不执行 → 无 pending 信封、无 sandbox.pending_added
        事件、不占 _MAX_PENDING 配额）。放在 legacy_client 判定之后：
        旧客户端兼容错误码的优先级语义不变。
        """
        if legacy_client:
            raise ClientUpgradeRequiredError(details={"method": method})
        rejection = self._run_precheck(method, params)
        if rejection is not None:
            raise self._precheck_rejection(method, params, rejection)
        from services.sandbox_manager import get_sandbox_manager

        preview: dict[str, Any] = {
            "action": method,
            "args": dict(params),
            "summary": f"RPC 方法 {method} 需要用户审批",
        }
        sandbox = get_sandbox_manager()
        pid = sandbox.add(method, dict(params), preview, kind="rpc")
        logger.info("R4 method %s enqueued for approval: pid=%s", method, pid)
        # P1-C 残留补刀（2026-09-18 实测）：add() 内部只脱敏了存储/事件副本，
        # 本函数返回给调用方的即时信封此前仍带明文（WS 探针实证链B-1 泄漏
        # settings.set 的 API key 原文）——返回面同样必须过 redact_preview_args。
        from services.preview_redact import redact_preview_args

        return {
            "pending": True,
            "requires_approval": True,
            "pending_id": pid,
            "action_id": method,
            "preview": redact_preview_args(preview),
            "message": f"[等待用户批准] {method} 已提交审批 pending_id={pid}",
        }

    def _validate_params(self, method: str, params: Any) -> None:
        """校验 params 是否符合已注册的 JSON Schema。

        校验失败时抛出 InvalidParamsError（JSON-RPC -32602）。
        """
        with self._lock:
            schema = self._schemas.get(method)
        if schema is None:
            return
        try:
            jsonschema.validate(instance=params, schema=schema)
        except JsonSchemaValidationError as exc:
            message = _format_validation_error(exc)
            raise InvalidParamsError(
                message=message,
                details={"method": method, "error": message},
            ) from exc

    @staticmethod
    def _coerce_page_int(name: str, value: Any, method: str) -> int:
        """把单个分页参数归一为 int，非法即抛 -32602。

        接受：int（非 bool）、整值 float、纯数值字符串（含首尾空白/正号，
        如 "50"、"+50"、" 20 "）。拒绝：bool（True/False 不是数量）、
        非整值 float（limit=1.5 大概率是传错参数）、非数值字符串、None 以外
        的其他类型。越界检查由调用方做（返回原始 int 值，含负数）。
        """
        lo_err = InvalidParamsError(
            message=f"Field '{name}' must be an integer",
            details={"method": method, "param": name, "value": repr(value)},
        )
        if isinstance(value, bool):  # bool 是 int 子类，必须先于 int 判断拒绝
            raise lo_err
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if value.is_integer():
                return int(value)
            raise lo_err
        if isinstance(value, str):
            text = value.strip()
            try:
                return int(text, 10)
            except ValueError:
                raise lo_err from None
        raise lo_err

    def _ratchet_paging(self, method: str, params: Any) -> None:
        """N-E 范围棘轮：对约定分页参数 limit/offset 做通用数值校验。

        仅当 params 是 dict 且显式携带该键时生效（缺省键交给 handler 默认值）。
        合法且类型本就是 int 的值零改动直通；字符串/整值 float 归一为 int
        **原地回写**，使后续 jsonschema 的 {"type":"integer"} 校验与 handler
        消费都拿到规范值。非法类型或越界 → InvalidParamsError(-32602)。
        原地回写的合理性：两个 dispatch 入口都以 `params = dict(params)`
        持有本轮调用的私有副本（见 _ratchet_paging 调用点前）。
        """
        if not isinstance(params, dict):
            return
        for name, (lo, hi) in PAGE_RANGES.items():
            if name not in params:
                continue
            raw = params[name]
            if raw is None:  # 显式 None：交由 handler 表达「未指定」
                continue
            norm = self._coerce_page_int(name, raw, method)
            if not (lo <= norm <= hi):
                raise InvalidParamsError(
                    message=f"Field '{name}' out of range [{lo}, {hi}]: {norm}",
                    details={"method": method, "param": name, "value": norm, "range": [lo, hi]},
                )
            params[name] = norm

    async def dispatch(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        client_id: str = "default",
        timeout: float | None = None,
        *,
        skip_approval: bool = False,
        legacy_client: bool = False,
    ) -> Any:
        """分发方法调用，返回结果。

        Args:
            method: JSON-RPC 方法名
            params: 方法参数
            client_id: 客户端标识，用于频率限制
            timeout: 本次调用的超时时间（秒）。None 表示使用注册中心默认值。
            skip_approval: True 时跳过 R4 审批闸门（仅供 sandbox.approve 重放执行）。
                同时会置位 is_r4_approved_replay() 标记，供 handler 内部还要再过
                ActionBroker/ActionGuard 高危拦截的动作（file.write）如实传递人工授权。
            legacy_client: True 表示该连接未声明 client_version（M2.1 R4 兼容：
                R4 方法改回显式 -32010 错误而非 pending 挂起）。

        async generator handler 兼容（P2 契约修复）：handler(**params) 的结果是
        async_generator 时（如 chat.send / chat.resume 这类纯流式方法在未带
        stream:true 的调用下走到本路径），迭代收集全部 chunk 并以 list 返回
        （非流式调用的合理降级），不再抛 TypeError → -32603。超时语义与
        dispatch_stream 一致：相邻 chunk 之间的空闲超时。普通 awaitable
        handler 行为不变。
        """
        # 频率限制检查（如果配置了 rate_limiter）
        if self._rate_limiter is not None:
            allowed = await self._rate_limiter.allow(client_id)
            if not allowed:
                from core.errors import RateLimitedError

                raise RateLimitedError(details={"retry_after": getattr(self._rate_limiter, "window", 60.0), "client_id": client_id})

        # B10 修复：在锁内获取 handler 引用，锁外执行避免长时间持锁
        with self._lock:
            handler = self._handlers.get(method)
        if handler is None:
            raise MethodNotFound(method)
        if params is None:
            params = {}
        # N-E 范围棘轮：在私有副本上归一（ws_server 直接把 json.loads 结果
        # 传入，原地回写不应污染调用方对象），再做分页参数通用校验。
        if isinstance(params, dict):
            params = dict(params)
        self._ratchet_paging(method, params)

        # V2.1 批次2：R4 破坏性方法强制 sandbox 审批（未标注默认 R3）。
        # 审批通过后由 _handle_sandbox_approve 以 skip_approval=True 重放执行。
        #
        # 2026-10-15（T1 复验修复）：参数校验**提到 R4 拦截之前**。原顺序为
        # 「先入队审批 → 批准重放时才 _validate_params」，于是参数非法的 R4 调用
        # 照样入队成一条垃圾审批项（实测 `reminder.delete {"reminder_id": …}`
        # 入队成功、批准后才 -32602）——既占 _MAX_PENDING 名额，又让用户白批一次。
        # 对无 schema 的方法 `_validate_params` 立即 return，故本调整零影响；
        # 仅让「有 schema 的 R4 方法」在入队前就拒掉非法参数。
        self._validate_params(method, params)

        if not skip_approval and self.is_r4(method, params):
            return await self._enqueue_r4_approval(method, params, legacy_client=legacy_client)

        effective_timeout = self._method_timeouts.get(method, self._timeout if timeout is None else timeout)
        # D6：R4 审批重放期间置位标记（作用于 handler 的整个执行期，含其内部
        # 经 broker/guard 的二次校验），退出时复位。仅 skip_approval=True 命中。
        replay_token = _R4_APPROVED_REPLAY.set(True) if skip_approval else None
        try:
            result = handler(**params)

            # P2 契约修复：async generator handler（如 chat.send / chat.resume）返回的
            # async_generator 不是 awaitable，直接 wait_for 会抛 TypeError → -32603。
            # 未带 stream:true 调用纯流式 handler 时降级为迭代收集全部 chunk 返回。
            if inspect.isasyncgen(result):
                chunks: list[Any] = []
                try:
                    while True:
                        try:
                            if effective_timeout is None:
                                chunk = await result.__anext__()
                            else:
                                async with asyncio.timeout(effective_timeout):
                                    chunk = await result.__anext__()
                        except StopAsyncIteration:
                            break
                        chunks.append(chunk)
                except asyncio.TimeoutError as exc:
                    raise RPCTimeoutError(
                        message=f"Method {method} idle timed out after {effective_timeout}s (no chunk emitted)",
                        details={"method": method, "timeout": effective_timeout, "stream": True},
                    ) from exc
                finally:
                    await result.aclose()
                return chunks

            try:
                return await asyncio.wait_for(result, timeout=effective_timeout)
            except asyncio.TimeoutError as exc:
                raise RPCTimeoutError(
                    message=f"Method {method} timed out after {effective_timeout}s",
                    details={"method": method, "timeout": effective_timeout},
                ) from exc
        finally:
            if replay_token is not None:
                _R4_APPROVED_REPLAY.reset(replay_token)

    async def dispatch_stream(
        self,
        method: str,
        params: dict[str, Any],
        send_chunk: Callable[..., Awaitable[None]],
        timeout: float | None = None,
        *,
        skip_approval: bool = False,
        client_id: str = "default",
        legacy_client: bool = False,
    ) -> AsyncIterator[Any]:
        """分发流式方法调用，返回异步迭代器。

        流式 handler 签名：async def handler(send_chunk, **params) -> AsyncIterator[str]
        注：**params 展开方式与 dispatch() 一致，同样支持 JSON Schema 校验。
        注：send_chunk 实际签名为 (chunk: str, chunk_type: str = "chunk")，
            此处用 Callable[..., ...] 兼容可选参数。

        超时语义：**空闲（activity-based）超时** — timeout 为相邻 chunk 之间的
        最大间隔，而非整个流的绝对时长。这样长时间运行的 agent 任务（工具调用、
        整理笔记等）只要持续产出进度/心跳 chunk 就不会被中途截断。

        A-1b：流式请求同样计入限流（1 次/条）——此前 dispatch_stream 完全
        不限流，chat.send 长流可绕过限流面；按条计数而非按 chunk，避免
        长流被误杀。
        """
        # 频率限制检查（如果配置了 rate_limiter；按流请求计数 1 次）
        if self._rate_limiter is not None:
            allowed = await self._rate_limiter.allow(client_id)
            if not allowed:
                from core.errors import RateLimitedError

                raise RateLimitedError(details={"retry_after": getattr(self._rate_limiter, "window", 60.0), "client_id": client_id})

        # B10 修复：在锁内获取 handler 引用
        with self._lock:
            handler = self._handlers.get(method)
        if handler is None:
            raise MethodNotFound(method)

        # N-E 范围棘轮（与 dispatch 一致，先在私有副本上归一再校验）
        params = dict(params) if isinstance(params, dict) else params
        self._ratchet_paging(method, params)

        # V2.1 批次2：R4 破坏性方法强制 sandbox 审批（审批通过后由
        # _handle_sandbox_approve 以 skip_approval=True 重放执行）。
        if not skip_approval and self.is_r4(method, params):
            pending = await self._enqueue_r4_approval(method, params, legacy_client=legacy_client)
            yield pending  # 流式通道对 R4 直接回 pending 单条，不再进入 handler
            return

        self._validate_params(method, params)

        effective_timeout = self._timeout if timeout is None else timeout
        stream = handler(send_chunk=send_chunk, **params)
        try:
            while True:
                try:
                    if effective_timeout is None:
                        chunk = await stream.__anext__()
                    else:
                        async with asyncio.timeout(effective_timeout):
                            chunk = await stream.__anext__()
                except StopAsyncIteration:
                    break
                yield chunk
        except asyncio.TimeoutError as exc:
            raise RPCTimeoutError(
                message=f"Stream method {method} idle timed out after {effective_timeout}s (no chunk emitted)",
                details={"method": method, "timeout": effective_timeout, "stream": True},
            ) from exc
        finally:
            await stream.aclose()
