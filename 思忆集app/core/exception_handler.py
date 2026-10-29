"""全局未捕获异常处理器 + 日志脱敏过滤器（P2-2）。

设计目标：
1. **唯一 excepthook 安装点**：三处入口（PyQt6 main / debug main / headless_main）
   统一调用 :func:`install_global_excepthook`，避免重复实现。
2. **日志脱敏**：通过 :class:`SanitizingLogFilter` 过滤器自动 redact 日志中的
   API key / token / secret / Authorization Bearer，避免 `.env` 密钥明文落盘。
3. **脱敏工具复用**：把 ``ObservabilityService._sanitize`` 的逻辑抽为模块级函数
   ``sanitize_text`` / ``sanitize_value``，供日志过滤器和 observability 共用。

使用示例：
    from core.exception_handler import install_global_excepthook, install_log_sanitizer

    install_global_excepthook(log_dir=Path("./logs"), app_name="headless")
    install_log_sanitizer()  # 给所有 siyi.* logger 加脱敏过滤器
"""
from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("siyi.core.exception_handler")

REDACT_HINT = "***REDACTED***"

# 敏感 key 识别关键词（小写匹配）
SENSITIVE_KEY_WORDS: tuple[str, ...] = (
    "token", "secret", "password", "api_key", "apikey",
    "authorization", "cookie", "credential",
)

# 敏感文本模式（key=value 或 Header: value 形式）
SENSITIVE_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)(token\s*[=:]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)(secret\s*[=:]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)(password\s*[=:]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s,;\"']+)"),
    # bearer token 裸值（sk-xxx / Bearer xxx）
    re.compile(r"(?i)(bearer\s+)([a-z0-9._\-]{20,})"),
    # OpenAI/DeepSeek 风格 sk-xxx
    re.compile(r"(sk-[a-zA-Z0-9]{20,})"),
)


def sanitize_text(text: str) -> str:
    """P2-2: 脱敏文本中的 API key / token / secret 等敏感值。

    Args:
        text: 原始文本（可能是日志消息、异常 traceback 等）

    Returns:
        脱敏后的文本，敏感值被替换为 :data:`REDACT_HINT`
    """
    if not text:
        return text
    value = str(text)
    for pattern in SENSITIVE_TEXT_PATTERNS:
        if pattern.groups >= 2:
            # 保留前缀（如 "api_key="），只替换值
            value = pattern.sub(rf"\1{REDACT_HINT}", value)
        else:
            # 无分组：整体替换
            value = pattern.sub(REDACT_HINT, value)
    return value


def sanitize_value(value: Any) -> Any:
    """P2-2: 递归脱敏 dict/list/str 中的敏感字段。

    - dict：key 匹配敏感词时整体替换为 REDACT_HINT
    - list：递归处理每个元素
    - str：调用 :func:`sanitize_text`
    - 其他类型：原样返回

    与 ``ObservabilityService._sanitize`` 行为一致，供其复用。
    """
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                result[key_text] = REDACT_HINT
                continue
            result[key_text] = sanitize_value(item)
        return result
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def _is_sensitive_key(key: str) -> bool:
    """判断 key 名是否包含敏感词。"""
    lowered = key.lower()
    return any(word in lowered for word in SENSITIVE_KEY_WORDS)


# ═══════════════════════════════════════════════════════════
# 日志过滤器
# ═══════════════════════════════════════════════════════════


