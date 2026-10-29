"""L7 一致性：profile ↔ settings 镜像不变测试（真实 LocalDataStore）。

聚焦双写源的一致性语义：
- one-way 迁移：settings 已有偏好（focus_topics / high_risk_require_confirm）→ 首次 load 迁移进 profile
- 镜像不变：profile 一旦有值即成为源，settings 后续变更不反向覆盖 profile
- 兜底 fallback：profile 为空时 get_focus_topics 从 settings 取
- save_settings_preferences 写 profile 不破坏 settings 原值
- reset_profile 后 fallback 重新生效
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from assistant.user_profile import UserProfileService
from core.settings_keys import (
    KEY_ASSISTANT_FOCUS_TOPICS,
    KEY_ASSISTANT_HIGH_RISK_REQUIRE_CONFIRM,
)
from data.local_storage import LocalDataStore
from repositories._settings import LocalSettingsRepository


@pytest.fixture
def storage(tmp_path):
    ds = LocalDataStore(base_dir=str(tmp_path / "data"))
    yield ds
    ds.close()


@pytest.fixture
def settings_repo(storage) -> LocalSettingsRepository:
    return LocalSettingsRepository(storage)


class TestOneWayMigration:
    def test_initial_profile_migrates_focus_topics(self, storage, settings_repo) -> None:
        settings_repo.set_setting(KEY_ASSISTANT_FOCUS_TOPICS, ["学习", "工作"])
        svc = UserProfileService(data_store=storage, settings_repository=settings_repo)
        profile = svc.load()
        assert profile["long_term"]["focus_topics"] == ["学习", "工作"]

    def test_initial_profile_migrates_high_risk_confirm(self, storage, settings_repo) -> None:
        settings_repo.set_setting(KEY_ASSISTANT_HIGH_RISK_REQUIRE_CONFIRM, False)
        svc = UserProfileService(data_store=storage, settings_repository=settings_repo)
        profile = svc.load()
        assert profile["permission"]["require_confirm_high_risk"] is False

    def test_migration_is_persisted(self, storage, settings_repo) -> None:
        settings_repo.set_setting(KEY_ASSISTANT_FOCUS_TOPICS, ["学习"])
        svc = UserProfileService(data_store=storage, settings_repository=settings_repo)
        svc.load()  # 触发初始化持久化
        # 新实例（模拟重启）仍能读到已迁移的 profile
        svc2 = UserProfileService(data_store=storage, settings_repository=settings_repo)
        assert svc2.load()["long_term"]["focus_topics"] == ["学习"]

    def test_empty_settings_no_migration(self, storage, settings_repo) -> None:
        svc = UserProfileService(data_store=storage, settings_repository=settings_repo)
        profile = svc.load()
        assert profile["long_term"]["focus_topics"] == []


class TestMirrorInvariant:
    def test_settings_change_after_profile_creation_does_not_overwrite(self, storage, settings_repo) -> None:
        """镜像不变：profile 已存在（有值）→ settings 变更不再影响 profile。"""
        settings_repo.set_setting(KEY_ASSISTANT_FOCUS_TOPICS, ["学习"])
        svc = UserProfileService(data_store=storage, settings_repository=settings_repo)
        svc.load()  # 创建 profile（迁移 ["学习"]）

        # 之后 settings 被改为别的值
        settings_repo.set_setting(KEY_ASSISTANT_FOCUS_TOPICS, ["电影"])
        profile = svc.load()
        assert profile["long_term"]["focus_topics"] == ["学习"]  # profile 保持迁移时的值

    def test_get_focus_topics_uses_profile_when_present(self, storage, settings_repo) -> None:
        settings_repo.set_setting(KEY_ASSISTANT_FOCUS_TOPICS, ["学习"])
        svc = UserProfileService(data_store=storage, settings_repository=settings_repo)
        svc.merge_focus_topics(["新主题"])
        assert svc.get_focus_topics() == ["新主题"]  # profile 优先

    def test_fallback_to_settings_when_profile_empty(self, storage, settings_repo) -> None:
        """profile 无主题时 get_focus_topics 从 settings 兜底。"""
        settings_repo.set_setting(KEY_ASSISTANT_FOCUS_TOPICS, ["兜底主题"])
        svc = UserProfileService(data_store=storage, settings_repository=settings_repo)
        # 显式清空 profile 主题（模拟手动编辑为空）
        profile = svc.load()
        profile["long_term"]["focus_topics"] = []
        svc.save(profile)
        assert svc.get_focus_topics() == ["兜底主题"]


class TestSaveSettingsPreferences:
    def test_write_profile_keeps_settings_untouched(self, storage, settings_repo) -> None:
        settings_repo.set_setting(KEY_ASSISTANT_FOCUS_TOPICS, ["旧值"])
        svc = UserProfileService(data_store=storage, settings_repository=settings_repo)
        svc.save_settings_preferences(focus_topics=["新值"])
        # settings 原值保留（镜像不破坏）
        assert settings_repo.get_setting(KEY_ASSISTANT_FOCUS_TOPICS, []) == ["旧值"]
        # profile 是新值
        assert svc.get_focus_topics() == ["新值"]

    def test_save_preferences_increases_versions(self, storage, settings_repo) -> None:
        svc = UserProfileService(data_store=storage, settings_repository=settings_repo)
        p1 = svc.save_settings_preferences(focus_topics=["a"])
        p2 = svc.save_settings_preferences(focus_topics=["a", "b"])
        assert p1.get("version", 0) <= p2.get("version", 0)
        assert p2["long_term"]["focus_topics"] == ["a", "b"]


class TestResetRecoversFallback:
    def test_reset_profile_fallback_to_settings(self, storage, settings_repo) -> None:
        settings_repo.set_setting(KEY_ASSISTANT_FOCUS_TOPICS, ["兜底"])
        svc = UserProfileService(data_store=storage, settings_repository=settings_repo)
        svc.merge_focus_topics(["临时"])
        assert svc.get_focus_topics() == ["临时"]
        svc.reset_profile()
        # 重置后 profile 为空 → fallback 到 settings
        assert svc.get_focus_topics() == ["兜底"]

    def test_reset_restores_defaults(self, storage, settings_repo) -> None:
        svc = UserProfileService(data_store=storage, settings_repository=settings_repo)
        svc.set_current_focus("写周报", project="工作")
        svc.reset_profile()
        profile = svc.load()
        assert profile["context"]["current_focus"] == ""
        assert profile["output"]["summary_style"] == "concise"
