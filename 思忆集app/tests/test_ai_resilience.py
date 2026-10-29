"""Tests for AI core modules: CircuitBreaker, ProviderRouting, LLM Resilience."""
from __future__ import annotations

import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

# ═══════════════════════════════════════════════════════════════
# Module: ai/circuit_breaker.py
# ═══════════════════════════════════════════════════════════════


class TestCircuitBreaker:
    """CircuitBreaker states: CLOSED → OPEN → HALF_OPEN → CLOSED"""

    def test_initial_state_is_closed(self):
        from ai.circuit_breaker import CircuitBreaker, CircuitState
        cb = CircuitBreaker("test")
        assert cb.state == CircuitState.CLOSED
        assert cb.is_call_permitted() is True

    def test_after_failure_threshold_opens(self):
        from ai.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=3, timeout_seconds=3600))
        for _ in range(3):
            cb.record_failure_sync(None)
        assert cb.state == CircuitState.OPEN

    def test_open_state_blocks_call(self):
        from ai.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=2, timeout_seconds=3600))
        cb.record_failure_sync(None)
        cb.record_failure_sync(None)
        # OPEN → not permitted
        assert cb.is_call_permitted() is False

    def test_open_transitions_to_half_open_after_timeout(self):
        from ai.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=1, timeout_seconds=0.05))
        cb.record_failure_sync(None)
        assert cb.state == CircuitState.OPEN
        # Wait for timeout
        time.sleep(0.1)
        assert cb.is_call_permitted() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_success_restores_to_closed(self):
        from ai.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=1, success_threshold=1, timeout_seconds=0.05))
        cb.record_failure_sync(None)
        time.sleep(0.1)
        cb.is_call_permitted()  # transitions to HALF_OPEN
        cb.record_success_sync()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        from ai.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=1, timeout_seconds=0.05))
        cb.record_failure_sync(None)
        time.sleep(0.1)
        cb.is_call_permitted()  # HALF_OPEN
        cb.record_failure_sync(None)
        assert cb.state == CircuitState.OPEN

    def test_stats_are_tracked(self):
        from ai.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("test")
        cb.record_success_sync()
        cb.record_success_sync()
        cb.record_failure_sync(None)
        s = cb.stats
        assert s.total_calls == 3
        assert s.total_successes == 2
        assert s.total_failures == 1

    def test_reset(self):
        from ai.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=1, timeout_seconds=3600))
        cb.record_failure_sync(None)
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED

    def test_get_status(self):
        from ai.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=3))
        status = cb.get_status()
        assert status["name"] == "test"
        assert status["state"] == "closed"
        assert status["config"]["failure_threshold"] == 3

    def test_half_open_allows_calls_and_transitions_to_closed_on_success(self):
        from ai.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=1, half_open_max_calls=1, timeout_seconds=0.05,
            success_threshold=2))
        cb.record_failure_sync(None)
        time.sleep(0.1)
        assert cb.is_call_permitted() is True  # OPEN → HALF_OPEN
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success_sync()
        cb.record_success_sync()  # reach success_threshold=2
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_async_record_success(self):
        from ai.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("async_test")
        await cb.record_success()
        assert cb.stats.total_successes == 1

    @pytest.mark.asyncio
    async def test_async_record_failure(self):
        from ai.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("async_test")
        await cb.record_failure(Exception("test error"))
        assert cb.stats.total_failures == 1


class TestCircuitBreakerRegistry:
    """CircuitBreakerRegistry — shared breaker management."""

    def test_get_or_create_breaker(self):
        from ai.circuit_breaker import CircuitBreakerRegistry
        reg = CircuitBreakerRegistry()
        cb1 = reg.get_or_create("svc1")
        cb2 = reg.get_or_create("svc1")
        assert cb1 is cb2

    def test_multiple_breakers(self):
        from ai.circuit_breaker import CircuitBreakerRegistry
        reg = CircuitBreakerRegistry()
        cb1 = reg.get_or_create("svc_a")
        cb2 = reg.get_or_create("svc_b")
        assert cb1 is not cb2

    def test_get_all_stats(self):
        from ai.circuit_breaker import CircuitBreakerRegistry
        reg = CircuitBreakerRegistry()
        cb1 = reg.get_or_create("svc1")
        cb2 = reg.get_or_create("svc2")
        assert reg.get("svc1") is cb1
        assert reg.get("svc2") is cb2

    def test_reset_all(self):
        from ai.circuit_breaker import CircuitBreakerRegistry, CircuitState
        reg = CircuitBreakerRegistry()
        cb = reg.get_or_create("svc1")
        cb.record_failure_sync(None)
        cb.record_failure_sync(None)
        cb.record_failure_sync(None)
        cb.record_failure_sync(None)
        cb.record_failure_sync(None)
        assert cb.state == CircuitState.OPEN
        reg.reset_all()
        assert cb.state == CircuitState.CLOSED

    def test_global_functions(self):
        from ai.circuit_breaker import get_circuit_breaker_registry, reset_circuit_breaker_registry
        reg1 = get_circuit_breaker_registry()
        reg2 = get_circuit_breaker_registry()
        assert reg1 is reg2
        reset_circuit_breaker_registry()
        reg3 = get_circuit_breaker_registry()
        assert reg3 is not reg1


