"""统一错误处理框架。

提供：
- 基础错误类 SiyiError 及子类
- 错误码常量
- 全局错误处理器（用于 aiohttp middleware）
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from aiohttp import web

logger = logging.getLogger("siyi.core.errors")


# ── 错误码常量 ──

class ErrorCode:
    """JSON-RPC 错误码常量。"""
    # 标准 JSON-RPC 错误
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # 自定义业务错误 (从 -32000 开始)
    NOT_FOUND = -32000
    VALIDATION_ERROR = -32001
    CONFLICT = -32002
    RATE_LIMITED = -32003
    UNAUTHORIZED = -32004
    FORBIDDEN = -32005
    TIMEOUT = -32006
    # 出站体积守卫：响应序列化后超过服务端上限，已被拦截而非下发
    # （实测 inbox.list 单帧 2.3MB 打爆客户端帧上限 → 同连接后续调用全灭）。
    RESPONSE_TOO_LARGE = -32007
    # M2.1 R4 兼容：旧客户端（未声明 client_version）调用 R4 破坏性方法时的
    # 显式错误码——不再返回 {pending:true} 静默挂起（数据完整性 P0）。
    CLIENT_UPGRADE_REQUIRED = -32010

    # AI 服务错误
    AI_SERVICE_ERROR = -32100
    AI_SERVICE_UNAVAILABLE = -32101
    AI_BUDGET_EXHAUSTED = -32102

    # 数据错误
    DATA_CORRUPTED = -32200
    DATA_LOCK_FAILED = -32201

    # 连接错误
    CONNECTION_LIMIT_EXCEEDED = -32300
    WEBSOCKET_CLOSED = -32301


# ── 基础错误类 ──

class SiyiError(Exception):
    """思忆集基础错误类。

    所有业务异常应继承此类，以便统一处理。
    """
    code: int = ErrorCode.INTERNAL_ERROR
    message: str = "内部错误"
    details: dict[str, Any] | None = None

    def __init__(
        self,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ):
        self.message = message or self.__class__.message
        self.details = details or {}
        self.__cause__ = cause
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """转换为 JSON-RPC 错误对象。"""
        result = {
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            result["data"] = self.details
        return result


class NotFoundError(SiyiError):
    """资源未找到。"""
    code = ErrorCode.NOT_FOUND
    message = "资源未找到"


class ValidationError(SiyiError):
    """参数验证失败。"""
    code = ErrorCode.VALIDATION_ERROR
    message = "参数验证失败"


class InvalidParamsError(SiyiError):
    """JSON-RPC 无效参数（标准 -32602）。"""
    code = ErrorCode.INVALID_PARAMS
    message = "Invalid params"


class ConflictError(SiyiError):
    """资源冲突（如重复创建）。"""
    code = ErrorCode.CONFLICT
    message = "资源冲突"


class RateLimitedError(SiyiError):
    """请求频率超限。"""
    code = ErrorCode.RATE_LIMITED
    message = "请求频率超限，请稍后重试"


class RPCTimeoutError(SiyiError):
    """RPC handler 执行超时。"""
    code = ErrorCode.TIMEOUT
    message = "Request timeout"


class ResponseTooLargeError(SiyiError):
    """出站响应体积超过服务端上限（core.errors.ErrorCode.RESPONSE_TOO_LARGE）。

    语义：handler 已成功执行，但结果序列化后超过 WebSocket 出站体积上限，
    服务端**拒绝下发并明确报错**（绝不静默丢帧——静默会让客户端表现为"断联"）。
    details 携带 method / bytes / limit，供调用方缩小分页参数重试。
    """
    code = ErrorCode.RESPONSE_TOO_LARGE
    message = "响应体积超过服务端上限，请缩小分页参数后重试"


class UnauthorizedError(SiyiError):
    """未授权。"""
    code = ErrorCode.UNAUTHORIZED
    message = "未授权，请先登录"


class ForbiddenError(SiyiError):
    """禁止访问。"""
    code = ErrorCode.FORBIDDEN
    message = "禁止访问"


# ── AI 服务错误 ──

class AIServiceError(SiyiError):
    """AI 服务错误。"""
    code = ErrorCode.AI_SERVICE_ERROR
    message = "AI 服务错误"
    transient: bool = False  # 是否为临时性错误（可重试）

    def __init__(
        self,
        message: str | None = None,
        transient: bool = False,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ):
        self.transient = transient
        super().__init__(message, details, cause)


class AIServiceUnavailableError(AIServiceError):
    """AI 服务不可用。"""
    code = ErrorCode.AI_SERVICE_UNAVAILABLE
    message = "AI 服务不可用"


class AIBudgetExhaustedError(AIServiceError):
    """AI 预算耗尽。"""
    code = ErrorCode.AI_BUDGET_EXHAUSTED
    message = "AI 预算耗尽"


# ── 数据错误 ──

class DataCorruptedError(SiyiError):
    """数据损坏。"""
    code = ErrorCode.DATA_CORRUPTED
    message = "数据损坏"


class DataLockFailedError(SiyiError):
    """数据锁获取失败。"""
    code = ErrorCode.DATA_LOCK_FAILED
    message = "数据锁获取失败"


# ── 连接错误 ──

class ConnectionLimitExceededError(SiyiError):
    """连接数超限。"""
    code = ErrorCode.CONNECTION_LIMIT_EXCEEDED
    message = "连接数超限"


class WebSocketClosedError(SiyiError):
    """WebSocket 已关闭。"""
    code = ErrorCode.WEBSOCKET_CLOSED
    message = "WebSocket 已关闭"


class ClientUpgradeRequiredError(SiyiError):
    """客户端过旧（未声明 client_version）调用 R4 破坏性方法（M2.1）。

    D21 已裁单机优先：「请升级客户端」引导文案与旧手机端过渡期管理降级为
    登记项（旧手机端=冻结历史包）；错误码本身为数据完整性 P0，照做。
    """
    code = ErrorCode.CLIENT_UPGRADE_REQUIRED
    message = "该破坏性操作需新版客户端审批流支持，请升级客户端"


# ── aiohttp Middleware ──

def create_error_middleware(
    debug: bool = False,
    on_error: Callable[[SiyiError], None] | None = None,
) -> web.middleware:
    """创建错误处理 middleware。

    Args:
        debug: 是否在响应中包含额外调试信息（仅限异常类名 + 短消息，
               **不会**回传 traceback / 路径 / 内部状态——traceback 永远只写本地日志）。
        on_error: 错误回调（用于日志/监控）
    """

    @web.middleware
    async def error_middleware(
        request: web.Request,
        handler: Callable[[web.Request], Any],
    ) -> web.StreamResponse:
        try:
            return await handler(request)
        except SiyiError as e:
            if on_error:
                on_error(e)
            logger.warning("SiyiError: %s (code=%d)", e.message, e.code)
            return web.json_response(
                {"jsonrpc": "2.0", "id": None, "error": e.to_dict()},
                status=_code_to_http_status(e.code),
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except web.HTTPException:
            raise  # 让 aiohttp 处理自己的异常
        except Exception as e:
            # P2-4: traceback 仅落本地日志，绝不回传客户端。
            logger.exception("Unhandled error: %s", e)
            if on_error:
                # 包装为通用错误
                on_error(SiyiError(str(e), cause=e))
            error_data: dict[str, Any] = {"message": "服务器内部错误"}
            if debug:
                # debug 模式下仅暴露异常类名 + 截断后的消息，避免泄漏内部路径/参数。
                error_data["exception"] = type(e).__name__
                error_data["detail"] = (str(e) or "")[:200]
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": ErrorCode.INTERNAL_ERROR,
                        "message": "服务器内部错误",
                        "data": error_data,
                    },
                },
                status=500,
                headers={"Access-Control-Allow-Origin": "*"},
            )

    return error_middleware


def _code_to_http_status(code: int) -> int:
    """将 JSON-RPC 错误码映射到 HTTP 状态码。"""
    if code == ErrorCode.NOT_FOUND:
        return 404
    if code in (ErrorCode.VALIDATION_ERROR, ErrorCode.INVALID_PARAMS):
        return 400
    if code == ErrorCode.CONFLICT:
        return 409
    if code == ErrorCode.RATE_LIMITED:
        return 429
    if code in (ErrorCode.UNAUTHORIZED,):
        return 401
    if code in (ErrorCode.FORBIDDEN,):
        return 403
    if code in (ErrorCode.AI_BUDGET_EXHAUSTED,):
        return 402  # Payment Required
    if code in (ErrorCode.CONNECTION_LIMIT_EXCEEDED,):
        return 503
    if -32200 < code <= -32100:
        # AI 服务错误码区间（-32100 ~ -32102）→ 502 Bad Gateway
        return 502
    return 500


# ── JSON-RPC 错误响应构建 ──

def make_jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    """构建 JSON-RPC 错误响应。"""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def error_from_exception(exc: Exception, debug: bool = False) -> dict[str, Any]:
    """将异常转换为 JSON-RPC 错误对象。

    P2-4: 即使在 debug=True 下也**不会**回传 traceback；
    traceback 应仅写入本地日志，避免泄漏服务端文件路径 / 内部状态给远端。
    """
    if isinstance(exc, SiyiError):
        return exc.to_dict()

    error: dict[str, Any] = {
        "code": ErrorCode.INTERNAL_ERROR,
        "message": str(exc) or "内部错误",
    }
    if debug:
        # 仅暴露异常类名 + 截断的消息（不含路径/参数）。
        error["data"] = {
            "exception": type(exc).__name__,
            "detail": (str(exc) or "")[:200],
        }
    return error