class SanitizingLogFilter(logging.Filter):
    """P2-2: 日志记录过滤器，自动脱敏消息和异常 traceback。

    挂到 logger 上后，所有经过该 logger 的 record.msg 和 record.exc_info
    都会被脱敏。避免 .env 密钥通过异常 traceback 落盘。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # 脱敏消息文本
            if isinstance(record.msg, str):
                record.msg = sanitize_text(record.msg)
            # 脱敏参数（避免 % 格式化后才发现）
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: sanitize_text(v) if isinstance(v, str) else v
                        for k, v in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        sanitize_text(a) if isinstance(a, str) else a
                        for a in record.args
                    )
            # 脱敏异常文本（exc_info 是 (type, value, tb) 三元组）
            # C2 修复: 新 LogRecord 的 exc_text 在 Formatter.format() 首次格式化前恒为 None
            # （exc_text 仅在此后才缓存）。过滤器运行在 logger 级别（先于 handler 格式化），
            # 必须主动用 exc_info 格式化 traceback 并脱敏——否则 .env 密钥会通过
            # logger.exception / logger.error(exc_info=True) 明文落盘到控制台与日志文件。
            if record.exc_info:
                try:
                    import traceback as _tb
                    exc_text = "".join(_tb.format_exception(*record.exc_info))
                    record.exc_text = sanitize_text(exc_text)
                except Exception:
                    pass  # 脱敏自身失败不阻塞日志
            elif record.exc_text:
                record.exc_text = sanitize_text(record.exc_text)
        except Exception:
            # 过滤器自身异常绝不阻塞日志输出
            pass
        return True


def install_log_sanitizer(logger_names: tuple[str, ...] | None = None) -> None:
    """P2-2: 给指定 logger（默认所有 ``siyi.*``）安装脱敏过滤器。

    Args:
        logger_names: 要安装过滤器的 logger 名前缀元组。
            默认 ``("siyi",)``，覆盖所有 siyi.* 命名空间。
    """
    if logger_names is None:
        logger_names = ("siyi",)

    filt = SanitizingLogFilter()
    for name in logger_names:
        target_logger = logging.getLogger(name)
        # 避免重复挂载
        existing = [f for f in target_logger.filters if isinstance(f, SanitizingLogFilter)]
        if not existing:
            target_logger.addFilter(filt)
            logger.debug("P2-2 日志脱敏过滤器已挂载: %s", name)


# ═══════════════════════════════════════════════════════════
# 全局 excepthook
# ═══════════════════════════════════════════════════════════

# 标记是否已安装，避免重复安装
_excepthook_installed: bool = False
# 保存上一次的 excepthook（用于链式调用，保留 Qt 等框架的原生钩子）
_previous_excepthook = None


def install_global_excepthook(
    log_dir: Path | str | None = None,
    app_name: str = "siyi",
    *,
    force: bool = False,
) -> None:
    """P2-2: 安装统一的全局未捕获异常处理器。

    替代 ``app_entry/main.py``、``backend/headless_main.py``、
    ``core/exception_handler.py``（旧版）中的三处重复实现。

    行为：
    - KeyboardInterrupt：透传给 ``sys.__excepthook__``，不记录
    - 其他异常：critical 级别记录到 ``siyi.crash`` logger，并写入独立崩溃日志文件
    - 崩溃日志路径：``<log_dir or tempfile.gettempdir()>/siyi_<app_name>_crash_<ts>.log``
    - 调用前一次 excepthook（保留 Qt 等框架的原生行为）

    Args:
        log_dir: 崩溃日志目录；None 时用系统 temp 目录
        app_name: 应用名标识，用于崩溃日志文件名
        force: 是否强制重新安装（即使已安装）
    """
    global _excepthook_installed, _previous_excepthook

    if _excepthook_installed and not force:
        logger.debug("excepthook already installed, skip")
        return

    # C1 修复: force=True 重装时，若 sys.excepthook 已是本模块的闭包，
    # 不能把它保存为 _previous_excepthook——否则新闭包调用 prev 会链回自身 → 无限递归
    # （closure_2 里 prev=closure_1，closure_1 里 prev 仍=closure_1 != sys.excepthook=closure_2 → 递归）。
    # 保留上一次保存的原始钩子链即可终止链式调用。
    if not (_excepthook_installed and force):
        _previous_excepthook = sys.excepthook
    crash_logger = logging.getLogger("siyi.crash")
    crash_dir = Path(log_dir) if log_dir else Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    # 兜底：如果路径不可写，回退到 temp 目录
    try:
        crash_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        crash_dir = Path(os.path.join(tempfile.gettempdir()))

    def _global_excepthook(exc_type, exc_value, exc_tb):
        # KeyboardInterrupt 不记录，直接交给原生钩子
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        # P2-2: 脱敏 traceback（避免 .env 密钥通过异常明文落盘）
        tb_text_sanitized = sanitize_text(tb_text)

        # 1. critical 级别日志（会触发 SanitizingLogFilter 二次脱敏，无害）
        crash_logger.critical(
            "unhandled exception: %s: %s",
            exc_type.__name__, exc_value,
            exc_info=(exc_type, exc_value, exc_tb),
        )

        # 2. 写入独立崩溃日志文件（脱敏后）
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            crash_file = crash_dir / f"siyi_{app_name}_crash_{timestamp}.log"
            with open(crash_file, "w", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] {app_name} crash\n")
                f.write(tb_text_sanitized)
            crash_logger.debug("crash log written to %s", crash_file)
        except Exception:
            crash_logger.warning("failed to write crash log file", exc_info=True)

        # 3. 调用前一次 excepthook（保留 Qt 等框架的原生行为）
        prev = _previous_excepthook
        if prev is not None and prev != sys.excepthook:
            try:
                prev(exc_type, exc_value, exc_tb)
            except Exception:
                crash_logger.debug("previous excepthook also failed", exc_info=True)

    sys.excepthook = _global_excepthook
    _excepthook_installed = True
    logger.info("P2-2 global excepthook installed (app=%s, log_dir=%s)", app_name, crash_dir)


def is_excepthook_installed() -> bool:
    """查询当前是否已安装统一 excepthook（测试用）。"""
    return _excepthook_installed


# 向后兼容别名（旧 API 名为 install_global_exception_handler，P2-2 重命名为
# install_global_excepthook 以与 sys.excepthook 命名一致）
install_global_exception_handler = install_global_excepthook
