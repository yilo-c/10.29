from __future__ import annotations

import base64
import hashlib
import secrets


def generate_oauth_state(length: int = 32) -> str:
    """生成高熵 state，防 CSRF。"""

    size = max(16, int(length))
    return secrets.token_urlsafe(size)


def generate_pkce_verifier_challenge() -> tuple[str, str]:
    """生成 RFC 7636 推荐的 verifier/challenge (S256)。"""

    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge
