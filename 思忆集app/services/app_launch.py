"""app_launch — 应用启动白名单（稳定区）。

原定义于 ``assistant/capability/_shell.py``，因 ``services/react_executor.py`` 需引用
而违背稳定区不得反向依赖（import）assistant 的边界，迁至本模块。

消费方：``assistant/capability/_shell.py``（实际执行域）与本模块同层。

B1/B2 落地（2026-09-11）：名单 = 代码默认集 ∪ settings 追加项
（``assistant_app_launch_extra_allowlist``）。此前名单是纯硬编码 frozenset，
用户想启动名单外的应用（如宿主 WorkBuddy）无任何途径——"有引擎无钥匙孔"
的又一次重演；与 action_guard 白名单的合并语义保持同一哲学：
默认集保证安全底线，settings 追加项尊重用户意图。
"""

from __future__ import annotations

from typing import Any

# 应用启动白名单默认集 — LLM 只能启动以下安全应用
# 所有路径自动解析 start menu / PATH 中的可执行文件
APP_LAUNCH_ALLOWLIST: set[str] = {
    "chrome",
    "firefox",
    "edge",
    "msedge",
    "brave",
    "code",
    "cursor",
    "trae",
    "trae-cn",
    "trae-solo",
    "notepad",
    "calc",
    "explorer",
    # 2026-09-12 M2.1 SEC-2：摘除 "cmd"（交互式 shell 载体，属任意命令执行攻击面，
    # 不属于"显式登记的应用"）。此前 SEC-2 只改了 MCP 层白名单，assistant/shell 生效
    # 集仍含 cmd → LLM 实际执行域可 cmd.exe /c 任意命令。禁止再经审批放行。
    "wt",
    "windows-terminal",
    "outlook",
    "mail",
    "slack",
    "discord",
    "telegram",
    "wechat",
    "微信",
    "spotify",
    "mspaint",
    "paint",
    "calculator",
    "snippingtool",
    # 宿主应用自身（2026-09-11）：「每天自动打开 WorkBuddy」是既定需求，
    # 启动已安装应用只是拉起/聚焦窗口，风险可控。
    "workbuddy",
}

SETTINGS_KEY_EXTRA_ALLOWLIST = "assistant_app_launch_extra_allowlist"


def _normalize_entries(value: Any) -> set[str]:
    """把 settings 值归一为小写应用名集合（兼容 str 逗号分隔 / 序列 / dict）。"""
    entries: set[str] = set()
    if isinstance(value, str):
        for part in value.split(","):
            item = part.strip().lower()
            if item:
                entries.add(item)
        return entries
    if isinstance(value, dict):
        value = list(value.keys())
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            text = str(item).strip().lower()
            if text:
                entries.add(text)
    return entries


def resolve_app_launch_allowlist(settings_repository: Any = None) -> set[str]:
    """解析生效的启动白名单：默认集 ∪ settings 追加项（B1 合并语义）。

    settings_repository 不可用 / 读取异常时回退默认集——与既有行为一致，
    不因配置层故障放大权限或误锁全部启动。
    """
    allowlist = set(APP_LAUNCH_ALLOWLIST)
    if settings_repository is None:
        return allowlist
    try:
        extra = settings_repository.get_setting(SETTINGS_KEY_EXTRA_ALLOWLIST, [])
    except Exception:
        return allowlist
    allowlist |= _normalize_entries(extra)
    return allowlist
