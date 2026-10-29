"""P1-21: AppContext 契约测试 — 拆分保险绳。

不测内部实现细节，只测三条关键路径：
1. 启动契约：headless 模式下能正常初始化，关键服务属性存在
2. headless 行为契约：Qt 相关服务为 None / 惰性加载
3. AI 客户端重建契约：recreate_ai_client 后引用正确更新

这些测试是 P0-10 AppContext Builder 拆分的前置条件 ——
拆分后这些契约必须仍然成立。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app_runtime.app_context import AppContext  # noqa: E402


@pytest.fixture
def ctx(tmp_path):
    """创建 headless 模式的 AppContext，使用临时数据目录。"""
    return AppContext(data_base_dir=str(tmp_path), headless=True)


# ──────────────────────────────────────────────────────
# 1. 启动契约：关键服务属性必须存在
# ──────────────────────────────────────────────────────


class TestStartupContract:
    """AppContext 初始化后必须暴露的关键服务属性。"""

    # 存储层
    def test_storage_services_exist(self, ctx):
        assert ctx.local_data_store is not None
        assert ctx.personal_note_repository is not None
        assert ctx.personal_task_repository is not None
        assert ctx.personal_inbox_repository is not None
        assert ctx.settings_repository is not None
        assert ctx.translation_history_repository is not None
        assert ctx.vocabulary_repository is not None
        assert ctx.reminder_repository is not None
        assert ctx.chat_session_repository is not None

    # AI 层
    def test_ai_services_exist(self, ctx):
        assert ctx.ai_client is not None
        assert ctx.ai_manager is not None
        assert ctx.judge_scheduler is not None
        assert ctx.cost_tracker is not None
        assert ctx.budget_manager is not None

    # 记忆层
    def test_memory_services_exist(self, ctx):
        assert ctx.memory_manager is not None
        assert ctx.memory_index_service is not None
        assert ctx.memory_extractor is not None
        assert ctx.memory_scheduler is not None
        assert ctx.memory_linker is not None

    # 助手层
    def test_assistant_services_exist(self, ctx):
        assert ctx.autonomous_assistant_manager is not None
        assert ctx.intent_router is not None
        assert ctx.assistant_inbox_service is not None
        assert ctx.suggestion_engine is not None
        assert ctx.subagent_manager is not None
        assert ctx.multi_agent_orchestrator is not None

    # 工具/集成层
    def test_tooling_services_exist(self, ctx):
        assert ctx.web_search_service is not None
        assert ctx.api_integration_hub is not None

    # 桥接层
    def test_bridge_services_exist(self, ctx):
        assert ctx.browser_assistant_bridge is not None
        assert ctx.copilot_engine is not None

    # 跨设备层
    def test_cross_device_services_exist(self, ctx):
        assert ctx.cross_device_manager is not None
        assert ctx.cross_device_session is not None

    # Cron + 插件
    def test_cron_and_plugin_exist(self, ctx):
        assert ctx.cron_service is not None
        assert ctx.plugin_registry is not None

    # 服务注册表
    def test_service_registry_populated(self, ctx):
        assert ctx.service_registry_manager is not None


# ──────────────────────────────────────────────────────
# 2. headless 行为契约
# ──────────────────────────────────────────────────────


class TestHeadlessContract:
    """headless=True 时 Qt 相关服务必须跳过。"""

    def test_web_bridge_service_is_none_in_headless(self, ctx):
        assert ctx.web_bridge_service is None

    def test_attachment_layer_is_none_in_headless(self, ctx):
        # attachment_layer 属性在 headless 时返回 None
        assert ctx.attachment_layer is None

    def test_headless_flag_set(self, ctx):
        assert ctx._headless is True


# ──────────────────────────────────────────────────────
# 3. AI 客户端重建契约
# ──────────────────────────────────────────────────────


class TestRecreateAiClient:
    """recreate_ai_client 必须更新所有持有 ai_client 引用的服务。"""

    def test_recreate_updates_ai_client(self, ctx):
        ctx.recreate_ai_client()
        assert ctx.ai_client is not None
        # 新客户端应该是 JudgeAwareClient 实例
        from ai.client import JudgeAwareClient

        assert isinstance(ctx.ai_client, JudgeAwareClient)

    def test_recreate_updates_memory_manager(self, ctx):
        ctx.recreate_ai_client()
        # MemoryManager.update_ai_client 更新内部 _memory.ai_client
        assert ctx.memory_manager._memory.ai_client is ctx.ai_client

    def test_recreate_updates_intent_router(self, ctx):
        ctx.recreate_ai_client()
        # IntentRouter 用 _ai_client 私有属性
        assert ctx.intent_router._ai_client is ctx.ai_client

    def test_recreate_updates_autonomous_assistant_manager(self, ctx):
        ctx.recreate_ai_client()
        assert ctx.autonomous_assistant_manager.ai_client is ctx.ai_client

    def test_recreate_updates_copilot_engine(self, ctx):
        ctx.recreate_ai_client()
        # CopilotEngine 用 _ai_client 私有属性
        assert ctx.copilot_engine._ai_client is ctx.ai_client


# ──────────────────────────────────────────────────────
# 4. 方法可调用契约
# ──────────────────────────────────────────────────────


class TestMethodCallable:
    """关键方法必须存在且可调用。"""

    def test_recreate_ai_client_callable(self, ctx):
        assert callable(ctx.recreate_ai_client)

    def test_prepare_assistant_runtime_callable(self, ctx):
        assert callable(ctx.prepare_assistant_runtime)

    def test_get_assistant_runtime_status_callable(self, ctx):
        assert callable(ctx.get_assistant_runtime_status)

    def test_start_browser_assistant_bridge_callable(self, ctx):
        assert callable(ctx.start_browser_assistant_bridge)

    def test_stop_browser_assistant_bridge_callable(self, ctx):
        assert callable(ctx.stop_browser_assistant_bridge)

    def test_create_automation_hub_client_callable(self, ctx):
        assert callable(ctx.create_automation_hub_client)


# ──────────────────────────────────────────────────────
# 5. 非 headless 模式契约（验证 headless=False 也能初始化）
# ──────────────────────────────────────────────────────


class TestNonHeadlessStartup:
    """非 headless 模式下 AppContext 也能正常初始化（Qt 服务惰性加载）。"""

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "非 headless 路径依赖的 PyQt6 ui/ 包已从仓库移除（桌面 UI 现仅 Electron，"
            "见 AGENTS.md）；app_entry/main.py 仍 import ui.* → AppContext(headless=False) "
            "抛 ModuleNotFoundError。遗留栈去留待定，决策前不作删除（2026-08-29 审查 M18）。"
        ),
    )
    def test_non_headless_init(self, tmp_path):
        ctx = AppContext(data_base_dir=str(tmp_path), headless=False)
        assert ctx._headless is False
        assert ctx.ai_client is not None
        assert ctx.personal_note_repository is not None
        # web_bridge_service 在非 headless 模式下应该被创建
        assert ctx.web_bridge_service is not None
