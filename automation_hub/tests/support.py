from __future__ import annotations

import asyncio

import httpx

from api import db
from api.config import settings
from api.main import app
from worker import arq_settings as arq_settings_module


class DummyArqQueue:
    """ARQ 入队模拟：记录调用但不连接 Redis。"""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, func_name, *args, **kwargs):
        self.calls.append(((func_name, args), kwargs))
        return "dummy-job-id"


class AppClient:
    def __init__(self, asgi_app) -> None:
        self.app = asgi_app
        self.base_url = "http://testserver"

    async def _request_async(self, method: str, url: str, **kwargs):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url=self.base_url) as client:
            return await client.request(method, url, **kwargs)

    def request(self, method: str, url: str, **kwargs):
        return asyncio.run(self._request_async(method, url, **kwargs))

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs):
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs):
        return self.request("DELETE", url, **kwargs)


def configure_test_env(tmp_path, monkeypatch, *, with_queue: bool = False):
    database_path = tmp_path / "automation_hub.sqlite3"
    runs_dir = tmp_path / "runs"
    data_dir = tmp_path / "data"
    workspace_dir = tmp_path / "workspace"

    monkeypatch.setattr(db, "DATABASE_PATH", str(database_path))
    monkeypatch.setattr(settings, "DATABASE_PATH", database_path)
    monkeypatch.setattr(settings, "RUNS_DIR", runs_dir)
    monkeypatch.setattr(settings, "DATA_DIR", data_dir)
    monkeypatch.setattr(settings, "WORKSPACE_DIR", workspace_dir)

    # Also patch the automation_hub.api.config settings (separate module instance
    # due to dual sys.path import). This ensures route handlers using absolute
    # automation_hub.api.xxx imports see the test database.
    import automation_hub.api.config as _ah_cfg
    monkeypatch.setattr(_ah_cfg.settings, "DATABASE_PATH", database_path)
    monkeypatch.setattr(_ah_cfg.settings, "RUNS_DIR", runs_dir)
    monkeypatch.setattr(_ah_cfg.settings, "DATA_DIR", data_dir)
    monkeypatch.setattr(_ah_cfg.settings, "WORKSPACE_DIR", workspace_dir)

    # 重置 SQLAlchemy 会话工厂，使其重新绑定到测试数据库
    from api.db.session import _reset_session_factory
    _reset_session_factory()

    dummy_queue = None
    if with_queue:
        dummy_queue = DummyArqQueue()

        # Patch the actual source module used by the app (automation_hub.* import path)
        import automation_hub.worker.arq_settings as _ah_arq
        monkeypatch.setattr(_ah_arq, "enqueue_job_sync", dummy_queue)

        # Patch all importing modules — use sys.modules directly to handle dual-path imports
        import sys
        for _mod_key in sorted(sys.modules):
            if _mod_key.endswith(("tool_runs", "approvals.router")) and hasattr(sys.modules[_mod_key], "enqueue_job_sync"):
                monkeypatch.setattr(sys.modules[_mod_key], "enqueue_job_sync", dummy_queue)

    db.init_db()
    return {
        "database_path": database_path,
        "runs_dir": runs_dir,
        "data_dir": data_dir,
        "workspace_dir": workspace_dir,
        "dummy_queue": dummy_queue,
    }


def bootstrap_headers(client: AppClient) -> dict[str, str]:
    response = client.post(
        "/auth/bootstrap",
        json={
            "admin_name": "Admin",
            "device_name": "pytest",
            "device_platform": "windows",
        },
    )
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def register_tool(
    client: AppClient,
    headers: dict[str, str],
    tool_id: str,
    risk_level: str,
    *,
    command: list[str] | None = None,
    executor: str = "host",
    args_schema: dict | None = None,
    allowed_paths: list[str] | None = None,
    cwd: str | None = None,
) -> None:
    response = client.post(
        "/tools",
        headers=headers,
        json={
            "id": tool_id,
            "name": tool_id,
            "description": f"tool {tool_id}",
            "risk_level": risk_level,
            "executor": executor,
            "command": command or ["echo", tool_id],
            "args_schema": args_schema or {},
            "timeout_sec": 10,
            "allowed_paths": allowed_paths or [],
            "cwd": cwd,
        },
    )
    assert response.status_code == 200, response.text


def make_client() -> AppClient:
    return AppClient(app)