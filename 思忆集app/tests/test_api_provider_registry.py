from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from api.oauth_security import generate_oauth_state, generate_pkce_verifier_challenge
from api.provider_registry import ApiProviderRegistry, ApiProviderSpec, create_default_api_provider_registry


def test_provider_registry_register_and_lookup():
    registry = ApiProviderRegistry()
    registry.register(
        ApiProviderSpec(
            provider_id="demo",
            display_name="Demo",
            auth_type="oauth2",
            scopes=("notes.read",),
        )
    )

    spec = registry.get("demo")
    assert spec is not None
    assert spec.provider_id == "demo"
    assert spec.auth_type == "oauth2"
    assert "demo" in registry.known_provider_ids()


def test_default_registry_contains_youdao_and_automation_hub():
    registry = create_default_api_provider_registry()
    assert registry.get("youdao") is not None
    assert registry.get("automation_hub") is not None


def test_oauth_security_generates_state_and_pkce_pair():
    state1 = generate_oauth_state()
    state2 = generate_oauth_state()
    assert state1
    assert state1 != state2

    verifier, challenge = generate_pkce_verifier_challenge()
    assert len(verifier) >= 43
    assert len(challenge) >= 43
