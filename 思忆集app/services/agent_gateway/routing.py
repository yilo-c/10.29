"""执行内核路由与网关访问（Flip 可观测性共享层）。

services 层模块，供 backend.handlers.chat_paths（功能路径）与
services.system_health（健康子系统）共用，避免跨层 import。

本模块只做"读取路由设置"与"取已注入的 gateway 引用"，不负责
构建/探测（构建由 headless_main.build_gateway_from_settings_value 在
启动时完成并注入 context.agent_gateway）。
"""

from __future__ import annotations

from typing import Any

from services.agent_gateway.factory import (
    AGENT_GATEWAY_ADAPTERS,
    DEFAULT_AGENT_KERNEL_CONFIG,
    build_gateway,
    parse_agent_kernel_value,
)

SETTINGS_KEY_CHAT_ROUTE = "chat_route"

_KERNEL_TYPE_KEYS = ("siyi_kernel_type", "siyi_kernel")

# F 项（L1 per-request 内核选择）：settings 键 agent_kernel.per_request_selection
# （A1① 起默认 true；存量不含该键的配置由 core.settings_migration 一次性补键）
# 与 params.kernel 具体内核集合。
_SETTINGS_KEY_PER_REQUEST = "per_request_selection"
_PER_REQUEST_KERNELS: tuple[str, ...] = ("dsh", "claude_code", "codex")


def resolve_chat_route(context: Any) -> str:
    """读设置 ``chat_route``（legacy|gateway|auto）。缺省 ``legacy``（壳内管家）。

    - ``gateway``：显式全量走内核网关（灰度开关语义）。
    - ``auto``（2026-09-11 点火）：分层路由——复杂任务走内核、简单对话走壳内；
      复杂度分流在 ``_use_gateway_route`` L3（依据 ``_is_complex_task``）。
    - 其余/缺省：legacy 壳内管家。2026-08-16 的 Flip（缺省翻为 gateway）
      已于 2026-09 回翻。
    """
    repo = getattr(context, "settings_repository", None)
    if repo is None:
        return "legacy"
    try:
        raw = repo.get_setting(SETTINGS_KEY_CHAT_ROUTE, "legacy")
    except Exception:
        return "legacy"
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized == "gateway":
            return "gateway"
        if normalized == "auto":
            return "auto"
    return "legacy"


def _per_request_selection_enabled(context: Any) -> bool:
    """读 settings 开关 ``agent_kernel.per_request_selection``（默认 true，A1① 翻转）。

    关闭时完全走现状逻辑：params 被忽略、行为逐字节一致。
    ``cfg.get(..., False)`` 仅是迁移未跑时的极端防御兜底——存量缺键配置正常由
    core.settings_migration 一次性补 True，不代表用户关闭了开关。
    值类型防御：字符串 ``"false"``/``"0"``/``"no"`` 按 False 处理（避免非空字符串
    被 ``bool()`` 误判为 True），与配置面校验（_is_bool_like）语义对齐。
    """
    cfg = _read_agent_kernel_config(context)
    raw = cfg.get(_SETTINGS_KEY_PER_REQUEST, False)
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes", "on")
    return bool(raw)


def _build_temp_gateway(context: Any, kernel: str):
    """按 ``factory`` 适配器注册表临时构建指定内核网关。

    - 复用 settings 里 ``agent_kernel.<kernel>`` 子配置（model / timeout 等），
      仅覆盖 ``type`` 为目标内核。
    - 纯对象构造：**不写 settings、不污染注入状态**（context.agent_gateway 不动）。
    - 内核不在注册表（防御）时返回 ``None``，调用方回落到注入单网关。
    """
    cls = AGENT_GATEWAY_ADAPTERS.get(kernel)
    if cls is None:
        return None
    cfg = dict(_read_agent_kernel_config(context))
    cfg["type"] = kernel
    return build_gateway(cfg)


