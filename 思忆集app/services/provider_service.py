"""ProviderService — 包装 core.provider_config 供 UI 层使用。"""

from __future__ import annotations

from core.provider_config import get_default_providers, provider_to_dict


class ProviderService:
    """AI 提供者配置服务，封装 provider_config 的 CRUD 辅助函数。"""

    @staticmethod
    def get_default_providers():
        return get_default_providers()

    @staticmethod
    def provider_to_dict(p):
        return provider_to_dict(p)
