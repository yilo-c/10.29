from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from api.provider_registry import create_default_api_provider_registry
from core.settings_schema import KEY_YOUDAO_APP_KEY, KEY_YOUDAO_APP_SECRET, KEY_YOUDAO_OAUTH_REDIRECT_URI
from integrations.youdao_oauth_provider import YoudaoOAuthProvider


class _FakeIntegrationHub:
    def __init__(self):
        self.token = None
        self.upsert_calls = []

    def upsert_oauth_token(self, provider: str, payload):
        record = {"provider": provider, **payload}
        self.upsert_calls.append(record)
        self.token = record
        return record

    def get_oauth_token(self, provider: str):
        if self.token and self.token.get("provider") == provider:
            return self.token
        return None


class _FakeHttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("no fake response left")
        return self.responses.pop(0)


class _FakeSettingsRepository:
    def __init__(self, values):
        self.values = dict(values)

    def get_setting(self, key, default=None):
        return self.values.get(key, default)


def _build_provider(http_client):
    settings_repo = _FakeSettingsRepository(
        {
            KEY_YOUDAO_APP_KEY: "demo-app-key",
            KEY_YOUDAO_APP_SECRET: "demo-app-secret",
            KEY_YOUDAO_OAUTH_REDIRECT_URI: "http://127.0.0.1:53682/callback/youdao",
        }
    )
    hub = _FakeIntegrationHub()
    provider = YoudaoOAuthProvider(
        integration_hub=hub,
        provider_registry=create_default_api_provider_registry(),
        settings_repository=settings_repo,
        http_client=http_client,
    )
    return provider, hub


def test_create_authorization_request_builds_youdao_authorize_url():
    provider, _hub = _build_provider(http_client=_FakeHttpClient([]))

    request = provider.create_authorization_request()

    parsed = urlparse(request["authorize_url"])
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "note.youdao.com"
    assert parsed.path == "/oauth/authorize2"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["demo-app-key"]
    assert query["redirect_uri"] == ["http://127.0.0.1:53682/callback/youdao"]
    assert query["state"] == [request["state"]]
    assert query["code_challenge"] == [request["code_challenge"]]
    assert query["code_challenge_method"] == ["S256"]
    scope_items = set(query["scope"][0].split())
    assert {"notes.read", "notes.write", "news.read"}.issubset(scope_items)
    assert request["code_verifier"]


def test_exchange_code_for_token_persists_normalized_token():
    http_client = _FakeHttpClient(
        [
            {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "expires_in": 3600,
                "scope": "notes.read",
                "token_type": "Bearer",
            }
        ]
    )
    provider, hub = _build_provider(http_client=http_client)

    persisted = provider.exchange_code_for_token(
        code="demo-auth-code",
        redirect_uri="http://127.0.0.1:53682/callback/youdao",
        code_verifier="pkce-verifier",
    )

    assert len(http_client.calls) == 1
    call = http_client.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://note.youdao.com/oauth/access2"
    assert call["data"]["grant_type"] == "authorization_code"
    assert call["data"]["code"] == "demo-auth-code"
    assert call["data"]["code_verifier"] == "pkce-verifier"

    assert persisted["provider"] == "youdao"
    assert persisted["access_token"] == "access-1"
    assert persisted["refresh_token"] == "refresh-1"
    assert persisted["expires_at"]
    assert hub.upsert_calls


def test_refresh_access_token_reuses_old_refresh_token_if_response_missing_it():
    http_client = _FakeHttpClient(
        [
            {
                "access_token": "access-2",
                "expires_in": 120,
                "scope": "notes.read",
            }
        ]
    )
    provider, hub = _build_provider(http_client=http_client)
    hub.token = {
        "provider": "youdao",
        "access_token": "stale",
        "refresh_token": "refresh-old",
    }

    refreshed = provider.refresh_access_token()

    assert len(http_client.calls) == 1
    call = http_client.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://note.youdao.com/oauth/access2"
    assert call["data"]["grant_type"] == "refresh_token"
    assert call["data"]["refresh_token"] == "refresh-old"

    assert refreshed["access_token"] == "access-2"
    assert refreshed["refresh_token"] == "refresh-old"
    assert refreshed["token_type"] == "Bearer"
    assert refreshed["expires_at"]


def test_refresh_access_token_raises_when_no_refresh_token():
    provider, hub = _build_provider(http_client=_FakeHttpClient([]))
    hub.token = {
        "provider": "youdao",
        "access_token": "stale",
        "refresh_token": "",
    }

    import pytest

    with pytest.raises(ValueError, match="refresh_token is missing"):
        provider.refresh_access_token()


def test_refresh_access_token_raises_when_no_existing_token():
    provider, hub = _build_provider(http_client=_FakeHttpClient([]))
    # hub.token is None by default

    import pytest

    with pytest.raises(ValueError, match="refresh_token is missing"):
        provider.refresh_access_token()


def test_refresh_access_token_adopts_new_refresh_token_from_response():
    http_client = _FakeHttpClient(
        [
            {
                "access_token": "access-new",
                "refresh_token": "refresh-new",
                "expires_in": 7200,
                "scope": "notes.read notes.write",
                "token_type": "Bearer",
            }
        ]
    )
    provider, hub = _build_provider(http_client=http_client)
    hub.token = {
        "provider": "youdao",
        "access_token": "stale",
        "refresh_token": "refresh-old",
    }

    refreshed = provider.refresh_access_token()

    assert refreshed["access_token"] == "access-new"
    assert refreshed["refresh_token"] == "refresh-new"
    assert refreshed["scope"] == "notes.read notes.write"


def test_refresh_access_token_raises_on_invalid_response():
    http_client = _FakeHttpClient([{"error": "invalid_grant"}])
    provider, hub = _build_provider(http_client=http_client)
    hub.token = {
        "provider": "youdao",
        "access_token": "stale",
        "refresh_token": "refresh-old",
    }

    import pytest

    with pytest.raises(ValueError, match="access_token"):
        provider.refresh_access_token()


def test_refresh_access_token_raises_on_non_dict_response():
    http_client = _FakeHttpClient(["not-a-dict"])
    provider, hub = _build_provider(http_client=http_client)
    hub.token = {
        "provider": "youdao",
        "access_token": "stale",
        "refresh_token": "refresh-old",
    }

    import pytest

    with pytest.raises(ValueError, match="invalid token response"):
        provider.refresh_access_token()


def test_exchange_code_raises_on_empty_code():
    provider, _hub = _build_provider(http_client=_FakeHttpClient([]))

    import pytest

    with pytest.raises(ValueError, match="code is required"):
        provider.exchange_code_for_token(code="", redirect_uri=None, code_verifier=None)


def test_normalize_token_response_handles_missing_expires_in():
    http_client = _FakeHttpClient(
        [
            {
                "access_token": "token-no-expiry",
                "refresh_token": "rt",
                "token_type": "Bearer",
            }
        ]
    )
    provider, hub = _build_provider(http_client=http_client)

    persisted = provider.exchange_code_for_token(
        code="auth-code",
        redirect_uri="http://127.0.0.1:53682/callback/youdao",
        code_verifier=None,
    )

    assert persisted["access_token"] == "token-no-expiry"
    assert persisted["expires_at"] is None
