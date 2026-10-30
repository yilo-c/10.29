#!/usr/bin/env python3
"""统一 stdout/stderr UTF-8 兜底，供 CI 门禁脚本复用。

## 为什么需要（2026-10-31 实测）

GitHub Actions 的 **windows runner** 子进程 stdout 编码是 **cp1252**（不是 cp936）。
门禁脚本一旦 `print` 中文（`[核心模块完整性] 通过：…`、`[密码扫描] 未发现…` 等），
就抛 `UnicodeEncodeError: 'charmap' codec can't encode characters in position N-M`
并退出 1 —— 而这一步失败会让 `python-tests.yml` 的 quality-gates 作业
**中断，其后所有步骤全被 skipped**，整条 CI 表现为一条红。

实测事故：run 41135881687 崩在 check_core_integrity.py（第 7 步），
修好后 run 41136713073 立刻崩在 scan_secrets.py（第 8 步）——同一个病灶，
只是被前一个挡住了。故一次性给全部 CI 门禁脚本补齐，而不是逐个打补丁。

## 用法

    from _stdio import ensure_utf8_stdio
    ensure_utf8_stdio()   # 放在 import 之后、任何 print 之前

或单行（不想加 import 时，与 done_check / gate_all 的既有写法一致）：

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
"""

from __future__ import annotations

import sys


def ensure_utf8_stdio() -> None:
    """把 stdout/stderr 切成 utf-8，避免中文 Windows 控制台编码崩溃。

    幂等；对不支持 reconfigure 的流（如被重定向为 StringIO）静默跳过。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


__all__ = ["ensure_utf8_stdio"]
