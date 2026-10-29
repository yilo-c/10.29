# -*- coding: utf-8 -*-
"""MCP 工具策略层（M5 硬前置）—— 工具分级 + 策略解析（独立实现）。

**独立性约束**：本模块**不依赖 assistant**，也不依赖 ``core/action_guard`` 的
内部实现（``core/action_guard.py`` 保留给 assistant 过渡期，观察期红线不得删改）。
分级语义对齐 ``core/action_guard._classify_mcp_risk`` 的 low/medium/high 三档，
但以工具名启发式**独立**声明。

工具级别（RO / RW / DANGER）::

    RO      ≈ low   —— 只读、无副作用（get/list/search/status/health/notify/query/recall/brief/classify）
    RW      ≈ medium—— 读写、有业务副作用但非系统级危险（create/update/set/toggle/add/save/store/tag/quiz）
    DANGER  ≈ high  —— 系统级危险（delete/backup_snapshot/app_launch/shell/exec/clipboard/screenshot）

settings 策略 ``agent_kernel.mcp_tool_policy``（默认 ``allow_all``，可 per-kernel 白名单）::

    {
      "mode": "allow_all" | "restrict",   # 默认 allow_all（全部放行 = 现状）
      "allow": ["siyi_tool_a", ...],      # 白名单：restrict 下仍放行
      "deny":  ["siyi_tool_b", ...],      # 黑名单：始终拦截（无副作用）
      "per_kernel": {                     # per-kernel 白名单（覆盖并合并进全局）
        "<kernel>": {"mode": ..., "allow": [...], "deny": [...]}
      }
    }

解析语义（``resolve_policy``）::

    - 工具名命中 deny       → deny（返回明确错误，无副作用）
    - 工具名命中 allow      → allow（白名单放行）
    - mode == allow_all     → allow（现状行为，全部放行）
    - mode == restrict：
        - level == DANGER   → require_approval（非 allow_all 下必须走 ConfirmationStore）
        - level == RW       → deny（白名单外读写被拦截，无副作用）
        - level == RO       → allow（只读放行）
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("siyi.tooling.tool_policy")

# ── 工具级别 ────────────────────────────────────────────────────────
TOOL_LEVEL_RO = "RO"
TOOL_LEVEL_RW = "RW"
TOOL_LEVEL_DANGER = "DANGER"
TOOL_LEVELS = (TOOL_LEVEL_RO, TOOL_LEVEL_RW, TOOL_LEVEL_DANGER)
DEFAULT_TOOL_LEVEL = TOOL_LEVEL_RW  # 缺声明视为 RW

# ── 策略决策 ────────────────────────────────────────────────────────
DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_REQUIRE_APPROVAL = "require_approval"

# ── 已知 siyi_* 工具的显式级别声明（确定性；启发式兜底供未知工具）──
_EXPLICIT_LEVELS: dict[str, str] = {
    # 核心数据工具
    "siyi_health": TOOL_LEVEL_RO,
    "siyi_notes_search": TOOL_LEVEL_RO,
    "siyi_notes_create": TOOL_LEVEL_RW,
    "siyi_tasks_list": TOOL_LEVEL_RO,
    "siyi_tasks_create": TOOL_LEVEL_RW,
    "siyi_inbox_list": TOOL_LEVEL_RO,
    "siyi_inbox_add": TOOL_LEVEL_RW,
    "siyi_tasks_toggle": TOOL_LEVEL_RW,
    "siyi_tasks_update": TOOL_LEVEL_RW,
    "siyi_tasks_search": TOOL_LEVEL_RO,
    "siyi_notes_get": TOOL_LEVEL_RO,
    "siyi_notes_update": TOOL_LEVEL_RW,
    "siyi_notes_tag": TOOL_LEVEL_RW,
    "siyi_inbox_update": TOOL_LEVEL_RW,
    "siyi_vocab_review_status": TOOL_LEVEL_RO,
    "siyi_vocab_quiz": TOOL_LEVEL_RW,
    "siyi_info_brief": TOOL_LEVEL_RO,
    "siyi_knowledge_query": TOOL_LEVEL_RO,
    "siyi_memory_recall": TOOL_LEVEL_RO,
    "siyi_inbox_classify": TOOL_LEVEL_RO,
    "siyi_news_list": TOOL_LEVEL_RO,
    "siyi_news_save": TOOL_LEVEL_RW,
    "siyi_notify": TOOL_LEVEL_RO,
    "siyi_memory_store": TOOL_LEVEL_RW,
    # 审批闭环
    "siyi_confirm_request": TOOL_LEVEL_RW,
    "siyi_confirm_status": TOOL_LEVEL_RO,
    # 桌面能力（risk=high，均 DANGER）
    "siyi_backup_snapshot": TOOL_LEVEL_DANGER,
    "siyi_desktop_screenshot": TOOL_LEVEL_DANGER,
    "siyi_clipboard_read": TOOL_LEVEL_DANGER,
    "siyi_clipboard_write": TOOL_LEVEL_DANGER,
    "siyi_app_launch": TOOL_LEVEL_DANGER,
    # 外部 CLI 工具桥（shell/exec）
    "siyi_tool_run": TOOL_LEVEL_DANGER,
    "siyi_tool_list": TOOL_LEVEL_RO,
}

# 启发式兜底关键词（供未知/新增工具）
_DANGER_HINTS = ("delete", "remove", "backup_snapshot", "app_launch", "shell",
                 "exec", "clipboard", "screenshot", "launch", "tool_run")
_RO_HINTS = ("get", "list", "search", "status", "health", "notify",
             "query", "recall", "brief", "classify", "read")


def classify_tool_level(tool_name: str) -> str:
    """按工具名启发式分类工具级别（缺声明视为 RW）。

    对齐 ``core/action_guard._classify_mcp_risk`` 的 low/medium/high 语义但独立实现：
    get/list/search/status → RO；create/update/set → RW；
    delete/backup_snapshot/app_launch/shell/exec → DANGER；health/notify → RO。
    已知工具走显式声明表（确定性），未知工具走关键词启发式兜底。
    """
    name = (tool_name or "").strip().lower()
    if not name:
        return DEFAULT_TOOL_LEVEL
    explicit = _EXPLICIT_LEVELS.get(name)
    if explicit:
        return explicit
    if any(hint in name for hint in _DANGER_HINTS):
        return TOOL_LEVEL_DANGER
    if any(hint in name for hint in _RO_HINTS):
        return TOOL_LEVEL_RO
    return DEFAULT_TOOL_LEVEL


def declare_tool_level(tool_name: str, level: str) -> None:
    """运行时登记工具级别（可覆盖默认启发式；供注册方显式声明）。"""
    level = (level or "").strip().upper()
    if level not in TOOL_LEVELS:
        logger.warning("declare_tool_level: 未知级别 %r for %r，视为 RW", level, tool_name)
        level = DEFAULT_TOOL_LEVEL
    _EXPLICIT_LEVELS[(tool_name or "").strip().lower()] = level


def is_explicitly_declared(tool_name: str) -> bool:
    """工具名是否在显式级别声明表中（否则走启发式兜底，视为缺声明）。"""
    return (tool_name or "").strip().lower() in _EXPLICIT_LEVELS


# ── settings 策略解析 ───────────────────────────────────────────────

_DEFAULT_POLICY: dict[str, Any] = {"mode": "allow_all"}


def parse_mcp_tool_policy(raw: Any) -> dict[str, Any]:
    """把 settings 里的 ``agent_kernel.mcp_tool_policy`` 原始值规范化为策略 dict。

    支持 dict / JSON 字符串 / None（回默认 allow_all）。坏值不崩溃，回默认。
    """
    cfg: dict[str, Any] = {}
    if isinstance(raw, str):
        raw = raw.strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    cfg = parsed
                else:
                    logger.warning("mcp_tool_policy JSON 非对象；使用默认 allow_all")
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("mcp_tool_policy JSON 解析失败（%s）；使用默认 allow_all", exc)
    elif isinstance(raw, dict):
        cfg = raw
    return _normalize_policy(cfg)


def _normalize_policy(policy: Any) -> dict[str, Any]:
    """规范化策略 dict：补齐默认字段、过滤非法值（防御式）。"""
    if not isinstance(policy, dict):
        return dict(_DEFAULT_POLICY)
    mode = str(policy.get("mode") or "allow_all").strip().lower()
    if mode not in ("allow_all", "restrict"):
        mode = "allow_all"
    normalized: dict[str, Any] = {"mode": mode}
    allow = policy.get("allow")
    deny = policy.get("deny")
    if isinstance(allow, (list, tuple, set)):
        normalized["allow"] = sorted({str(a).strip() for a in allow if str(a).strip()})
    if isinstance(deny, (list, tuple, set)):
        normalized["deny"] = sorted({str(d).strip() for d in deny if str(d).strip()})
    per_kernel = policy.get("per_kernel")
    if isinstance(per_kernel, dict):
        normalized["per_kernel"] = {
            str(k).strip().lower(): _normalize_policy(v)
            for k, v in per_kernel.items()
            if str(k).strip() and isinstance(v, dict)
        }
    return normalized


def _effective_policy(settings_policy: Any, kernel: str | None) -> dict[str, Any]:
    """取当前内核生效的策略：per_kernel[kernel] 与全局合并（per_kernel 补充全局）。

    - allow/deny：per_kernel 白名单**并入**全局（并集）。
    - mode：**仅当 per_kernel 显式指定**时覆盖全局，否则继承全局
      （per_kernel 只是白名单补充，不应把 restrict 悄悄改回 allow_all）。
    """
    base = _normalize_policy(settings_policy)
    if not kernel:
        return base
    raw = settings_policy if isinstance(settings_policy, dict) else {}
    per_kernel = raw.get("per_kernel")
    if not isinstance(per_kernel, dict):
        return base
    entry = per_kernel.get(str(kernel).strip().lower())
    if not isinstance(entry, dict):
        return base
    merged = dict(base)
    merged.pop("per_kernel", None)
    for key in ("allow", "deny"):
        values = entry.get(key)
        if isinstance(values, (list, tuple, set)):
            merged[key] = sorted(set(merged.get(key) or [])
                                 | {str(v).strip() for v in values if str(v).strip()})
    if "mode" in entry:
        mode = str(entry.get("mode") or "").strip().lower()
        if mode in ("allow_all", "restrict"):
            merged["mode"] = mode
    return merged


def resolve_policy(
    tool_level: str,
    settings_policy: Any,
    tool_name: str,
    kernel: str | None = None,
) -> str:
    """解析工具调用决策：``allow`` | ``deny`` | ``require_approval``。

    Args:
        tool_level: 工具级别 RO/RW/DANGER（缺省空视为 RW，告警日志）。
        settings_policy: ``agent_kernel.mcp_tool_policy`` 原始值（dict/JSON 串/None）。
        tool_name: 工具名（如 siyi_notes_delete）。
        kernel: 发起调用的内核名（per-kernel 白名单用；None=仅全局策略）。

    Returns:
        DECISION_ALLOW / DECISION_DENY / DECISION_REQUIRE_APPROVAL 之一。
    """
    level = (tool_level or "").strip().upper()
    if level not in TOOL_LEVELS:
        logger.warning("resolve_policy: 工具 %r 缺级别声明，视为 RW", tool_name)
        level = DEFAULT_TOOL_LEVEL
    name = (tool_name or "").strip().lower()
    policy = _effective_policy(settings_policy, kernel)
    mode = policy.get("mode", "allow_all")
    allow = set(policy.get("allow") or [])
    deny = set(policy.get("deny") or [])

    if name in deny:
        return DECISION_DENY
    if name in allow:
        return DECISION_ALLOW
    if mode != "allow_all":  # restrict：白名单外按级别裁决
        if level == TOOL_LEVEL_DANGER:
            return DECISION_REQUIRE_APPROVAL  # 非 allow_all 下 DANGER 必须走 ConfirmationStore
        if level == TOOL_LEVEL_RW:
            return DECISION_DENY  # 白名单外读写拦截（无副作用）
        return DECISION_ALLOW  # RO 只读放行
    return DECISION_ALLOW  # allow_all 全部放行（现状）


# ── 内核身份解析（--kernel 传参用）──────────────────────────────

# ``agent_kernel.type`` → 内核身份（与 ``services.agent_gateway`` 适配器 name 对齐，
# 即 per-kernel 白名单 ``per_kernel[<kernel>]`` 的键）。
_KERNEL_IDENTITY_MAP: dict[str, str] = {
    "dsh": "dsh",
    "claude_code": "claude_code",
    "codex": "codex",
}


def resolve_kernel_identity(agent_kernel_raw: Any) -> str:
    """从 ``agent_kernel`` 原始值解析当前生效内核身份（MCP ``--kernel`` 传参用）。

    - type ∈ {dsh, claude_code, codex} → 对应网关名（per-kernel 白名单键）。
    - type ∈ {none, 空} / 缺失 → ""（无内核：per-kernel 策略不生效，仅全局策略）。
    - type == auto → 独立进程内无法确定性探测活动内核 → ""（回退仅全局策略）。
    - 坏值 / JSON 解析失败 → ""（防御式，与现状默认一致）。

    与 backend 内 ``routing.kernel_type(context)``（读注入 gateway 的 name，单一事实
    来源）语义对齐；本函数供无法访问 context 的独立 MCP 进程回退使用。
    """
    cfg = agent_kernel_raw
    if isinstance(cfg, str):
        cfg = cfg.strip()
        if not cfg:
            return ""
        try:
            parsed = json.loads(cfg)
        except (json.JSONDecodeError, ValueError):
            return ""
        if not isinstance(parsed, dict):
            return ""
        cfg = parsed
    if not isinstance(cfg, dict):
        return ""
    ktype = str(cfg.get("type") or "").strip().lower()
    return _KERNEL_IDENTITY_MAP.get(ktype, "")


__all__ = [
    "TOOL_LEVEL_RO",
    "TOOL_LEVEL_RW",
    "TOOL_LEVEL_DANGER",
    "TOOL_LEVELS",
    "DEFAULT_TOOL_LEVEL",
    "DECISION_ALLOW",
    "DECISION_DENY",
    "DECISION_REQUIRE_APPROVAL",
    "classify_tool_level",
    "declare_tool_level",
    "is_explicitly_declared",
    "parse_mcp_tool_policy",
    "resolve_policy",
    "resolve_kernel_identity",
]
