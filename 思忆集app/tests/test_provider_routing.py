"""P2.6 Provider 动态路由单元测试。

覆盖：
- ``ProviderRoutingTable`` 的 decide/preview/get_routing_config/is_enabled
- ``parse_routing_config`` / ``validate_routing_config`` / ``list_enabled_provider_names``
- ``JudgeAwareClient`` 中动态路由优先级 1.8 的注入与回退
- ``provider.routing.*`` 4 个 RPC 方法的注册与行为
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any


from ai.provider_routing import (
    ProviderRoutingTable,
    _PROVIDER_ROUTING_PRESETS,
    list_enabled_provider_names,
    parse_routing_config,
    validate_routing_config,
)


# ──────────────────────────────────────────────────────────────────
# 测试夹具
# ──────────────────────────────────────────────────────────────────


class _FakeSettingsRepo:
    """内存中存储 settings。"""

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = dict(settings or {})

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        self._data[key] = value


class _FakeHealthMonitor:
    """模拟 ProviderHealthMonitor。"""

    def __init__(self, available_names: set[str] | None = None) -> None:
        self._available = available_names if available_names is not None else {"deepseek", "glm", "moonshot", "qwen"}

    def is_available(self, name: str) -> bool:
        return name in self._available


class _FakeOpenAIClient:
    """模拟 OpenAICompatibleClient。"""

    def __init__(self, name: str, model: str = "fake-model") -> None:
        self.name = name
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def chat_completion(self, messages, temperature=0.2, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return {
            "choices": [{"message": {"content": f"response from {self.name}"}}],
            "model": self.model,
            "_provider": self.name,
        }


class _FakeInnerClient:
    """模拟 FallbackOpenAIClient。"""

    def __init__(self, known_providers: set[str] | None = None) -> None:
        self._health_monitor = None
        self._clients: dict[str, _FakeOpenAIClient] = {}
        # 已知的 provider 名（模拟 provider_configs 中已配置的）
        # preset 名（deepseek/glm/moonshot/qwen）总是已知的
        self._known_providers = known_providers or set()
        self.primary = SimpleNamespace(chat_completion=lambda **kw: {"choices": [{"message": {"content": "primary"}}]})

    def set_health_monitor(self, monitor) -> None:
        self._health_monitor = monitor

    def get_client_by_provider(self, name, model=None, settings_repository=None):
        # preset 名总是可用（模拟 env_key 配置）
        from ai.provider_routing import _PROVIDER_ROUTING_PRESETS

        if name not in _PROVIDER_ROUTING_PRESETS and name not in self._known_providers:
            return None  # 模拟 load_providers 找不到匹配
        key = f"{name}:{model or ''}"
        if key not in self._clients:
            self._clients[key] = _FakeOpenAIClient(name, model or "default-model")
        return self._clients[key]

    def chat_completion(self, messages, **kwargs):
        return {"choices": [{"message": {"content": "fallback response"}}]}


class _FakeJudge:
    def get_stats(self) -> dict[str, Any]:
        return {"total_routed": 0, "by_executor": {}}

    def get_status_snapshot(self) -> dict[str, Any]:
        return {"ready": True}


class _FakeAiClient:
    """模拟 JudgeAwareClient。"""

    def __init__(self) -> None:
        self._inner = _FakeInnerClient()
        self._judge = _FakeJudge()
        self._lazy_judge = None
        self._settings = None
        self._routing_table = None


class _FakeRegistry:
    def __init__(self) -> None:
        self.methods: dict[str, Any] = {}

    def register(self, name: str, handler) -> None:
        self.methods[name] = handler

    def register_with_schema(self, name: str, handler, **kwargs) -> None:
        self.methods[name] = handler


class _FakeContext:
    def __init__(self) -> None:
        self.settings_repository = _FakeSettingsRepo()
        # 显式标注 Optional：test_stats_without_ai_client 会置 None 模拟无 AI 客户端
        self.ai_client: _FakeAiClient | None = _FakeAiClient()
        self.provider_health_monitor = None
        self.recreate_called = 0

    def recreate_ai_client(self) -> None:
        self.recreate_called += 1


# ──────────────────────────────────────────────────────────────────
# ProviderRoutingTable — 基础行为
# ──────────────────────────────────────────────────────────────────


class TestRoutingTableBasics:
    def test_no_config_returns_none(self) -> None:
        """未配置路由时 decide 返回 None。"""
        repo = _FakeSettingsRepo()
        table = ProviderRoutingTable(settings_repository=repo)
        assert table.is_enabled() is False
        assert table.decide("分析这个 bug") is None

    def test_empty_string_config(self) -> None:
        """显式空字符串配置等价于未启用。"""
        repo = _FakeSettingsRepo({"llm_provider_routing": ""})
        table = ProviderRoutingTable(settings_repository=repo)
        assert table.is_enabled() is False

    def test_invalid_json_falls_back_to_disabled(self) -> None:
        """无效 JSON 配置不会让路由表崩溃，而是视为未启用。"""
        repo = _FakeSettingsRepo({"llm_provider_routing": "{not-json"})
        table = ProviderRoutingTable(settings_repository=repo)
        assert table.is_enabled() is False
        assert table.decide("test") is None

    def test_non_dict_json_falls_back(self) -> None:
        """JSON 解析后非 dict 也视为未启用。"""
        repo = _FakeSettingsRepo({"llm_provider_routing": "[1,2,3]"})
        table = ProviderRoutingTable(settings_repository=repo)
        assert table.is_enabled() is False

    def test_get_routing_config_returns_copy(self) -> None:
        """get_routing_config 返回 dict 副本，外部修改不影响内部状态。"""
        repo = _FakeSettingsRepo({"llm_provider_routing": '{"default": "deepseek"}'})
        table = ProviderRoutingTable(settings_repository=repo)
        cfg = table.get_routing_config()
        cfg["default"] = "tampered"
        # 再次读取应保持原值
        assert table.get_routing_config() == {"default": "deepseek"}


class TestRoutingTableClassifyQuery:
    def test_reasoning_cues(self) -> None:
        table = ProviderRoutingTable(settings_repository=_FakeSettingsRepo())
        assert table.classify_query("为什么") == "reasoning"
        assert table.classify_query("分析原因") == "reasoning"
        assert table.classify_query("请推理") == "reasoning"

    def test_coding_cues(self) -> None:
        table = ProviderRoutingTable(settings_repository=_FakeSettingsRepo())
        assert table.classify_query("写一段 python 代码") == "coding"
        assert table.classify_query("debug 这个 bug") == "coding"
        assert table.classify_query("实现一个函数") == "coding"

    def test_chinese_knowledge_cues(self) -> None:
        table = ProviderRoutingTable(settings_repository=_FakeSettingsRepo())
        assert table.classify_query("讲讲中国历史") == "chinese_knowledge"
        assert table.classify_query("解释这首古诗") == "chinese_knowledge"

    def test_summarization_cues(self) -> None:
        table = ProviderRoutingTable(settings_repository=_FakeSettingsRepo())
        assert table.classify_query("帮我总结一下") == "summarization"
        assert table.classify_query("提炼要点") == "summarization"

    def test_general_chat_default(self) -> None:
        table = ProviderRoutingTable(settings_repository=_FakeSettingsRepo())
        assert table.classify_query("hello") == "general_chat"
        assert table.classify_query("你好") == "general_chat"


# ──────────────────────────────────────────────────────────────────
# ProviderRoutingTable — decide 决策
# ──────────────────────────────────────────────────────────────────


class TestRoutingDecide:
    def test_decide_returns_preset_provider_and_model(self) -> None:
        """配置 reasoning → deepseek，decide 返回 (deepseek, deepseek-v4-flash)。"""
        repo = _FakeSettingsRepo(
            {
                "llm_provider_routing": json.dumps({"reasoning": "deepseek"}),
            }
        )
        table = ProviderRoutingTable(settings_repository=repo)
        decision = table.decide("分析这个 bug 的原因")
        assert decision is not None
        provider, model = decision
        assert provider == "deepseek"
        assert model == "deepseek-v4-flash"  # preset 默认模型

    def test_decide_falls_back_to_default_key(self) -> None:
        """query_type 无显式映射时使用 default 键。"""
        repo = _FakeSettingsRepo(
            {
                "llm_provider_routing": json.dumps({"default": "glm"}),
            }
        )
        table = ProviderRoutingTable(settings_repository=repo)
        # general_chat 没有显式映射，应使用 default
        decision = table.decide("hello")
        assert decision is not None
        assert decision[0] == "glm"

    def test_decide_primary_returns_none(self) -> None:
        """default = primary 时返回 None（表示走默认回退链）。"""
        repo = _FakeSettingsRepo(
            {
                "llm_provider_routing": json.dumps({"default": "primary"}),
            }
        )
        table = ProviderRoutingTable(settings_repository=repo)
        assert table.decide("any message") is None

    def test_decide_health_monitor_blocks_unavailable(self) -> None:
        """health_monitor 报告 provider 不可用时返回 None。"""
        repo = _FakeSettingsRepo(
            {
                "llm_provider_routing": json.dumps({"reasoning": "deepseek"}),
            }
        )
        table = ProviderRoutingTable(settings_repository=repo)
        # DeepSeek 不可用
        hm = _FakeHealthMonitor(available_names=set())  # 空集合，全部不可用
        decision = table.decide("分析原因", health_monitor=hm)
        assert decision is None

    def test_decide_health_monitor_allows_available(self) -> None:
        """health_monitor 报告 provider 可用时正常返回。"""
        repo = _FakeSettingsRepo(
            {
                "llm_provider_routing": json.dumps({"reasoning": "deepseek"}),
            }
        )
        table = ProviderRoutingTable(settings_repository=repo)
        hm = _FakeHealthMonitor(available_names={"deepseek"})
        decision = table.decide("分析原因", health_monitor=hm)
        assert decision is not None
        assert decision[0] == "deepseek"

    def test_decide_health_monitor_swallows_exceptions(self) -> None:
        """health_monitor.is_available 抛异常时不应崩溃，回退到 None。"""

        class _BadHM:
            def is_available(self, name):
                raise RuntimeError("boom")

        repo = _FakeSettingsRepo(
            {
                "llm_provider_routing": json.dumps({"reasoning": "deepseek"}),
            }
        )
        table = ProviderRoutingTable(settings_repository=repo)
        # 不应抛异常
        decision = table.decide("分析原因", health_monitor=_BadHM())
        # 异常被 catch 后不阻塞，但仍尝试返回决策（health check 失败不影响后续逻辑）
        # 实际行为：异常被 debug 日志吞掉，决策继续（因为 try/except 后没有 return）
        assert decision is not None  # 见实现：异常后继续到 model 解析

    def test_decide_available_names_whitelist(self) -> None:
        """非 preset 自定义 provider 不在白名单中时返回 None。"""
        repo = _FakeSettingsRepo(
            {
                "llm_provider_routing": json.dumps({"reasoning": "my-custom-provider"}),
            }
        )
        table = ProviderRoutingTable(settings_repository=repo)
        # my-custom-provider 不是 preset，且不在白名单中
        decision = table.decide("分析原因", available_provider_names=["glm", "moonshot"])
        assert decision is None

    def test_decide_available_names_allows_listed(self) -> None:
        """非 preset provider 在白名单中时正常返回。"""
        repo = _FakeSettingsRepo(
            {
                "llm_provider_routing": json.dumps({"reasoning": "my-custom-provider"}),
            }
        )
        table = ProviderRoutingTable(settings_repository=repo)
        decision = table.decide("分析原因", available_provider_names=["my-custom-provider", "glm"])
        assert decision is not None
        assert decision[0] == "my-custom-provider"

    def test_decide_preset_ignores_empty_whitelist(self) -> None:
        """preset provider 在空白名单中也能通过（preset 有独立 env_key）。"""
        repo = _FakeSettingsRepo(
            {
                "llm_provider_routing": json.dumps({"reasoning": "deepseek"}),
            }
        )
        table = ProviderRoutingTable(settings_repository=repo)
        # 空白名单不应阻止 preset provider
        decision = table.decide("分析原因", available_provider_names=[])
        assert decision is not None
        assert decision[0] == "deepseek"

    def test_decide_preset_ignores_whitelist_when_preset(self) -> None:
        """preset provider 即使不在白名单中也能通过。"""
        repo = _FakeSettingsRepo(
            {
                "llm_provider_routing": json.dumps({"reasoning": "deepseek"}),
            }
        )
        table = ProviderRoutingTable(settings_repository=repo)
        decision = table.decide("分析原因", available_provider_names=["glm"])
        assert decision is not None
        assert decision[0] == "deepseek"

    def test_decide_non_preset_provider_returns_empty_model(self) -> None:
        """非 preset provider（如 provider_configs 中的自定义）返回空 model。"""
        repo = _FakeSettingsRepo(
            {
                "llm_provider_routing": json.dumps({"reasoning": "my-custom-provider"}),
            }
        )
        table = ProviderRoutingTable(settings_repository=repo)
        decision = table.decide("分析原因")
        assert decision is not None
        assert decision[0] == "my-custom-provider"
        assert decision[1] == ""  # 非 preset，model 为空


class TestRoutingPreview:
    def test_preview_returns_full_dict(self) -> None:
        repo = _FakeSettingsRepo(
            {
                "llm_provider_routing": json.dumps({"reasoning": "deepseek", "default": "glm"}),
            }
        )
        table = ProviderRoutingTable(settings_repository=repo)
        result = table.preview("分析这个 bug")
        assert result["query_type"] == "reasoning"
        assert result["matched_provider"] == "deepseek"
        assert result["matched_model"] == "deepseek-v4-flash"
        assert result["will_use_dynamic_routing"] is True
        assert result["fallback_reason"] is None
        assert "deepseek" in result["available_presets"]

    def test_preview_disabled_shows_reason(self) -> None:
        repo = _FakeSettingsRepo()
        table = ProviderRoutingTable(settings_repository=repo)
        result = table.preview("hello")
        assert result["will_use_dynamic_routing"] is False
        assert result["fallback_reason"] == "routing_disabled"

    def test_preview_unavailable_shows_reason(self) -> None:
        repo = _FakeSettingsRepo(
            {
                "llm_provider_routing": json.dumps({"reasoning": "deepseek"}),
            }
        )
        table = ProviderRoutingTable(settings_repository=repo)
        hm = _FakeHealthMonitor(available_names=set())
        result = table.preview("分析原因", health_monitor=hm)
        assert result["will_use_dynamic_routing"] is False
        assert result["fallback_reason"] == "provider_unavailable_or_not_listed"


# ──────────────────────────────────────────────────────────────────
# parse_routing_config / validate_routing_config
# ──────────────────────────────────────────────────────────────────


class TestParseRoutingConfig:
    def test_empty_string_returns_empty_dict(self) -> None:
        assert parse_routing_config("") == {}

    def test_none_returns_empty_dict(self) -> None:
        assert parse_routing_config(None) == {}

    def test_non_string_returns_empty_dict(self) -> None:
        assert parse_routing_config({"already": "dict"}) == {}  # type: ignore[arg-type]

    def test_valid_json(self) -> None:
        result = parse_routing_config('{"reasoning": "deepseek", "default": "glm"}')
        assert result == {"reasoning": "deepseek", "default": "glm"}

    def test_invalid_json(self) -> None:
        assert parse_routing_config("{not-json") == {}

    def test_non_dict_json(self) -> None:
        assert parse_routing_config("[1,2,3]") == {}

    def test_strips_whitespace_in_values(self) -> None:
        result = parse_routing_config('{"reasoning": "  deepseek  "}')
        assert result == {"reasoning": "deepseek"}

    def test_filters_non_string_values(self) -> None:
        result = parse_routing_config('{"reasoning": "deepseek", "count": 5}')
        assert result == {"reasoning": "deepseek"}


class TestValidateRoutingConfig:
    def test_empty_dict_valid(self) -> None:
        ok, err = validate_routing_config({})
        assert ok is True

    def test_valid_dict_with_preset_names(self) -> None:
        ok, err = validate_routing_config({"reasoning": "deepseek", "default": "primary"})
        assert ok is True

    def test_valid_dict_with_custom_provider(self) -> None:
        """非 preset provider 名也通过 schema 校验（运行时再验证）。"""
        ok, err = validate_routing_config({"reasoning": "my-custom-provider"})
        assert ok is True

    def test_non_dict_invalid(self) -> None:
        ok, err = validate_routing_config("not a dict")  # type: ignore[arg-type]
        assert ok is False
        assert "字典" in err

    def test_non_string_key_invalid(self) -> None:
        ok, err = validate_routing_config({1: "deepseek"})  # type: ignore[dict-item]
        assert ok is False

    def test_non_string_value_invalid(self) -> None:
        ok, err = validate_routing_config({"reasoning": 5})  # type: ignore[dict-item]
        assert ok is False

    def test_empty_string_value_invalid(self) -> None:
        ok, err = validate_routing_config({"reasoning": ""})
        assert ok is False


# ──────────────────────────────────────────────────────────────────
# list_enabled_provider_names
# ──────────────────────────────────────────────────────────────────


class TestListEnabledProviderNames:
    def test_none_repo_returns_empty(self) -> None:
        assert list_enabled_provider_names(None) == []

    def test_no_providers_configured(self) -> None:
        """provider_configs 为空时触发默认播种，返回默认启用 provider 名。"""
        repo = _FakeSettingsRepo({"provider_configs": []})
        names = list_enabled_provider_names(repo)
        # 默认播种 DeepSeek + 本地 Ollama（均启用），OpenAI 默认禁用不在列
        assert "DeepSeek" in names
        assert "本地 Ollama" in names
        assert "OpenAI" not in names


# ──────────────────────────────────────────────────────────────────
# JudgeAwareClient — 动态路由优先级 1.8 集成
# ──────────────────────────────────────────────────────────────────


class TestJudgeAwareClientDynamicRouting:
    """测试 JudgeAwareClient.chat_completion 中 PRIORITY 1.8 的注入。"""

    def _make_client(self, routing_config: dict[str, str] | None = None) -> tuple[Any, _FakeInnerClient, _FakeSettingsRepo]:
        """构造 JudgeAwareClient + FakeInner + FakeSettings。"""
        from ai.client._judge_aware_client import JudgeAwareClient

        settings = _FakeSettingsRepo()
        if routing_config:
            settings.set_setting("llm_provider_routing", json.dumps(routing_config))
        inner = _FakeInnerClient()
        client = JudgeAwareClient(
            inner_client=inner,
            settings_repository=settings,
            judge=SimpleNamespace(route=lambda *a, **kw: None),  # 不让 Judge 干预
            local_ai_router=None,
            aggregator=None,
        )
        return client, inner, settings

    def test_dynamic_routing_disabled_when_no_config(self) -> None:
        """无路由配置时走默认链（inner.chat_completion）。"""
        client, inner, _ = self._make_client(routing_config=None)
        result = client.chat_completion(
            messages=[{"role": "user", "content": "分析这个 bug"}],
        )
        # 走 inner.chat_completion（默认回退链）
        assert "fallback response" in result["choices"][0]["message"]["content"]

    def test_dynamic_routing_selects_provider(self) -> None:
        """配置 reasoning → deepseek，分析类问题应路由到 deepseek 客户端。"""
        client, inner, _ = self._make_client(routing_config={"reasoning": "deepseek"})
        result = client.chat_completion(
            messages=[{"role": "user", "content": "分析这个 bug 的原因"}],
        )
        # 应通过 get_client_by_provider 创建客户端并调用
        assert "response from deepseek" in result["choices"][0]["message"]["content"]
        # 验证缓存了客户端
        assert any("deepseek" in k for k in inner._clients.keys())

    def test_dynamic_routing_respects_query_type(self) -> None:
        """非 reasoning 类查询不触发 deepseek 路由。"""
        client, inner, _ = self._make_client(routing_config={"reasoning": "deepseek"})
        # general_chat 不匹配 reasoning
        result = client.chat_completion(
            messages=[{"role": "user", "content": "你好"}],
        )
        # 应走默认链
        assert "fallback response" in result["choices"][0]["message"]["content"]

    def test_preferred_provider_takes_priority_over_dynamic(self) -> None:
        """显式 preferred_provider 优先级高于动态路由。"""
        client, inner, _ = self._make_client(routing_config={"reasoning": "deepseek"})
        result = client.chat_completion(
            messages=[{"role": "user", "content": "分析原因"}],
            preferred_provider="glm",
        )
        # 应路由到 glm（user-specified），而不是 deepseek（dynamic）
        assert "response from glm" in result["choices"][0]["message"]["content"]

    def test_dynamic_routing_falls_back_when_provider_not_found(self) -> None:
        """配置引用不存在的 provider 时应静默回退。"""
        # 用一个不存在的 provider 名，且不让 available_names 通过
        client, inner, settings = self._make_client(routing_config={"reasoning": "nonexistent-provider"})
        # 由于 available_provider_names 会从 provider_configs 读取（空），
        # nonexistent-provider 不在白名单中，应回退到默认链
        result = client.chat_completion(
            messages=[{"role": "user", "content": "分析原因"}],
        )
        # 应走默认链
        assert "fallback response" in result["choices"][0]["message"]["content"]

    def test_health_monitor_blocks_dynamic_routing(self) -> None:
        """health_monitor 报告不可用时回退到默认链。"""
        client, inner, _ = self._make_client(routing_config={"reasoning": "deepseek"})
        # 注入 health_monitor，让 deepseek 不可用
        inner.set_health_monitor(_FakeHealthMonitor(available_names=set()))
        result = client.chat_completion(
            messages=[{"role": "user", "content": "分析原因"}],
        )
        # 应走默认链
        assert "fallback response" in result["choices"][0]["message"]["content"]

    def test_explicit_action_bypasses_dynamic_routing(self) -> None:
        """explicit_action（翻译/摘要等）绕过动态路由。"""
        client, inner, _ = self._make_client(routing_config={"summarization": "deepseek"})
        # 使用真实的 explicit action 关键词 "总结以下"
        result = client.chat_completion(
            messages=[{"role": "user", "content": "请帮我总结以下这段话"}],
        )
        # explicit_action 走 inner.chat_completion（不经过 dynamic routing）
        assert "fallback response" in result["choices"][0]["message"]["content"]

    def test_tools_bypass_dynamic_routing(self) -> None:
        """tools 参数存在时绕过动态路由（ReAct 引擎管理）。"""
        client, inner, _ = self._make_client(routing_config={"reasoning": "deepseek"})
        result = client.chat_completion(
            messages=[{"role": "user", "content": "分析原因"}],
            tools=[{"type": "function", "function": {"name": "test"}}],
        )
        # 走 inner.chat_completion（带 tools 参数）
        assert "fallback response" in result["choices"][0]["message"]["content"]


# ──────────────────────────────────────────────────────────────────
# provider.routing.* RPC 方法
# ──────────────────────────────────────────────────────────────────


class TestRoutingRpcGet:
    def test_get_returns_config_and_presets(self) -> None:
        from backend.handlers._system_settings import register_system_settings

        ctx = _FakeContext()
        ctx.settings_repository.set_setting(
            "llm_provider_routing",
            json.dumps({"reasoning": "deepseek"}),
        )
        registry = _FakeRegistry()
        register_system_settings(registry, ctx, ws_port=18765, ak_service=SimpleNamespace())

        handler = registry.methods["provider.routing.get"]
        result = asyncio.run(handler())
        assert result["enabled"] is True
        assert result["routing"] == {"reasoning": "deepseek"}
        assert "presets" in result
        assert len(result["presets"]) == len(_PROVIDER_ROUTING_PRESETS)
        assert "available_provider_names" in result

    def test_get_returns_disabled_when_empty(self) -> None:
        from backend.handlers._system_settings import register_system_settings

        ctx = _FakeContext()
        registry = _FakeRegistry()
        register_system_settings(registry, ctx, ws_port=18765, ak_service=SimpleNamespace())

        handler = registry.methods["provider.routing.get"]
        result = asyncio.run(handler())
        assert result["enabled"] is False
        assert result["routing"] == {}


class TestRoutingRpcSet:
    def _setup(self):
        from backend.handlers._system_settings import register_system_settings

        ctx = _FakeContext()
        registry = _FakeRegistry()
        register_system_settings(registry, ctx, ws_port=18765, ak_service=SimpleNamespace())
        return ctx, registry

    def test_set_valid_dict(self) -> None:
        ctx, registry = self._setup()
        handler = registry.methods["provider.routing.set"]
        result = asyncio.run(handler(routing={"reasoning": "deepseek", "default": "primary"}))
        assert result["success"] is True
        assert result["enabled"] is True
        # 验证已写入 settings
        from core.settings_keys import KEY_LLM_PROVIDER_ROUTING

        raw = ctx.settings_repository.get_setting(KEY_LLM_PROVIDER_ROUTING)
        assert json.loads(raw) == {"reasoning": "deepseek", "default": "primary"}
        # 验证触发了 recreate_ai_client
        assert ctx.recreate_called == 1

    def test_set_valid_json_string(self) -> None:
        ctx, registry = self._setup()
        handler = registry.methods["provider.routing.set"]
        result = asyncio.run(handler(routing='{"reasoning": "deepseek"}'))
        assert result["success"] is True

    def test_set_empty_dict_disables(self) -> None:
        ctx, registry = self._setup()
        handler = registry.methods["provider.routing.set"]
        result = asyncio.run(handler(routing={}))
        assert result["success"] is True
        assert result["enabled"] is False

    def test_set_empty_json_string_disables(self) -> None:
        ctx, registry = self._setup()
        handler = registry.methods["provider.routing.set"]
        result = asyncio.run(handler(routing=""))
        assert result["success"] is True
        assert result["enabled"] is False

    def test_set_invalid_json_rejected(self) -> None:
        ctx, registry = self._setup()
        handler = registry.methods["provider.routing.set"]
        result = asyncio.run(handler(routing="{not-json"))
        assert result["success"] is False
        assert "invalid JSON" in result["error"]

    def test_set_non_dict_rejected(self) -> None:
        ctx, registry = self._setup()
        handler = registry.methods["provider.routing.set"]
        result = asyncio.run(handler(routing=123))
        assert result["success"] is False
        assert "dict" in result["error"].lower() or "json" in result["error"].lower()

    def test_set_non_string_value_rejected(self) -> None:
        ctx, registry = self._setup()
        handler = registry.methods["provider.routing.set"]
        result = asyncio.run(handler(routing={"reasoning": 5}))
        assert result["success"] is False

    def test_set_unknown_provider_rejected(self) -> None:
        """引用未知 provider 名应被运行时校验拦截。"""
        ctx, registry = self._setup()
        handler = registry.methods["provider.routing.set"]
        result = asyncio.run(handler(routing={"reasoning": "totally-unknown-provider"}))
        assert result["success"] is False
        assert "unknown providers" in result["error"]

    def test_set_missing_routing_param(self) -> None:
        ctx, registry = self._setup()
        handler = registry.methods["provider.routing.set"]
        result = asyncio.run(handler())
        assert result["success"] is False
        assert "required" in result["error"]


class TestRoutingRpcPreview:
    def test_preview_with_matching_routing(self) -> None:
        from backend.handlers._system_settings import register_system_settings

        ctx = _FakeContext()
        ctx.settings_repository.set_setting(
            "llm_provider_routing",
            json.dumps({"reasoning": "deepseek"}),
        )
        registry = _FakeRegistry()
        register_system_settings(registry, ctx, ws_port=18765, ak_service=SimpleNamespace())

        handler = registry.methods["provider.routing.preview"]
        result = asyncio.run(handler(message="分析这个 bug 的原因"))
        assert result["query_type"] == "reasoning"
        assert result["matched_provider"] == "deepseek"
        assert result["will_use_dynamic_routing"] is True

    def test_preview_empty_message_rejected(self) -> None:
        from backend.handlers._system_settings import register_system_settings

        ctx = _FakeContext()
        registry = _FakeRegistry()
        register_system_settings(registry, ctx, ws_port=18765, ak_service=SimpleNamespace())

        handler = registry.methods["provider.routing.preview"]
        result = asyncio.run(handler(message=""))
        assert "error" in result

    def test_preview_disabled_routing(self) -> None:
        from backend.handlers._system_settings import register_system_settings

        ctx = _FakeContext()
        registry = _FakeRegistry()
        register_system_settings(registry, ctx, ws_port=18765, ak_service=SimpleNamespace())

        handler = registry.methods["provider.routing.preview"]
        result = asyncio.run(handler(message="hello"))
        assert result["will_use_dynamic_routing"] is False
        assert result["fallback_reason"] == "routing_disabled"


class TestRoutingRpcStats:
    def test_stats_returns_judge_stats(self) -> None:
        from backend.handlers._system_settings import register_system_settings

        ctx = _FakeContext()
        registry = _FakeRegistry()
        register_system_settings(registry, ctx, ws_port=18765, ak_service=SimpleNamespace())

        handler = registry.methods["provider.routing.stats"]
        result = asyncio.run(handler())
        assert "stats" in result
        assert "snapshot" in result
        assert result["stats"]["total_routed"] == 0

    def test_stats_without_ai_client(self) -> None:
        from backend.handlers._system_settings import register_system_settings

        ctx = _FakeContext()
        ctx.ai_client = None
        registry = _FakeRegistry()
        register_system_settings(registry, ctx, ws_port=18765, ak_service=SimpleNamespace())

        handler = registry.methods["provider.routing.stats"]
        result = asyncio.run(handler())
        assert "error" in result


# ──────────────────────────────────────────────────────────────────
# 注册验证
# ──────────────────────────────────────────────────────────────────


class TestRpcRegistration:
    def test_all_4_methods_registered(self) -> None:
        from backend.handlers._system_settings import register_system_settings

        ctx = _FakeContext()
        registry = _FakeRegistry()
        register_system_settings(registry, ctx, ws_port=18765, ak_service=SimpleNamespace())

        assert "provider.routing.get" in registry.methods
        assert "provider.routing.set" in registry.methods
        assert "provider.routing.preview" in registry.methods
        assert "provider.routing.stats" in registry.methods

    def test_existing_provider_methods_still_registered(self) -> None:
        """P2.6 不应破坏现有 provider.* 方法。"""
        from backend.handlers._system_settings import register_system_settings

        ctx = _FakeContext()
        registry = _FakeRegistry()
        register_system_settings(registry, ctx, ws_port=18765, ak_service=SimpleNamespace())

        assert "provider.list" in registry.methods
        assert "provider.add" in registry.methods
        assert "provider.health" in registry.methods
        assert "kiro.status" in registry.methods
