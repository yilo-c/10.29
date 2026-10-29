"""DeepSeek 账户客户端解析层测试（2026-10-15 首次覆盖）。

背景：该模块零测试覆盖，网页 API 改结构后无人发现——`_extract_content` 只找
`content` 字段而新 API 把正文放在 `fragments[]`，导致 get_messages 恒返回空、
fetch_conversations 抓到的会话没有正文（**同步功能实际坏死**，并连带造成图谱
deepseek 出处无法反查、详情补不上）。本组测试把新旧两种结构都钉死。

真实结构（2026-10-15 实测自 chat.deepseek.com）：
    {"role": "USER", "fragments": [{"id": 1, "type": "REQUEST", "content": "..."}]}
    {"role": "ASSISTANT", "fragments": [{"type": "THINK", ...}, {"type": "RESPONSE", ...}]}
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from integrations.deepseek_client import DeepSeekAccountClient  # noqa: E402


class TestExtractRole:
    def test_uppercase_normalized_to_lower(self) -> None:
        """新 API 返回大写 USER/ASSISTANT → 归一小写（下游按 "user" 判等）。"""
        assert DeepSeekAccountClient._extract_role({"role": "USER"}) == "user"
        assert DeepSeekAccountClient._extract_role({"role": "ASSISTANT"}) == "assistant"
        assert DeepSeekAccountClient._extract_role({"role": "System"}) == "system"

    def test_missing_role_falls_back_to_heuristic(self) -> None:
        """无 role 字段 → 旧启发式兜底（不抛、给可用值）。"""
        assert DeepSeekAccountClient._extract_role({"is_user": True}) == "user"
        assert DeepSeekAccountClient._extract_role({}) == "assistant"


class TestExtractContent:
    def test_fragments_request_type(self) -> None:
        """新结构：fragments[type=REQUEST] 就是用户消息正文。"""
        msg = {"role": "USER", "fragments": [{"id": 1, "type": "REQUEST", "content": "glm5.3 很慢吗？"}]}
        assert DeepSeekAccountClient._extract_content(msg) == "glm5.3 很慢吗？"

    def test_fragments_response_preferred_over_think(self) -> None:
        """助手消息：取 RESPONSE 正文，不把 THINK 思考过程当正文。"""
        msg = {
            "role": "ASSISTANT",
            "fragments": [
                {"type": "THINK", "content": "用户想知道模型速度，我需要检索……"},
                {"type": "RESPONSE", "content": "GLM 5.3 Flash 本身不算慢。"},
            ],
        }
        out = DeepSeekAccountClient._extract_content(msg)
        assert out == "GLM 5.3 Flash 本身不算慢。"
        assert "检索" not in out

    def test_fragments_only_think_returns_empty(self) -> None:
        """只有 THINK（思考未产出正文）→ 返回空，不把推理当正文。"""
        msg = {"role": "ASSISTANT", "fragments": [{"type": "THINK", "content": "思考中……"}]}
        assert DeepSeekAccountClient._extract_content(msg) == ""

    def test_fragments_unknown_types_joined(self) -> None:
        """无正文类型但有其他 fragment → 拼接非 THINK 部分（尽力而为）。"""
        msg = {"fragments": [{"type": "SEARCH", "content": "检索到 3 条结果"}, {"type": "THINK", "content": "推理"}]}
        assert DeepSeekAccountClient._extract_content(msg) == "检索到 3 条结果"

    def test_legacy_content_string_still_works(self) -> None:
        """旧结构（content 字符串）保持兼容。"""
        assert DeepSeekAccountClient._extract_content({"content": "旧结构正文"}) == "旧结构正文"
        assert DeepSeekAccountClient._extract_content({"content": {"text": "包一层"}}) == "包一层"
        assert DeepSeekAccountClient._extract_content({"text": "纯 text 字段"}) == "纯 text 字段"

    def test_empty_message_returns_empty(self) -> None:
        assert DeepSeekAccountClient._extract_content({}) == ""
        assert DeepSeekAccountClient._extract_content({"fragments": []}) == ""


class TestSafeSessionCount:
    def test_count_clamped_to_safe_upper_bound(self) -> None:
        """count 超上限会被夹到安全值（旧默认 500 恒失败）。"""
        client = DeepSeekAccountClient("fake-token")
        assert client._SAFE_SESSION_COUNT == 50

    def test_not_ready_raises(self) -> None:
        """无 token → 明确报错，不静默返回空列表。"""
        client = DeepSeekAccountClient("")
        try:
            client.list_sessions()
        except RuntimeError as exc:
            assert "token" in str(exc).lower()
        else:  # pragma: no cover
            raise AssertionError("空 token 应抛 RuntimeError")
