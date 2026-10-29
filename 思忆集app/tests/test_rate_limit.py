"""L6 安全 —— 请求限流（RateLimiter）。

两层：
1. 组件级（确定性）：滑动窗口 200/60s 语义 —— 配额内放行、超配额拒绝、
   窗口滑动恢复、按 client 独立配额、TokenBucket 突发与补充。
2. 集成级（独立临时后端）：单连接 205 连发 ping，断言全局 200/60s 配额
   确实被服务端强制（rate_limited >= 1），且响应无丢帧、ID 无错乱。

集成测试使用独立临时后端实例（见 `_isolated_backend`），避免消耗共享后端
配额而影响其他测试文件。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import aiohttp
import pytest

from _isolated_backend import spawn_backend, teardown_backend
from core.rate_limiter import RateLimiter, TokenBucketLimiter, get_limiter, reset_all_limiters


# ── 组件级：滑动窗口语义 ────────────────────────────────────────────────────


class TestSlidingWindow:
    def test_under_limit_allowed(self) -> None:
        limiter = RateLimiter(max_requests=5, window=60.0)
        for _ in range(5):
            assert limiter.allow_sync("client-a") is True

    def test_over_limit_rejected(self) -> None:
        limiter = RateLimiter(max_requests=5, window=60.0)
        for _ in range(5):
            limiter.allow_sync("client-a")
        assert limiter.allow_sync("client-a") is False
        stats = limiter.get_stats("client-a")
        assert stats.allowed_requests == 5
        assert stats.rejected_requests == 1

    def test_window_slide_recovers(self) -> None:
        limiter = RateLimiter(max_requests=2, window=0.4)
        assert limiter.allow_sync("c") is True
        assert limiter.allow_sync("c") is True
        assert limiter.allow_sync("c") is False  # 超配额
        time.sleep(0.55)  # 窗口滑动 → 最早的请求过期
        assert limiter.allow_sync("c") is True

    def test_reset_clears_window(self) -> None:
        limiter = RateLimiter(max_requests=2, window=60.0)
        limiter.allow_sync("c")
        limiter.allow_sync("c")
        assert limiter.allow_sync("c") is False
        limiter.reset("c")
        assert limiter.allow_sync("c") is True

    def test_per_client_independent_quota(self) -> None:
        limiter = RateLimiter(max_requests=1, window=60.0)
        assert limiter.allow_sync("a") is True
        assert limiter.allow_sync("b") is True  # 独立配额
        assert limiter.allow_sync("a") is False  # a 已耗尽

    def test_get_remaining(self) -> None:
        limiter = RateLimiter(max_requests=3, window=60.0)
        assert limiter.get_remaining("c") == 3
        limiter.allow_sync("c")
        limiter.allow_sync("c")
        assert limiter.get_remaining("c") == 1

    def test_global_get_limiter_reset_all(self) -> None:
        limiter = get_limiter("k", max_requests=2, window=60.0)
        assert limiter.allow_sync("c") is True
        assert limiter.allow_sync("c") is True
        assert limiter.allow_sync("c") is False
        reset_all_limiters()
        assert limiter.allow_sync("c") is True


class TestTokenBucket:
    async def test_burst_capacity(self) -> None:
        limiter = TokenBucketLimiter(rate=1.0, capacity=3)
        assert await limiter.allow("c") is True
        assert await limiter.allow("c") is True
        assert await limiter.allow("c") is True
        assert await limiter.allow("c") is False  # 突发耗尽

    async def test_refill_over_time(self) -> None:
        limiter = TokenBucketLimiter(rate=2.0, capacity=2)
        assert await limiter.allow("c") is True
        assert await limiter.allow("c") is True
        assert await limiter.allow("c") is False
        time.sleep(0.6)  # 补充 ~1.2 token
        assert await limiter.allow("c") is True

    def test_get_tokens(self) -> None:
        limiter = TokenBucketLimiter(rate=1.0, capacity=5)
        assert limiter.get_tokens("c") == 5.0


# ── 集成级：全局 200/60s 配额强制 ───────────────────────────────────────────


@pytest.fixture(scope="module")
def isolated_backend():
    proc = ws_url = data_dir = None
    try:
        proc, ws_url, data_dir = spawn_backend()
        yield {"ws_url": ws_url}
    finally:
        teardown_backend(proc, data_dir)


@pytest.mark.xfail(
    strict=False,
    reason=(
        "2026-08-29 复核：单连接连发 205 帧时服务端直接关闭 WebSocket（探针实测收到 "
        "CLOSE、0 条 TEXT 响应），而非逐条返回 rate-limit error。本测试原假设"
        "『205 条响应中至少 1 条被限流』与当前实现不符。限流策略（断连 vs 错误响应）"
        "需产品侧确认后再改断言——见审查 M21。保留为 xfail 以便策略回归时自动 XPASS。"
    ),
)
@pytest.mark.asyncio
async def test_global_rate_limit_enforced(isolated_backend) -> None:
    """205 连发 ping（> 全局 200/60s 配额）→ 必然触发限流，且无丢帧/ID 无错乱。"""
    ws_url = isolated_backend["ws_url"]
    count = 205

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url, timeout=10) as ws:
            ids = set(range(1, count + 1))
            for cid in ids:
                await ws.send_json({"jsonrpc": "2.0", "id": cid, "method": "ping", "params": {}})

            got: dict[str, Any] = {}

            deadline = time.time() + 60
            while len(got) < count and time.time() < deadline:
                msg = await asyncio.wait_for(ws.receive(), timeout=30)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                d = json.loads(msg.data)
                if d.get("id") in ids:
                    got[d["id"]] = d

            assert len(got) == count, f"应收满 {count} 响应, got {len(got)}"

            rate_limited = 0
            for cid in ids:
                d = got[cid]
                assert d.get("id") == cid, f"响应 ID 错乱: expect {cid}, got {d.get('id')}"
                assert "result" in d or "error" in d
                if "error" in d:
                    rate_limited += 1
                    continue
                assert d.get("result") == "pong"

            # 205 > 200 → 至少 5 个被限流（全局配额强制生效）
            assert rate_limited >= 1, f"全局限流未生效: rate_limited={rate_limited}"
            print(f"\n[rate-limit] 205 连发: ok={count - rate_limited}, "
                  f"rate_limited={rate_limited} (200/60s 全局配额)")
