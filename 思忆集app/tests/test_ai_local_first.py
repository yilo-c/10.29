"""local_first（本地优先）测试：本地腿先行、云端兜底。

用户决策（2026-09-02）：云端优先、本地第三顺位兜底是有意决策（本地 7B 能力不足、
隐私未上云），不是缺陷。本开关仅是把"将来部署更强本地模型后切回本地优先"显式化为
配置项——默认 False = 维持现状（云端优先），行为与历史版本逐字节一致。

开关：设置键 ai_local_first（KEY_AI_LOCAL_FIRST）+ 环境变量 SIYI_AI_LOCAL_FIRST（优先级更高）。
与 cloud_only 关系：local_first 优先级更高（两者同开时 local_first 胜出）。

覆盖：
1. is_ai_local_first 判定（设置键 / 环境变量 / 字符串容错 / 默认 False）
2. FallbackOpenAIClient：默认时链序不变（回归锁定）；local_first 时本地腿先行、云端兜底
3. 流式路径（chat_completion_stream）：同上
4. route_chat（_build_provider_chain）：同上
5. 设置迁移：KEY_AI_LOCAL_FIRST 登记进 BOOL_KEYS，启动迁移不 KeyError
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


class _Settings:
    """最小 settings 桩：仅暴露 get_setting，两个 AI 开关均可配。"""

    def __init__(self, local_first: bool | str = False, cloud_only: bool = False):
        self._local_first = local_first
        self._cloud_only = cloud_only

    def get_setting(self, key: str, default=None):
        if key == "ai_local_first":
            return self._local_first
        if key == "ai_cloud_only":
            return self._cloud_only
        return default


class _MockClient:
    """Minimal OpenAICompatibleClient mock（同 test_ai_cloud_only）。"""

    def __init__(self, model="mock-model"):
        self.model = model
        self.last_messages = None
        self.calls = 0

    def is_configured(self):
        return True

    def chat_completion(self, messages, temperature=0.2, max_tokens=900, tools=None, tool_choice=None, **kwargs):
        self.last_messages = messages
        self.calls += 1
        return {"choices": [{"message": {"content": f"from {self.model}"}}]}

    def chat_completion_stream(self, messages, temperature=0.2, max_tokens=900, tools=None, tool_choice=None, **kwargs):
        self.last_messages = messages
        self.calls += 1
        yield "mock "
        yield "response"


# ── 1. is_ai_local_first 判定 ──


class TestIsAiLocalFirst:
    def test_default_false(self):
        from ai.client.config import is_ai_local_first

        assert is_ai_local_first(None) is False
        assert is_ai_local_first(_Settings(False)) is False

    def test_settings_true(self):
        from ai.client.config import is_ai_local_first

        assert is_ai_local_first(_Settings(True)) is True

    def test_settings_string_truthy(self):
        from ai.client.config import is_ai_local_first

        assert is_ai_local_first(_Settings("1")) is True

    def test_settings_string_falsy(self):
        from ai.client.config import is_ai_local_first

        assert is_ai_local_first(_Settings("false")) is False

    def test_env_takes_precedence(self, monkeypatch):
        from ai.client.config import is_ai_local_first

        monkeypatch.setenv("SIYI_AI_LOCAL_FIRST", "1")
        assert is_ai_local_first(_Settings(False)) is True
        monkeypatch.delenv("SIYI_AI_LOCAL_FIRST")
        assert is_ai_local_first(_Settings(False)) is False

    def test_env_only(self, monkeypatch):
        from ai.client.config import is_ai_local_first

        monkeypatch.setenv("SIYI_AI_LOCAL_FIRST", "1")
        assert is_ai_local_first(None) is True
        monkeypatch.delenv("SIYI_AI_LOCAL_FIRST")
        assert is_ai_local_first(None) is False


# ── 2. FallbackOpenAIClient 同步路径 ──


class TestFallbackLocalFirst:
    def _fb(self, local_first: bool):
        from ai.client.fallback import FallbackOpenAIClient

        return FallbackOpenAIClient(
            primary=_MockClient(model="primary"),
            fallback=_MockClient(model="fallback"),
            local=_MockClient(model="local"),
            local_coder=_MockClient(model="local_coder"),
            local_first=local_first,
        )

    def test_default_cloud_first(self):
        """默认（关闭）：云端 primary 先行（回归锁定——历史行为逐字节一致）。"""
        fb = self._fb(False)
        resp = fb.chat_completion([{"role": "user", "content": "hi"}])
        assert resp["choices"][0]["message"]["content"] == "from primary"
        assert fb.primary.calls == 1
        assert fb.local.calls == 0, "默认时本地腿不应被触达"

    def test_local_first_local_leg_first(self):
        """local_first：本地腿先行，成功则云端不被调用。"""
        fb = self._fb(True)
        resp = fb.chat_completion([{"role": "user", "content": "hi"}])
        assert resp["choices"][0]["message"]["content"] == "from local"
        assert fb.local.calls == 1
        assert fb.primary.calls == 0, "本地成功时云端不应被调用"

    def test_local_first_falls_back_to_cloud(self):
        """local_first：本地腿（local→local_coder）用尽后才回退云端 primary。"""
        fb = self._fb(True)

        def _boom(*args, **kwargs):
            raise RuntimeError("local down")

        fb.local.chat_completion = _boom
        resp = fb.chat_completion([{"role": "user", "content": "hi"}])
        # local 挂 → local_coder 也是本地腿，先顶上（本地优先=本地腿用尽再上云）
        assert resp["choices"][0]["message"]["content"] == "from local_coder"
        assert fb.local_coder.calls == 1
        assert fb.primary.calls == 0, "本地腿仍有可用者时云端不应被调用"

    def test_local_first_all_local_down_falls_to_cloud(self):
        """local_first：整个本地腿都挂 → 回退云端 primary（兜底语义）。"""
        fb = self._fb(True)

        def _boom(*args, **kwargs):
            raise RuntimeError("local down")

        fb.local.chat_completion = _boom
        fb.local_coder.chat_completion = _boom
        resp = fb.chat_completion([{"role": "user", "content": "hi"}])
        assert resp["choices"][0]["message"]["content"] == "from primary"
        assert fb.primary.calls == 1


# ── 3. 流式路径 ──


class TestLocalFirstStream:
    def _fb(self, local_first: bool):
        from ai.client.fallback import FallbackOpenAIClient

        return FallbackOpenAIClient(
            primary=_MockClient(model="primary"),
            local=_MockClient(model="local"),
            local_first=local_first,
        )

    def test_default_stream_cloud_first(self):
        fb = self._fb(False)
        chunks = list(fb.chat_completion_stream([{"role": "user", "content": "hi"}]))
        assert "".join(chunks) == "mock response"
        assert fb.primary.calls == 1
        assert fb.local.calls == 0

    def test_local_first_stream_local_first(self):
        fb = self._fb(True)
        chunks = list(fb.chat_completion_stream([{"role": "user", "content": "hi"}]))
        assert "".join(chunks) == "mock response"
        assert fb.local.calls == 1, "local_first 时流式应走本地腿"
        assert fb.primary.calls == 0

    def test_local_first_privacy_content_still_local(self):
        """local_first + 隐私内容：本地腿先行（隐私守卫本就跳云端，行为相容）。"""
        from ai.client.fallback import FallbackOpenAIClient

        fb = FallbackOpenAIClient(
            primary=_MockClient(model="primary"),
            local=_MockClient(model="local"),
            local_first=True,
            privacy_filter=lambda _t: "local_only",
        )
        fb.chat_completion([{"role": "user", "content": "银行卡 6222"}])
        assert fb.local.calls == 1
        assert fb.primary.calls == 0


# ── 4. route_chat（_build_provider_chain） ──


class TestLocalFirstRouteChat:
    def test_default_chain_order(self):
        from ai.client.fallback import FallbackOpenAIClient

        fb = FallbackOpenAIClient(primary=_MockClient(), local=_MockClient(), local_first=False)
        chain = [name for name, _ in fb._build_provider_chain("primary")]
        assert chain == ["primary", "fallback", "local", "local_coder"]

    def test_local_first_chain_order(self):
        """local_first：显式 preferred 仍最前，其余按本地腿先行的链序。"""
        from ai.client.fallback import FallbackOpenAIClient

        fb = FallbackOpenAIClient(primary=_MockClient(), local=_MockClient(), local_first=True)
        chain = [name for name, _ in fb._build_provider_chain("local")]
        assert chain == ["local", "local_coder", "primary", "fallback"]

    def test_local_first_preferred_still_first(self):
        """显式 preferred 的 provider 仍排最前（不受 local_first 影响）。"""
        from ai.client.fallback import FallbackOpenAIClient

        fb = FallbackOpenAIClient(primary=_MockClient(), local=_MockClient(), local_first=True)
        chain = [name for name, _ in fb._build_provider_chain("primary")]
        assert chain[0] == "primary"


# ── 5. local_first 与 cloud_only 互斥：local_first 胜出 ──


class TestLocalFirstOverCloudOnly:
    def test_create_fallback_client_local_first_wins(self, monkeypatch):
        """两者同开时 local_first 胜出：构造出的客户端应带 local_first=True / cloud_only=False。"""
        monkeypatch.setenv("SIYI_AI_LOCAL_FIRST", "1")
        monkeypatch.setenv("SIYI_AI_CLOUD_ONLY", "1")
        try:
            from ai.client.config import create_fallback_client

            client = create_fallback_client(settings_repository=None)
            assert getattr(client, "_local_first", False) is True
            assert getattr(client, "_cloud_only", False) is False
        finally:
            monkeypatch.delenv("SIYI_AI_LOCAL_FIRST")
            monkeypatch.delenv("SIYI_AI_CLOUD_ONLY")

    def test_default_envs_off_keeps_legacy(self, monkeypatch):
        """两个环境变量都不设：local_first=False / cloud_only=False（回归锁定）。"""
        monkeypatch.delenv("SIYI_AI_LOCAL_FIRST", raising=False)
        monkeypatch.delenv("SIYI_AI_CLOUD_ONLY", raising=False)
        from ai.client.config import create_fallback_client

        client = create_fallback_client(settings_repository=None)
        assert getattr(client, "_local_first", False) is False
        assert getattr(client, "_cloud_only", False) is False


# ── 6. 设置迁移：新增 BOOL 键必须有默认值，启动迁移不 KeyError ──


class TestSettingsMigrationLocalFirstKey:
    """回归锁定：KEY_AI_LOCAL_FIRST 登记进 BOOL_KEYS 后，migrate_settings_repository
    迭代 BOOL_KEYS 时必须能在 SETTINGS_DEFAULTS 里取到默认值（同 ai_cloud_only 的教训，
    见 test_ai_cloud_only.py）。"""

    def test_migration_empty_store_does_not_keyerror(self, tmp_path):
        from data.local_storage import LocalDataStore
        from repositories import LocalSettingsRepository
        from core.settings_schema import SETTINGS_DEFAULTS, migrate_settings_repository

        data_store = LocalDataStore(base_dir=str(tmp_path))
        repo = LocalSettingsRepository(data_store)
        migrate_settings_repository(repo)  # 不得 KeyError
        migrated = data_store.load_json("settings", {})
        assert "ai_local_first" in migrated
        assert migrated["ai_local_first"] is False
        assert migrated["ai_local_first"] == SETTINGS_DEFAULTS["ai_local_first"]

    def test_migration_preserves_existing_true(self, tmp_path):
        from data.local_storage import LocalDataStore
        from repositories import LocalSettingsRepository
        from core.settings_schema import migrate_settings_repository

        data_store = LocalDataStore(base_dir=str(tmp_path))
        data_store.save_json("settings", {"ai_local_first": True})
        repo = LocalSettingsRepository(data_store)
        migrate_settings_repository(repo)
        migrated = data_store.load_json("settings", {})
        assert migrated["ai_local_first"] is True  # 用户已开的本地优先不被迁移重置
