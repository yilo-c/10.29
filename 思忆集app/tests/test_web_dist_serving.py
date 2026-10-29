# -*- coding: utf-8 -*-
"""L4 前端托管可达性测试（T7 · 2026-08-30 全模块测试战役）。

背景（T7 侦察，2026-08-30）：
- "思忆集app/web_dist/" 是 desk-notes 桌面前端的 PWA 构建产物副本
  （desk-notes/scripts/sync-dist.mjs: "packages/desktop/dist" ->
  "思忆集app/web_dist"），共 369 个文件，含 index.html / assets/ / sw.js。
- 但 "backend/ws_server.py" 只注册 JSON-RPC / 健康 / 配对端点
  （ws_server.py:143-153），没有任何 add_static 托管 web_dist
  （全仓 grep "add_static" = 0 命中）。Electron 桌面壳经 file:// 加载
  自身 dist；浏览器/手机端无法经 HTTP 访问该前端。

本测试对 SIYI_INTEGRATION_AUTOSTART 隔离临时实例做 HTTP 探测，断言
web_dist 被 HTTP 托管且返回 HTML。T7 已修复（2026-08-30）：
WsServer._mount_web_dist() 经 add_static("/app" | "/web_dist" | "/")
+ 显式 index 回退（"/"、"/app"、"/app/"、"/index.html"、"/web_dist/"）
挂载 web_dist，静态前端产物免鉴权（与 /discover 同语义）；
本测试已摘除 xfail(strict=True) 标记转正。

红/绿对照（证明红不是"后端没起来"的假红）：
- test_http_positive_control_health: /health 带 Bearer 可达（200 JSON）；
  实测临时实例 /health 强制认证（V2.1 批次1#4），无 token 返回 401。
- test_http_positive_control_discover: /discover 免鉴权可达（200）。
"""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import pytest

pytestmark = pytest.mark.integration

APP_DIR = Path(__file__).resolve().parents[1]
WEB_DIST = APP_DIR / "web_dist"

# 候选托管路径（常见约定全试一遍）
CANDIDATE_PATHS = (
    "/",
    "/app",
    "/app/",
    "/index.html",
    "/app/index.html",
    "/web_dist/index.html",
    "/web_dist/",
)

# 路由注册现状（T7 逐行核对 backend/ws_server.py WsServer.__init__，2026-08-30）
REGISTERED_ROUTES = {
    "GET /ws": "思忆集app/backend/ws_server.py:143",
    "GET /health": "思忆集app/backend/ws_server.py:144",
    "GET /stats": "思忆集app/backend/ws_server.py:145",
    "GET /metrics": "思忆集app/backend/ws_server.py:146",
    "POST /rpc": "思忆集app/backend/ws_server.py:147",
    "OPTIONS /rpc": "思忆集app/backend/ws_server.py:148",
    "POST /auth": "思忆集app/backend/ws_server.py:149",
    "GET /discover": "思忆集app/backend/ws_server.py:150",
    "POST /pair": "思忆集app/backend/ws_server.py:151",
    "OPTIONS /discover": "思忆集app/backend/ws_server.py:152",
    "OPTIONS /pair": "思忆集app/backend/ws_server.py:153",
}


@pytest.fixture(scope="module")
def http_base_url():
    """模块级隔离后端（随机端口+临时数据目录，模块结束即启停干净）。

    P2 隔离修复（2026-08-31）：原 session 级 fixture 经 conftest
    _maybe_autostart_backend() 拉起临时后端后持有全局单实例锁
    %TEMP%/siyi_headless.lock 直至 atexit（整个 pytest 会话结束），
    与 test_startup_smoke.py 的隔离后端在同一进程连跑时必然锁冲突
    （TAKEOVER=0，3 次重试全撞锁 → 3 errors）。

    现改用 tests/_isolated_backend.spawn_backend/teardown_backend：
    - 自动选取空闲端口 + 独立临时数据目录（spawn_backend 内建）；
    - 模块级作用域：本模块最后一个用例结束即在 finally 中 terminate
      进程并清理目录，锁随进程退出释放，不跨模块持有；
    - token 注入 SIYI_DIAG_TOKEN（供 conftest _auth_headers() 使用），
      finally 中还原环境变量（module 级 fixture 不能用 function 级
      monkeypatch，故手动 setenv/restore）。
    """
    import os

    from _isolated_backend import spawn_backend, teardown_backend

    proc = ws_url = data_dir = None
    last_err: Exception | None = None
    for _attempt in range(3):  # 与 test_startup_smoke 同款退避：撞锁时重试
        try:
            proc, ws_url, data_dir = spawn_backend(timeout=120.0)
            break
        except RuntimeError as exc:
            last_err = exc
            time.sleep(15.0)
    if proc is None:
        pytest.fail(f"隔离后端 3 次尝试均未能启动：{last_err}")

    saved_token = os.environ.get("SIYI_DIAG_TOKEN")
    try:
        port = int(urlparse(ws_url).port)
        token_path = Path(data_dir) / "auth_token"
        deadline = time.time() + 30.0
        token = ""
        while time.time() < deadline:
            if token_path.is_file():
                token = token_path.read_text(encoding="utf-8").strip()
                if token:
                    break
            if proc.poll() is not None:
                break
            time.sleep(0.3)
        assert token, f"隔离后端未产出 auth_token（{token_path}）"
        os.environ["SIYI_DIAG_TOKEN"] = token
        yield f"http://127.0.0.1:{port}"
    finally:
        if saved_token is None:
            os.environ.pop("SIYI_DIAG_TOKEN", None)
        else:
            os.environ["SIYI_DIAG_TOKEN"] = saved_token
        teardown_backend(proc, data_dir)


