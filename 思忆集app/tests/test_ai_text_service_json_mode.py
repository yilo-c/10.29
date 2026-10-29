"""AiTextService 端口契约测试：E3 json_mode 透传/降级 + E1 extract + E2 embed。"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from services.ai_text_service import (
    JSON_OBJECT_FORMAT,
    AiTextService,
    LegacyLlmTextService,
    _endpoint_supports_response_format,
)
from ai.client.base import OpenAICompatibleClient
from ai.types import AIServiceError


class _RecordingClient:
    """记录最近一次 chat_completion 调用参数的 FakeClient（注入范式）。"""

    def __init__(self, content: str = "{}", supports: bool | None = None):
        self._content = content
        self.calls: list[dict] = []
        if supports is not None:
            # 仅在显式指定时挂能力标记；不挂时模拟无标记的裸 fake
            self.supports_response_format = supports

    def chat_completion(self, messages, temperature=0.1, max_tokens=800, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return {"choices": [{"message": {"content": self._content}}]}


class _JudgeLike:
    """模拟 JudgeAwareClient：无能力标记，真实端点藏在 _inner，自身有 chat_completion。"""

    def __init__(self, inner, content: str = "{}"):
        self._inner = inner
        self._content = content
        self.calls: list[dict] = []

    def chat_completion(self, messages, temperature=0.1, max_tokens=800, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return {"choices": [{"message": {"content": self._content}}]}


class _FallbackLike:
    """模拟 FallbackOpenAIClient：无能力标记，主端点藏在 primary。"""

    def __init__(self, primary):
        self.primary = primary


class _ScriptedClient:
    """按脚本顺序逐次返回 content，记录每次调用（extract 重试用 fake）。"""

    def __init__(self, contents: list[str], supports: bool | None = None):
        self._contents = list(contents)
        self.calls: list[dict] = []
        if supports is not None:
            self.supports_response_format = supports

    def chat_completion(self, messages, temperature=0.1, max_tokens=800, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        content = self._contents.pop(0) if self._contents else ""
        return {"choices": [{"message": {"content": content}}]}


class _FakeEmbeddingService:
    """记录 get_embeddings 调用参数的嵌入服务 fake（E2 委托验证）。"""

    def __init__(self, vectors: list[list[float]]):
        self._vectors = vectors
        self.calls: list[list[str]] = []

    def get_embeddings(self, texts):
        self.calls.append(list(texts))
        return self._vectors


_PROSE_WRAPPED = (
    "我分析如下：\n"
    "```json\n"
    '{"kind": "task", "confidence": 0.9, "reason": "包含待办关键词", '
    '"suggested_action": "suggest_convert"}\n'
    "```\n"
    "希望对你有帮助。"
)


# ── 场景③：json_mode=False 行为与改动前完全一致 ──────────────────────
def test_json_mode_off_behavior_unchanged():
    client = _RecordingClient(content="ok")
    svc = LegacyLlmTextService(client)
    resp = svc.complete("问题", system_prompt="系统", temperature=0.3, max_tokens=50)
    call = client.calls[0]
    assert resp == {"choices": [{"message": {"content": "ok"}}]}
    assert call["kwargs"] == {}
    assert call["messages"] == [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "问题"},
    ]


# ── 场景：json_mode=True 且端点支持 → 透传 response_format ────────────
def test_json_mode_supported_sends_response_format():
    client = _RecordingClient(content="{}")
    svc = LegacyLlmTextService(client)
    svc.complete("给我 JSON", json_mode=True)
    call = client.calls[0]
    assert call["kwargs"] == {"response_format": JSON_OBJECT_FORMAT}
    assert call["kwargs"]["response_format"] == {"type": "json_object"}
    # 提示词不应被追加降级文案
    assert call["messages"][-1]["content"] == "给我 JSON"


# ── 场景②：provider 不支持 → 降级为提示词约束，不带 response_format ───
def test_json_mode_unsupported_degrades_to_prompt_hint():
    client = _RecordingClient(content="{}", supports=False)
    svc = LegacyLlmTextService(client)
    svc.complete("给我 JSON", json_mode=True)
    call = client.calls[0]
    assert "response_format" not in call["kwargs"]
    assert call["messages"][-1]["content"].startswith("给我 JSON")
    assert "仅输出合法 JSON" in call["messages"][-1]["content"]


# ── 场景②扩展：包装链（Judge -> Fallback -> 端点）能力探测 ────────────
def test_capability_detected_through_wrapper_chain():
    # Judge 层自身不带标记，能力须沿 _inner -> primary 探测到真实端点
    judge_no = _JudgeLike(_FallbackLike(_RecordingClient(supports=False)))
    svc = LegacyLlmTextService(judge_no)
    svc.complete("x", json_mode=True)
    assert "response_format" not in judge_no.calls[0]["kwargs"]
    assert "仅输出合法 JSON" in judge_no.calls[0]["messages"][-1]["content"]

    judge_yes = _JudgeLike(_FallbackLike(_RecordingClient(supports=True)))
    svc2 = LegacyLlmTextService(judge_yes)
    svc2.complete("x", json_mode=True)
    assert judge_yes.calls[0]["kwargs"] == {"response_format": {"type": "json_object"}}
    assert "仅输出合法 JSON" not in judge_yes.calls[0]["messages"][-1]["content"]


def test_capability_default_true_for_markerless_chain():
    assert _endpoint_supports_response_format(_JudgeLike(_FallbackLike(object()))) is True
    assert _endpoint_supports_response_format(None) is True


# ── 场景①：模型返回 JSON+散文包裹 → classify 正确解析出 dict ──────────
async def test_classify_parses_prose_wrapped_json():
    client = _RecordingClient(content=_PROSE_WRAPPED)
    svc = LegacyLlmTextService(client)
    result = await svc.classify(content="明天需要完成项目报告", title="待办")
    assert isinstance(result, dict)
    assert result["kind"] == "task"
    assert result["confidence"] == 0.9
    assert result["suggested_action"] == "suggest_convert"
    # classify 预期 JSON 输出，默认应启用 json_mode（裸 fake 无标记 → 视为支持）
    assert client.calls[0]["kwargs"] == {"response_format": {"type": "json_object"}}


async def test_classify_local_endpoint_degrades_but_still_parses():
    client = _RecordingClient(content=_PROSE_WRAPPED, supports=False)
    svc = LegacyLlmTextService(client)
    result = await svc.classify(content="下周一开会")
    assert result["kind"] == "task"
    assert result["confidence"] == 0.9
    call = client.calls[0]
    assert "response_format" not in call["kwargs"]
    assert "仅输出合法 JSON" in call["messages"][-1]["content"]


# ── translate / summarize 保持纯文本语义（不传 response_format）───────
async def test_translate_summarize_stay_plain_text():
    client = _RecordingClient(content="你好世界")
    svc = LegacyLlmTextService(client)
    assert await svc.translate("hello world") == "你好世界"
    assert await svc.summarize("长文本") == "你好世界"
    for call in client.calls:
        assert "response_format" not in call["kwargs"]
        assert "仅输出合法 JSON" not in call["messages"][-1]["content"]


# ── 底层能力标记本体：本地端点 False / 云端 True ──────────────────────
def test_base_client_capability_marker():
    local = OpenAICompatibleClient(api_base="http://127.0.0.1:11434/v1", api_key="")
    cloud = OpenAICompatibleClient(api_base="https://api.deepseek.com", api_key="sk-x")
    assert local.supports_response_format is False
    assert cloud.supports_response_format is True


# ── E1/E2 契约：Protocol 声明 extract/embed 且实现签名一致 ─────────────
_SCHEMA = {
    "type": "object",
    "properties": {"memories": {"type": "array", "items": {"type": "object"}}},
    "required": ["memories"],
}


def test_protocol_declares_extract_and_embed():
    assert "extract" in AiTextService.__dict__
    assert "embed" in AiTextService.__dict__
    proto_e = inspect.signature(AiTextService.__dict__["extract"])
    impl_e = inspect.signature(LegacyLlmTextService.extract)
    assert list(proto_e.parameters) == list(impl_e.parameters)
    assert list(proto_e.parameters) == [
        "self",
        "text",
        "schema",
        "system_prompt",
        "temperature",
        "max_tokens",
    ]
    assert impl_e.parameters["system_prompt"].default == ""
    assert impl_e.parameters["temperature"].default == 0.1
    assert impl_e.parameters["max_tokens"].default == 800
    assert proto_e.return_annotation == "dict[str, Any]"

    proto_m = inspect.signature(AiTextService.__dict__["embed"])
    impl_m = inspect.signature(LegacyLlmTextService.embed)
    assert list(proto_m.parameters) == list(impl_m.parameters) == ["self", "texts"]
    assert proto_m.return_annotation == "list[list[float]]"


# ── E1 extract：json_mode 成功路径（response_format 强约束 + schema 注入）──
def test_extract_success_sends_response_format_and_injects_schema():
    client = _RecordingClient(content='{"memories": [{"content": "x"}]}')
    svc = LegacyLlmTextService(client)
    result = svc.extract("对话原文", _SCHEMA)
    assert result == {"memories": [{"content": "x"}]}
    call = client.calls[0]
    assert call["kwargs"] == {"response_format": {"type": "json_object"}}
    system_msg = call["messages"][0]["content"]
    user_msg = call["messages"][-1]["content"]
    assert user_msg == "对话原文"
    assert "输出 JSON Schema" in system_msg
    assert '"memories"' in system_msg  # schema 注入系统提示词指导输出结构


def test_extract_custom_system_prompt_kept_with_schema():
    client = _RecordingClient(content='{"a": 1}')
    svc = LegacyLlmTextService(client)
    assert svc.extract("t", _SCHEMA, system_prompt="领域指引") == {"a": 1}
    system_msg = client.calls[0]["messages"][0]["content"]
    assert system_msg.startswith("领域指引")
    assert '"memories"' in system_msg


# ── E1 extract：解析失败重试 1 次后成功 ────────────────────────────────
def test_extract_retries_once_on_parse_failure():
    client = _ScriptedClient(["这不是 JSON", '{"memories": []}'])
    svc = LegacyLlmTextService(client)
    result = svc.extract("原文", _SCHEMA)
    assert result == {"memories": []}
    assert len(client.calls) == 2
    first_user = client.calls[0]["messages"][-1]["content"]
    retry_user = client.calls[1]["messages"][-1]["content"]
    assert first_user == "原文"
    assert retry_user.startswith("原文")
    assert "上一次输出不是合法的 JSON 对象" in retry_user  # 纠错提醒


def test_extract_non_object_json_triggers_retry():
    client = _ScriptedClient(['["a", "b"]', '{"ok": true}'])
    svc = LegacyLlmTextService(client)
    assert svc.extract("t", _SCHEMA) == {"ok": True}  # 顶层数组非法 → 重试
    assert len(client.calls) == 2


# ── E1 extract：重试 1 次后仍失败 → 抛端口错误约定 AIServiceError ──────
def test_extract_raises_ai_service_error_after_one_retry():
    client = _ScriptedClient(["坏输出", "还是坏输出"])
    svc = LegacyLlmTextService(client)
    with pytest.raises(AIServiceError) as exc_info:
        svc.extract("t", _SCHEMA)
    assert len(client.calls) == 2  # 首次 + 仅重试 1 次
    assert isinstance(exc_info.value, RuntimeError)  # 端口既有错误约定兼容


# ── E1 extract：端点不支持 response_format → 降级提示词路径同样生效 ────
def test_extract_degrades_when_endpoint_unsupported():
    client = _ScriptedClient(["坏输出", '{"ok": 1}'], supports=False)
    svc = LegacyLlmTextService(client)
    assert svc.extract("t", _SCHEMA) == {"ok": 1}
    assert len(client.calls) == 2
    for call in client.calls:
        assert "response_format" not in call["kwargs"]
        assert "仅输出合法 JSON" in call["messages"][-1]["content"]
    assert "上一次输出不是合法的 JSON 对象" in client.calls[1]["messages"][-1]["content"]


def test_extract_requires_client():
    svc = LegacyLlmTextService(None)
    with pytest.raises(RuntimeError, match="ai_client not available"):
        svc.extract("t", _SCHEMA)


# ── E2 embed：委托注入的 EmbeddingService，参数透传、整批一次调用 ──────
def test_embed_delegates_to_injected_service():
    fake = _FakeEmbeddingService(vectors=[[0.1, 0.2], [0.3, 0.4]])
    svc = LegacyLlmTextService(None, embedding_service=fake)
    assert svc.embed(["甲", "乙"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert fake.calls == [["甲", "乙"]]  # 一次批量调用，透传顺序与内容


def test_embed_accepts_non_list_sequence():
    fake = _FakeEmbeddingService(vectors=[[1.0]])
    svc = LegacyLlmTextService(None, embedding_service=fake)
    assert svc.embed(("唯一",)) == [[1.0]]
    assert fake.calls == [["唯一"]]


def test_embed_empty_input_short_circuits():
    fake = _FakeEmbeddingService(vectors=[])
    svc = LegacyLlmTextService(None, embedding_service=fake)
    assert svc.embed([]) == []
    assert svc.embed(None) == []
    assert fake.calls == []  # 空批不进底层


def test_embed_uses_lazy_singleton_when_not_injected(monkeypatch):
    import services.ai_text_service as mod

    fake = _FakeEmbeddingService(vectors=[[0.5]])
    monkeypatch.setattr(mod, "_embedding_singleton", fake)
    svc = LegacyLlmTextService(None)  # 未注入 → 走模块级懒加载单例
    assert svc.embed(["x"]) == [[0.5]]
    assert fake.calls == [["x"]]


def test_default_embedding_service_singleton_created_once():
    import services.ai_text_service as mod

    original = mod._embedding_singleton
    try:
        mod._embedding_singleton = None
        created: list[int] = []

        def _factory():
            created.append(1)
            return _FakeEmbeddingService(vectors=[[0.0]])

        first = mod._get_default_embedding_service(factory=_factory)
        second = mod._get_default_embedding_service(factory=_factory)
        assert first is second
        assert len(created) == 1  # 懒加载：仅首次构造，之后复用单例
    finally:
        mod._embedding_singleton = original
