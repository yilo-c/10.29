"""Provider 配置数据模型与 CRUD 方法。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Any

logger = logging.getLogger("siyi.provider_config")


@dataclass
class ProviderConfig:
    """AI 服务提供者配置。"""
    name: str = ""                          # 显示名称，如 "DeepSeek"
    api_base: str = ""                      # API 地址，如 "https://api.deepseek.com/v1"
    api_key: str = ""                       # API Key
    model: str = ""                         # 默认模型，如 "deepseek-chat"
    models: list[str] | None = None         # 该 provider 可用的完整模型列表（None 表示只有默认模型）
    timeout: int = 300                      # 超时秒数
    enabled: bool = True                    # 是否启用
    is_fallback: bool = False               # 是否作为备胎
    is_local: bool = False                  # 是否为本地模型（无需 API Key）
    description: str = ""                   # 备注说明
    purpose: str = "general"                # 用途：general 通用 / vision 图像理解（vision 请求优先路由）

    def is_valid(self) -> bool:
        return bool(self.name) and bool(self.api_base) and (bool(self.api_key) or self.is_local)

    def get_all_models(self) -> list[str]:
        """返回此 provider 可用的所有模型名。"""
        if self.models:
            return self.models
        return [self.model] if self.model else []

    def has_model(self, model_name: str) -> bool:
        """检查此 provider 是否支持指定模型。"""
        return model_name in self.get_all_models()

    @classmethod
    def find_provider_by_model(cls, providers: list[ProviderConfig], model_name: str) -> ProviderConfig | None:
        """在所有 provider 中查找第一个包含指定模型的。"""
        for p in providers:
            if p.has_model(model_name):
                return p
        return None

    @classmethod
    def validate_model_selection(cls, providers: list[ProviderConfig],
                                  provider_name: str, model_name: str) -> tuple[bool, str]:
        """验证选中的 provider+model 组合是否有效。返回 (valid, reason)。"""
        provider = find_provider(providers, provider_name)
        if not provider:
            return False, f"Provider '{provider_name}' 不存在"
        if not provider.enabled:
            return False, f"Provider '{provider_name}' 已禁用"
        if model_name and not provider.has_model(model_name):
            all_models = provider.get_all_models()
            return False, f"模型 '{model_name}' 不在 {provider.name} 的可用模型列表 {all_models} 中"
        return True, "ok"

    async def test_connectivity(self, timeout: int = 10) -> tuple[bool, str]:
        """
        测试提供商真实可用性。

        旧实现只调用 GET /v1/models，该接口不触发计费，账户欠费时仍可能返回 200，
        导致用户误以为云端 API 可用。现改为发送一条极小的真实 chat completion 请求
        （max_tokens=1），只有真正成功才认为可用，并在响应中识别欠费/配额类错误。

        返回 (是否可用, 消息)。消息会包含具体错误类型，如“账户欠费/配额耗尽”。
        """
        import aiohttp
        import asyncio

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model or "gpt-4o",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "temperature": 0.1,
        }

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.post(
                    f"{self.api_base.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    body_text = await resp.text()
                    if resp.status == 200:
                        try:
                            data = await resp.json()
                        except Exception:
                            data = {}
                        choices = data.get("choices") if isinstance(data, dict) else None
                        if choices:
                            return True, "推理可用"
                        return False, "响应异常：无 choices"

                    # 优先解析错误体中的可读消息
                    from ai.llm_resilience import extract_error_text, classify_error

                    error_msg = extract_error_text(body_text)
                    classification = classify_error(resp.status, error_msg)
                    if classification.kind == "billing_error":
                        detail = error_msg or f"HTTP {resp.status}"
                        return False, f"账户欠费/配额耗尽 ({detail})"
                    if resp.status == 401:
                        return False, "认证失败 (401)"
                    if resp.status == 403:
                        return False, "权限不足 (403)"
                    if resp.status == 429:
                        return False, f"请求过于频繁 (429): {error_msg or ''}".strip()
                    return False, f"推理不可用 ({resp.status}): {error_msg or body_text[:200]}".strip()
        except asyncio.TimeoutError:
            return False, "连接超时"
        except aiohttp.ClientConnectorError as e:
            return False, f"连接失败: {e}"
        except Exception as e:
            return False, f"错误: {e}"

    async def discover_models(self, timeout: int = 10) -> list[str]:
        """发现可用模型列表。对 Ollama 优先使用 /api/tags，否则回退 /v1/models。"""
        import aiohttp
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # 检查是否为 Ollama（api_base 含 localhost:11434 或 127.0.0.1:11434）
        is_ollama = "11434" in self.api_base and "ollama" in self.name.lower()
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                if is_ollama:
                    # Ollama 原生 /api/tags 端点
                    base = self.api_base.rstrip("/").replace("/v1", "").replace("/v1/", "")
                    try:
                        async with session.get(f"{base}/api/tags", headers=headers) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                return [m["name"] for m in data.get("models", []) if "name" in m]
                    except Exception:
                        pass
                # 回退 OpenAI 兼容 /v1/models
                async with session.get(f"{self.api_base.rstrip('/')}/v1/models", headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return [m["id"] for m in data.get("data", []) if "id" in m]
        except Exception:
            pass
        return []


# ── CRUD 辅助函数 ──

def load_providers(settings_repository, data_dir: str | None = None) -> list[ProviderConfig]:
    """从设置仓储加载 provider 列表。

    自动解密 api_key 字段，支持新版安全存储 token 与旧版 Fernet token。
    若从未配置任何 provider（存储为空），自动播种 :func:`get_default_providers`
    并持久化，避免 model.list / provider_list 返回空导致“模型列表不可用”。
    """
    from core.settings_schema import KEY_PROVIDER_CONFIGS
    raw_list = settings_repository.get_setting(KEY_PROVIDER_CONFIGS, [])

    # ── 首次播种：存储为空且无任何 provider 时写入默认配置（幂等，仅触发一次） ──
    if not raw_list:
        seeds = [provider_to_dict(p) for p in get_default_providers()]
        if seeds:
            try:
                settings_repository.set_setting(KEY_PROVIDER_CONFIGS, seeds)
                raw_list = seeds
                logger.info("provider_configs 为空，已播种 %d 个默认 provider", len(seeds))
            except Exception:
                logger.exception("播种默认 provider 失败，继续按空列表处理")

    # 若未显式传入 data_dir，尝试从 settings_repository.data_store.base_dir 获取
    if data_dir is None:
        data_store = getattr(settings_repository, "data_store", None)
        if data_store is not None:
            data_dir = getattr(data_store, "base_dir", None)

    decrypt_fn = _build_provider_decrypt_fn(data_dir, settings_repository=settings_repository)

    return [dict_to_provider(p, decrypt_fn=decrypt_fn) for p in raw_list if isinstance(p, dict)]


def _build_provider_decrypt_fn(data_dir: str | None = None, settings_repository: Any = None):
    """构建 provider api_key 解密函数。

    P1.8: 通过 SecretResolver 统一入口，复用全局 SecureKeyStorage 单例。
    优先使用 SecureKeyStorage 处理新版安全 token；对旧版 Fernet token 回退到
    core.crypto_utils，保持向后兼容。

    Args:
        data_dir: data 目录路径（用于旧版 Fernet 解密）
        settings_repository: 可选 — 传入则启用旧版/明文凭据的透明迁移
    """
    from core.secret_resolver import SecretResolver

    resolver = SecretResolver(
        settings_repository=settings_repository,
        data_dir=data_dir,
    )

    def _decrypt(value: str) -> str:
        if not value:
            return value
        # 迁移由 load_providers 调用方负责（无 settings_repository 时仅解密）
        return resolver.decrypt_token(
            value,
            migrate=settings_repository is not None,
        )

    return _decrypt


def save_providers(settings_repository, providers: list[ProviderConfig]):
    """保存 provider 列表到设置仓储。"""
    from core.settings_schema import KEY_PROVIDER_CONFIGS
    settings_repository.set_setting(KEY_PROVIDER_CONFIGS, [provider_to_dict(p) for p in providers])


def provider_to_dict(p: ProviderConfig) -> dict[str, Any]:
    d = asdict(p)
    # 排除 None 值，保持 JSON 整洁
    if d.get("models") is None:
        del d["models"]
    return d


def dict_to_provider(d: dict[str, Any], decrypt_fn=None) -> ProviderConfig:
    raw_models = d.get("models")
    models_list: list[str] | None = None
    if isinstance(raw_models, list):
        models_list = [str(m) for m in raw_models if isinstance(m, str)]
    api_key = str(d.get("api_key", ""))
    if decrypt_fn and api_key:
        api_key = decrypt_fn(api_key)
    return ProviderConfig(
        name=str(d.get("name", "")),
        api_base=str(d.get("api_base", "")),
        api_key=api_key,
        model=str(d.get("model", "")),
        models=models_list,
        timeout=int(d.get("timeout", 300)),
        enabled=bool(d.get("enabled", True)),
        is_fallback=bool(d.get("is_fallback", False)),
        is_local=bool(d.get("is_local", False)),
        description=str(d.get("description", "")),
        purpose=str(d.get("purpose", "") or "general"),
    )


# ── 默认配置 ──

def get_default_providers() -> list[ProviderConfig]:
    """返回默认的 provider 配置列表。"""
    return [
        ProviderConfig(
            name="DeepSeek",
            api_base="https://api.deepseek.com/v1",
            api_key="",
            model="deepseek-v4-flash",
            timeout=300,
            enabled=True,
            description="DeepSeek 主力模型",
        ),
        ProviderConfig(
            name="OpenAI",
            api_base="https://api.openai.com/v1",
            api_key="",
            model="gpt-4o",
            timeout=300,
            enabled=False,
            description="OpenAI GPT-4o",
        ),
        ProviderConfig(
            name="本地 Ollama",
            api_base="http://localhost:11434/v1",
            api_key="",
            model="qwen3-4b",
            timeout=60,
            enabled=True,
            is_local=True,
            description="本地 Ollama 模型（无需 API Key）",
        ),
    ]


def find_provider(providers: list[ProviderConfig], name: str) -> ProviderConfig | None:
    for p in providers:
        if p.name == name:
            return p
    return None


def provider_to_safe_dict(p: ProviderConfig) -> dict:
    """序列化为前端安全的 dict，api_key 脱敏。"""
    d = {
        "name": p.name,
        "api_base": p.api_base,
        "model": p.model,
        "models": p.models or [],
        "is_local": p.is_local,
        "is_fallback": p.is_fallback,
        "enabled": p.enabled,
        "description": p.description,
        "has_api_key": bool(p.api_key),
    }
    return d
