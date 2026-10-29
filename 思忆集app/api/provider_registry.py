from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ApiProviderSpec:
    """Provider 元数据单一事实源。"""

    provider_id: str
    display_name: str
    auth_type: str = "oauth2"
    base_url: str = ""
    auth_url: str = ""
    token_url: str = ""
    revoke_url: str = ""
    introspect_url: str = ""
    scopes: tuple[str, ...] = ()


class ApiProviderRegistry:
    """参考 nanobot/provider registry 的统一注册中心。"""

    def __init__(self, specs: Iterable[ApiProviderSpec] | None = None):
        self._specs: dict[str, ApiProviderSpec] = {}
        for spec in specs or ():
            self.register(spec)

    def register(self, spec: ApiProviderSpec) -> None:
        key = self._normalize_id(spec.provider_id)
        if not key:
            raise ValueError("provider_id is required")
        if key in self._specs:
            raise ValueError(f"provider already registered: {key}")
        self._specs[key] = ApiProviderSpec(
            provider_id=key,
            display_name=str(spec.display_name or key),
            auth_type=str(spec.auth_type or "oauth2").strip().lower(),
            base_url=str(spec.base_url or "").strip(),
            auth_url=str(spec.auth_url or "").strip(),
            token_url=str(spec.token_url or "").strip(),
            revoke_url=str(spec.revoke_url or "").strip(),
            introspect_url=str(spec.introspect_url or "").strip(),
            scopes=tuple(str(item).strip() for item in (spec.scopes or ()) if str(item).strip()),
        )

    def get(self, provider_id: str) -> ApiProviderSpec | None:
        return self._specs.get(self._normalize_id(provider_id))

    def list_all(self) -> list[ApiProviderSpec]:
        return [self._specs[key] for key in sorted(self._specs.keys())]

    def known_provider_ids(self) -> list[str]:
        return sorted(self._specs.keys())

    def _normalize_id(self, provider_id: str) -> str:
        return str(provider_id or "").strip().lower()


def create_default_api_provider_registry() -> ApiProviderRegistry:
    """默认 provider 清单，可持续扩展。"""

    return ApiProviderRegistry(
        [
            ApiProviderSpec(
                provider_id="automation_hub",
                display_name="Automation Hub",
                auth_type="api_key",
            ),
            ApiProviderSpec(
                provider_id="youdao",
                display_name="有道云",
                auth_type="oauth2",
                base_url="https://note.youdao.com",
                auth_url="https://note.youdao.com/oauth/authorize2",
                token_url="https://note.youdao.com/oauth/access2",
                scopes=("notes.read", "notes.write", "news.read", "news.write"),
            ),
        ]
    )
