"""本地 Qwen3 关思考注入测试（2026-10-15）。

背景（U1 实测）：本地兜底 qwen3:4b 在云端余额耗尽时形同虚设——
① 默认 num_ctx=4096 < 思忆集 prompt（实测 5381 tokens）→ 400 超限；
② 即使 context 够（已建 qwen3-4b-16k 变体），Qwen3 默认开思考，同一 prompt
   开思考 231.9s vs 关思考 11.2s（**20 倍**）→ 必超 chat.send 60s idle 超时。

本测试钉死 ③ 的修复：仅「本机端点 + qwen3 模型」的请求追加 `/no_think`，
云端 provider 与消息原样不受影响（`/no_think` 是 Qwen3 prompt 约定）。
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from ai.client.base import OpenAICompatibleClient, _with_no_think  # noqa: E402

MSGS = [{"role": "system", "content": "你是助手"}, {"role": "user", "content": "你好"}]


def _client(base: str, model: str) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(api_base=base, api_key="", model=model)


class TestNoThinkInjection:
    def test_local_qwen3_gets_no_think(self) -> None:
        """本机端点 + qwen3 → 最后一条 user 消息追加 /no_think。"""
        c = _client("http://localhost:11434/v1", "qwen3-4b-16k")
        out = c._payload(MSGS)["messages"]
        assert out[-1]["content"] == "你好\n/no_think"
        assert out[0]["content"] == "你是助手"  # system 不动

    def test_cloud_provider_untouched(self) -> None:
        """云端 provider（DeepSeek）→ 绝不追加（不能污染云端请求）。"""
        c = _client("https://api.deepseek.com/v1", "deepseek-v4-flash")
        out = c._payload(MSGS)["messages"]
        assert out[-1]["content"] == "你好"

    def test_remote_non_qwen3_untouched(self) -> None:
        """非 qwen3 模型（即便本机端点）→ 不追加。"""
        c = _client("http://localhost:11434/v1", "llama3:8b")
        assert c._payload(MSGS)["messages"][-1]["content"] == "你好"

    def test_qwen3_remote_endpoint_untouched(self) -> None:
        """qwen3 但非本机端点（如云端托管的 qwen3）→ 不追加（保守边界）。"""
        c = _client("https://api.example.com/v1", "qwen3-32b")
        assert c._payload(MSGS)["messages"][-1]["content"] == "你好"

    def test_payload_does_not_mutate_input(self) -> None:
        """注入走副本，不改调用方传入的 messages（避免污染复用列表）。"""
        c = _client("http://127.0.0.1:11434/v1", "qwen3-4b-16k")
        msgs = [{"role": "user", "content": "原始"}]
        c._payload(msgs)
        assert msgs[0]["content"] == "原始"


class TestWithNoThinkHelper:
    def test_idempotent(self) -> None:
        """已含 /no_think 不重复追加。"""
        out = _with_no_think([{"role": "user", "content": "x /no_think"}])
        assert out[0]["content"] == "x /no_think"

    def test_uses_last_user_message(self) -> None:
        """追加到最后一条 user（多轮对话中历史消息不动）。"""
        msgs = [
            {"role": "user", "content": "第一轮"},
            {"role": "assistant", "content": "回复"},
            {"role": "user", "content": "第二轮"},
        ]
        out = _with_no_think(msgs)
        assert out[0]["content"] == "第一轮"
        assert out[2]["content"] == "第二轮\n/no_think"

    def test_empty_and_no_user(self) -> None:
        assert _with_no_think([]) == []
        only_system = [{"role": "system", "content": "s"}]
        assert _with_no_think(only_system)[0]["content"] == "s"

    def test_blank_user_content(self) -> None:
        """空 content 的 user 消息：直接给后缀，不留前导换行。"""
        out = _with_no_think([{"role": "user", "content": "   "}])
        assert out[0]["content"] == "/no_think"
