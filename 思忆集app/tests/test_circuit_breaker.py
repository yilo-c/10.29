"""Tests for ai.circuit_breaker module."""
from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Allow `import ai.circuit_breaker` when pytest is invoked from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from ai.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
    get_circuit_breaker_registry,
    reset_circuit_breaker_registry,
)


@pytest.fixture(autouse=True)
def reset_global_registry():
    """Reset the global registry before and after each test."""
    reset_circuit_breaker_registry()
    yield
    reset_circuit_breaker_registry()


def make_config(
    failure_threshold: int = 3,
    success_threshold: int = 2,
    timeout_seconds: float = 0.01,
    half_open_max_calls: int = 1,
) -> CircuitBreakerConfig:
    return CircuitBreakerConfig(
        failure_threshold=failure_threshold,
        success_threshold=success_threshold,
        timeout_seconds=timeout_seconds,
        half_open_max_calls=half_open_max_calls,
    )


class TestCircuitBreaker:
    def test_closed_breaker_permits_calls(self):
        breaker = CircuitBreaker("test", make_config())
        assert breaker.state == CircuitState.CLOSED
        assert breaker.is_call_permitted() is True

    def test_failure_threshold_opens_breaker(self):
        config = make_config(failure_threshold=3)
        breaker = CircuitBreaker("test", config)

        assert breaker.is_call_permitted() is True
        breaker.record_failure_sync(Exception("e1"))
        breaker.record_failure_sync(Exception("e2"))
        assert breaker.state == CircuitState.CLOSED
        assert breaker.is_call_permitted() is True

        breaker.record_failure_sync(Exception("e3"))
        assert breaker.state == CircuitState.OPEN
        assert breaker.stats.consecutive_failures == 3

    def test_open_breaker_rejects_calls(self):
        config = make_config(failure_threshold=2, timeout_seconds=60.0)
        breaker = CircuitBreaker("test", config)

        breaker.record_failure_sync(Exception("e1"))
        breaker.record_failure_sync(Exception("e2"))
        assert breaker.state == CircuitState.OPEN
        assert breaker.is_call_permitted() is False
        assert breaker.is_call_permitted() is False

    def test_timeout_transitions_to_half_open_and_permits_limited_probe_calls(self):
        config = make_config(failure_threshold=2, timeout_seconds=0.01, half_open_max_calls=2)
        breaker = CircuitBreaker("test", config)

        breaker.record_failure_sync(Exception("e1"))
        breaker.record_failure_sync(Exception("e2"))
        assert breaker.state == CircuitState.OPEN

        time.sleep(0.02)

        assert breaker.is_call_permitted() is True
        assert breaker.state == CircuitState.HALF_OPEN
        assert breaker._half_open_calls == 1

        assert breaker.is_call_permitted() is True
        assert breaker._half_open_calls == 2

        assert breaker.is_call_permitted() is False

    def test_half_open_success_threshold_closes_breaker(self):
        config = make_config(failure_threshold=2, success_threshold=2, timeout_seconds=0.01)
        breaker = CircuitBreaker("test", config)

        breaker.record_failure_sync(Exception("e1"))
        breaker.record_failure_sync(Exception("e2"))
        assert breaker.state == CircuitState.OPEN

        time.sleep(0.02)

        breaker.is_call_permitted()
        assert breaker.state == CircuitState.HALF_OPEN

        breaker.record_success_sync()
        assert breaker.state == CircuitState.HALF_OPEN

        breaker.is_call_permitted()
        breaker.record_success_sync()
        assert breaker.state == CircuitState.CLOSED

    def test_half_open_failure_re_opens_breaker(self):
        config = make_config(failure_threshold=2, timeout_seconds=0.01)
        breaker = CircuitBreaker("test", config)

        breaker.record_failure_sync(Exception("e1"))
        breaker.record_failure_sync(Exception("e2"))
        assert breaker.state == CircuitState.OPEN

        time.sleep(0.02)

        breaker.is_call_permitted()
        assert breaker.state == CircuitState.HALF_OPEN

        breaker.record_failure_sync(Exception("probe failed"))
        assert breaker.state == CircuitState.OPEN

    def test_reset_manually_closes_breaker(self):
        breaker = CircuitBreaker("test", make_config(failure_threshold=2))
        breaker.record_failure_sync(Exception("e1"))
        breaker.record_failure_sync(Exception("e2"))
        assert breaker.state == CircuitState.OPEN

        breaker.reset()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.is_call_permitted() is True
        assert breaker.stats.consecutive_failures == 0

    def test_stats_are_tracked(self):
        breaker = CircuitBreaker("test", make_config(failure_threshold=5))
        breaker.record_success_sync()
        breaker.record_success_sync()
        breaker.record_failure_sync(Exception("e1"))

        assert breaker.stats.total_calls == 3
        assert breaker.stats.total_successes == 2
        assert breaker.stats.total_failures == 1
        assert breaker.stats.consecutive_failures == 1

    def test_get_status_returns_snapshot(self):
        breaker = CircuitBreaker("test", make_config(failure_threshold=2))
        status = breaker.get_status()

        assert status["name"] == "test"
        assert status["state"] == "closed"
        assert status["config"]["failure_threshold"] == 2
        assert "stats" in status


