# -*- coding: utf-8 -*-
"""子进程文本输出解码基元（P1-A 治本，2026-09-18）。

背景（实测取证 deliverables/shell-capability-test-2026-09-18.md §3 P1-A）：
`subprocess.run(..., text=True)` 不带 `encoding=` 时，解码编码取自
`locale.getpreferredencoding()`。在 `PYTHONUTF8=1`（gate_all / done_check 等
门禁环境会设）下该值变成 "utf-8"，而 Windows 系统 CLI（powershell / tasklist /
ipconfig / wmic）输出是 cp936 —— reader 线程解码抛 `UnicodeDecodeError`，
**被 subprocess 内部吞掉、`CompletedProcess.stdout` 静默变 None**（主进程无异常），
下游 `.strip()` 炸 `'NoneType' object has no attribute 'strip'`。默认 cp936（仅
`PYTHONIOENCODING` 或裸环境）时反而正常 —— 故缺陷由 `PYTHONUTF8=1` 专属触发。

判据（元规则）：强制 `encoding="utf-8"` 是错的 —— 会把当前**正常**的中文输出
变成乱码（mojibake），因为那些命令本就吐 cp936。正确解 = **抓字节 + 多编码回退解码**，
既保留可读性（命中 gbk），又永不因解码抛错、与 PYTHONUTF8 无关。

用法：把
    subprocess.run(cmd, capture_output=True, text=True, timeout=N, **kw)
换成
    run_text(cmd, timeout=N, **kw)     # 返回 .stdout/.stderr 恒为 str，.returncode 原样
其余参数（cwd/env/input/check）透传；`input` 传 str 时按 utf-8 编码给子进程。

`思忆集app` 内所有需要文本输出的子进程一律经本基元；
`scripts/check_subprocess_encoding.py` 棘轮锁死（存量只降不升）。
"""

from __future__ import annotations

import subprocess
from typing import Any

# 解码回退链：utf-8 严格 → gbk（中文 Windows 控制台）→ cp936 → utf-8/replace 兜底。
# 绝不用 errors="replace" 打头，避免把合法 gbk 中文错解成乱码。
_FALLBACK_ENCODINGS = ("utf-8", "gbk", "cp936")


def decode_output(raw: bytes | str | None) -> str:
    """把子进程 stdout/stderr 解成 str，绝不抛、绝不再返回 None。

    - None（未捕获 / 已解码）→ ""
    - 已是 str（text 模式遗留）→ 原样
    - bytes → 按回退链试解，全失败则 utf-8/replace（保证有 str 且非 None）
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    for enc in _FALLBACK_ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


class _TextCompletedProcess:
    """轻量包装：仅把 returncode / stdout / stderr / args 透出为已解码视图。"""

    __slots__ = ("returncode", "stdout", "stderr", "args")

    def __init__(self, cp: subprocess.CompletedProcess) -> None:
        self.returncode = cp.returncode
        self.stdout = decode_output(cp.stdout)
        self.stderr = decode_output(cp.stderr)
        self.args = cp.args

    def __repr__(self) -> str:  # pragma: no cover - 调试友好
        return f"_TextCompletedProcess(returncode={self.returncode!r}, stdout_len={len(self.stdout)}, stderr_len={len(self.stderr)})"


def run_text(
    cmd: Any,
    *,
    timeout: float | None = None,
    cwd: Any = None,
    env: Any = None,
    input_text: str | None = None,
    shell: bool = False,
    check: bool = False,
    extra_kwargs: dict[str, Any] | None = None,
) -> _TextCompletedProcess:
    """以字节抓取子进程输出并多编码回退解码，返回 .stdout/.stderr 恒为 str。

    刻意不向 subprocess 传 text/encoding —— 由本函数掌控解码，彻底规避
    PYTHONUTF8 静默置 None。需要 stdin 数据用 `input_text`（str，内部 utf-8 编码）。
    """
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "timeout": timeout,
        "cwd": cwd,
        "env": env,
        "shell": shell,
        "check": check,
    }
    if input_text is not None:
        kwargs["input"] = input_text.encode("utf-8", errors="replace")
    if extra_kwargs:
        kwargs.update(extra_kwargs)
    completed = subprocess.run(cmd, **kwargs)  # noqa: S603 - 调用方负责 cmd 可信
    return _TextCompletedProcess(completed)


__all__ = ["run_text", "decode_output", "_TextCompletedProcess"]