# ═══════════════════════════════════════════════════════════════
# Module: ai/provider_routing.py
# ═══════════════════════════════════════════════════════════════

class FakeClient:
    """Minimal mock for OpenAICompatibleClient."""
    def __init__(self, name="fake"):
        self._name = name

    def __repr__(self):
        return self._name


class TestProviderRoutingTable:
    """ProviderRoutingTable — query classification and client routing."""

    def test_classify_reasoning(self):
        from ai.provider_routing import ProviderRoutingTable
        r = ProviderRoutingTable()
        assert r.classify_query("为什么天是蓝的？") == "reasoning"
        assert r.classify_query("分析一下这个数据") == "reasoning"

    def test_classify_coding(self):
        from ai.provider_routing import ProviderRoutingTable
        r = ProviderRoutingTable()
        assert r.classify_query("帮我写个Python函数") == "coding"
        assert r.classify_query("这个bug怎么修") == "coding"

    def test_classify_chinese_knowledge(self):
        from ai.provider_routing import ProviderRoutingTable
        r = ProviderRoutingTable()
        assert r.classify_query("中国的首都是哪里") == "chinese_knowledge"

    def test_classify_summarization(self):
        from ai.provider_routing import ProviderRoutingTable
        r = ProviderRoutingTable()
        assert r.classify_query("帮我总结一下这篇文章") == "summarization"

    def test_default_fallback(self):
        from ai.provider_routing import ProviderRoutingTable
        r = ProviderRoutingTable()
        assert r.classify_query("你好吗？") == "general_chat"

    def test_classify_empty_text(self):
        from ai.provider_routing import ProviderRoutingTable
        r = ProviderRoutingTable()
        assert r.classify_query("") == "general_chat"

    def test_classify_with_partial_match(self):
        from ai.provider_routing import ProviderRoutingTable
        r = ProviderRoutingTable()
        assert r.classify_query("这段代码需要推理") == "reasoning"

    def test_no_routing_returns_primary(self, monkeypatch):
        from ai.provider_routing import ProviderRoutingTable
        monkeypatch.delenv("SIYI_LLM_PROVIDER_ROUTING", raising=False)
        r = ProviderRoutingTable(environ={"SIYI_LLM_PROVIDER_ROUTING": ""})
        primary = FakeClient("primary")
        result = r.get_client_for_query("为什么", primary)
        assert result is primary

    def test_configured_routing_returns_specialized(self, monkeypatch):
        from ai.provider_routing import ProviderRoutingTable
        monkeypatch.setenv("SIYI_LLM_PROVIDER_ROUTING", '{"reasoning": "deepseek"}')
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key")
        r = ProviderRoutingTable()
        primary = FakeClient("primary")
        result = r.get_client_for_query("分析原因", primary)
        # deepseek preset matches → creates a new client
        assert result is not primary

    def test_configured_routing_preset_missing_key_returns_primary(self, monkeypatch):
        from ai.provider_routing import ProviderRoutingTable
        monkeypatch.setenv("SIYI_LLM_PROVIDER_ROUTING", '{"reasoning": "deepseek"}')
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        r = ProviderRoutingTable()
        primary = FakeClient("primary")
        result = r.get_client_for_query("推理", primary)
        # no DEEPSEEK_API_KEY → falls back to primary
        assert result is primary

    def test_invalid_routing_json_logs_warning(self, monkeypatch, caplog):
        from ai.provider_routing import ProviderRoutingTable
        monkeypatch.setenv("SIYI_LLM_PROVIDER_ROUTING", "not-json")
        r = ProviderRoutingTable()
        # Should not crash
        primary = FakeClient("primary")
        result = r.get_client_for_query("hello", primary)
        assert result is primary

    def test_routing_with_default_key(self, monkeypatch):
        from ai.provider_routing import ProviderRoutingTable
        monkeypatch.setenv("SIYI_LLM_PROVIDER_ROUTING", '{"default": "deepseek"}')
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        r = ProviderRoutingTable()
        primary = FakeClient("primary")
        result = r.get_client_for_query("随便聊点什么", primary)
        assert result is not primary


# ═══════════════════════════════════════════════════════════════
# Module: ai/llm_resilience.py
# ═══════════════════════════════════════════════════════════════

