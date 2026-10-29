from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import urlencode

from api.oauth_security import generate_oauth_state, generate_pkce_verifier_challenge
from api.provider_registry import ApiProviderRegistry, ApiProviderSpec
from .integration_http_client import ApiHttpClient
from core.settings_schema import KEY_YOUDAO_APP_KEY, KEY_YOUDAO_APP_SECRET, KEY_YOUDAO_OAUTH_REDIRECT_URI, SETTINGS_DEFAULTS


class YoudaoOAuthProvider:
    """有道 OAuth provider：授权 URL、code 换 token、refresh、持久化。"""

    PROVIDER_ID = "youdao"

    def __init__(
        self,
        *,
        integration_hub,
        provider_registry: ApiProviderRegistry,
        settings_repository=None,
        http_client: ApiHttpClient | None = None,
        app_key: str | None = None,
        app_secret: str | None = None,
    ):
        self.integration_hub = integration_hub
        self.provider_registry = provider_registry
        self.settings_repository = settings_repository
        self.http_client = http_client or ApiHttpClient(allowed_methods=("GET", "HEAD", "OPTIONS", "POST"))
        self._app_key = str(app_key or "").strip()
        self._app_secret = str(app_secret or "").strip()

    def create_authorization_request(self, *, redirect_uri: str | None = None, scope: str | None = None) -> dict[str, str]:
        spec = self._get_spec()
        redirect = self._resolve_redirect_uri(redirect_uri)
        state = generate_oauth_state()
        verifier, challenge = generate_pkce_verifier_challenge()
        scope_text = self._resolve_scope(scope, spec)

        params = {
            "response_type": "code",
            "client_id": self._resolve_app_key(),
            "redirect_uri": redirect,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if scope_text:
            params["scope"] = scope_text

        return {
            "provider": self.PROVIDER_ID,
            "authorize_url": f"{spec.auth_url}?{urlencode(params)}",
            "state": state,
            "code_verifier": verifier,
            "code_challenge": challenge,
            "redirect_uri": redirect,
            "scope": scope_text,
        }

    def exchange_code_for_token(self, *, code: str, redirect_uri: str | None, code_verifier: str | None) -> dict[str, Any]:
        spec = self._get_spec()
        if not str(code or "").strip():
            raise ValueError("code is required")

        payload = {
            "grant_type": "authorization_code",
            "client_id": self._resolve_app_key(),
            "client_secret": self._resolve_app_secret(),
            "code": str(code).strip(),
            "redirect_uri": self._resolve_redirect_uri(redirect_uri),
        }
        if str(code_verifier or "").strip():
            payload["code_verifier"] = str(code_verifier).strip()

        token_response = self.http_client.request_json("POST", spec.token_url, data=payload)
        normalized = self._normalize_token_response(token_response)
        return self.integration_hub.upsert_oauth_token(self.PROVIDER_ID, normalized)

    def refresh_access_token(self) -> dict[str, Any]:
        spec = self._get_spec()
        existing = self.integration_hub.get_oauth_token(self.PROVIDER_ID) or {}
        refresh_token = str(existing.get("refresh_token") or "").strip()
        if not refresh_token:
            raise ValueError("refresh_token is missing")

        payload = {
            "grant_type": "refresh_token",
            "client_id": self._resolve_app_key(),
            "client_secret": self._resolve_app_secret(),
            "refresh_token": refresh_token,
        }
        token_response = self.http_client.request_json("POST", spec.token_url, data=payload)
        normalized = self._normalize_token_response(token_response)
        if not normalized.get("refresh_token"):
            normalized["refresh_token"] = refresh_token
        return self.integration_hub.upsert_oauth_token(self.PROVIDER_ID, normalized)

    def _resolve_scope(self, scope: str | None, spec: ApiProviderSpec) -> str:
        explicit = str(scope or "").strip()
        if explicit:
            return explicit
        return " ".join(spec.scopes or ())

    def _get_spec(self) -> ApiProviderSpec:
        spec = self.provider_registry.get(self.PROVIDER_ID)
        if spec is None:
            raise ValueError("youdao provider is not registered")
        if not spec.auth_url or not spec.token_url:
            raise ValueError("youdao provider oauth endpoints are not configured")
        return spec

    def _resolve_redirect_uri(self, override: str | None) -> str:
        text = str(override or "").strip()
        if text:
            return text
        if self.settings_repository is not None:
            stored = self.settings_repository.get_setting(KEY_YOUDAO_OAUTH_REDIRECT_URI, "")
            stored_text = str(stored or "").strip()
            if stored_text:
                return stored_text
        return SETTINGS_DEFAULTS[KEY_YOUDAO_OAUTH_REDIRECT_URI]

    def _resolve_app_key(self) -> str:
        if self._app_key:
            return self._app_key
        env_value = str(os.getenv("SIYI_YOUDAO_APP_KEY") or "").strip()
        if env_value:
            return env_value
        if self.settings_repository is not None:
            repo_value = str(self.settings_repository.get_setting(KEY_YOUDAO_APP_KEY, "") or "").strip()
            if repo_value:
                return repo_value
        raise ValueError("youdao app key is missing")

    def _resolve_app_secret(self) -> str:
        if self._app_secret:
            return self._app_secret
        env_value = str(os.getenv("SIYI_YOUDAO_APP_SECRET") or "").strip()
        if env_value:
            return env_value
        if self.settings_repository is not None:
            repo_value = str(self.settings_repository.get_setting(KEY_YOUDAO_APP_SECRET, "") or "").strip()
            if repo_value:
                return repo_value
        raise ValueError("youdao app secret is missing")

    def _normalize_token_response(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("invalid token response")

        access_token = str(raw.get("access_token") or "").strip()
        if not access_token:
            raise ValueError("token response missing access_token")

        expires_in = self._safe_int(raw.get("expires_in"))
        expires_at = None
        if expires_in and expires_in > 0:
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

        return {
            "access_token": access_token,
            "refresh_token": str(raw.get("refresh_token") or "").strip(),
            "scope": str(raw.get("scope") or "").strip(),
            "token_type": str(raw.get("token_type") or "Bearer").strip() or "Bearer",
            "expires_at": expires_at,
        }

    def _safe_int(self, value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
