"""AI 服务配置解析与工厂函数。

从 ai_service.py 提取，职责：
- 三层配置解析（primary / secondary / local / local_coder）
- 客户端工厂函数

P2-5 批次 C：
* 4 个 ``resolve_*_openai_compatible_config`` 几乎逐字复制；现在共享 ``_LayerSpec``
  + ``_resolve_layer`` 实现，函数体收敛到 1 行 + 1 个 spec 实例。
* ``create_client_from_provider_config`` 用 ``ProviderLike`` Protocol 取代 hasattr
  duck typing 校验。
* 顺手补全显式 ``Any`` import（之前依赖 ``from __future__ import annotations``
  绕过未导入名）。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Iterable, Protocol, runtime_checkable

from core.settings_keys import (
    KEY_LLM_API_BASE,
    KEY_LLM_API_KEY,
    KEY_LLM_FALLBACK_API_BASE,
    KEY_LLM_FALLBACK_API_KEY,
    KEY_LLM_FALLBACK_MODEL,
    KEY_LLM_LOCAL_API_BASE,
    KEY_LLM_LOCAL_API_KEY,
    KEY_LLM_LOCAL_MODEL,
    KEY_LLM_LOCAL_CODER_API_BASE,
    KEY_LLM_LOCAL_CODER_API_KEY,
    KEY_LLM_LOCAL_CODER_MODEL,
    KEY_LLM_MODEL,
    KEY_LLM_TIMEOUT,
)
from ai.types import OpenAICompatibleConfig
from .base import OpenAICompatibleClient

logger = logging.getLogger("siyi.ai.config")


# ── 配置解析 ──────────────────────────────────────────


@dataclass(frozen=True)
class _LayerSpec:
    """单层 LLM 配置的「源信息」声明（环境变量前缀 + settings 键名 + 默认值）。

    解析逻辑 ``_resolve_layer`` 完全由 spec 驱动，避免 4 个层各写一份近似复制。
    """

    # 环境变量名（按优先级遍历）；命中第一个非空者
    env_api_key: tuple[str, ...]
    env_base_url: tuple[str, ...]
    env_model: tuple[str, ...]
    env_timeout: str
    # settings 键名（兜底）
    setting_api_key: str
    setting_base_url: str
    setting_model: str
    # 默认值（settings 也读不到时使用）
    default_base_url: str | None
    default_model: str | None
    default_timeout: int
    # 是否在使用扁平 LLM 键时打 warning（仅主层启用）
    warn_legacy: bool = False


_PRIMARY_SPEC = _LayerSpec(
    # AGENTS.md 文档的环境变量 DEEPSEEK_API_KEY 也作为回退源，
    # 避免用户已设置该变量但未设置 SIYI_LLM_API_KEY 时 api_key 解析为空 → 401。
    env_api_key=("SIYI_LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"),
    env_base_url=("SIYI_LLM_BASE_URL", "OPENAI_BASE_URL"),
    env_model=("SIYI_LLM_MODEL", "OPENAI_MODEL"),
    env_timeout="SIYI_LLM_TIMEOUT",
    setting_api_key=KEY_LLM_API_KEY,
    setting_base_url=KEY_LLM_API_BASE,
    setting_model=KEY_LLM_MODEL,
    default_base_url="https://api.deepseek.com/v1",
    default_model="deepseek-v4-flash",
    default_timeout=300,
    warn_legacy=True,
)

_SECONDARY_SPEC = _LayerSpec(
    env_api_key=("SIYI_LLM_FALLBACK_API_KEY", "OPENAI_FALLBACK_API_KEY"),
    env_base_url=("SIYI_LLM_FALLBACK_BASE_URL", "OPENAI_FALLBACK_BASE_URL"),
    env_model=("SIYI_LLM_FALLBACK_MODEL", "OPENAI_FALLBACK_MODEL"),
    env_timeout="SIYI_LLM_FALLBACK_TIMEOUT",
    setting_api_key=KEY_LLM_FALLBACK_API_KEY,
    setting_base_url=KEY_LLM_FALLBACK_API_BASE,
    setting_model=KEY_LLM_FALLBACK_MODEL,
    default_base_url=None,
    default_model=None,
    default_timeout=60,
)

_LOCAL_SPEC = _LayerSpec(
    env_api_key=("SIYI_LLM_LOCAL_API_KEY", "OPENAI_LOCAL_API_KEY"),
    env_base_url=("SIYI_LLM_LOCAL_BASE_URL", "OPENAI_LOCAL_BASE_URL"),
    env_model=("SIYI_LLM_LOCAL_MODEL", "OPENAI_LOCAL_MODEL"),
    env_timeout="SIYI_LLM_LOCAL_TIMEOUT",
    setting_api_key=KEY_LLM_LOCAL_API_KEY,
    setting_base_url=KEY_LLM_LOCAL_API_BASE,
    setting_model=KEY_LLM_LOCAL_MODEL,
    default_base_url=None,
    default_model=None,
    default_timeout=60,
)

_LOCAL_CODER_SPEC = _LayerSpec(
    env_api_key=("SIYI_LLM_LOCAL_CODER_API_KEY", "OPENAI_LOCAL_CODER_API_KEY"),
    env_base_url=("SIYI_LLM_LOCAL_CODER_BASE_URL", "OPENAI_LOCAL_CODER_BASE_URL"),
    env_model=("SIYI_LLM_LOCAL_CODER_MODEL", "OPENAI_LOCAL_CODER_MODEL"),
    env_timeout="SIYI_LLM_LOCAL_CODER_TIMEOUT",
    setting_api_key=KEY_LLM_LOCAL_CODER_API_KEY,
    setting_base_url=KEY_LLM_LOCAL_CODER_API_BASE,
    setting_model=KEY_LLM_LOCAL_CODER_MODEL,
    default_base_url=None,
    default_model=None,
    default_timeout=60,
)


def _first_env(environ: Any, keys: Iterable[str]) -> str | None:
    for k in keys:
        val = environ.get(k)
        if val:
            return val
    return None


def _decrypt_setting_value(value: Any, key_name: str = "") -> str:
    """解密 settings 中的 API key：支持新版安全存储 token 与旧版 Fernet token。

    P1.8: 通过 SecretResolver 统一入口，复用全局 SecureKeyStorage 单例，
    避免每次调用都新建实例。key_name 用于旧版 Fernet / 明文的透明迁移
    （仅在 _resolve_layer 中传入 spec.setting_api_key 时生效）。
    """
    if not isinstance(value, str) or not value:
        return value
    from core.secret_resolver import get_default_resolver

    resolver = get_default_resolver()
    # 此处不传 migrate=True 的 settings_repository，避免在解析阶段触发写回；
    # 迁移由 ApiKeyService / load_providers 负责（它们持有 settings_repository）。
    return resolver.decrypt_token(value, key_name=key_name, migrate=False)


def _resolve_layer(
    spec: _LayerSpec,
    settings_repository: Any = None,
    environ: Any = None,
) -> OpenAICompatibleConfig:
    """通用层解析：环境变量优先 → settings 兜底 → 默认值。"""
    # P1.8 修复：使用 `is None` 判断而非 `or`，避免 caller 显式传入空 dict 时
    # 被当作 falsy 回退到 os.environ，导致 DEEPSEEK_API_KEY 等系统环境变量
    # 在测试或显式禁用 env 时仍被错误读取。
    resolved_environ = os.environ if environ is None else environ
    api_key = _first_env(resolved_environ, spec.env_api_key)
    base_url = _first_env(resolved_environ, spec.env_base_url)
    model = _first_env(resolved_environ, spec.env_model)
    timeout_value = resolved_environ.get(spec.env_timeout)

    env_key_hit = bool(_first_env(resolved_environ, spec.env_api_key))

    if settings_repository is not None:
        api_key = api_key or settings_repository.get_setting(spec.setting_api_key, "")
        base_url = base_url or settings_repository.get_setting(spec.setting_base_url, "")
        model = model or settings_repository.get_setting(spec.setting_model, "")
        if spec.warn_legacy and api_key and not env_key_hit:
            logger.warning("使用已弃用的扁平 LLM 键配置，请迁移至 provider_configs")
        timeout_value = timeout_value or settings_repository.get_setting(KEY_LLM_TIMEOUT, spec.default_timeout)

    # 解密可能的安全存储 token 或旧版 Fernet token
    api_key = _decrypt_setting_value(api_key, key_name=spec.setting_api_key)

    normalized_api_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
    normalized_base_url = base_url.strip().rstrip("/") if isinstance(base_url, str) and base_url.strip() else spec.default_base_url
    normalized_model = model.strip() if isinstance(model, str) and model.strip() else spec.default_model
    try:
        normalized_timeout = int(timeout_value or spec.default_timeout)
    except (TypeError, ValueError):
        normalized_timeout = spec.default_timeout

    return OpenAICompatibleConfig(
        api_key=normalized_api_key,
        base_url=normalized_base_url,
        model=normalized_model,
        timeout=max(15, normalized_timeout),
    )


def resolve_openai_compatible_config(settings_repository=None, environ=None) -> OpenAICompatibleConfig:
    """解析主 AI 服务配置：环境变量优先，设置兜底。"""
    return _resolve_layer(_PRIMARY_SPEC, settings_repository, environ)


def resolve_secondary_openai_compatible_config(settings_repository=None, environ=None) -> OpenAICompatibleConfig:
    """解析 fallback AI 服务配置。应与主服务不同（不同的 API Key / provider），否则回退无意义。"""
    return _resolve_layer(_SECONDARY_SPEC, settings_repository, environ)


def resolve_local_openai_compatible_config(settings_repository=None, environ=None) -> OpenAICompatibleConfig:
    """解析本地 AI 服务配置（Ollama / 本地推理）。"""
    return _resolve_layer(_LOCAL_SPEC, settings_repository, environ)


def resolve_local_coder_openai_compatible_config(settings_repository=None, environ=None) -> OpenAICompatibleConfig:
    """解析本地 Coder AI 服务配置（qwen2.5-coder / 代码专用模型）。"""
    return _resolve_layer(_LOCAL_CODER_SPEC, settings_repository, environ)


# ── Provider 接口（替代 hasattr duck typing）──────────


@runtime_checkable
class ProviderLike(Protocol):
    """``create_client_from_provider_config`` 接受的最小化 provider 接口。

    在不引入对 ``core.provider_config.ProviderConfig`` 的硬依赖（保留向后兼容）
    的前提下，让类型注解清晰可读；任何同形状的对象都能被 ``runtime_checkable``
    校验通过。
    """

    api_key: str
    api_base: str
    model: str
    timeout: int

    def get_all_models(self) -> list[str]: ...


# ── 工厂函数 ──────────────────────────────────────────


# 已知有效的 DeepSeek 模型名集合，用于在启动期对 provider_configs 做合理性校验。
# 2026-07-24 起 DeepSeek 官方停用 deepseek-chat/reasoner，正式 API 使用 v4 系列
# （deepseek-v4-flash 已于 2026-07-31 上线，deepseek-v4-pro 即将发布）。
_VALID_DEEPSEEK_MODELS: frozenset[str] = frozenset(
    {
        "deepseek-chat",
        "deepseek-reasoner",
        "deepseek-coder",
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    }
)


def validate_provider_configs(providers) -> dict[str, list[str]]:
    """在启动期对 provider_configs 做合理性校验并记录日志。

    检查项：
      1. api_base 中含 ``api.deepseek.com`` 时是否带 ``/v1`` 后缀（缺少会 404 / 400）。
      2. 已知 provider（DeepSeek）使用了不存在的 model 名（例如曾经写错的
         ``deepseek-v4-flash`` / ``deepseek-v4-pro``）。

    校验只做日志输出（WARNING / ERROR），不修改用户 settings。

    Returns:
        dict 包含 ``warnings`` / ``errors`` 列表，便于上层暴露给前端或测试用。
    """
    warnings: list[str] = []
    errors: list[str] = []
    if not providers:
        return {"warnings": warnings, "errors": errors}

    for p in providers:
        try:
            name = getattr(p, "name", "?") or "?"
            api_base = (getattr(p, "api_base", "") or "").strip()
            model = (getattr(p, "model", "") or "").strip()
            is_local = bool(getattr(p, "is_local", False))
            enabled = bool(getattr(p, "enabled", True))
            if not enabled:
                continue

            # 1. DeepSeek api_base 缺 /v1
            lowered = api_base.rstrip("/").lower()
            if "api.deepseek.com" in lowered and not lowered.endswith("/v1"):
                msg = f"provider '{name}' api_base 缺少 /v1 后缀（当前: {api_base}），运行时会自动补全，但建议在 UI 中改正"
                logger.warning(msg)
                warnings.append(msg)

            # 2. 模型名非法（仅校验我们已知的 DeepSeek 提供商）
            if not is_local and "deepseek" in lowered and model and model not in _VALID_DEEPSEEK_MODELS:
                msg = (
                    f"provider '{name}' 使用了未知的 DeepSeek 模型 '{model}'，"
                    f"运行时将被回退为 'deepseek-v4-flash'。请在 UI 中修正为 "
                    f"{sorted(_VALID_DEEPSEEK_MODELS)} 之一"
                )
                logger.error(msg)
                errors.append(msg)

            # 3. 启用但缺 api_key（非本地）
            if not is_local and not (getattr(p, "api_key", "") or "").strip():
                msg = f"provider '{name}' 已启用但缺少 api_key，云端调用将失败"
                logger.warning(msg)
                warnings.append(msg)
        except Exception as exc:
            logger.debug("validate_provider_configs: skip provider due to %s", exc)

    if not warnings and not errors:
        logger.info("provider_configs 校验通过（%d 个 provider）", len(providers))
    return {"warnings": warnings, "errors": errors}


def create_openai_compatible_client(
    settings_repository=None,
    api_key=None,
    base_url=None,
    model=None,
    timeout=None,
    session=None,
):
    """构建 OpenAICompatibleClient，配置来自参数或设置。"""
    from .base import OpenAICompatibleClient

    config = resolve_openai_compatible_config(settings_repository=settings_repository)
    return OpenAICompatibleClient(
        api_key=api_key if api_key is not None else config.api_key,
        api_base=base_url or config.base_url,
        model=model or config.model,
        timeout=timeout or config.timeout,
    )


def create_judge_aware_client(settings_repository=None, judge=None, privacy_filter=None, receipt_callback=None):
    """创建包装了 Judge 路由能力的客户端。"""
    inner = create_fallback_client(settings_repository=settings_repository, privacy_filter=privacy_filter, receipt_callback=receipt_callback)
    from ._judge_aware_client import JudgeAwareClient

    return JudgeAwareClient(
        inner,
        settings_repository=settings_repository,
        judge=judge,
    )


def _provider_to_client(cfg: dict, default_key: str = "", default_model: str = "gpt-4o", default_timeout: int = 300):
    """将 provider 配置 dict 转为 OpenAICompatibleClient。"""
    from .base import OpenAICompatibleClient

    return OpenAICompatibleClient(
        api_key=cfg.get("api_key", "") or default_key,
        api_base=cfg.get("api_base", ""),
        model=cfg.get("model", "") or default_model,
        timeout=int(cfg.get("timeout", default_timeout) or default_timeout),
    )


def is_ai_cloud_only(settings_repository=None) -> bool:
    """云化模式判定：AI 全链路走云端，禁用本地腿路由。

    用户决策（2026-08-30）：本地基本不用、敏感信息未上传，全改为云端。
    判定优先级：环境变量 SIYI_AI_CLOUD_ONLY > 设置键 ai_cloud_only（KEY_AI_CLOUD_ONLY）。
    默认 False（关闭）→ 保持既有隐私守卫/本地腿行为。
    """
    env = os.environ.get("SIYI_AI_CLOUD_ONLY")
    if env is not None and str(env).strip().lower() not in ("", "0", "false", "none"):
        return True
    if settings_repository is None:
        return False
    try:
        from core.settings_keys import KEY_AI_CLOUD_ONLY

        val = settings_repository.get_setting(KEY_AI_CLOUD_ONLY, False)
    except Exception:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    s = str(val).strip().lower()
    return s not in ("", "0", "false", "none")


def is_ai_local_first(settings_repository=None) -> bool:
    """本地优先判定：本地模型先行、云端兜底（用户决策 2026-09-02 的未来开关）。

    当前默认 False = 维持现状（云端优先，本地第三顺位兜底）——这是用户 2026-09-02
    拍板的有意决策（本地 7B 能力不足、隐私内容未上云，云端干活优先）。
    将来部署了更强的本地模型后，翻转设置键 ai_local_first（KEY_AI_LOCAL_FIRST）
    或设置环境变量 SIYI_AI_LOCAL_FIRST 即可切换，无需改代码。

    判定优先级：环境变量 SIYI_AI_LOCAL_FIRST > 设置键 ai_local_first。
    与 cloud_only 的关系：local_first 优先级更高（两者同开时 local_first 胜出，
    调用方应先查 local_first 再查 cloud_only）。
    默认 False（关闭）→ 行为与历史版本逐字节一致。
    """
    env = os.environ.get("SIYI_AI_LOCAL_FIRST")
    if env is not None and str(env).strip().lower() not in ("", "0", "false", "none"):
        return True
    if settings_repository is None:
        return False
    try:
        from core.settings_keys import KEY_AI_LOCAL_FIRST

        val = settings_repository.get_setting(KEY_AI_LOCAL_FIRST, False)
    except Exception:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    s = str(val).strip().lower()
    return s not in ("", "0", "false", "none")


def create_fallback_client(
    settings_repository=None,
    local_base_url: str | None = None,
    local_api_key: str | None = None,
    local_coder: OpenAICompatibleClient | None = None,
    cache_path: str | None = None,
    cost_tracker=None,
    budget_manager=None,
    privacy_filter=None,
    receipt_callback=None,
):
    """构建带回退的客户端。

    优先从 provider_configs 读取配置（主/备/本地），
    没有 provider 配置时回退到旧版 llm_api_* 单配置。
    """
    from .base import OpenAICompatibleClient
    from .fallback import FallbackOpenAIClient
    from core.provider_config import load_providers

    primary: OpenAICompatibleClient | None = None
    fallback_client: OpenAICompatibleClient | None = None
    local_client: OpenAICompatibleClient | None = None
    vision_client: OpenAICompatibleClient | None = None

    if settings_repository is not None:
        # AG 裁决（2026-09-13，fallback flat-key 语义）：用户显式写下的旧式 flat 键
        # （llm_api_base / llm_api_key 任一非空）优先于 load_providers 的播种默认
        # ——播种只为 model.list 不为空，不得劫持客户端工厂的 primary 选择。
        # 优先级：显式 provider_configs > 显式 flat 键 > 播种默认 + env 兜底。
        from core.settings_schema import KEY_PROVIDER_CONFIGS
        raw_providers = settings_repository.get_setting(KEY_PROVIDER_CONFIGS, [])
        flat_explicit = bool(
            settings_repository.get_setting(KEY_LLM_API_BASE, "")
            or _decrypt_setting_value(
                settings_repository.get_setting(KEY_LLM_API_KEY, ""),
                key_name=KEY_LLM_API_KEY,
            )
        )
        use_provider_branch = bool(raw_providers) or not flat_explicit
        providers = load_providers(settings_repository) if use_provider_branch else []

        # 启动优化：Kiro 自动发现改为运行时按需探测。
        # create_fallback_client 在启动阶段被同步调用，此处同步 HTTP 探测会导致
        # Kiro 未运行时阻塞 2 秒。Kiro 的实际使用点在 JudgeAwareClient 路由到
        # ROUTE_EXECUTOR_KIRO 时，会调用 create_kiro_client() 再次探测（带 60s
        # 缓存），因此启动阶段无需预先注册。若用户已显式在 provider_configs 中
        # 配置 Kiro，则保留原有行为。
        if providers:
            # 优先选择有 API key 的主 provider，避免选了无 key 的默认条目
            # 注意：is_local=True 的 provider 不能作为 primary（primary 语义上代表云端）
            primary_cfg = next(
                (p for p in providers if p.enabled and not p.is_fallback and not p.is_local and p.api_key),
                next(
                    (p for p in providers if p.enabled and not p.is_fallback and not p.is_local),
                    None,
                ),
            )
            fallback_cfg = next((p for p in providers if p.enabled and p.is_fallback), None)
            # 本地文本 provider：优先排除 purpose=vision 的专用视觉 provider，
            # 避免视觉模型被误选为文本回退客户端
            local_cfg = next(
                (p for p in providers if p.enabled and p.is_local and p.purpose != "vision"),
                next((p for p in providers if p.enabled and p.is_local), None),
            )
            # purpose=vision 的专用视觉 provider（可为本地 Ollama 视觉模型或云端 VLM）
            vision_cfg = next(
                (p for p in providers if p.enabled and p.purpose == "vision" and p.api_base),
                None,
            )

            if primary_cfg:
                # Fall back to flat keys when provider_configs fields are empty
                resolved_key = primary_cfg.api_key
                resolved_base = primary_cfg.api_base
                resolved_model = primary_cfg.model or "gpt-4o"
                # 归一化 DeepSeek api_base：缺少 /v1 时自动补全
                if resolved_base and "api.deepseek.com" in resolved_base.rstrip("/").lower():
                    if not resolved_base.rstrip("/").endswith("/v1"):
                        resolved_base = resolved_base.rstrip("/") + "/v1"
                        logger.info("自动补全 DeepSeek api_base /v1 后缀 → %s", resolved_base)
                # 归一化 DeepSeek 模型名：未知模型回退到 deepseek-v4-flash
                # （2026-07-24 起旧名 deepseek-chat/reasoner 已停用，回退到已停用名会导致 API 调用失败）
                if resolved_base and "api.deepseek.com" in resolved_base.lower() and resolved_model:
                    if resolved_model not in _VALID_DEEPSEEK_MODELS:
                        logger.warning("未知 DeepSeek 模型 '%s'，回退为 'deepseek-v4-flash'", resolved_model)
                        resolved_model = "deepseek-v4-flash"
                if (not resolved_key or not resolved_base) and settings_repository is not None:
                    flat_key = _decrypt_setting_value(
                        settings_repository.get_setting("llm_api_key", ""),
                        key_name="llm_api_key",
                    )
                    flat_base = settings_repository.get_setting("llm_api_base", "")
                    if flat_key and not resolved_key:
                        logger.warning("正在使用已弃用的 llm_api_key 配置，请迁移至 provider_configs")
                        resolved_key = flat_key
                    if flat_base and not resolved_base:
                        logger.warning("正在使用已弃用的 llm_api_base 配置，请迁移至 provider_configs")
                        resolved_base = flat_base
                    if not resolved_model or resolved_model == "gpt-4o":
                        flat_model = settings_repository.get_setting("llm_model", "")
                        if flat_model:
                            resolved_model = flat_model
                primary = OpenAICompatibleClient(
                    api_key=resolved_key,
                    api_base=resolved_base,
                    model=resolved_model,
                    timeout=primary_cfg.timeout or 300,
                )
            if fallback_cfg and primary_cfg:
                fb_base = fallback_cfg.api_base or resolved_base
                fb_model = fallback_cfg.model or resolved_model
                if fb_base and "api.deepseek.com" in fb_base.rstrip("/").lower():
                    if not fb_base.rstrip("/").endswith("/v1"):
                        fb_base = fb_base.rstrip("/") + "/v1"
                    if fb_model not in _VALID_DEEPSEEK_MODELS:
                        fb_model = "deepseek-v4-flash"
                fallback_client = OpenAICompatibleClient(
                    api_key=fallback_cfg.api_key or resolved_key,
                    api_base=fb_base,
                    model=fb_model,
                    timeout=fallback_cfg.timeout or 300,
                )
            if local_cfg:
                resolved_local_base = local_base_url or local_cfg.api_base
                if resolved_local_base:
                    local_client = OpenAICompatibleClient(
                        api_key=local_api_key or local_cfg.api_key or "",
                        api_base=resolved_local_base,
                        model=local_cfg.model or (primary_cfg.model if primary_cfg else "qwen3-4b"),
                        timeout=local_cfg.timeout or 60,
                        # 本地 AI（如 Ollama）未运行时连接被拒绝是持久性错误，
                        # 不需要 3 次重试（默认 2+4+8=14s 浪费），1 次足矣
                        max_retries=1,
                    )
            if vision_cfg:
                vision_client = OpenAICompatibleClient(
                    api_key=vision_cfg.api_key or "",
                    api_base=vision_cfg.api_base,
                    model=vision_cfg.model or "qwen2.5vl",
                    timeout=vision_cfg.timeout or 300,
                    # 本地视觉模型（Ollama）未运行时重试无意义
                    max_retries=1 if vision_cfg.is_local else 3,
                )
                logger.info(
                    "vision provider 已配置: name=%s model=%s local=%s",
                    vision_cfg.name,
                    vision_cfg.model,
                    vision_cfg.is_local,
                )

    # 回退：没有（或缺少主） provider 配置时使用旧版单配置
    if primary is None:
        primary = create_openai_compatible_client(settings_repository=settings_repository)
        secondary_config = resolve_secondary_openai_compatible_config(settings_repository=settings_repository)
        if secondary_config.model or secondary_config.base_url or secondary_config.api_key:
            fallback_client = OpenAICompatibleClient(
                api_key=secondary_config.api_key or primary.api_key,
                # 修复：OpenAICompatibleClient 的属性是 api_base，不是 base_url
                api_base=secondary_config.base_url or primary.api_base,
                model=secondary_config.model or primary.model,
                timeout=secondary_config.timeout or primary.timeout,
            )
        # 仅当 provider_configs 未提供本地客户端时才从旧键解析
        if local_client is None:
            local_client = _resolve_local_client(settings_repository, local_base_url, local_api_key, primary, fallback_client)

    cloud_only = is_ai_cloud_only(settings_repository)
    # local_first（默认 False）：开启时本地腿先行、云端兜底；优先级高于 cloud_only
    local_first = is_ai_local_first(settings_repository)
    if local_first:
        cloud_only = False
    return FallbackOpenAIClient(
        primary=primary,
        fallback=fallback_client,
        local=local_client,
        local_coder=local_coder,
        vision_client=vision_client,
        cache_path=cache_path,
        cost_tracker=cost_tracker,
        budget_manager=budget_manager,
        privacy_filter=privacy_filter,
        receipt_callback=receipt_callback,
        cloud_only=cloud_only,
        local_first=local_first,
    )


def _resolve_local_client(settings_repository, local_base_url, local_api_key, primary, fallback_client):
    """解析本地 AI 客户端（旧版单配置回退路径）。"""
    from .base import OpenAICompatibleClient

    local_config = resolve_local_openai_compatible_config(settings_repository=settings_repository)
    resolved_local_base_url = local_base_url or local_config.base_url
    resolved_local_api_key = local_api_key or local_config.api_key or os.getenv("OPENAI_API_KEY")
    if resolved_local_base_url:
        local_model = local_config.model or (fallback_client.model if fallback_client is not None else None) or primary.model
        return OpenAICompatibleClient(
            api_key=resolved_local_api_key,
            api_base=resolved_local_base_url,
            model=local_model,
            timeout=local_config.timeout or primary.timeout,
            # 本地 AI（如 Ollama）未运行时连接被拒绝是持久性错误，
            # 不需要 3 次重试（默认 2+4+8=14s 浪费），1 次足矣
            max_retries=1,
        )
    return None


def create_client_from_provider_config(cfg: ProviderLike | Any) -> OpenAICompatibleClient:
    """从 ProviderConfig 创建 OpenAICompatibleClient。

    接受任何匹配 ``ProviderLike`` Protocol 的对象（包括 ``core.provider_config.ProviderConfig``）；
    fallback 字段缺失时退回到 ``getattr`` 默认值，保持与历史调用方的兼容。
    """
    from .base import OpenAICompatibleClient

    model_name = (getattr(cfg, "model", "") or "").strip()
    if not model_name:
        get_all_models = getattr(cfg, "get_all_models", None)
        if callable(get_all_models):
            models = get_all_models() or []
            if models:
                model_name = str(models[0])
    if not model_name:
        model_name = "gpt-4o"

    return OpenAICompatibleClient(
        api_key=getattr(cfg, "api_key", "") or "",
        api_base=getattr(cfg, "api_base", "") or "",
        model=model_name,
        timeout=int(getattr(cfg, "timeout", 300) or 300),
    )


def create_kiro_client() -> OpenAICompatibleClient | None:
    """
    创建 Kiro-gateway 客户端。
    如果 Kiro 未运行或未配置则返回 None。

    P3-2: 之前每次调用都重建 OpenAICompatibleClient 并发起 HTTP /health
    探测；JudgeAwareClient 在每次路由到 Kiro 时都会触发，造成严重的请求
    放大。这里加 60 秒 TTL 缓存，同 base/key 下复用客户端实例。

    Environment ``SIYI_KIRO_HEALTH_TTL`` 可覆盖缓存秒数（最小 0 = 禁用）。
    """
    from .base import OpenAICompatibleClient
    import httpx
    import time

    kiro_base = os.environ.get("SIYI_KIRO_API_BASE", "http://127.0.0.1:8000")
    kiro_key = os.environ.get("SIYI_KIRO_API_KEY", "sk-kiro-local")

    try:
        ttl = float(os.environ.get("SIYI_KIRO_HEALTH_TTL", "60"))
    except (TypeError, ValueError):
        ttl = 60.0

    cache_key = (kiro_base, kiro_key)
    now = time.monotonic()
    cached = _KIRO_CLIENT_CACHE.get(cache_key)
    if ttl > 0 and cached is not None:
        client, deadline = cached
        if deadline > now:
            return client  # 可能是 None（之前探测失败）也复用，避免风暴

    # 快速检测 Kiro 是否可达
    try:
        resp = httpx.get(f"{kiro_base}/health", timeout=3)
        if resp.status_code != 200:
            _KIRO_CLIENT_CACHE[cache_key] = (None, now + ttl)
            return None
    except httpx.RequestError:
        _KIRO_CLIENT_CACHE[cache_key] = (None, now + ttl)
        return None

    client = OpenAICompatibleClient(
        api_base=kiro_base,
        api_key=kiro_key,
        model="claude-sonnet-4.5",
        timeout=120,
    )
    if ttl > 0:
        _KIRO_CLIENT_CACHE[cache_key] = (client, now + ttl)
    return client


# P3-2: cache 形如 {(base, key): (client_or_None, deadline_monotonic)}
_KIRO_CLIENT_CACHE: dict[tuple[str, str], tuple[Any, float]] = {}


def _reset_kiro_client_cache() -> None:
    """测试钩子：清空 Kiro 客户端缓存。"""
    _KIRO_CLIENT_CACHE.clear()


def create_kiro_provider_config():
    """生成 Kiro 的标准 ProviderConfig。"""
    from core.provider_config import ProviderConfig

    return ProviderConfig(
        name="Kiro",
        api_base="http://127.0.0.1:8000",
        api_key="sk-kiro-local",
        model="claude-sonnet-4.5",
        models=["claude-sonnet-4.5", "claude-sonnet-4", "claude-haiku-4.5", "auto-kiro"],
        enabled=True,
        is_fallback=False,
        is_local=False,
        description="Kiro-gateway 多账号 Claude 代理",
    )


__all__ = [
    "create_openai_compatible_client",
    "create_judge_aware_client",
    "create_fallback_client",
    "create_client_from_provider_config",
    "create_kiro_client",
    "create_kiro_provider_config",
    "resolve_openai_compatible_config",
    "resolve_secondary_openai_compatible_config",
    "resolve_local_openai_compatible_config",
    "resolve_local_coder_openai_compatible_config",
]
