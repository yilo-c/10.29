"""ApiKeyService — API 密钥的增删改查管理。

统一管理两类密钥：
  1. LLM Providers (provider_configs 数组): DeepSeek, OpenAI, Ollama 等
  2. Service Keys (settings.json 扁平键): 有道, Todoist, Tavily 等

P1.8: 密钥解析统一通过 core.secret_resolver.SecretResolver 入口，
ENV_KEY_MAP 引用 LLM_FLAT_KEY_ENV_MAP 单一真实源（消除两份不一致映射）。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from core.secret_resolver import LLM_FLAT_KEY_ENV_MAP, SecretResolver
from core.secure_key_storage import SecureKeyStorage

logger = logging.getLogger("siyi.services.api_key")

# ── 已知服务密钥的定义 ──
# (key_in_settings, display_name, category, doc_url)
SERVICE_KEY_DEFS: list[tuple[str, str, str, str]] = [
    ("youdao_app_key",      "有道应用 Key",     "translation", ""),
    ("youdao_app_secret",   "有道应用 Secret",   "translation", ""),
    ("todoist_api_token",   "Todoist Token",     "task",        ""),
    ("automation_hub_api_token", "自动化中心 Token", "automation", ""),
    ("web_search_tavily_api_key", "Tavily 搜索 Key", "search",   ""),
    ("web_search_searxng_base_url", "SearXNG 地址", "search",   ""),
]

# P1.8: 引用单一真实源，消除两份不一致的环境变量映射
ENV_KEY_MAP: dict[str, list[str]] = LLM_FLAT_KEY_ENV_MAP


def mask_key(key: str, keep_front: int = 4, keep_back: int = 4) -> str:
    """将密钥中间部分替换为 *，始终返回掩码后的字符串。"""
    s = key.strip()
    if not s:
        return ""
    if len(s) <= keep_front + keep_back:
        keep = max(1, len(s) // 3)
        return s[:keep] + "*" * (len(s) - keep)
    # 至少 mask 一半，最低 4 个 *
    mask_len = max(4, (len(s) + 1) // 2)
    front = (len(s) - mask_len + 1) // 2
    back = len(s) - mask_len - front
    return s[:front] + "*" * mask_len + (s[-back:] if back > 0 else "")


@dataclass
class ApiKeyEntry:
    """统一的 API 密钥条目表示。"""
    id: str                                          # 唯一标识符
    name: str                                        # 显示名称
    category: str = "other"                          # llm | translation | search | task | automation
    key_value: str = ""                              # 密钥值（创建/更新时明文，列表时脱敏）
    base_url: str = ""                               # API 地址（LLM Provider）
    model: str = ""                                  # 默认模型（LLM Provider）
    is_provider: bool = False                        # 是否 provider_configs 条目
    enabled: bool = True                             # 是否启用
    is_local: bool = False                           # 是否本地模型
    is_fallback: bool = False                        # 是否备用
    description: str = ""                            # 备注
    from_env: bool = False                           # 是否来自环境变量（只读）
    env_var_names: list[str] = field(default_factory=list)  # 对应的环境变量名


class ApiKeyService:
    """API 密钥 CRUD 服务。"""

    def __init__(self, settings_repository: Any, data_dir: str | None = None):
        self._settings = settings_repository
        self._data_dir = data_dir
        # P1.8: 复用 SecretResolver 单一入口；保留独立 _secure 实例以支持
        # 透明迁移（resolver.decrypt_token 需要 settings_repository 才会写回）
        self._secure = SecureKeyStorage(
            namespace="com.siyi.apikey",
            fallback_data_dir=data_dir,
        )
        self._resolver = SecretResolver(
            secure_storage=self._secure,
            settings_repository=settings_repository,
            data_dir=data_dir,
        )
        logger.info("ApiKeyService 使用安全存储后端: %s", self._secure.backend_name)

    # ── 列出所有密钥 ──

    def list_keys(self) -> list[dict[str, Any]]:
        """返回所有密钥的脱敏列表。"""
        providers = self._load_providers()
        result: list[dict[str, Any]] = []

        # 1) LLM 扁平密钥（已弃用 — 仅在没有 provider_configs 时列出）
        has_providers = bool(providers)
        flat_llm_keys: list[tuple[str, str, str]] = []
        if not has_providers:
            flat_llm_keys = [
                ("llm_api_key",       "主力 LLM Key（旧版）",     "llm"),
                ("llm_api_base",      "主力 API 地址（旧版）",    "llm"),
                ("llm_model",         "主力模型名称（旧版）",     "llm"),
                ("llm_fallback_api_key",  "备用 LLM Key（旧版）", "llm"),
                ("llm_fallback_api_base", "备用 API 地址（旧版）", "llm"),
                ("llm_fallback_model",    "备用模型名称（旧版）",  "llm"),
                ("llm_local_api_key",     "本地 LLM Key",  "llm"),
                ("llm_local_api_base",    "本地 API 地址", "llm"),
                ("llm_local_model",       "本地模型名称",  "llm"),
            ]

        for key_name, display_name, cat in flat_llm_keys:
            env_vars = ENV_KEY_MAP.get(key_name, [])
            env_val = self._read_env(env_vars)
            store_val: str = str(self._settings.get_setting(key_name, "") or "")
            actual = env_val or store_val
            result.append({
                "id": f"flat:{key_name}",
                "name": display_name,
                "category": cat,
                "key_value": mask_key(actual) if actual else "",
                "base_url": "",
                "model": "",
                "is_provider": False,
                "enabled": bool(actual),
                "is_local": "local" in key_name,
                "is_fallback": "fallback" in key_name,
                "description": "",
                "from_env": bool(env_val),
                "env_var_names": env_vars,
            })

        # 2) Provider 条目
        for p in providers:
            provider_name = p.get("name", "unnamed")
            p_key = self._decrypt_provider_key(p)
            p_id = f"provider:{provider_name}"
            result.append({
                "id": p_id,
                "name": p.get("name", "未命名"),
                "category": "llm",
                "key_value": mask_key(p_key) if p_key else "",
                "base_url": p.get("api_base", ""),
                "model": p.get("model", ""),
                "is_provider": True,
                "enabled": bool(p.get("enabled", True)),
                "is_local": bool(p.get("is_local", False)),
                "is_fallback": bool(p.get("is_fallback", False)),
                "description": p.get("description", ""),
                "purpose": p.get("purpose", ""),
                "from_env": False,
                "env_var_names": [],
            })

        # 3) 服务密钥（有道、Todoist 等）
        for key_name, display_name, cat, _doc in SERVICE_KEY_DEFS:
            val: str = str(self._settings.get_setting(key_name, "") or "")
            result.append({
                "id": f"service:{key_name}",
                "name": display_name,
                "category": cat,
                "key_value": mask_key(val) if val else "",
                "base_url": "",
                "model": "",
                "is_provider": False,
                "enabled": bool(val),
                "is_local": False,
                "is_fallback": False,
                "description": "",
                "from_env": False,
                "env_var_names": [],
            })

        return result

    # ── 获取单个密钥（完整值） ──

    def get_key(self, key_id: str) -> dict[str, Any] | None:
        """返回单个密钥详情（完整 key_value）。"""
        all_keys = self.list_keys()
        for k in all_keys:
            if k["id"] == key_id:
                # 重新获取明文值
                raw = self._get_raw_key_value(key_id)
                k["key_value"] = raw
                return k
        return None

    # ── 创建密钥 ──

    def create_key(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建新的 API 密钥。"""
        provider_name = (data.get("name") or "").strip()
        key_value = (data.get("key_value") or "").strip()

        is_local = bool(data.get("is_local", False))
        if not key_value and not is_local:
            raise ValueError("key_value is required")

        if data.get("is_provider"):
            # 添加到 provider_configs
            providers = self._load_providers()
            # 检查重名
            if any(p.get("name") == provider_name for p in providers):
                raise ValueError(f"Provider '{provider_name}' 已存在")
            providers.append({
                "name": provider_name,
                "api_base": (data.get("base_url") or "").strip(),
                "api_key": self._encrypt_provider_key(provider_name, key_value),
                "model": (data.get("model") or "").strip(),
                "timeout": int(data.get("timeout", 300)),
                "enabled": bool(data.get("enabled", True)),
                "is_fallback": bool(data.get("is_fallback", False)),
                "is_local": bool(data.get("is_local", False)),
                "description": (data.get("description") or "").strip(),
                "purpose": (data.get("purpose") or "").strip(),
            })
            self._save_providers(providers)
            return {"id": f"provider:{provider_name}", "name": provider_name}
        else:
            # 扁平密钥
            key_name = data.get("flat_key") or ""
            if not key_name:
                raise ValueError("flat_key is required for non-provider keys")
            self._settings.set_setting(key_name, self._encrypt_flat_key(key_name, key_value))
            return {"id": f"flat:{key_name}", "name": key_name}

    # ── 更新密钥 ──

    def update_key(self, key_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """更新现有密钥。"""
        if key_id.startswith("provider:"):
            provider_name = key_id[len("provider:"):]
            providers = self._load_providers()
            for p in providers:
                if p.get("name") == provider_name:
                    if "key_value" in data:
                        p["api_key"] = self._encrypt_provider_key(provider_name, (data["key_value"] or "").strip())
                    if "base_url" in data:
                        p["api_base"] = (data["base_url"] or "").strip()
                    if "model" in data:
                        p["model"] = (data["model"] or "").strip()
                    if "enabled" in data:
                        p["enabled"] = bool(data["enabled"])
                    if "description" in data:
                        p["description"] = (data["description"] or "").strip()
                    if "is_fallback" in data:
                        p["is_fallback"] = bool(data["is_fallback"])
                    if "purpose" in data:
                        p["purpose"] = (data["purpose"] or "").strip()
                    self._save_providers(providers)
                    return {"id": key_id, "name": provider_name}
            raise ValueError(f"Provider '{provider_name}' 不存在")
        elif key_id.startswith("flat:") or key_id.startswith("service:"):
            _, key_name = key_id.split(":", 1)
            if "key_value" in data:
                self._settings.set_setting(key_name, self._encrypt_flat_key(key_name, (data["key_value"] or "").strip()))
            return {"id": key_id, "name": key_name}
        else:
            raise ValueError(f"Unknown key_id format: {key_id}")

    # ── 删除密钥 ──

    def delete_key(self, key_id: str) -> dict[str, Any]:
        """删除密钥。"""
        if key_id.startswith("provider:"):
            provider_name = key_id[len("provider:"):]
            providers = self._load_providers()
            new_providers = [p for p in providers if p.get("name") != provider_name]
            if len(new_providers) == len(providers):
                raise ValueError(f"Provider '{provider_name}' 不存在")
            self._save_providers(new_providers)
            return {"id": key_id, "deleted": True}
        elif key_id.startswith("flat:") or key_id.startswith("service:"):
            _, key_name = key_id.split(":", 1)
            self._settings.set_setting(key_name, "")
            return {"id": key_id, "deleted": True}
        else:
            raise ValueError(f"Unknown key_id format: {key_id}")

    # ── 测试连通性 ──

    async def test_key(self, key_id: str, ai_client: Any = None) -> dict[str, Any]:
        """测试 API 密钥真实可用性。

        P0 修复：旧实现直接调用 FallbackOpenAIClient.chat_completion，云端失败时会
        自动回退到本地模型，导致测试显示成功。现改为只针对该 key 对应的 provider
        做点对点真实推理测试，明确识别欠费/配额错误，不走回退链。
        """
        import asyncio

        entry = self.get_key(key_id)
        if not entry:
            return {"success": False, "error": "密钥不存在"}
        if not entry.get("key_value") and not entry.get("is_local"):
            return {"success": False, "error": "密钥为空"}

        # 1) provider 类型：直接对应该 provider 做真实推理测试
        if key_id.startswith("provider:"):
            from core.provider_config import dict_to_provider

            provider_name = key_id[len("provider:"):]
            providers = self._load_providers()
            raw = next((p for p in providers if p.get("name") == provider_name), None)
            if not raw:
                return {"success": False, "error": f"Provider '{provider_name}' 未找到"}

            # 先透明迁移（如有需要），再用安全存储解密
            self._decrypt_provider_key(raw)
            provider = dict_to_provider(
                raw,
                decrypt_fn=lambda v: self._secure.decrypt(v) if self._secure.is_secure_token(v) else v,
            )
            try:
                ok, msg = await asyncio.wait_for(
                    provider.test_connectivity(),
                    timeout=provider.timeout or 15,
                )
                return {
                    "success": ok,
                    "error": None if ok else msg,
                    "message": msg,
                    "inference_ok": ok,
                }
            except asyncio.TimeoutError:
                return {"success": False, "error": "测试超时", "inference_ok": False}
            except Exception as exc:
                return {"success": False, "error": str(exc), "inference_ok": False}

        # 2) LLM 扁平密钥（旧版）：构造独立客户端点对点测试，避免 Fallback 回退
        cat = entry.get("category", "")
        if cat == "llm" and entry.get("key_value"):
            from ai.client.base import OpenAICompatibleClient

            base_url = entry.get("base_url") or ""
            model = entry.get("model") or "gpt-4o"
            if not base_url:
                return {"success": False, "error": "缺少 API 地址（base_url）"}
            try:
                client = OpenAICompatibleClient(
                    api_key=entry["key_value"],
                    api_base=base_url,
                    model=model,
                    timeout=15,
                )
                result = await asyncio.to_thread(
                    client.chat_completion,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                    temperature=0.1,
                )
                if result and isinstance(result, dict) and result.get("choices"):
                    return {"success": True, "message": "推理可用", "inference_ok": True}
                return {"success": False, "error": "响应格式异常", "inference_ok": False}
            except Exception as exc:
                from ai.llm_resilience import classify_error

                err_text = str(exc)
                classification = classify_error(getattr(exc, "status", None), err_text)
                if classification.kind == "billing_error":
                    return {
                        "success": False,
                        "error": f"账户欠费/配额耗尽: {err_text}",
                        "inference_ok": False,
                        "billing_error": True,
                    }
                return {"success": False, "error": err_text, "inference_ok": False}

        # 3) 其他服务密钥：无法自动测试
        if entry.get("key_value"):
            return {"success": True, "note": "密钥已保存（无法自动测试此类型）"}
        return {"success": False, "error": "无法测试"}

    # ── 内部方法 ──

    def _load_providers(self) -> list[dict[str, Any]]:
        raw = self._settings.get_setting("provider_configs", [])
        if not isinstance(raw, list):
            return []
        return [p for p in raw if isinstance(p, dict)]

    def _save_providers(self, providers: list[dict[str, Any]]) -> None:
        self._settings.set_setting("provider_configs", providers)

    def _get_raw_key_value(self, key_id: str) -> str:
        if key_id.startswith("provider:"):
            pname = key_id[len("provider:"):]
            for p in self._load_providers():
                if p.get("name") == pname:
                    return self._decrypt_provider_key(p)
            return ""
        elif key_id.startswith("flat:") or key_id.startswith("service:"):
            _, key_name = key_id.split(":", 1)
            env_vars = ENV_KEY_MAP.get(key_name, [])
            env_val = self._read_env(env_vars)
            if env_val:
                return env_val
            stored = str(self._settings.get_setting(key_name, "") or "")
            return self._decrypt_flat_key(key_name, stored)
        return ""

    @staticmethod
    def _read_env(var_names: list[str]) -> str:
        for name in var_names:
            val = os.environ.get(name, "")
            if val:
                return val
        return ""

    def _encrypt_flat_key(self, key_name: str, value: str) -> str:
        if not value:
            return value
        return self._secure.encrypt(value, key_id=key_name)

    def _encrypt_provider_key(self, provider_name: str, value: str) -> str:
        if not value:
            return value
        return self._secure.encrypt(value, key_id=f"provider/{provider_name}")

    def _decrypt_flat_key(self, key_name: str, stored: str) -> str:
        """解密扁平/服务密钥；遇到明文或旧版 Fernet 时透明迁移。

        P1.8: 委托给 SecretResolver.decrypt_token（含相同迁移逻辑）。
        """
        if not stored:
            return stored
        return self._resolver.decrypt_token(stored, key_name=key_name, migrate=True)

    def _decrypt_provider_key(self, provider: dict[str, Any]) -> str:
        """解密 provider api_key；遇到明文或旧版 Fernet 时透明迁移。

        P1.8: 使用 SecretResolver.resolve_provider_key 处理解密 + 迁移逻辑。
        若 resolver 检测到旧版/明文格式并完成迁移，需把新的加密 token 写回
        provider_configs 列表（resolver 只写 settings 扁平键，不感知 provider 结构）。
        """
        stored = provider.get("api_key", "") or ""
        if not stored:
            return stored
        provider_name = provider.get("name", "unnamed")
        key_id = f"provider/{provider_name}"

        # 已经是新版安全 token：直接解密
        if self._secure.is_secure_token(stored):
            try:
                return self._secure.decrypt(stored)
            except Exception:
                logger.warning("provider secure token decrypt failed: %s", provider_name, exc_info=True)
                return stored

        # 旧版 Fernet / 明文：委托给 resolver 解密 + 迁移到安全存储 token，
        # 然后把迁移后的 token 写回 provider_configs 列表
        plaintext = self._resolver.decrypt_token(stored, key_name=key_id, migrate=False)
        if plaintext and plaintext != stored:
            # 解密成功，重新加密为安全存储 token 并写回 provider dict
            migrated = self._secure.encrypt(plaintext, key_id=key_id)
            provider["api_key"] = migrated
            self._save_providers(self._load_providers())
            logger.info("已迁移 provider 密钥到安全存储: %s", provider_name)
            return plaintext
        # 明文（未触发迁移）：直接迁移到加密 token
        if stored and plaintext == stored and not self._secure.is_legacy_fernet(stored):
            migrated = self._secure.encrypt(stored, key_id=key_id)
            provider["api_key"] = migrated
            self._save_providers(self._load_providers())
            logger.info("已迁移 provider 明文凭据到安全存储: %s", provider_name)
            return stored
        return plaintext

    def _decrypt_legacy_fernet(self, ciphertext: str) -> str:
        """使用旧版 core.crypto_utils 解密（仅迁移用）。"""
        if not self._data_dir:
            return ciphertext
        from core.crypto_utils import decrypt as _legacy_decrypt

        plaintext = _legacy_decrypt(ciphertext, self._data_dir)
        # 如果解密失败（密钥不匹配），legacy decrypt 会返回原值；这里返回原值让上层处理
        return plaintext
