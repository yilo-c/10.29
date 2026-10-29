"""复C1：handler 静默失败统一包装器。

七维报告 §3.1：handlers 层真正的静默失败主体是 63 处 DEBUG 级 except
（生产默认日志级别下不可见）。§3.3 C1 / §3.4 规定：捕获异常时必须以
ERR 级（logger.exception，含 traceback）落日志，并产出结构化错误体
{"code", "message", "degraded": True}；错误码采用草案
"SIYI-<域>-<类别>"（域 CHAT/AI/TRA/TRN/MEM/SYN/OCR/EVT/STO/CFG；
类别 CONN/AUTH/QUOTA/TIMEOUT/MISS/DATA/INT）。

用法一 —— 既有 except Exception: 点内联升级（logger.exception 依赖
当前 except 上下文的 exc_info，必须在 except 块内调用）::

    from backend.handlers._wrap import CODE_MEM_DATA, degrade

    try:
        return await asyncio.to_thread(kg.get_relations, entity_id)
    except Exception:
        return degrade(logger, CODE_MEM_DATA, "knowledge.relations failed", fallback=[])

用法二 —— 整个 handler 包裹::

    @wrap_degraded(CODE_STO_DATA, fallback={"items": []}, message="notes.list failed")
    async def notes_list(...) -> dict: ...

降级合并规则：fallback 为 dict 时并入错误体键（前端按额外键向后兼容，
degraded: true 只亮角标不弹 toast）；list/str/bool 等类型无法携带错误体，
仅 ERR 日志可见 code。
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Callable, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])


def siyi_code(domain: str, category: str) -> str:
    """按 §3.4 草案拼错误码：SIYI-<域>-<类别>。"""
    return f"SIYI-{domain}-{category}"


# 本批先落地的常用错误码（覆盖三个优先 handler 的域类别对）
CODE_CHAT_INT = siyi_code("CHAT", "INT")  # 对话锚点/上下文增强内部失败
CODE_AI_CONN = siyi_code("AI", "CONN")  # AI 调用失败（后台自动打标等）
CODE_MEM_DATA = siyi_code("MEM", "DATA")  # 记忆/知识图谱数据访问失败
CODE_EVT_INT = siyi_code("EVT", "INT")  # best-effort 事件发射失败
CODE_STO_DATA = siyi_code("STO", "DATA")  # 笔记/设置等存储层读写失败

KNOWN_CODES: tuple[str, ...] = (
    CODE_CHAT_INT,
    CODE_AI_CONN,
    CODE_MEM_DATA,
    CODE_EVT_INT,
    CODE_STO_DATA,
)


def error_body(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """构造结构化降级错误体 {"code", "message", "degraded": True}。"""
    body: dict[str, Any] = {"code": code, "message": message, "degraded": True}
    body.update(extra)
    return body


def log_degradation(logger: logging.Logger, code: str, message: str) -> dict[str, Any]:
    """在 except 块内调用：ERR 级（含 traceback）落日志，返回错误体。

    使用 logger.exception —— 只能在 except 上下文中调用才能带上 traceback。
    """
    logger.exception("[%s] %s", code, message)
    return error_body(code, message)


def _merge_degraded(fallback: Any, body: dict[str, Any]) -> Any:
    """把错误体并入可携带它的 fallback；list 做浅拷贝防共享可变默认值。"""
    if isinstance(fallback, dict):
        return {**fallback, **body}
    if isinstance(fallback, list):
        return list(fallback)
    return fallback


def degrade(
    logger: logging.Logger,
    code: str,
    message: str,
    *,
    fallback: Any = None,
) -> Any:
    """既有 except Exception: 点的统一升级助手。

    ERR 级落日志（含 traceback）+ 返回降级结果；fallback 为 dict 时并入错误体。
    """
    body = log_degradation(logger, code, message)
    return _merge_degraded(fallback, body)


def wrap_degraded(
    code: str,
    *,
    fallback: Any = None,
    message: str = "handler degraded",
    logger: logging.Logger | None = None,
) -> Callable[[_F], _F]:
    """装饰器：包装 sync/async handler，异常 → ERR 日志 + 降级返回。

    - sync 与 async handler 均可包裹（自动识别协程函数）；
    - fallback 为 dict 时返回值携带 {code, message, degraded}；
    - 成功路径原样透传，不混入任何降级键。
    """
    log = logger or logging.getLogger("siyi.backend.handlers._wrap")

    def _degrade() -> Any:
        return _merge_degraded(fallback, log_degradation(log, code, message))

    def decorator(fn: _F) -> _F:
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await fn(*args, **kwargs)
                except Exception:  # noqa: BLE001 统一降级出口
                    return _degrade()

            return _async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception:  # noqa: BLE001 统一降级出口
                return _degrade()

        return _sync_wrapper  # type: ignore[return-value]

    return decorator


__all__ = [
    "CODE_AI_CONN",
    "CODE_CHAT_INT",
    "CODE_EVT_INT",
    "CODE_MEM_DATA",
    "CODE_STO_DATA",
    "KNOWN_CODES",
    "degrade",
    "error_body",
    "log_degradation",
    "siyi_code",
    "wrap_degraded",
]
