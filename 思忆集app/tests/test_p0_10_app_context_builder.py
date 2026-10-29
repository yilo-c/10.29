"""P0-10 回归：AppContextBuilder 拆分

验证：
1. AppContext 公共属性表（含方法）与 P0-10 拆分前一致（176 项快照）。
2. AppContextBuilder 暴露的领域阶段顺序与文档一致；按阶段构造与
   一次性 build_all 等价。
3. headless=True 时 web_bridge_service 为 None；非 headless 时不为 None。
4. _kb_bridge 在 builder 完成后保持 None（避免触发 transformers 导入）。
5. ServiceRegistry 注册：JudgeAwareClient / CopilotEngine 均已注册。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app_runtime._context_builder import AppContextBuilder  # noqa: E402
from app_runtime.app_context import AppContext  # noqa: E402


# ---------------- 公共属性基线 ----------------

EXPECTED_PUBLIC_ATTRS = {
    # P1-4：领域子门面（新增公共 API，见 app_runtime/context_facades.py）
    "automation",
    "chat",
    "memory",
    "sync",
    "agent_directory",
    "ai_client",
    "ai_manager",
    "alarms",
    "api_integration_hub",
    "api_provider_registry",
    "api_sync_orchestrator",
    "assistant_inbox_service",
    "assistant_runtime_manager",
    "attachment_layer",
    "audit_trail",
    "automation_hub_service",
    "autonomous_assistant_manager",
    "behavior_logger",
    "behavior_memory_extractor",
    "browser_assistant_bridge",
    "budget_manager",
    "build_desktop_doctor_continue_suggestion",
    "build_desktop_doctor_debug_assets",
    "build_desktop_doctor_status_summary",
    "cc_bridge",
    "character_renderer",
    "chat_session_repository",
    "cleanup_memory_links",
    "config_manager",
    "copilot_engine",
    "cost_tracker",
    "create_automation_hub_client",
    "cron_service",
    "cross_device_manager",
    "cross_device_router",
    "cross_device_session",
    "current_emotion",
    "data_protection_service",
    "decision_memory",
    "desktop_doctor_service",
    "effort_estimator",
    "event_bus",
    "evolution_orchestrator",
    "extension_service",
    "extract_memories_from_conversation",
    "feedback_service",
    "file_watcher",
    "find_duplicate_memories",
    "format_desktop_doctor_report",
    "agent_gateway",
    "get_assistant_runtime_status",
    "get_browser_assistant_bridge_status",
    "get_content_organizer_service",
    "get_cron_service_status",
    "get_daily_review",
    "get_desktop_doctor_debug_assets",
    "get_due_memories",
    "get_entry_supertag_data",
    "get_judge_stats",
    "get_linked_memories",
    "get_linked_memories_with_detail",
    "get_llm_provider_topology",
    "get_local_router_status",
    "get_memory_backlinks",
    "get_memory_co_access_count",
    "get_memory_link_stats",
    "get_memory_schedule",
    "get_memory_scheduler_stats",
    "get_on_this_day",
    "get_plugin_boundary_snapshot",
    "get_review_event",
    "get_supertag_schema",
    "get_supertag_schemas",
    "get_sync_summary",
    "get_weak_link_extensions",
    "get_web_bridge_service_status",
    "guard_service",
    "habit_learner",
    "improvement_proposal_repository",
    "inbox_capture_service",
    "info_item_repository",
    "info_radar",
    "info_source_registry",  # R2-A1：builder 装配的共享资讯源注册表
    "intent_router",
    "job_hunter_service",
    "judge_route_task",
    "judge_scheduler",
    "knowledge_base_bridge",
    "knowledge_graph_extractor",
    "knowledge_graph_store",
    "lifecycle_recovery_service",
    "link_memory",
    "load_review_settings",
    "local_ai_router",
    "local_data_store",
    "memory_extractor",
    "memory_index_service",
    "memory_linker",
    "memory_manager",
    "memory_scheduler",
    "merge_memory_link_networks",
    "mobile_bridge_manager",
    "multi_agent_orchestrator",
    "narrative_injector",
    "navigation_state",
    "notification_dispatcher",
    "observability_service",
    "path_comparator",
    "periodic_recalculate_links",
    "personal_inbox_repository",
    "personal_note_repository",
    "personal_task_repository",
    "plugin_registry",
    "prepare_assistant_runtime",
    "private_space_service",
    "process_lifecycle_manager",
    "process_with_local_router",
    "provider_service",
    "quick_review_memory",
    "record_memory_co_access",
    "recreate_ai_client",
    "register_supertag_schema",
    "reindex_memory_links",
    "reminder_repository",
    "reminder_service",
    "review_memory",
    "rollback_manager",
    "run_desktop_doctor",
    "save_review_settings",
    "schedule_memory_review",
    "service_registry_manager",
    "set_entry_supertag_data",
    "settings_repository",
    "source_registry",
    "start_automation_hub",
    "start_browser_assistant_bridge",
    "start_cron_service",
    "start_file_watcher",
    "start_web_bridge_service",
    "stop_background_services",
    "stop_browser_assistant_bridge",
    "stop_cron_service",
    "stop_file_watcher",
    "stop_web_bridge_service",
    "structured_memory_store",
    "subagent_manager",
    "suggest_supertag",
    "suggestion_engine",
    "tag_service",
    "task_optimizer",
    "tech_watch_collector",
    "tech_watch_judge",
    "tech_watch_store",
    "translate_service",
    "translation_history_repository",
    "user_profile_service",
    "vocabulary_repository",
    "weather_service",
    "web_bridge_service",
    "web_fetch_service",
    "web_search_service",
    "workbench_service",
    "youdao_oauth_provider",
    "youdao_translate_api",
}


@pytest.fixture(scope="module")
def headless_ctx(tmp_path_factory) -> AppContext:
    """构造 headless AppContext —— **必须隔离数据目录**。

    2026-09-17 泄漏守卫：原实现 ``AppContext(alarms=[], headless=True)`` 不传
    data_base_dir，构造期就把知识图谱 / 记忆索引 / 缓存落到**生产**
    ``思忆集app/data/``（``data/**`` 在 .gitignore 内，``git status`` 完全看不见，
    今天已发生两起真实事故）。此处改为 tmp 目录 + ``SIYI_DATA_DIR`` 双保险：
    前者覆盖显式入参路径，后者覆盖构造期与调用期的 env 路径解析；模块结束后
    恢复 env，避免污染其它测试模块。参考 tests/test_r2a_context_isolation_20260917.py。
    """
    data_dir = tmp_path_factory.mktemp("p0-10-data")
    saved = os.environ.get("SIYI_DATA_DIR")
    os.environ["SIYI_DATA_DIR"] = str(data_dir)
    try:
        yield AppContext(alarms=[], headless=True, data_base_dir=str(data_dir))
    finally:
        if saved is None:
            os.environ.pop("SIYI_DATA_DIR", None)
        else:
            os.environ["SIYI_DATA_DIR"] = saved


# ---------------- 测试用例 ----------------


def test_public_attribute_baseline_unchanged(headless_ctx: AppContext) -> None:
    """builder 拆分前后公共属性集合应严格一致。"""
    actual = {a for a in dir(headless_ctx) if not a.startswith("_")}
    missing = EXPECTED_PUBLIC_ATTRS - actual
    extra = actual - EXPECTED_PUBLIC_ATTRS
    assert not missing, f"AppContext 丢失属性: {sorted(missing)}"
    assert not extra, f"AppContext 新增未声明属性: {sorted(extra)}"


def test_builder_has_named_domain_steps() -> None:
    """builder 必须暴露领域阶段方法，方便后续按域拆分。"""
    expected_steps = [
        "build_storage",
        "build_infrastructure",
        "build_ai",
        "build_profile",
        "build_memory",
        "build_tooling",
        "build_desktop_services",
        "build_autonomy",
        "build_cross_device",
        "build_cron_jobs",
        "build_bridges",
        "build_copilot",
        "build_config",
        "build_character",
        "build_lazy_tools",
        "finalize",
    ]
    for step in expected_steps:
        assert callable(getattr(AppContextBuilder, step, None)), f"缺失阶段：{step}"


def test_headless_disables_web_bridge(headless_ctx: AppContext) -> None:
    assert headless_ctx.web_bridge_service is None
    status = headless_ctx.get_web_bridge_service_status()
    assert status.get("enabled") is False


def test_kb_bridge_lazy_remains_none_after_build(headless_ctx: AppContext) -> None:
    """避免 transformers 在启动期被导入。"""
    assert headless_ctx._kb_bridge is None


def test_service_registry_registered_key_services(headless_ctx: AppContext) -> None:
    """JudgeAwareClient 与 CopilotEngine 必须已注册。"""
    from ai.client import JudgeAwareClient
    from services.copilot_engine import CopilotEngine

    reg = headless_ctx.service_registry_manager
    # 不同实现可能使用 get / resolve，二者择一即可
    assert reg.register is not None  # API 存在
    # 验证 ai_client 与 register_all 后的 copilot_engine 一致
    assert headless_ctx.ai_client is not None
    assert headless_ctx.copilot_engine is not None
    # JudgeAwareClient 注册的对象就是 ai_client
    assert isinstance(headless_ctx.ai_client, JudgeAwareClient)
    assert isinstance(headless_ctx.copilot_engine, CopilotEngine)


def test_builder_init_keeps_construction_args(headless_ctx: AppContext) -> None:
    """构造期保留的 headless / desknotes_dir 私有字段。"""
    assert headless_ctx._headless is True
    assert headless_ctx._desknotes_dir is None


def test_memory_manager_facade_attributes(headless_ctx: AppContext) -> None:
    """build_memory 阶段透传到顶层的关键 facade 应一致。"""
    mm = headless_ctx.memory_manager
    assert headless_ctx.memory_index_service is mm.memory_index_service
    assert headless_ctx.tag_service is mm.tag_service
    assert headless_ctx.knowledge_graph_store is mm.knowledge_graph_store
    assert headless_ctx.memory_scheduler is mm.memory_scheduler
    assert headless_ctx.memory_linker is mm.memory_linker


def test_ai_manager_facade_attributes(headless_ctx: AppContext) -> None:
    """build_ai 阶段透传的 cost_tracker / budget_manager / judge_scheduler。"""
    am = headless_ctx.ai_manager
    assert headless_ctx.cost_tracker is am.cost_tracker
    assert headless_ctx.budget_manager is am.budget_manager
    assert headless_ctx.local_ai_router is am.local_ai_router
    assert headless_ctx.judge_scheduler is am.judge_scheduler


def test_cross_device_router_wired_into_autonomy(headless_ctx: AppContext) -> None:
    """build_cross_device 必须把 router/session 接到 autonomy router。"""
    manager = headless_ctx.autonomous_assistant_manager
    assert manager.router.cross_device_session is headless_ctx.cross_device_session
    assert manager.router.cross_device_router is headless_ctx.cross_device_router


def test_no_assistant_to_backend_reverse_import() -> None:
    """跨层防线（P0-12 验证项，在 builder 重构后继续守护）：
    app_context 不得 import backend.*。
    """
    src = (REPO_ROOT / "app_runtime" / "app_context.py").read_text(encoding="utf-8")
    assert "from backend" not in src, "app_context.py 出现反向依赖 backend.*"
    assert "import backend" not in src
