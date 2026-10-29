from __future__ import annotations

import sys
from pathlib import Path

import httpx

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from integrations.integration_http_client import ApiHttpClient


def test_http_client_mounts_retry_adapter_and_sends_timeout():
    client = ApiHttpClient(timeout=(3.05, 9.0))
    assert client.timeout == (3.05, 9.0)
    assert client._client is not None
    assert isinstance(client._client, httpx.Client)
