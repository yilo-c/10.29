"""Tests for core.rate_limiter module."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.errors import RateLimitedError
from core.rate_limiter import (
    RateLimiter,
    TokenBucketLimiter,
    get_limiter,
    rate_limit,
    reset_all_limiters,
)


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global limiters before and after each test."""
    reset_all_limiters()
    yield
    reset_all_limiters()


class TestRateLimiter:
    async def test_allows_up_to_max_requests_then_rejects(self):
        limiter = RateLimiter(max_requests=3, window=0.2, cleanup_interval=60.0)
        client_id = "client_1"

        assert await limiter.allow(client_id) is True
        assert await limiter.allow(client_id) is True
        assert await limiter.allow(client_id) is True
        assert await limiter.allow(client_id) is False

    async def test_window_expiration_allows_requests_again(self):
        limiter = RateLimiter(max_requests=2, window=0.1, cleanup_interval=60.0)
        client_id = "client_2"

        assert await limiter.allow(client_id) is True
        assert await limiter.allow(client_id) is True
        assert await limiter.allow(client_id) is False

        time.sleep(0.15)

        assert await limiter.allow(client_id) is True
        assert await limiter.allow(client_id) is True
        assert await limiter.allow(client_id) is False

    async def test_get_remaining_and_stats_accuracy(self):
        limiter = RateLimiter(max_requests=5, window=0.2, cleanup_interval=60.0)
        client_id = "client_3"

        assert limiter.get_remaining(client_id) == 5

        await limiter.allow(client_id)
        assert limiter.get_remaining(client_id) == 4

        await limiter.allow(client_id)
        await limiter.allow(client_id)
        assert limiter.get_remaining(client_id) == 2

        await limiter.allow(client_id)
        await limiter.allow(client_id)
        assert limiter.get_remaining(client_id) == 0
        assert await limiter.allow(client_id) is False

        stats = limiter.get_stats(client_id)
        assert stats.total_requests == 6
        assert stats.allowed_requests == 5
        assert stats.rejected_requests == 1
        assert stats.last_rejected_at > 0

        all_stats = limiter.get_stats()
        assert client_id in all_stats
        assert all_stats[client_id].total_requests == 6

    def test_allow_sync(self):
        limiter = RateLimiter(max_requests=2, window=0.2, cleanup_interval=60.0)
        client_id = "client_sync"

        assert limiter.allow_sync(client_id) is True
        assert limiter.allow_sync(client_id) is True
        assert limiter.allow_sync(client_id) is False

    async def test_reset(self):
        limiter = RateLimiter(max_requests=2, window=0.2, cleanup_interval=60.0)
        client_id = "client_reset"

        await limiter.allow(client_id)
        await limiter.allow(client_id)
        assert await limiter.allow(client_id) is False

        limiter.reset(client_id)
        assert limiter.get_remaining(client_id) == 2
        assert await limiter.allow(client_id) is True

        limiter.reset()
        assert limiter.get_stats() == {}


class TestTokenBucketLimiter:
    async def test_allows_up_to_capacity_burst(self):
        limiter = TokenBucketLimiter(rate=10.0, capacity=5)
        client_id = "bucket_1"

        for _ in range(5):
            assert await limiter.allow(client_id) is True

        assert await limiter.allow(client_id) is False

    async def test_refills_over_time(self):
        limiter = TokenBucketLimiter(rate=20.0, capacity=4)
        client_id = "bucket_2"

        for _ in range(4):
            assert await limiter.allow(client_id) is True
        assert await limiter.allow(client_id) is False

        time.sleep(0.15)

        assert await limiter.allow(client_id) is True

    async def test_get_tokens_and_stats(self):
        limiter = TokenBucketLimiter(rate=10.0, capacity=5)
        client_id = "bucket_3"

        assert limiter.get_tokens(client_id) == 5.0

        await limiter.allow(client_id)
        tokens = limiter.get_tokens(client_id)
        assert 3.9 < tokens <= 4.1

        stats = limiter.get_stats(client_id)
        assert stats.total_requests == 1
        assert stats.allowed_requests == 1
        assert stats.rejected_requests == 0

        assert await limiter.allow(client_id, tokens=10) is False
        # Tokens are not consumed on rejection; bucket should still be ~4.0
        assert limiter.get_tokens(client_id) > 3.9
        rejected_stats = limiter.get_stats(client_id)
        assert rejected_stats.rejected_requests == 1

    async def test_reset(self):
        limiter = TokenBucketLimiter(rate=10.0, capacity=3)
        client_id = "bucket_reset"

        for _ in range(3):
            assert await limiter.allow(client_id) is True
        assert await limiter.allow(client_id) is False

        limiter.reset(client_id)
        assert limiter.get_tokens(client_id) == 3.0
        assert await limiter.allow(client_id) is True

        limiter.reset()
        assert limiter.get_stats() == {}


class TestRateLimitDecorator:
    async def test_raises_rate_limited_error_when_exceeded(self):
        limiter = RateLimiter(max_requests=2, window=0.2, cleanup_interval=60.0)

        @rate_limit(limiter, client_id_getter=lambda ctx: ctx["client_id"])
        async def handle_request(ctx):
            return "ok"

        ctx = {"client_id": "decorated_client"}

        assert await handle_request(ctx) == "ok"
        assert await handle_request(ctx) == "ok"

        with pytest.raises(RateLimitedError) as exc_info:
            await handle_request(ctx)

        assert exc_info.value.details.get("client_id") == "decorated_client"
        assert exc_info.value.details.get("retry_after") == 0.2

    async def test_on_rejected_callback(self):
        limiter = RateLimiter(max_requests=1, window=0.2, cleanup_interval=60.0)

        async def rejected_handler(ctx):
            return {"rejected": True}

        @rate_limit(
            limiter,
            client_id_getter=lambda ctx: ctx["client_id"],
            on_rejected=rejected_handler,
        )
        async def handle_request(ctx):
            return "ok"

        ctx = {"client_id": "callback_client"}

        assert await handle_request(ctx) == "ok"
        assert await handle_request(ctx) == {"rejected": True}


class TestGlobalLimiters:
    def test_get_limiter_creates_and_reuses_instances(self):
        limiter_a = get_limiter("test_sw", limiter_type="sliding_window", max_requests=10, window=60.0)
        limiter_b = get_limiter("test_sw", limiter_type="sliding_window", max_requests=99, window=1.0)

        assert limiter_a is limiter_b
        assert limiter_a.max_requests == 10
        assert isinstance(limiter_a, RateLimiter)

        bucket_a = get_limiter("test_tb", limiter_type="token_bucket", rate=5.0, capacity=10)
        bucket_b = get_limiter("test_tb", limiter_type="token_bucket", rate=1.0, capacity=1)

        assert bucket_a is bucket_b
        assert bucket_a.rate == 5.0
        assert isinstance(bucket_a, TokenBucketLimiter)

    def test_reset_all_limiters(self):
        sw = get_limiter("sw_reset", limiter_type="sliding_window", max_requests=2, window=60.0)
        tb = get_limiter("tb_reset", limiter_type="token_bucket", rate=1.0, capacity=2)

        reset_all_limiters()

        sw2 = get_limiter("sw_reset", limiter_type="sliding_window", max_requests=2, window=60.0)
        tb2 = get_limiter("tb_reset", limiter_type="token_bucket", rate=1.0, capacity=2)

        assert sw is not sw2
        assert tb is not tb2