class TestCircuitBreakerRegistry:
    def test_get_or_create_manages_multiple_breakers(self):
        registry = CircuitBreakerRegistry()
        a = registry.get_or_create("a", make_config())
        b = registry.get_or_create("b", make_config())

        assert a is not b
        assert registry.get("a") is a
        assert registry.get("b") is b

    def test_is_call_permitted_returns_true_for_unknown_breaker(self):
        registry = CircuitBreakerRegistry()
        assert registry.is_call_permitted("unknown") is True

    def test_record_success_sync_and_record_failure_sync_delegate(self):
        registry = CircuitBreakerRegistry()
        registry.get_or_create("svc", make_config(failure_threshold=2))

        registry.record_success_sync("svc")
        assert registry.get("svc").stats.total_successes == 1

        registry.record_failure_sync("svc", Exception("e"))
        registry.record_failure_sync("svc", Exception("e"))
        assert registry.get("svc").state == CircuitState.OPEN

    def test_reset_all_resets_all_breakers(self):
        registry = CircuitBreakerRegistry()
        registry.get_or_create("a", make_config(failure_threshold=1))
        registry.get_or_create("b", make_config(failure_threshold=1))

        registry.record_failure_sync("a")
        registry.record_failure_sync("b")
        assert registry.get("a").state == CircuitState.OPEN
        assert registry.get("b").state == CircuitState.OPEN

        registry.reset_all()
        assert registry.get("a").state == CircuitState.CLOSED
        assert registry.get("b").state == CircuitState.CLOSED

    def test_global_registry_functions(self):
        registry = get_circuit_breaker_registry()
        assert isinstance(registry, CircuitBreakerRegistry)
        assert get_circuit_breaker_registry() is registry

        reset_circuit_breaker_registry()
        assert get_circuit_breaker_registry() is not registry


class TestThreadSafety:
    def test_concurrent_records_dont_corrupt_state(self):
        breaker = CircuitBreaker("test", make_config(failure_threshold=1000))
        successes = 500
        failures = 500

        def success_task():
            breaker.record_success_sync()

        def failure_task():
            breaker.record_failure_sync(Exception("err"))

        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = (
                [executor.submit(success_task) for _ in range(successes)]
                + [executor.submit(failure_task) for _ in range(failures)]
            )
            for f in futures:
                f.result()

        assert breaker.stats.total_calls == successes + failures
        assert breaker.stats.total_successes == successes
        assert breaker.stats.total_failures == failures
        assert breaker.state == CircuitState.CLOSED

    def test_concurrent_is_call_permitted_in_half_open(self):
        config = make_config(failure_threshold=2, timeout_seconds=0.01, half_open_max_calls=5)
        breaker = CircuitBreaker("test", config)
        breaker.record_failure_sync(Exception("e1"))
        breaker.record_failure_sync(Exception("e2"))
        assert breaker.state == CircuitState.OPEN

        time.sleep(0.02)

        allowed = 0
        barrier = threading.Barrier(20)

        def attempt():
            barrier.wait()
            return breaker.is_call_permitted()

        with ThreadPoolExecutor(max_workers=20) as executor:
            results = list(executor.map(lambda _: attempt(), range(20)))

        allowed = sum(1 for r in results if r)
        assert allowed == config.half_open_max_calls
