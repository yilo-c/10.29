"""MCP 桌面能力层（OQ2 决策：桌面/浏览器 P1 工具，安全边界 = 审批门控）。

安全边界
========
1. **审批门控**：所有桌面工具 ``risk=high``，执行前必须存在对应 ``action_id``
   的**已批准**确认（ConfirmationStore，跨进程共享），否则返回
   ``requires_approval``（内核不可自我批准，契约 I2）。用户在前端批准后内核
   重调即可执行。
2. **app_launch 白名单**：仅允许显式登记的应用（路径或已知命令），禁止任意
   shell 命令执行。
3. **优雅降级**：依赖缺失（PIL / pyperclip / 系统能力）时返回
   ``{status: "unavailable", reason}``，不崩溃。
4. **browser.* 暂缓**：浏览器自动化攻击面最大、依赖重（Playwright），OQ2
   仅放行桌面能力，浏览器工具待单独决议 + 依赖评估。

流程（与既有 siyi_confirm_request 一致）：
  1. 内核调 ``siyi_confirm_request``（已在 tools.py）发起审批 → 用户批准。
  2. 批准后内核重调桌面工具 → 本层查 store 存在 approved 记录 → 执行。
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

from services.confirmation_store import compute_args_sha256

logger = logging.getLogger("siyi.mcp.desktop")

# app_launch 白名单：显式登记的应用（名称 → 启动命令/路径）。
# 只允许此表内的应用，禁止任意路径/shell 命令。
# 2026-09-11：登记宿主应用 workbuddy，与 services/app_launch.py 默认集对齐
# （「每天自动打开 WorkBuddy」在 MCP/内核路径同样可用）。
# 注：settings 追加合并语义暂未接入（本进程无 settings 单例，DB 路径需接线），
# 新应用暂在此表显式登记。
# 2026-09-12 M2.1 SEC-2：摘除 "cmd"（交互式 shell 载体）白名单项——
# 属任意命令执行攻击面，不属于"显式登记的应用"，禁止再经审批放行。
APP_LAUNCH_ALLOWLIST: dict[str, list[str]] = {
    "notepad": ["notepad.exe"],
    "calc": ["calc.exe"],
    "workbuddy": [r"D:\Program Files (x86)\WorkBuddy\WorkBuddy.exe"],
}


def _single_line(text: str) -> str:
    """SEC-2/B2：审批卡摘要单行化——剥离/转义换行，防伪造第二命令行。"""
    return str(text).replace("\r", " ").replace("\n", " ")


def approval_preview(action_id: str, args: dict[str, Any]) -> tuple[str, str]:
    """M2.1 SEC-2：审批 preview 参数摘要 ``(title, detail)``。

    app_launch：title=app_name，detail=命令全串（白名单解析后）截 ≤200 字符，
    替换旧"detail 为空"；其余工具维持现状（title=action_id、detail 空）。
    SEC-2/B2：detail 单行化——app_name 注入换行不得伪造多行命令行。
    """
    args = args or {}
    if action_id == "siyi_app_launch":
        name = _single_line(str(args.get("app_name", "")).strip())
        command = APP_LAUNCH_ALLOWLIST.get(name.lower())
        full = _single_line(" ".join(command) if command else name)
        return (name or action_id, _single_line(f"{name}: {full}")[:200])
    return (action_id, "")


def _require_approved(store: Any, action_id: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """检查 ConfirmationStore 是否已有该 action_id **且同参数指纹**的**已批准且未过期**记录。

    返回 None = 已批准（可执行）；返回 dict = 拒绝原因（含 requires_approval）。

    M2.1 SEC-1（A1）：消费侧必须比对 args_sha256 与批准条目 TTL——
    ① 只比 action_id 会放行"换参数重放"（批准 benign 后执行 exfil）；
    ② 过期 approved 条目不得再作授权（批准一次性 + 有效期）。
    """
    if store is None:
        return {"status": "unavailable", "reason": "confirmation store unavailable"}
    try:
        import time as _time

        now = _time.time()
        wanted_sha = compute_args_sha256(args)
        pending_approval = None
        for entry in store.list():
            if entry.get("action_id") != action_id:
                continue
            if entry.get("status") == "approved":
                expires_at = entry.get("expires_at")
                if expires_at is not None and now > expires_at:
                    continue  # A1c：过期 approved 不作授权
                if entry.get("args_sha256") != wanted_sha:
                    continue  # A1b：参数指纹不符 → 换参数重放不放行
                return None  # 同参且未过期已批准 → 放行
            if entry.get("status") == "pending" and pending_approval is None:
                pending_approval = entry
        if pending_approval:
            return {
                "status": "requires_approval",
                "confirmation_id": pending_approval["confirmation_id"],
                "message": "该操作需要用户批准后重试（内核不可自我批准）",
            }
        # 无 pending 也无 approved：发起一条待批确认，供前端批准
        # M2.1 SEC-2：审批 preview 携带参数摘要（app_launch：title=app_name +
        # 命令全串 ≤200 字符），用户批准前看得见将执行什么。
        title, detail = approval_preview(action_id, args)
        cid = store.create(
            action_id, dict(args or {}),
            source="mcp:kernel", title=title, detail=detail, ttl_s=300,
        )
        return {
            "status": "requires_approval",
            "confirmation_id": cid,
            "message": "已发起审批请求，批准后重试即可执行",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("approval gate failed for %s: %s", action_id, exc)
        return {"status": "error", "reason": str(exc)[:200]}


def _screenshots_whitelist_dir() -> str:
    """M2.1 SEC-8：截屏唯一合法落盘目录 ``<data_dir>/screenshots/``。"""
    from services.confirmation_store import _resolve_default_data_dir

    return os.path.join(_resolve_default_data_dir(), "screenshots")


def desktop_screenshot(target_dir: str) -> dict[str, Any]:
    """截取当前屏幕，保存 PNG 到 target_dir，返回文件路径。

    M2.1 SEC-8：写边界收敛为 ``<data_dir>/screenshots/`` 白名单目录；
    外部 target_dir 不再任意 ``makedirs``，改写到白名单路径并记 warning。
    """
    try:
        from PIL import ImageGrab
    except ImportError:
        return {"status": "unavailable", "reason": "Pillow 未安装，无法截屏"}
    try:
        allowed = _screenshots_whitelist_dir()
        # M2.1 D1/SEC-8：用 realpath（Windows 3.8+ 解析 symlink 与 junction）判界，
        # 而非 abspath——白名单目录内的 junction 会把写落点解析到边界外。
        boundary = os.path.normcase(os.path.realpath(allowed))
        requested = os.path.normcase(os.path.realpath(os.path.expanduser(str(target_dir or allowed))))
        if requested == boundary or requested.startswith(boundary + os.sep):
            out_dir = requested
        else:
            logger.warning(
                "SEC-8: screenshot target_dir=%r 越过写边界，改写到白名单 %s", target_dir, allowed
            )
            out_dir = boundary
        os.makedirs(out_dir, exist_ok=True)
        img = ImageGrab.grab()
        path = os.path.join(out_dir, "screenshot.png")
        img.save(path)
        return {"status": "ok", "path": path}
    except Exception as exc:  # noqa: BLE001
        logger.warning("screenshot failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:200]}


def clipboard_read() -> dict[str, Any]:
    """读取剪贴板文本。

    依赖 pyperclip；未安装或读取失败 → unavailable/error（不崩溃）。
    注：headless 会话无交互桌面剪贴板，Windows ctypes 兜底在无桌面会话下
    会崩溃（OpenClipboard/GlobalLock 无效），故不提供 ctypes 兜底。
    """
    try:
        import pyperclip
    except ImportError:
        return {"status": "unavailable", "reason": "pyperclip 未安装"}
    try:
        return {"status": "ok", "text": pyperclip.paste()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("clipboard read failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:200]}


def clipboard_write(text: str) -> dict[str, Any]:
    """写入剪贴板（依赖 pyperclip）。"""
    try:
        import pyperclip
    except ImportError:
        return {"status": "unavailable", "reason": "pyperclip 未安装"}
    try:
        pyperclip.copy(str(text))
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("clipboard write failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:200]}


def app_launch(app_name: str) -> dict[str, Any]:
    """白名单内启动应用。"""
    name = (app_name or "").strip().lower()
    command = APP_LAUNCH_ALLOWLIST.get(name)
    if command is None:
        return {
            "status": "refused",
            "reason": f"应用不在白名单（允许：{', '.join(sorted(APP_LAUNCH_ALLOWLIST))}）",
        }
    try:
        subprocess.Popen(command)
        return {"status": "ok", "app": name}
    except Exception as exc:  # noqa: BLE001
        logger.warning("app_launch %s failed: %s", name, exc)
        return {"status": "error", "reason": str(exc)[:200]}
