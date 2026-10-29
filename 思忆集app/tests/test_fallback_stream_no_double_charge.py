"""P0-3 验证：FallbackOpenAIClient 流式失败时的回退行为。

历史 bug：stream 失败后无条件调用同 client 的非流式 chat_completion 一次，
导致：
- 已 yield 部分 chunk 时再 yield 完整文本 → UI 重复展示
- 同一次请求被计费两次（stream API 调用 + non-stream API 调用）

修复后：
- 仅在 stream 从未 yield 过任何 chunk（buffer 空）时，才允许同 client 非流式回退
- 已 yield 内容则跳到下一个 client，避免重复扣费
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from ai.client.fallback import FallbackOpenAIClient  # noqa: E402
from ai.types import AIServiceError  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_ai_cache_dir(tmp_path, monkeypatch):
    """测试隔离（生产数据泄漏守卫）：未传 cache_path 的 FallbackOpenAIClient 默认缓存
    回落到 SIYI_DATA_DIR（未设则生产 思忆集app/data/ai_cache.json），流式成功回退的
    缓存写入会污染生产数据。将 SIYI_DATA_DIR 指向函数级 tmp 目录隔离；
    已显式传 cache_path 的用例不受影响（显式参数优先）。"""
    monkeypatch.setenv("SIYI_DATA_DIR", str(tmp_path))


class _PartialStreamThenFailClient:
    """先 yield N 个 chunk 然后抛错的 client。模拟"流式开了一半失败"。"""

    def __init__(self, model="partial-fail-model", chunks_before_fail=2, allow_nonstream=True):
        self.model = model
        self._chunks_before_fail = chunks_before_fail
        self._allow_nonstream = allow_nonstream
        self.nonstream_call_count = 0
        self.stream_call_count = 0
        self._last_stream_tool_calls: list[dict] = []

    def is_configured(self):
        return True

    def chat_completion_stream(self, messages, temperature=0.2, max_tokens=900, tools=None, tool_choice=None):
        self.stream_call_count += 1
        for i in range(self._chunks_before_fail):
            yield f"part{i}"
        raise AIServiceError("simulated mid-stream failure", transient=True)

    def chat_completion(self, messages, temperature=0.2, max_tokens=900, tools=None, tool_choice=None):
        self.nonstream_call_count += 1
        if not self._allow_nonstream:
            raise AIServiceError("nonstream also fails", transient=True)
        return {
            "choices": [{"message": {"content": "FULL_NONSTREAM_RESPONSE"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }


class _SuccessfulFallbackClient:
    """简单 stream 成功 client，用作 next-in-chain。"""

    def __init__(self, model="fallback-model"):
        self.model = model
        self.stream_call_count = 0
        self._last_stream_tool_calls: list[dict] = []

    def is_configured(self):
        return True

    def chat_completion_stream(self, messages, temperature=0.2, max_tokens=900, tools=None, tool_choice=None):
        self.stream_call_count += 1
        yield "fallback "
        yield "ok"

    def chat_completion(self, messages, temperature=0.2, max_tokens=900, tools=None, tool_choice=None):
        return {"choices": [{"message": {"content": "fallback ok"}}], "usage": {}}


class _FailFromStartClient:
    """stream 一开就抛错（buffer 空），但非流式可成功。"""

    def __init__(self, model="empty-fail-model"):
        self.model = model
        self.nonstream_call_count = 0
        self.stream_call_count = 0
        self._last_stream_tool_calls: list[dict] = []

    def is_configured(self):
        return True

    def chat_completion_stream(self, messages, temperature=0.2, max_tokens=900, tools=None, tool_choice=None):
        self.stream_call_count += 1
        if False:
            yield  # type: ignore  # make this a generator
        raise AIServiceError("immediate failure", transient=True)

    def chat_completion(self, messages, temperature=0.2, max_tokens=900, tools=None, tool_choice=None):
        self.nonstream_call_count += 1
        return {
            "choices": [{"message": {"content": "RECOVERED_NONSTREAM"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 10},
        }


# ── 核心修复：buffer 已有 chunk 时不重发非流式 ──────────────────────────


def test_partial_stream_failure_does_not_replay_nonstream():
    """关键测试：流式 yield 部分 chunk 后失败 → 不重发非流式同 client。"""
    primary = _PartialStreamThenFailClient(chunks_before_fail=2)
    fallback = _SuccessfulFallbackClient()
    fb = FallbackOpenAIClient(primary=primary, fallback=fallback)

    chunks = list(fb.chat_completion_stream([{"role": "user", "content": "hi"}]))
    full = "".join(chunks)

    # primary 已 yield 部分 → 不应再调用 chat_completion
    assert primary.nonstream_call_count == 0, (
        f"primary 已 yield {primary.stream_call_count} 次流式 chunk 后应跳到下一 client，"
        f"不应触发 chat_completion 非流式回退（重复计费）。"
        f"实际调用次数：{primary.nonstream_call_count}"
    )

    # 应切到下一个 client（fallback）继续流式
    assert fallback.stream_call_count == 1
    # 输出应同时包含 primary 已 yield 的部分和 fallback 的完整流
    assert "part0" in full and "part1" in full
    assert "fallback ok" in full


def test_partial_stream_failure_does_not_yield_full_response_text():
    """前半段流 + 完整 RESPONSE 文本不应叠加在输出里。"""
    primary = _PartialStreamThenFailClient(chunks_before_fail=2, allow_nonstream=True)
    fallback = _SuccessfulFallbackClient()
    fb = FallbackOpenAIClient(primary=primary, fallback=fallback)

    chunks = list(fb.chat_completion_stream([{"role": "user", "content": "hi"}]))
    full = "".join(chunks)

    # 关键断言：FULL_NONSTREAM_RESPONSE 不应出现在输出里
    assert "FULL_NONSTREAM_RESPONSE" not in full, (
        "已 yield 部分 chunk 时绝不能再 yield 整个非流式响应，"
        "否则用户会看到部分 chunk + 完整文本拼接（UI 重复展示）"
    )


def test_empty_stream_failure_no_same_client_nonstream_retry(tmp_path):
    """P1-fix 行为：即使 buffer 为空（流刚启动就失败），也不再对同 client
    做非流式重试——同一 provider 流式失败意味着非流式大概率也失败，
    且重试会重复计费。应直接切下一个 client；无下一个 client 且无缓存
    时抛出 AIServiceError。用独立 cache_path 隔离，避免命中全局缓存。"""
    primary = _FailFromStartClient()
    fb = FallbackOpenAIClient(primary=primary, cache_path=str(tmp_path / "ai_cache.json"))

    with pytest.raises(AIServiceError):
        list(fb.chat_completion_stream([{"role": "user", "content": "hi"}]))

    # 不再对同 client 非流式回退
    assert primary.nonstream_call_count == 0


def test_partial_stream_failure_records_only_one_cost():
    """验证不会双重 _record_ai_cost 调用。"""
    primary = _PartialStreamThenFailClient(chunks_before_fail=2)
    fallback = _SuccessfulFallbackClient()
    fb = FallbackOpenAIClient(primary=primary, fallback=fallback)

    cost_calls = []
    original = fb._record_ai_cost

    def _spy(*args, **kwargs):
        cost_calls.append((args, kwargs))
        return original(*args, **kwargs)

    fb._record_ai_cost = _spy  # type: ignore[method-assign]

    list(fb.chat_completion_stream([{"role": "user", "content": "hi"}]))

    # 应只对 fallback client 记录一次成本（primary 失败未 record，nonstream 回退被禁用）
    # 关键是：不应同时出现 stream + stream_fallback 两个 task_type 都记 primary
    primary_recorded_tasks = [
        c[1].get("task_type") or (c[0][2] if len(c[0]) > 2 else None)
        for c in cost_calls
    ]
    # 我们关心的是 primary 没有 stream_fallback 这种成本记录
    # 通过 client 实参鉴别
    primary_calls_with_fallback_task = [
        c for c in cost_calls
        if (c[0][1] is primary if len(c[0]) > 1 else False)
        and (c[1].get("task_type") == "chat_completion_stream_fallback"
             or (len(c[0]) > 2 and c[0][2] == "chat_completion_stream_fallback"))
    ]
    assert primary_calls_with_fallback_task == [], (
        f"primary client 不应被记录 stream_fallback 任务（重复计费）："
        f"{primary_calls_with_fallback_task}"
    )