def get_gateway(context: Any, params: dict[str, Any] | None = None) -> Any:
    """返回本次请求应使用的执行内核网关；无/未启用返回 ``None``。

    F 项（L1 per-request 内核选择，settings 开关 ``agent_kernel.per_request_selection``
    默认 true，A1① 翻转）：
    - 开关关闭 → 完全走现状逻辑：params 被忽略、返回注入单网关。
    - 开关开启且 ``params.kernel ∈ {dsh, claude_code, codex}`` → 按 factory 适配器
      注册表临时构建对应内核网关（不写 settings / 不污染注入状态）。
    - ``params.kernel ∈ {fast, deep}`` → 走现有 ``resolve_dsh_profile`` 路径
      （返回注入网关，其 submit profile 由调用方映射）。
    - 无 / ``auto`` / 其他 → 返回现状注入单网关（行为与现状一致）。
    """
    if _per_request_selection_enabled(context) and params:
        kernel = str(params.get("kernel") or "").strip().lower()
        if kernel in _PER_REQUEST_KERNELS:
            tmp = _build_temp_gateway(context, kernel)
            if tmp is not None:
                return tmp
    # 现状逻辑：开关关闭 / 非具体内核请求
    gw = getattr(context, "agent_gateway", None)
    if gw is None:
        return None
    if getattr(gw, "name", "") == "none":
        return None
    return gw


def kernel_type(context: Any) -> str:
    """读取实际生效的执行内核类型。

    优先取已注入 gateway 的 name（auto/dsh/claude_code/codex/none）——
    这是单一事实来源（settings 热换后立即反映）。旧键
    ``siyi_kernel_type`` / ``siyi_kernel`` 仅作历史兜底。
    """
    gw = getattr(context, "agent_gateway", None)
    if gw is not None:
        name = str(getattr(gw, "name", "") or "").strip()
        if name:
            return name
    repo = getattr(context, "settings_repository", None)
    if repo is None:
        return "none"
    for key in _KERNEL_TYPE_KEYS:
        try:
            val = repo.get_setting(key, "")
        except Exception:
            val = ""
        if isinstance(val, str) and val.strip():
            return val.strip()
    return "none"


_PROFILE_DEFAULTS: dict[str, str] = {"fast": "siyi-fast", "deep": "siyi-deep"}


def _read_agent_kernel_config(context: Any) -> dict:
    """读 settings 的 agent_kernel 并解析为完整配置；异常时返回空 dict。"""
    repo = getattr(context, "settings_repository", None)
    if repo is None:
        return {}
    try:
        return parse_agent_kernel_value(repo.get_setting("agent_kernel", None))
    except Exception:
        return {}


def _default_dsh_profile(context: Any) -> str:
    """取配置 dsh.profile，缺失时回 DEFAULT（headless）——与观察期路径一致。"""
    dsh = _read_agent_kernel_config(context).get("dsh") or {}
    profile = dsh.get("profile") if isinstance(dsh, dict) else None
    return str(profile) if profile else str(DEFAULT_AGENT_KERNEL_CONFIG["dsh"]["profile"])


def resolve_dsh_profile(context: Any, params: dict[str, Any] | None = None) -> str:
    """按 params.kernel 解析目标 DSH profile（契约 §2.4 成本分级）。

    - kernel == "deep" -> profiles.deep.dsh_profile（默认 siyi-deep）
    - kernel == "fast" -> profiles.fast.dsh_profile（默认 siyi-fast）
    - 其他（无/空/shell/legacy/未知）-> dsh.profile（默认 headless）

    默认分支回 headless，与观察期已验证路径一致（不引入行为变化）。
    """
    params = params or {}
    kernel = str(params.get("kernel") or "").strip().lower()
    if kernel not in ("fast", "deep"):
        return _default_dsh_profile(context)
    mapped = (_read_agent_kernel_config(context).get("profiles") or {}).get(kernel) or {}
    dsh_profile = mapped.get("dsh_profile") if isinstance(mapped, dict) else None
    if dsh_profile:
        return str(dsh_profile)
    return _PROFILE_DEFAULTS.get(kernel, _default_dsh_profile(context))