class TestLLMResilience:
    """Error classification, retry logic, and circuit breaker utilities."""

    def test_extract_error_text_from_json(self):
        from ai.llm_resilience import extract_error_text
        result = extract_error_text('{"error": {"message": "rate limit exceeded"}}')
        assert result == "rate limit exceeded"

    def test_extract_error_text_from_top_level_message(self):
        from ai.llm_resilience import extract_error_text
        result = extract_error_text('{"message": "server error"}')
        assert result == "server error"

    def test_extract_error_text_raw_string(self):
        from ai.llm_resilience import extract_error_text
        result = extract_error_text("connection refused")
        assert result == "connection refused"

    def test_extract_error_text_empty(self):
        from ai.llm_resilience import extract_error_text
        assert extract_error_text("") == ""
        assert extract_error_text(None) == ""

    def test_extract_error_text_from_response_object(self):
        from ai.llm_resilience import extract_error_text_from_response
        resp = type("Resp", (), {"text": '{"error": {"message": "forbidden"}}'})()
        assert extract_error_text_from_response(resp) == "forbidden"

    def test_extract_error_text_from_none_response(self):
        from ai.llm_resilience import extract_error_text_from_response
        assert extract_error_text_from_response(None) == ""

    def test_parse_retry_after_seconds_header(self):
        from ai.llm_resilience import parse_retry_after_seconds
        result = parse_retry_after_seconds("120")
        assert result == 120.0

    def test_parse_retry_after_seconds_text(self):
        from ai.llm_resilience import parse_retry_after_seconds
        result = parse_retry_after_seconds(None, error_text="retry_after: 30")
        assert result == 30.0

    def test_parse_retry_after_seconds_no_match(self):
        from ai.llm_resilience import parse_retry_after_seconds
        result = parse_retry_after_seconds(None, error_text="unknown error")
        assert result is None

    def test_classify_error_rate_limit(self):
        from ai.llm_resilience import classify_error
        result = classify_error(429, "rate limit")
        assert result.kind == "rate_limit"
        assert result.transient is True

    @pytest.mark.parametrize(
        "status,error_text",
        [
            (402, "Payment Required"),
            (429, "insufficient_balance"),
            (400, "billing_disabled for this account"),
            (401, "account disabled due to quota exceeded"),
            (None, "out of credits"),
            (None, "余额不足"),
        ],
    )
    def test_classify_error_billing(self, status, error_text):
        from ai.llm_resilience import classify_error
        result = classify_error(status, error_text)
        assert result.kind == "billing_error"
        assert result.transient is False

    def test_classify_error_server_error(self):
        from ai.llm_resilience import classify_error
        result = classify_error(503, "service unavailable")
        assert result.kind == "server_error"
        assert result.transient is True

    def test_classify_error_auth(self):
        from ai.llm_resilience import classify_error
        result = classify_error(401, "invalid api key")
        assert result.kind == "auth_error"
        assert result.transient is False

    def test_classify_error_not_found(self):
        from ai.llm_resilience import classify_error
        result = classify_error(404, "model not found")
        assert result.kind == "unknown_error"
        assert result.transient is False

    def test_classify_error_unknown_status(self):
        from ai.llm_resilience import classify_error
        result = classify_error(999, "something weird")
        assert result.kind == "unknown_error"
        assert result.transient is False

    def test_compute_retry_delay_seconds(self):
        from ai.llm_resilience import compute_retry_delay_seconds, RetryPolicy
        policy = RetryPolicy(attempts=3, min_delay_seconds=1.0, max_delay_seconds=10.0)
        delay = compute_retry_delay_seconds(attempt=1, policy=policy, retry_after_seconds=None)
        assert 0.9 <= delay <= 1.1  # min_delay + jitter

    def test_compute_retry_delay_uses_retry_after(self):
        from ai.llm_resilience import compute_retry_delay_seconds, RetryPolicy
        policy = RetryPolicy(attempts=3)
        delay = compute_retry_delay_seconds(attempt=0, policy=policy, retry_after_seconds=30.0)
        assert delay >= 30.0

    def test_retry_delay_increases_with_attempts(self):
        from ai.llm_resilience import compute_retry_delay_seconds, RetryPolicy
        policy = RetryPolicy(attempts=5, min_delay_seconds=1.0, max_delay_seconds=60.0)
        delay_1 = compute_retry_delay_seconds(attempt=1, policy=policy)
        delay_3 = compute_retry_delay_seconds(attempt=3, policy=policy)
        assert delay_3 > delay_1  # exponential backoff

    def test_build_observability_tags(self):
        from ai.llm_resilience import build_observability_tags
        tags = build_observability_tags(
            provider="deepseek", model="deepseek-chat", status=429,
            transient=True, retry_after_seconds=30.0, fallback_stage="primary",
            cache_hit=False, error_kind="rate_limit",
        )
        assert tags["error_kind"] == "rate_limit"
        assert tags["status"] == 429
        assert tags["provider"] == "deepseek"
