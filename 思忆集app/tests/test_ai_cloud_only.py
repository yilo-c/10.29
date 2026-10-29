"""云化模式（ai_cloud_only）测试：全链路 AI 走云端，禁用本地腿路由。

用户决策（2026-08-30）：本地基本不用、敏感信息未上传，全改为云端。
开关：设置键 ai_cloud_only（True=云化）+ 环境变量 SIYI_AI_CLOUD_ONLY（优先级更高）。
默认关闭（False）→ 保持既有隐私守卫/本地腿行为（回归面由既有测试锁定）。

覆盖：
1. is_ai_cloud_only 判定（设置键 / 环境变量 / 字符串容错）
2. FallbackOpenAIClient：云化时隐私守卫永远放行云端；默认时 local_only 仍走本地
3. JudgeAwareClient._resolve_skip_cloud：云化时恒 False；默认时隐私内容 True
4. JudgeScheduler.route：云化时跳过 0.1 简单预检 / 0.5 隐私预检 / 0.7 LocalAIRouter；
   默认时隐私内容仍路由 local（回归锁定）
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


from assistant.routing.judge_decision import ROUTE_EXECUTOR_LOCAL


class _Settings:
    """最小 settings 桩：仅暴露 get_setting，ai_cloud_only 可配。"""

    def __init__(self, cloud_only: bool | str = False):
        self._cloud_only = cloud_only

    def get_setting(self, key: str, default=None):
        if key == "ai_cloud_only":
            return self._cloud_only
        return default


class _MockClient:
    """Minimal OpenAICompatibleClient mock（同 test_fallback_client）。"""

    def __init__(self, model="mock-model"):
        self.model = model
        self.last_messages = None

    def is_configured(self):
        return True

    def chat_completion(self, messages, temperature=0.2, max_tokens=900, tools=None, tool_choice=None, **kwargs):
        self.last_messages = messages
        return {"choices": [{"message": {"content": "mock response"}}]}

    def chat_completion_stream(self, messages, temperature=0.2, max_tokens=900, tools=None, tool_choice=None, **kwargs):
        self.last_messages = messages
        yield "mock "
        yield "response"


class _LocalRouterHandled:
    """LocalAIRouter 桩：恒判 handled（触发 0.7 本地路由）。"""

    def process(self, text, ctx=None):
        class _Result:
            handled = True
            confidence = 0.99
            reason = "mock local handled"
            response = "local reply"

        return _Result()


# ── 1. is_ai_cloud_only 判定 ──


class TestIsAiCloudOnly:
    def test_settings_true(self):
        from ai.client.config import is_ai_cloud_only

        assert is_ai_cloud_only(_Settings(True)) is True

    def test_settings_false(self):
        from ai.client.config import is_ai_cloud_only

        assert is_ai_cloud_only(_Settings(False)) is False

    def test_settings_string_truthy(self):
        from ai.client.config import is_ai_cloud_only

        assert is_ai_cloud_only(_Settings("1")) is True

    def test_settings_string_falsy(self):
        from ai.client.config import is_ai_cloud_only

        assert is_ai_cloud_only(_Settings("false")) is False

    def test_env_takes_precedence(self, monkeypatch):
        from ai.client.config import is_ai_cloud_only

        monkeypatch.setenv("SIYI_AI_CLOUD_ONLY", "1")
        assert is_ai_cloud_only(_Settings(False)) is True
        monkeypatch.delenv("SIYI_AI_CLOUD_ONLY")
        assert is_ai_cloud_only(_Settings(False)) is False

    def test_env_only(self, monkeypatch):
        from ai.client.config import is_ai_cloud_only

        monkeypatch.setenv("SIYI_AI_CLOUD_ONLY", "1")
        assert is_ai_cloud_only(None) is True
        monkeypatch.delenv("SIYI_AI_CLOUD_ONLY")
        assert is_ai_cloud_only(None) is False


# ── 2. FallbackOpenAIClient 隐私守卫 ──


class TestFallbackCloudOnly:
    def _fb(self, cloud_only: bool, classifier):
        from ai.client.fallback import FallbackOpenAIClient

        return FallbackOpenAIClient(
            primary=_MockClient(),
            local=_MockClient(),
            cloud_only=cloud_only,
            privacy_filter=classifier,
        )

    @staticmethod
    def _local_only_classifier(text: str) -> str:
        return "local_only"

    def test_cloud_only_never_skips_cloud(self):
        """云化：即使内容被判 local_only，也走云端 primary，不落本地。"""
        fb = self._fb(True, self._local_only_classifier)
        fb.chat_completion(
            [
                {"role": "system", "content": "隐私 通讯录"},
                {"role": "user", "content": "帮我整理项目的笔记"},
            ]
        )
        assert fb.primary.last_messages is not None, "云端 primary 应被调用"
        assert fb.local.last_messages is None, "本地腿不应被触达"

    def test_default_still_routes_local(self):
        """默认（关闭云化）：local_only 内容跳过云端、落本地（回归锁定）。"""
        fb = self._fb(False, self._local_only_classifier)
        fb.chat_completion([{"role": "user", "content": "我的银行卡号是 6222 8888 0000"}])
        assert fb.primary.last_messages is None, "云端 primary 应被跳过"
        assert fb.local.last_messages is not None, "本地腿应被使用"

    def test_cloud_only_stream_never_skips_cloud(self):
        fb = self._fb(True, self._local_only_classifier)
        chunks = list(
            fb.chat_completion_stream(
                [
                    {"role": "system", "content": "隐私 通讯录"},
                    {"role": "user", "content": "整理笔记"},
                ]
            )
        )
        assert "".join(chunks) == "mock response", f"流式应走云端，got {chunks}"
        assert fb.primary.last_messages is not None


# ── 3. JudgeAwareClient._resolve_skip_cloud ──


class TestJudgeAwareSkipCloud:
    def _jac(self, cloud_only: bool):
        from ai.client._judge_aware_client import JudgeAwareClient
        from ai.client.fallback import FallbackOpenAIClient

        inner = FallbackOpenAIClient(primary=_MockClient())
        return JudgeAwareClient(inner, settings_repository=_Settings(cloud_only))

    def test_cloud_only_returns_false(self):
        jac = self._jac(True)
        assert jac._resolve_skip_cloud([{"role": "user", "content": "我的通讯录和隐私信息"}]) is False

    def test_default_returns_true_for_privacy(self):
        jac = self._jac(False)
        assert jac._resolve_skip_cloud([{"role": "user", "content": "我的通讯录和隐私信息"}]) is True

    def test_cloud_only_routes_all_to_cloud(self):
        """云化 + 隐私内容：chat_completion 应走云端 primary，不 _route_local。"""
        from ai.client._judge_aware_client import JudgeAwareClient
        from ai.client.fallback import FallbackOpenAIClient

        primary = _MockClient()
        inner = FallbackOpenAIClient(primary=primary, local=_MockClient())
        jac = JudgeAwareClient(inner, settings_repository=_Settings(True))
        resp = jac.chat_completion([{"role": "user", "content": "我的通讯录里有隐私"}])
        assert resp["choices"][0]["message"]["content"] == "mock response"
        assert primary.last_messages is not None


# ── 4. JudgeScheduler.route 本地倾向段 ──


class TestJudgeSchedulerCloudOnly:
    def test_cloud_only_privacy_not_forced_local(self):
        from assistant.routing.judge import JudgeScheduler

        j = JudgeScheduler(ai_client=None, cloud_only=True)
        d = j.route("我的银行卡号是 6222 8888 0000 密码 abc")
        assert d.executor != ROUTE_EXECUTOR_LOCAL, f"云化下隐私内容不得强制本地: {d.reason}"

    def test_cloud_only_simple_greeting_not_local(self):
        from assistant.routing.judge import JudgeScheduler

        j = JudgeScheduler(ai_client=None, cloud_only=True)
        d = j.route("你好呀")
        assert d.executor != ROUTE_EXECUTOR_LOCAL, f"云化下简单问候不得路由本地: {d.reason}"

    def test_cloud_only_skips_local_router(self):
        from assistant.routing.judge import JudgeScheduler

        j = JudgeScheduler(ai_client=None, local_router=_LocalRouterHandled(), cloud_only=True)
        d = j.route("帮我想个方案")
        assert d.executor != ROUTE_EXECUTOR_LOCAL, f"云化下 LocalAIRouter 预检应被跳过: {d.reason}"

    def test_cloud_only_safety_net_not_override(self):
        from assistant.routing.judge import JudgeScheduler

        # ai_client 返回 deepseek 决策；云化下安全网不得再覆盖为 local
        class _AI:
            def chat_completion(self, messages, **kwargs):
                return {
                    "choices": [{"message": {"content": '{"executor": "deepseek", "reason": "llm_route", "confidence": 0.8, "reasoning": "x"}'}}],
                }

        j = JudgeScheduler(ai_client=_AI(), cloud_only=True)
        d = j.route("我的银行卡号是 6222 8888 0000")
        assert d.executor != ROUTE_EXECUTOR_LOCAL, f"云化下安全网不得覆盖为 local: {d.reason}"

    def test_default_privacy_still_local(self):
        """默认（关闭云化）：隐私关键词路由 local（回归锁定）。"""
        from assistant.routing.judge import JudgeScheduler

        j = JudgeScheduler(ai_client=None, cloud_only=False)
        d = j.route("我的银行卡号是 6222 8888 0000")
        assert d.executor == ROUTE_EXECUTOR_LOCAL, f"默认隐私应路由 local: {d.reason}"

    def test_default_local_router_handled(self):
        from assistant.routing.judge import JudgeScheduler

        j = JudgeScheduler(ai_client=None, local_router=_LocalRouterHandled(), cloud_only=False)
        d = j.route("帮我想个方案")
        assert d.executor == ROUTE_EXECUTOR_LOCAL, f"默认 LocalAIRouter 应接管: {d.reason}"

    def test_cloud_llm_routes_local_gets_coerced(self):
        """云化：LLM 裁决 local（如"简单任务"）必须被强制改走云端。"""
        from assistant.routing.judge import JudgeScheduler

        class _AI:
            def chat_completion(self, messages, **kwargs):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": '{"executor": "local", "reason": "简单文本概括任务，无需联网或深度推理", "confidence": 0.95, "reasoning": "simple"}'
                            }
                        }
                    ],
                }

        j = JudgeScheduler(ai_client=_AI(), cloud_only=True)
        d = j.route("帮我总结一下今天的进展")
        assert d.executor == "deepseek", f"云化下 LLM 判 local 必须强制云端: {d.reason}"
        assert d.reason == "cloud_only_override", f"override 原因标记: {d.reason}"

    def test_default_llm_routes_local_kept(self):
        """默认（关闭云化）：LLM 裁决 local 保持 local（回归锁定）。"""
        from assistant.routing.judge import JudgeScheduler

        class _AI:
            def chat_completion(self, messages, **kwargs):
                return {
                    "choices": [{"message": {"content": '{"executor": "local", "reason": "simple", "confidence": 0.95}'}}],
                }

        j = JudgeScheduler(ai_client=_AI(), cloud_only=False)
        d = j.route("帮我总结一下今天的进展")
        assert d.executor == ROUTE_EXECUTOR_LOCAL, f"默认 LLM 判 local 应保持: {d.reason}"


# ── 6. 设置迁移：新增 BOOL 键必须有默认值，启动迁移不 KeyError ──


class TestSettingsMigrationCloudKey:
    """回归锁定：KEY_AI_CLOUD_ONLY 登记进 BOOL_KEYS 后，migrate_settings_repository
    迭代 BOOL_KEYS 时必须能在 SETTINGS_DEFAULTS 里取到默认值，否则应用启动即崩
    （KeyError: 'ai_cloud_only'，见 2026-08-31 启动演练）。"""

    def test_migration_empty_store_does_not_keyerror(self, tmp_path):
        from data.local_storage import LocalDataStore
        from repositories import LocalSettingsRepository
        from core.settings_schema import (
            SETTINGS_DEFAULTS,
            migrate_settings_repository,
        )

        data_store = LocalDataStore(base_dir=str(tmp_path))
        repo = LocalSettingsRepository(data_store)
        migrate_settings_repository(repo)  # 不得 KeyError
        migrated = data_store.load_json("settings", {})
        assert "ai_cloud_only" in migrated
        assert migrated["ai_cloud_only"] is False
        assert migrated["ai_cloud_only"] == SETTINGS_DEFAULTS["ai_cloud_only"]

    def test_migration_preserves_existing_true(self, tmp_path):
        from data.local_storage import LocalDataStore
        from repositories import LocalSettingsRepository
        from core.settings_schema import migrate_settings_repository

        data_store = LocalDataStore(base_dir=str(tmp_path))
        data_store.save_json("settings", {"ai_cloud_only": True})
        repo = LocalSettingsRepository(data_store)
        migrate_settings_repository(repo)
        migrated = data_store.load_json("settings", {})
        assert migrated["ai_cloud_only"] is True  # 用户已开的云化不被迁移重置