def _auth_headers() -> dict[str, str]:
    """透传 conftest 的 Bearer token（AUTOSTART 后端强制认证，HTTP 端点同样校验）。"""
    import conftest as _ct

    return _ct._auth_headers()


async def _probe(base_url: str, path: str, headers: dict | None = None) -> dict:
    """GET 探测单路径，返回 {status, content_type, body_head}。"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                base_url + path,
                headers=headers or {},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                body = await resp.text(errors="replace")
                return {
                    "status": resp.status,
                    "content_type": resp.headers.get("Content-Type", ""),
                    "body_head": body[:120],
                }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "content_type": "",
            "body_head": f"{type(exc).__name__}: {exc}",
        }


async def test_http_positive_control_health(http_base_url):
    """阳性对照1（绿）：/health 带 Bearer token 可达且返回 JSON。"""
    r = await _probe(http_base_url, "/health", headers=_auth_headers())
    assert r["status"] == 200, f"/health 带 token 应 200，got {r}"
    assert "application/json" in r["content_type"], f"/health 应返回 JSON, got {r}"


async def test_http_health_requires_auth_on_temp_instance(http_base_url):
    """现状记录（绿）：临时实例 /health 无 token 返回 401（强制认证语义）。"""
    r = await _probe(http_base_url, "/health")
    assert r["status"] == 401, f"/health 无 token 预期 401（后端强制认证），got {r}"


async def test_http_positive_control_discover(http_base_url):
    """阳性对照2（绿）：/discover 免鉴权可达 —— 未认证客户端也有可达面。"""
    r = await _probe(http_base_url, "/discover")
    assert r["status"] == 200, f"/discover（免鉴权端点）应 200, got {r}"
    assert "application/json" in r["content_type"], f"/discover 应返回 JSON, got {r}"


def test_web_dist_artifact_present():
    """前置断言（绿）：web_dist 构建产物在盘上存在 —— 红只能是"未托管"。"""
    index_html = WEB_DIST / "index.html"
    assert index_html.is_file(), f"web_dist/index.html 缺失: {index_html}"
    html = index_html.read_text(encoding="utf-8", errors="replace")
    assert "<!doctype html" in html.lower() or "<html" in html.lower(), f"web_dist/index.html 不是 HTML 文档: {html[:120]}"
    assets = WEB_DIST / "assets"
    assert assets.is_dir() and any(assets.iterdir()), "web_dist/assets 为空"


async def test_web_dist_served_over_http(http_base_url):
    """核心断言（T7 已修复转正）：web_dist 应被后端 HTTP 托管且返回 HTML。

    修复（2026-08-30）：backend/ws_server.py WsServer._mount_web_dist() 以
    add_static 挂载 /app、/web_dist 与根路径 + 显式 index 回退；
    静态前端产物免鉴权（与 /discover 同语义），未认证客户端可达。
    """
    results = {p: await _probe(http_base_url, p) for p in CANDIDATE_PATHS}
    hits = [p for p, r in results.items() if r["status"] == 200 and "text/html" in r["content_type"].lower()]
    assert hits, (
        "web_dist 前端未被 HTTP 托管：所有候选路径均非 200/text/html。\n"
        f"探测结果: {results}\n"
        "核对 backend/ws_server.py WsServer._mount_web_dist()：应挂载 "
        "/app、/web_dist 与根路径静态路由 + '/'->index.html 回退"
    )
    r = results[hits[0]]
    assert "<!doctype html" in r["body_head"].lower() or "<html" in r["body_head"].lower(), f"命中路径 {hits[0]} 返回内容不是 HTML 文档: {r}"
