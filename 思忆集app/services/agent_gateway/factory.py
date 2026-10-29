"""AgentGateway 注册表与配置工厂（M1）。

**换内核的唯一改动点**：settings 键 ``agent_kernel`` 的 ``type`` 字段。

配置示例（settings 中存为 JSON 字符串或 dict）::

    {
      "type": "dsh",                      # none | dsh | claude_code | codex
      "dsh": {"profile": "headless", "timeout_s": 240},
      "claude_code": {"model": null, "max_turns": 8},
      "codex": {"model": null}
    }

工厂职责：
* ``parse_agent_kernel_value(raw)`` —— dict / JSON 字符串 / None → 合并默认后的配置
* ``build_gateway(config)``         —— 按 ``type`` 实例化对应适配器
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .adapters import ClaudeCodeGateway, CodexGateway, DshGateway, NoneGateway, adapter_kwargs
from .auto import DEFAULT_CANDIDATE_ORDER, AutoGateway
from .protocol import AgentGateway

logger = logging.getLogger("siyi.agent_gateway.factory")

SETTINGS_KEY = "agent_kernel"

AGENT_GATEWAY_ADAPTERS: dict[str, type] = {
    "none": NoneGateway,
    "dsh": DshGateway,
    "claude_code": ClaudeCodeGateway,
    "codex": CodexGateway,
}

DEFAULT_AGENT_KERNEL_CONFIG: dict[str, Any] = {
    "type": "dsh",
    "dsh": {"profile": "headless", "timeout_s": 240},
    "claude_code": {"model": None, "max_turns": 8, "timeout_s": 240},
    "codex": {"model": None, "timeout_s": 240},
    # auto 增强：候选顺序 + 失败切换阈值（type=auto 时生效）
    "auto": {"candidates": list(DEFAULT_CANDIDATE_ORDER), "max_failures": 2},
}


def parse_agent_kernel_value(raw: Any) -> dict[str, Any]:
    """把 settings 里的原始值（dict / JSON 字符串 / 空）合并成完整配置。"""
    cfg: dict[str, Any] = {}
    if isinstance(raw, str):
        raw = raw.strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    cfg = parsed
                else:
                    logger.warning("agent_kernel JSON is not an object; using defaults")
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("agent_kernel JSON parse failed (%s); using defaults", exc)
    elif isinstance(raw, dict):
        cfg = raw
    merged: dict[str, Any] = {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULT_AGENT_KERNEL_CONFIG.items()}
    for key, value in cfg.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def build_gateway(config: dict[str, Any] | None = None) -> AgentGateway:
    """按配置实例化 AgentGateway；未知 type 回退 none（降级而非崩溃）。

    ``type="auto"`` 返回 AutoGateway：启动时探测可用内核并选定当前内核，
    连续失败自动切换到下一个候选（见 auto.py）。
    """
    cfg = parse_agent_kernel_value(config if config is not None else {})
    ktype = str(cfg.get("type", "dsh")).strip().lower()
    if ktype == "auto":
        auto_cfg = cfg.get("auto") or {}
        candidates = auto_cfg.get("candidates") or list(DEFAULT_CANDIDATE_ORDER)
        max_failures = int(auto_cfg.get("max_failures", 2))
        gateway: AgentGateway = AutoGateway(
            candidates=[str(c) for c in candidates],
            cfg=cfg,
            max_failures=max_failures,
        )
        logger.info("AgentGateway built: kernel=auto (active=%s)", gateway.health().get("active"))
        return gateway
    cls = AGENT_GATEWAY_ADAPTERS.get(ktype)
    if cls is None:
        logger.warning("unknown agent_kernel.type=%r; falling back to none", ktype)
        return NoneGateway()
    kwargs = adapter_kwargs(cls, cfg.get(ktype) or {})
    gateway = cls(**kwargs)
    logger.info("AgentGateway built: kernel=%s", gateway.name)
    return gateway


def build_gateway_from_settings_value(raw: Any) -> AgentGateway:
    """从 settings 原始值直接构建（headless_main 注入用）。"""
    return build_gateway(parse_agent_kernel_value(raw))


def _resolve_provider_env_refs(raw_env: dict, repo: Any) -> dict[str, str]:
    """解析 dsh ``provider_env`` 配置中的密钥引用 → 明文 env 值（方案 B 注入源）。

    settings 中不存明文密钥，值支持三种引用形态：
    * ``flat:<key>``   —— settings 扁平键（经 SecretResolver 四段式解析，如
      ``flat:llm_api_key``，优先环境变量再加密存储）
    * ``provider:<name>`` —— provider_configs 条目的 api_key（解密后注入，
      如 ``provider:DeepSeek``）
    * 其他            —— 明文字面量原样透传

    单个引用解析失败只告警跳过该键，不中断网关构建（降级而非崩溃）。
    """
    if not isinstance(raw_env, dict) or not raw_env:
        return {}
    resolver = None
    if repo is not None:
        try:
            from core.secret_resolver import SecretResolver

            data_dir = getattr(getattr(repo, "data_store", None), "base_dir", None)
            resolver = SecretResolver(settings_repository=repo, data_dir=data_dir)
        except Exception:
            logger.warning("SecretResolver 构建失败，provider_env 引用按字面量处理", exc_info=True)
    resolved: dict[str, str] = {}
    for env_name, raw in raw_env.items():
        value = str(raw)
        try:
            if value.startswith("flat:") and resolver is not None:
                resolved[str(env_name)] = resolver.resolve(value[len("flat:"):])
            elif value.startswith("provider:") and resolver is not None:
                resolved[str(env_name)] = _resolve_provider_key(resolver, repo, value[len("provider:"):])
            else:
                resolved[str(env_name)] = value
        except Exception:
            logger.warning("provider_env[%s] 引用解析失败，跳过该注入项", env_name, exc_info=True)
    return resolved


def _resolve_provider_key(resolver: Any, repo: Any, provider_name: str) -> str:
    """从 provider_configs 设置中找到 name 匹配的条目并解密其 api_key。"""
    from core.settings_schema import KEY_PROVIDER_CONFIGS

    raw_list = repo.get_setting(KEY_PROVIDER_CONFIGS, []) or []
    entry = next((p for p in raw_list if isinstance(p, dict) and p.get("name") == provider_name), None)
    if entry is None:
        logger.warning("provider_env 引用了不存在的 provider %r，跳过注入", provider_name)
        return ""
    return resolver.resolve_provider_key(entry)


def build_gateway_from_repository(repo: Any, cli_path: str = "") -> AgentGateway:
    """从 settings_repository 构建（headless_main 启动注入 + settings.set 热换共用）。

    读取 ``agent_kernel`` 设置并合并 ``kernel_cli_path`` 配置锚点。
    repo 为 None 或读取失败时按默认配置构建（不崩溃）。
    """
    try:
        raw_cfg = repo.get_setting(SETTINGS_KEY, "") if repo is not None else ""
    except Exception:
        raw_cfg = ""
    cfg = parse_agent_kernel_value(raw_cfg)
    try:
        resolved_cli = repo.get_setting("kernel_cli_path", "") if repo is not None else ""
    except Exception:
        resolved_cli = ""
    if resolved_cli:
        cfg.setdefault("dsh", {})["cli_path"] = resolved_cli
    if cli_path:
        cfg.setdefault("dsh", {})["cli_path"] = cli_path
    dsh_cfg = cfg.get("dsh") or {}
    if "provider_env" in dsh_cfg:
        resolved_env = _resolve_provider_env_refs(dsh_cfg.get("provider_env") or {}, repo)
        if resolved_env:
            cfg.setdefault("dsh", {})["provider_env"] = resolved_env
        else:
            cfg["dsh"].pop("provider_env", None)
    return build_gateway(cfg)
