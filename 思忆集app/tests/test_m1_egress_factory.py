"""M1.6a 出域治理：``services/http_client`` 工厂 + ``scripts/egress_ratchet`` 棘轮。

工厂侧断言：超时兜底、UA 注入与不覆盖、出域事件的 host 级脱敏、钩子异常隔离、
session 关闭保证、审计钩子复用既有 integration_audit 表（不新建表）。
棘轮侧断言：直连/工厂调用点统计、新文件禁直连、存量只减不增。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services import http_client as hc  # noqa: E402


def _load_ratchet():
    path = REPO_ROOT / "scripts" / "egress_ratchet.py"
    spec = importlib.util.spec_from_file_location("egress_ratchet", str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hooks():
    """隔离全局钩子表，避免用例间串扰。"""
    saved = list(hc.EGRESS_HOOKS)
    hc.EGRESS_HOOKS.clear()
    collected: list[dict] = []
    hc.register_egress_hook(collected.append)
    yield collected
    hc.EGRESS_HOOKS.clear()
    hc.EGRESS_HOOKS.extend(saved)


@pytest.fixture(scope="module")
def ratchet():
    return _load_ratchet()


# ── 工厂：超时 / UA ──


@pytest.mark.asyncio
async def test_new_session_applies_default_timeout_and_user_agent():
    session = hc.new_session("probe.default")
    try:
        assert session.timeout.total == hc.DEFAULT_TIMEOUT_TOTAL
        assert session.timeout.connect == hc.DEFAULT_TIMEOUT_CONNECT
        assert session.headers["User-Agent"] == hc.DEFAULT_USER_AGENT
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_explicit_timeout_and_user_agent_win():
    session = hc.new_session(
        "probe.explicit",
        timeout=hc.make_timeout(total=1.5, connect=None),
        user_agent="custom-agent/9",
    )
    try:
        assert session.timeout.total == 1.5
        assert session.headers["User-Agent"] == "custom-agent/9"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_caller_headers_user_agent_is_never_overridden():
    session = hc.new_session("probe.ua", headers={"User-Agent": "real-browser/120", "Accept": "application/json"})
    try:
        assert session.headers["User-Agent"] == "real-browser/120"
        assert session.headers["Accept"] == "application/json"
    finally:
        await session.close()


def test_egress_target_redacts_path_and_query():
    assert hc.egress_target("https://api.example.com:8443/v1/secrets?token=abc") == "api.example.com:8443"
    assert hc.egress_target("https://api.example.com/feed") == "api.example.com"
    assert "secrets" not in hc.egress_target("https://api.example.com/v1/secrets?token=abc")


# ── 工厂：出域日志钩子 ──


@pytest.mark.asyncio
async def test_request_emits_redacted_egress_event(hooks):
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/deep/path", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        async with hc.egress_session("probe.request", total=5) as session:
            async with session.get(server.make_url("/deep/path?secret=leak")) as resp:
                assert resp.status == 200
    finally:
        await server.close()

    kinds = [e["kind"] for e in hooks]
    assert hc.KIND_SESSION_OPEN in kinds and hc.KIND_SESSION_CLOSE in kinds
    req = [e for e in hooks if e["kind"] == hc.KIND_REQUEST]
    assert len(req) == 1
    assert req[0]["purpose"] == "probe.request"
    assert req[0]["method"] == "GET"
    assert req[0]["status"] == 200
    assert isinstance(req[0]["elapsed_ms"], (int, float))
    # 脱敏：只留 host[:port]，绝不含 path / query
    assert "deep" not in req[0]["target"] and "secret" not in req[0]["target"]
    assert req[0]["target"].startswith("127.0.0.1")


@pytest.mark.asyncio
async def test_egress_request_exception_is_reported(hooks):
    server = TestServer(web.Application())
    await server.start_server()
    url = server.make_url("/gone")
    await server.close()  # 端口已释放 → 连接必然失败

    # 2026-09-18：显式 `trust_env=False` —— 本用例验证的是"连接失败必须被记账"，
    # 不能依赖运行环境里有没有代理。实测：本沙箱注入了
    # `HTTP_PROXY=http://127.0.0.1:63628`（平台透明代理），而 `new_session` 默认
    # trust_env=True → 本该失败的连接被代理接走并返回响应 → `DID NOT RAISE` 假红。
    # 对照实验（清掉代理环境变量后单跑本文件）：13 passed，确认是环境干扰非代码缺陷。
    async with hc.egress_session("probe.error", total=5, trust_env=False) as session:
        with pytest.raises(Exception):
            await session.get(url)

    req = [e for e in hooks if e["kind"] == hc.KIND_REQUEST]
    assert req and req[-1]["error"]
    assert req[-1]["status"] is None


@pytest.mark.asyncio
async def test_broken_hook_never_breaks_egress_request(hooks):
    def boom(_event: dict) -> None:
        raise RuntimeError("hook exploded")

    hc.register_egress_hook(boom)

    async def handler(request: web.Request) -> web.Response:
        return web.Response(text="pong")

    app = web.Application()
    app.router.add_get("/ping", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        async with hc.egress_session("probe.hookfail", total=5) as session:
            async with session.get(server.make_url("/ping")) as resp:
                assert resp.status == 200
                assert await resp.text() == "pong"
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_egress_session_always_closes_session(hooks):
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/x", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        async with hc.egress_session("probe.close", total=5) as session:
            async with session.get(server.make_url("/x")) as resp:
                assert resp.status == 200
        assert session.closed is True
    finally:
        await server.close()


def test_register_and_unregister_egress_hook_are_idempotent():
    saved = list(hc.EGRESS_HOOKS)
    hc.EGRESS_HOOKS.clear()
    try:
        seen: list[dict] = []
        hc.register_egress_hook(seen.append)
        hc.register_egress_hook(seen.append)  # 同一对象重复注册只生效一次
        assert len(hc.EGRESS_HOOKS) == 1
        hc.emit_egress({"kind": "x"})
        assert len(seen) == 1
        assert hc.unregister_egress_hook(seen.append) is True
        assert hc.unregister_egress_hook(seen.append) is False
        with pytest.raises(TypeError):
            hc.register_egress_hook("not-callable")  # type: ignore[arg-type]
    finally:
        hc.EGRESS_HOOKS.clear()
        hc.EGRESS_HOOKS.extend(saved)


def test_make_audit_hook_reuses_existing_audit_table(tmp_path):
    from core.audit_writer import AuditWriter

    writer = AuditWriter(str(tmp_path))
    hook = hc.make_audit_hook(writer)
    try:
        hook(
            {
                "kind": hc.KIND_REQUEST,
                "purpose": "probe.audit",
                "target": "api.example.com",
                "method": "POST",
                "status": 201,
                "elapsed_ms": 12.5,
                "error": None,
            }
        )
        writer.drain()
        rows = writer.fetch_integration(provider="egress")
        assert len(rows) == 1
        assert rows[0]["event"] == hc.KIND_REQUEST
        assert rows[0]["status"] == "ok"
        assert rows[0]["payload"]["source"] == "egress_factory"
        assert rows[0]["payload"]["target"] == "api.example.com"
    finally:
        writer.shutdown()


# ── 棘轮脚本 ──


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_ratchet_scan_counts_direct_and_factory_sites(tmp_path, ratchet):
    root = tmp_path / "prod"
    _write(
        root / "svc_direct.py",
        "import aiohttp\n\n\nasync def f():\n    async with aiohttp.ClientSession() as s:\n        return s\n",
    )
    _write(
        root / "svc_factory.py",
        'from services.http_client import egress_session\n\n\nasync def f():\n    async with egress_session("x") as s:\n        return s\n',
    )
    _write(
        root / "pkg" / "svc_two.py",
        "import aiohttp\n\n\nasync def a():\n    return aiohttp.ClientSession()\n\n\nasync def b():\n    return aiohttp.ClientSession()\n",
    )
    _write(root / "svc_ignored.py", "import aiohttp\n\n\nasync def f():\n    return aiohttp.ClientSession()  # egress-ratchet: ignore\n")

    result = ratchet.scan([str(root)])
    keys = list(result["production"])
    rel_direct = [k for k in keys if k.endswith("svc_direct.py")]
    assert len(rel_direct) == 1
    assert result["factory_sites"] == 1
    assert result["production_direct_total"] == 3, result["production"]
    assert len(result["production"][rel_direct[0]]) == 1
    assert not any(k.endswith("svc_ignored.py") for k in keys), "带 ignore 标记的行不应计入"
    assert not any(k.endswith("svc_factory.py") for k in keys), "走工厂的文件不应出现在直连清单"


def test_ratchet_baseline_rejects_new_file_and_increases(tmp_path, ratchet):
    root = tmp_path / "prod"
    _write(root / "svc_a.py", "import aiohttp\n\n\nasync def f():\n    return aiohttp.ClientSession()\n")
    result = ratchet.scan([str(root)])
    key = next(k for k in result["production"] if k.endswith("svc_a.py"))

    baseline = {"version": 1, "total_direct": 2, "files": {key: 2}}
    assert ratchet.check_against_baseline(result, baseline) == []

    grown = {"version": 1, "total_direct": 0, "files": {key: 0, "legacy/other": 0}}
    problems = ratchet.check_against_baseline(result, grown)
    assert any("只减不增" in p for p in problems), problems

    stranger = {"version": 1, "total_direct": 1, "files": {"prod/svc_other.py": 1}}
    problems = ratchet.check_against_baseline(result, stranger)
    assert any("新增直连文件" in p for p in problems), problems


def test_ratchet_shipped_baseline_matches_repo(ratchet):
    """仓库基线必须与当前代码一致（防止有人加了直连却不刷基线）。"""
    baseline_path = REPO_ROOT / "scripts" / "egress-baseline.json"
    assert baseline_path.exists(), "缺少 scripts/egress-baseline.json，请跑 scripts/egress_ratchet.py --update-baseline"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    result = ratchet.scan(list(ratchet.DEFAULT_ROOTS))
    problems = ratchet.check_against_baseline(result, baseline)
    assert problems == [], problems
