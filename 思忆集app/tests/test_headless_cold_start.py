"""L0 冷启动稳定：headless 后端可重复冷启动且就绪（计时断言 <30s）。

聚焦 L0 验收门「冷启动耗时 < 30s」：
- 从进程 spawn 到首次 RPC 就绪（``ping`` 返回 ``pong``）的冷启动耗时 < 30s；
- 可重复性：连续 N 轮冷启动全部就绪且耗时在预算内；
- 就绪后 RPC 契约正常：``ping``→pong、``kernel.status``→dict 且字段齐全。

真实进程冷启动，不做 mock；每轮启动独立临时实例（随机端口 + 临时数据目录），
由 ``_isolated_backend`` 承担 spawn / teardown。
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from _isolated_backend import rpc, spawn_backend, teardown_backend

# 计划验收门（docs/test-observation-plan.md §4）：冷启动耗时 < 30s
COLD_START_BUDGET_SECONDS = 30.0
# 可重复性轮次：默认 2 轮（CI 保守），可用 SIYI_COLD_START_ROUNDS 覆盖
DEFAULT_ROUNDS = max(1, int(os.environ.get("SIYI_COLD_START_ROUNDS", "2")))
# ping 就绪等待预算（首次 RPC 握手）
PING_READY_BUDGET_SECONDS = 20.0


async def _wait_ping_ready(ws_url: str, timeout: float = PING_READY_BUDGET_SECONDS) -> float:
    """轮询 ``ping`` 直到返回 ``pong``，返回就绪耗时秒数。"""
    t0 = time.monotonic()
    deadline = t0 + timeout
    last_error: Exception | None = None
    while True:
        try:
            resp = await rpc(ws_url, "ping", timeout=5)
            if resp.get("result") == "pong":
                return time.monotonic() - t0
        except Exception as exc:  # noqa: BLE001 - 连接未就绪时正常
            last_error = exc
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"ping 在 {timeout}s 内未就绪：{last_error!r}"
            )
        await asyncio.sleep(0.3)


def _run_rpc(ws_url: str, method: str) -> dict:
    return asyncio.run(rpc(ws_url, method, timeout=5))


def _cold_start_once(round_no: int) -> dict:
    """启动一轮独立后端并测量冷启动耗时，返回观测结果。"""
    proc = None
    data_dir = None
    t0 = time.monotonic()
    try:
        proc, ws_url, data_dir = spawn_backend()  # 阻塞至 TCP 就绪
        ready_elapsed = asyncio.run(_wait_ping_ready(ws_url))  # 至 RPC 就绪
        total_elapsed = time.monotonic() - t0

        # 就绪后契约校验
        ping_resp = _run_rpc(ws_url, "ping")
        kernel_resp = _run_rpc(ws_url, "kernel.status")
        assert ping_resp.get("result") == "pong", ping_resp
        kernel = kernel_resp.get("result")
        assert isinstance(kernel, dict), kernel_resp
        for field in ("route", "effective_engine", "degraded", "gateway_present"):
            assert field in kernel, f"kernel.status 缺字段 {field}: {kernel}"

        return {
            "round": round_no,
            "ready_elapsed": ready_elapsed,
            "total_elapsed": total_elapsed,
            "kernel": kernel,
        }
    finally:
        teardown_backend(proc, data_dir)


class TestHeadlessColdStart:
    def test_cold_start_under_30s(self) -> None:
        """冷启动耗时 < 30s（计划验收门）。"""
        obs = _cold_start_once(1)
        assert obs["total_elapsed"] < COLD_START_BUDGET_SECONDS, (
            f"冷启动耗时 {obs['total_elapsed']:.1f}s 超预算 "
            f"{COLD_START_BUDGET_SECONDS}s"
        )

    def test_repeatable_cold_starts(self) -> None:
        """连续多轮冷启动全部就绪且在预算内（可重复性）。"""
        obs_list = [_cold_start_once(i + 1) for i in range(DEFAULT_ROUNDS)]
        assert len(obs_list) == DEFAULT_ROUNDS
        for obs in obs_list:
            assert obs["total_elapsed"] < COLD_START_BUDGET_SECONDS, (
                f"第 {obs['round']} 轮冷启动耗时 {obs['total_elapsed']:.1f}s 超预算"
            )
            assert obs["ready_elapsed"] < COLD_START_BUDGET_SECONDS

    def test_kernel_status_contract_after_start(self) -> None:
        """就绪后 kernel.status 契约：8 字段齐全且类型合法。"""
        obs = _cold_start_once(1)
        kernel = obs["kernel"]
        assert kernel["route"] in ("gateway", "legacy")
        assert isinstance(kernel["degraded"], bool)
        assert isinstance(kernel["gateway_present"], bool)
        assert isinstance(kernel["effective_engine"], str)
        assert isinstance(kernel.get("details"), dict)
