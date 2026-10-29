"""Capability router '?core routing and orchestration for assistant capabilities.
Capability implementations are in the capability/ subpackage.
"""

from __future__ import annotations
import asyncio
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from core.action_guard import ActionGuard
from core.events import AppEvent, event_bus
from core.shell_execution import ShellExecutionModel
from core.observability import SEVERITY_DEGRADED
from ._router_project import ProjectHandlerMixin
from ._router_notes import NotesRouterMixin
from ._router_content import ContentRouterMixin
from ._router_schedule import ScheduleRouterMixin
from ._router_agent import AgentRouterMixin
from services.action_broker import ActionBroker, ActionRequest
from .capability import (
    BrowserCapabilityDomain,
    BrowserAutomationDomain,
    DesktopAutomationCapabilityDomain,
    FileCapabilityDomain,
    KnowledgeCapabilityDomain,
    ShellCapabilityDomain,
    TranslationCapabilityDomain,
    TraeCapabilityDomain,
)
from services.capability_types import CapabilityRegistry, CapabilitySpec
from assistant.execution.environment import AssistantEnvironmentProbe
from .models import PlannedStep
from ._router_mcp import MCPRouterMixin

try:
    from .capability._dynamic_tools import DynamicToolDomain
    from services.tooling.tool_spec import ToolSpecRegistry
except ImportError:
    DynamicToolDomain = None  # type: ignore[assignment,misc]
    ToolSpecRegistry = None  # type: ignore[assignment,misc]
    logger = logging.getLogger("siyi.assistant.autonomy.router")
    logger.warning("Dynamic tool capability modules are unavailable")
logger = logging.getLogger("siyi.assistant.autonomy.router")


class AssistantCapabilityRouter(ProjectHandlerMixin, NotesRouterMixin, ContentRouterMixin, ScheduleRouterMixin, AgentRouterMixin, MCPRouterMixin):
    """执行?层插件路由：鎸?action_id 调用具体能力（集鎴愬?计快照）。?
    当配缃?簡 action_broker 时，鎵?鏈?execute() 调用会先经过 ActionBroker
    鐨?capability 验证銆侀?闄╂?查和 execution_receipt 生成链路。?"""

    _DEFAULT_DOWNLOAD_DIRNAME = "assistant_downloads"

    # R2-A1：由 AppContextBuilder.build_autonomy 注入的共享资讯源注册表
    # （未注入时为 None，采集路径退回按 settings 自建）。
    info_source_registry: Any = None

    def __init__(
        self,
        *,
        data_store,
        settings_repository,
        observability_service,
        note_repository,
        task_repository,
        news_repository=None,
        inbox_repository,
        integration_hub=None,
        sync_orchestrator=None,
        write_guard=None,
        mcp_manager=None,
        subagent_manager=None,
        memory_index_service=None,
        user_profile_service=None,
        inbox_service=None,
        capability_registry=None,
        source_registry=None,
        evolution_orchestrator=None,
        tech_watch_collector=None,
        tech_watch_judge=None,
        tech_watch_store=None,
        ai_client=None,
        cc_bridge=None,
        action_broker=None,  #
        agent_directory=None,  # Agent 编排目录 鈫?鏂板?
        process_lifecycle_manager=None,
    ):
        self.data_store = data_store
        self.settings_repository = settings_repository
        self.observability_service = observability_service
        self.note_repository = note_repository
        self.task_repository = task_repository
        self.news_repository = news_repository
        self.inbox_repository = inbox_repository
        self.integration_hub = integration_hub
        self.sync_orchestrator = sync_orchestrator
        self.write_guard = write_guard
        self.mcp_manager = mcp_manager
        self.subagent_manager = subagent_manager
        self.memory_index_service = memory_index_service
        self.user_profile_service = user_profile_service
        self.inbox_service = inbox_service
        self.capability_registry = capability_registry
        self.source_registry = source_registry
        self.evolution_orchestrator = evolution_orchestrator
        self.tech_watch_collector = tech_watch_collector
        self.tech_watch_judge = tech_watch_judge
        self.tech_watch_store = tech_watch_store
        self.ai_client = ai_client
        self.cc_bridge = cc_bridge
        self.action_broker = action_broker  #
        self.agent_directory = agent_directory  # Agent 编排目录鈫?鏂板?：可閫?Broker 网关
        self.process_lifecycle_manager = process_lifecycle_manager
        self.environment_probe = AssistantEnvironmentProbe()
        self.action_guard = ActionGuard(settings_repository=settings_repository, observability_service=observability_service)
        self.shell_execution = ShellExecutionModel(workspace_root=Path(__file__).resolve().parent.parent)
        # Capability domains
        self.knowledge_domain = KnowledgeCapabilityDomain(self)
        self.file_domain = FileCapabilityDomain(self)
        self.shell_domain = ShellCapabilityDomain(self)
        self.translation_domain = TranslationCapabilityDomain(self)
        self.browser_domain = BrowserCapabilityDomain(self)
        self.browser_automation_domain = BrowserAutomationDomain(self)
        self.desktop_domain = DesktopAutomationCapabilityDomain(self)
        self.trae_domain = TraeCapabilityDomain(self)
        self._event_bus = event_bus
        self._launched_processes: dict[str, subprocess.Popen] = {}
        self._handlers = {
            "news.lookup": self.knowledge_domain.news_lookup,
            "news.briefing": self.knowledge_domain.news_briefing,
            "journal.daily_draft": self._journal_daily_draft,
            "notes.sync": self._notes_sync,
            "notes.organize": self._notes_organize,
            "usage.snapshot": self._usage_snapshot,
            "memory.recall": self.knowledge_domain.memory_recall,
            "upgrade.check": self._upgrade_check,
            "browser.open": self.browser_domain.browser_open,
            "file.read": self.file_domain.file_read,
            "file.write": self.file_domain.file_write,
            "clipboard.read": self.shell_domain.clipboard_read,
            "clipboard.write": self.shell_domain.clipboard_write,
            "app.launch": self.shell_domain.app_launch,
            # C-1 执行器收敛步骤 3：确认闭环状态查询此前只在旧 ReAct 执行器
            # （B 套）里有分支、A 套从未注册 → 收缩后模型/沙箱经本路由调用即断线。
            "confirmation.status": self._confirmation_status,
            "window.find": self.desktop_domain.window_find,
            "window.activate": self.desktop_domain.window_activate,
            "screen.read": self.desktop_domain.screen_read,
            "mouse.click": self.desktop_domain.mouse_click,
            "mouse.click_text": self.desktop_domain.mouse_click_text,
            "keyboard.type": self.desktop_domain.keyboard_type,
            "keyboard.press": self.desktop_domain.keyboard_press,
            "file.find": self.file_domain.file_find,
            "file.grep": self.file_domain.file_grep,
            "file.edit": self.file_domain.file_edit,
            "file.copy": self.file_domain.file_copy,
            "file.delete": self.file_domain.file_delete,
            "desktop.screenshot": self.desktop_domain.desktop_screenshot,
            "shell.run": self.shell_domain.shell_run,
            "web.download": self.file_domain.web_download,
            "web.search": self.knowledge_domain.web_search,
            "web.fetch": self.knowledge_domain.web_fetch,
            "process.list": self._process_list,
            "process.kill": self._process_kill,
            "system.info": self._system_info,
            "cron.list": self._cron_list,
            "cron.add": self._cron_add,
            "cron.remove": self._cron_remove,
            "mcp.tools.list": self._mcp_tools_list,
            "mcp.tool.call": self._mcp_tool_call,
            "subagent.spawn": self.knowledge_domain.subagent_spawn,
            "subagent.list": self.knowledge_domain.subagent_list,
            "subagent.get": self.knowledge_domain.subagent_get,
            "code.execute": self._code_execute,
            "system.capabilities": self._system_capabilities,
            "system.environment": self._system_environment,
            "system.scan": self._system_scan,
            "evolution.daily_scan": self._evolution_daily_scan,
            "tech_watch.collect": self._tech_watch_collect,
            "tech_watch.judge": self._tech_watch_judge,
            "vision.analyze": self._vision_analyze,
            "project.health_check": self._project_health_check,
            "project.auto_repair": self._project_auto_repair,
            "translate.text": self.translation_domain.translate_text,
            "browser.context.get": self.browser_domain.browser_context_get,
            "browser.summarize": self.browser_domain.browser_summarize,
            # ── 浏览器全自动化（Playwright）──
            "browser.navigate": self.browser_automation_domain.browser_navigate,
            "browser.click": self.browser_automation_domain.browser_click,
            "browser.type": self.browser_automation_domain.browser_type,
            "browser.extract": self.browser_automation_domain.browser_extract,
            "browser.screenshot": self.browser_automation_domain.browser_screenshot,
            "browser.scroll": self.browser_automation_domain.browser_scroll,
            "browser.get_tabs": self.browser_automation_domain.browser_get_tabs,
            "browser.switch_tab": self.browser_automation_domain.browser_switch_tab,
            "browser.wait": self.browser_automation_domain.browser_wait,
            "browser.close": self.browser_automation_domain.browser_close,
            "browser.automate": self.browser_automation_domain.browser_automate,
            "todoist.task.list": self.translation_domain.todoist_task_list,
            "todoist.task.create": self.translation_domain.todoist_task_create,
            "todoist.project.list": self.translation_domain.todoist_project_list,
            "youdao.note.list": self.translation_domain.youdao_note_list,
            "youdao.note.create": self.translation_domain.youdao_note_create,
            "youdao.note.get": self.translation_domain.youdao_note_get,
            "youdao.note.update": self.translation_domain.youdao_note_update,
            "workflow.execute": self._workflow_execute,
            "decision.compare_paths": self._decision_compare_paths,
            "cross_device.route": self._cross_device_route,
            "cross_device.save_session": self._cross_device_save_session,
            "cross_device.upload_attachment": self._cross_device_upload_attachment,
            "proactive.tick": self._proactive_tick,
            "suggestion.daily": self._suggestion_daily,
            "dashboard.show": self._dashboard_show,
            "digest.show": self._digest_show,
            "digest.create": self._digest_create,
            "info.collect": self._info_collect,
            "image.generate": self._image_generate,
            "info.publish": self._info_publish,
            "workflow.publish_article": self._workflow_publish_article,
            "settings.shortcuts": self._settings_show,
            "settings.sources": self._settings_show,
            "settings.proactive": self._settings_show,
            "evolution.show": self._evolution_show,
            "habit.status": self._habit_status,
            "review.daily": self._review_daily,
            # ── 任务调度执行（CronService 桥接）──
            "task.execute": self._task_execute,
            # ── S3: 自动备份（每周日 03:00 由 cron 触发）──
            "system.auto_backup": self._system_auto_backup,
            # ── P0-4: 记忆健康巡检（每日由 cron 触发，异常走连续失败告警）──
            "system.memory_health": self._system_memory_health,
            # ── Agent 编排 ──
            "agent.delegate": self._agent_delegate,
            "agent.ask": self._agent_ask,
            "agent.directory": self._agent_directory,
            # ── Trae IDE 控制 ──
            "trae.open": self.trae_domain.trae_open,
            "trae.open_file": self.trae_domain.trae_open_file,
            "trae.diff": self.trae_domain.trae_diff,
            "trae.list_extensions": self.trae_domain.trae_list_extensions,
            "trae.install_extension": self.trae_domain.trae_install_extension,
            "trae.uninstall_extension": self.trae_domain.trae_uninstall_extension,
        }
        # ── 动态工具注册（ToolSpec 配置驱动）──
        # 从 data/tool_specs.json 加载外部工具，自动注册为 tool.* action_id
        self.tool_registry = None
        self.dynamic_tool_domain = None
        if DynamicToolDomain is not None and ToolSpecRegistry is not None:
            try:
                tool_specs_path = Path(__file__).resolve().parent.parent / "data" / "tool_specs.json"
                self.tool_registry = ToolSpecRegistry(config_path=tool_specs_path)
                self.dynamic_tool_domain = DynamicToolDomain(self)
                dynamic_handlers = self.dynamic_tool_domain.get_dynamic_handlers()
                self._handlers.update(dynamic_handlers)
                if dynamic_handlers:
                    logger.info("Loaded %d dynamic tool actions from ToolSpec config", len(dynamic_handlers))
            except Exception:
                self.tool_registry = None
                self.dynamic_tool_domain = None
                logger.exception("Failed to load dynamic tools; builtin capabilities remain available")
        # ── 本地资源工具接线 ──
        # notes/tasks/inbox/vocab 等工具已声明给 butler，但实现停留在旧
        # ReAct 执行器中未注册到 _handlers（迁移遗留断线），此处统一接线。
        try:
            from .capability._local_resource_tools import register_local_resource_handlers

            register_local_resource_handlers(self)
        except Exception:
            logger.exception("Failed to register local resource tool handlers")
        # 注册内置能力到 registry（若提供）
        if self.capability_registry is not None:
            self._register_builtin_capabilities()
        # 自动实例化 ActionBroker（若未传入且 registry 可用）
        if self.action_broker is None and self.capability_registry is not None:
            self.action_broker = ActionBroker(
                capability_registry=self.capability_registry,
                action_guard=self.action_guard,
                executor=self._dispatch_via_broker,
                observability_service=self.observability_service,
            )

    async def _dispatch_via_broker(self, capability_id: str, input_data: dict) -> dict:
        handler = self._handlers.get(capability_id)
        if handler is not None:
            return handler(input_data)
        result = self._dispatch_dynamic(capability_id, input_data)
        if result is None:
            raise ValueError(f"unsupported action: {capability_id}")
        return result

    def shutdown(self) -> None:
        """Shutdown all launched sub-processes."""
        for proc_id, proc in list(self._launched_processes.items()):
            if proc.poll() is None:
                try:
                    logger.debug("terminating launched process %s (pid=%s)", proc_id, proc.pid)
                    proc.terminate()
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    logger.warning("force-killing timed-out process %s (pid=%s)", proc_id, proc.pid)
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception as exc:
                    logger.warning("failed to clean up process %s: %s", proc_id, exc)
        self._launched_processes.clear()

    def _register_builtin_capabilities(self) -> None:
        """把与当前 _handlers 一致的内建能力注册到 registry。

        审计波次② C-1 步骤 4：声明源唯一 —— 只读
        ``services.react_tool_catalog.REACT_TOOL_CATALOG``（原 planner
        CAPABILITY_CATALOG 的独有条目已并入该目录，risk 也已统一到
        read/write/modify/destructive 词表）。历史上这里还有一个从 planner
        目录注册、再对 REACT 目录做 backfill 的双源结构，已随之删除。
        """
        from services.react_tool_catalog import REACT_TOOL_CATALOG

        registry = self.capability_registry
        if registry is None:
            return
        for group in CapabilityRegistry.create_default_groups():
            if registry.get_group(group.group_id) is None:
                registry.register_group(group)
        for entry in REACT_TOOL_CATALOG:
            action_id = entry.get("action_id")
            if not action_id or action_id not in self._handlers:
                continue
            spec = CapabilitySpec(
                capability_id=action_id,
                description=entry.get("description", ""),
                risk_level=entry.get("risk", "read"),
                group=CapabilityRegistry._categorize_action(action_id),
                source="builtin",
                enabled=True,
                payload_schema=dict(entry.get("payload_schema") or {}),
                timeout_default=int(entry.get("timeout_default", 20)),
                confirmation_level=entry.get("confirmation", "auto"),
                rollback_policy=entry.get("rollback", "none"),
                audit_level=entry.get("audit", "summary"),
                privacy_egress_policy=entry.get("privacy", "local_model"),
                idempotency_class=entry.get("idempotency", "idempotent"),
            )
            registry.register_capability(spec)
        # 注册动态工具（ToolSpec 配置）到 registry
        dynamic_handlers = getattr(self, "dynamic_tool_domain", None)
        if dynamic_handlers is not None:
            dynamic_handlers._register_to_capability_registry(registry, self.tool_registry)
        # 注册工具管理能力（tool.management.list/samples/register）—
        # 让 AI 在 chat 中能查询可用工具、看样例、注册新工具（含 LLM 安全校验）
        try:
            from .capability._tool_management import register_tool_management_capabilities

            register_tool_management_capabilities(self)
        except ImportError:
            logger.warning("Dynamic tool management capability is unavailable")
        except Exception:
            logger.exception("Failed to register tool management capabilities")
        # 注册 tool 类型生命周期插件为可调用能力（plugin.<plugin_id>）
        try:
            from .capability._plugin_tools import register_plugin_tools

            register_plugin_tools(self)
        except Exception:
            logger.exception("Failed to register plugin tools")

    def set_cron_service(self, cron_service):
        self._injected_cron_service = cron_service

    def set_task_manager(self, task_manager):
        self._injected_task_manager = task_manager

    def get_supported_actions(self) -> frozenset:
        return frozenset(self._handlers.keys())

    @staticmethod
    def _summarize_result(result: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for k, v in result.items():
            if isinstance(v, (str, int, float, bool)):
                summary[k] = v
            elif isinstance(v, (list, tuple)):
                summary[k] = f"[{type(v).__name__}:{len(v)}]"
            elif isinstance(v, dict):
                summary[k] = f"{{dict:{len(v)}}}"
            elif v is None:
                summary[k] = None
            else:
                summary[k] = str(type(v).__name__)
        return summary

    def execute(self, step: PlannedStep, context: dict[str, Any] | None = None) -> dict[str, Any]:
        # ActionBroker 优先璺?緞：capability 验证 鈫?安全校验 鈫?execution_receipt
        if self.action_broker is not None:
            request = ActionRequest(
                capability_id=step.action_id,
                input=dict(step.payload or {}),
                # C-1 dispatch 经 context 透传来源标记；未提供时沿用既有 "assistant"，向后兼容
                source=str((context or {}).get("source") or "assistant"),
                approved=bool(context and context.get("approved")),
            )
            result = self.action_broker.execute_sync(request)
            if result.blocked:
                raise ValueError(f"action blocked by broker: {result.blocked_reason}{': ' + result.error if result.error else ''}")
            if not result.success:
                raise RuntimeError(f"action failed via broker: {result.error}")
            return dict(result.output)
        # Static handler dispatch
        handler = self._handlers.get(step.action_id)
        if handler is None:
            # Fallthrough: try dynamic handlers (MCP tools, plugin capabilities)
            result = self._dispatch_dynamic(step.action_id, dict(step.payload or {}))
            if result is None:
                raise ValueError(f"unsupported action: {step.action_id}")
            return result
        payload = dict(step.payload or {})
        if context:
            # approved 是 broker 路径的授权标记，不注入 handler 入参
            extra = {k: v for k, v in context.items() if k != "approved"}
            if extra:
                payload["_context"] = extra
        result = handler(payload)
        if self.user_profile_service is not None:
            try:
                self.user_profile_service.observe_action(step.action_id)
            except Exception:
                logger.warning("profile observe_action failed for %s", step.action_id, exc_info=True)
                if self.observability_service is not None:
                    try:
                        self.observability_service.track_event(
                            "assistant.profile.observe_failed",
                            severity=SEVERITY_DEGRADED,
                            payload={"action_id": step.action_id},
                        )
                    except Exception:
                        logger.debug("track_event failed for profile observe failure", exc_info=True)
        # 注意：不在单步级鍒?氦付到收件绠扁?斺?旂敱执行?灞?execute_plan()
        # 在汇总时统一调用 _deliver_summary() 携带正确鐨?delivery_channel。?        # 每个姝ラ?单独交付会绕杩囬?氶亾璺?敱锛岄?犳垚閲嶅?收件箱项。?
        try:
            if hasattr(self, "_event_bus") and self._event_bus is not None:
                self._event_bus.emit(
                    AppEvent(
                        event_type="capability.executed",
                        source="capability_router",
                        payload={"action": step.action_id, "result": self._summarize_result(result)},
                    )
                )
        except Exception:
            logger.debug("EventBus emit failed for %s", step.action_id, exc_info=True)
        return result

    async def execute_direct(self, action_id: str, args: dict[str, Any] | None = None, approved: bool = False) -> dict[str, Any]:
        """异步直接执行入口（PydanticAI 管家 / 工具调用路径）。

        把同步 execute() 包进 asyncio.to_thread；异常转为结构化错误 dict，
        保证工具结果可被 LLM 读回并自我纠正，而不是静默失败。

        approved=True 表示该动作已经过确认闭环的人工授权（如用户点击批准），
        会透传给 ActionGuard 跳过 LLM 高危拦截。
        """
        step = PlannedStep(
            step_id=f"direct_{action_id}",
            action_id=action_id,
            payload=dict(args or {}),
            description=f"direct: {action_id}",
        )
        try:
            if approved:
                return await asyncio.to_thread(self.execute, step, {"approved": True})
            return await asyncio.to_thread(self.execute, step)
        except Exception as exc:
            logger.warning("execute_direct failed for %s: %s", action_id, exc)
            return {"success": False, "error": str(exc), "action_id": action_id}

    def dispatch_sync(
        self,
        action_id: str,
        payload: dict[str, Any],
        *,
        source: str = "assistant",
        approved: bool = False,
    ) -> dict[str, Any]:
        """``dispatch`` 的同步孪生入口（审计波次② C-1 步骤 3）。

        存在理由：底层 ``execute()`` 本就是同步函数（broker 走 ``execute_sync``），
        而沙箱工具执行器是被 ``asyncio.to_thread`` 包进 worker 线程调用的同步闭包
        ——那里既没有 running loop 可 await，也不能 ``asyncio.run``（会在已有 loop 的
        上下文里炸）。故直接提供同步入口，与 ``dispatch`` 共用同一条分派链：
        ActionBroker 优先 → ``_handlers`` 静态表 → ``_dispatch_dynamic``。

        异常统一转为结构化错误 dict（与 ``execute_direct``/``dispatch`` 同构）。
        """
        step = PlannedStep(
            step_id=f"dispatch_{action_id}",
            action_id=action_id,
            payload=dict(payload or {}),
            description=f"dispatch[{source}]: {action_id}",
        )
        context: dict[str, Any] = {"source": source}
        if approved:
            context["approved"] = True
        try:
            return self.execute(step, context)
        except Exception as exc:
            logger.warning("dispatch_sync failed for %s (source=%s): %s", action_id, source, exc)
            return {"success": False, "error": str(exc), "action_id": action_id, "source": source}

    async def dispatch(
        self,
        action_id: str,
        payload: dict[str, Any],
        *,
        source: str = "assistant",
        approved: bool = False,
    ) -> dict[str, Any]:
        """公开分派入口（审计波次② C-1 步骤 1）。

        复用与 ``execute``/``execute_direct`` 完全一致的分派链——
        ActionBroker 优先 → ``_handlers`` 静态表 → ``_dispatch_dynamic``（MCP/插件）——
        但对外暴露为稳定的公共契约，并携带 ``source`` 来源标记（透传给 ActionBroker
        的 ``ActionRequest.source``）。**供 sandbox / react 适配层取代对 ``_handlers``
        的私有访问**：调用方只需给出 action_id + payload，不再直接读私有字典。

        异常统一转为结构化错误 dict（与 execute_direct 同构），保证工具结果可被
        LLM 读回并自我纠正，而不是抛出打断上层。不删除 execute/execute_direct
        （现有消费者众多）。
        """
        try:
            return await asyncio.to_thread(self.dispatch_sync, action_id, payload, source=source, approved=approved)
        except Exception as exc:  # pragma: no cover - dispatch_sync 已自吞异常，此处仅兜 to_thread 自身失败
            logger.warning("dispatch failed for %s (source=%s): %s", action_id, source, exc)
            return {"success": False, "error": str(exc), "action_id": action_id, "source": source}

    def _dispatch_dynamic(self, action_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Fallthrough dispatch for dynamically registered capabilities (MCP, plugins)."""
        # MCP tools: action_id = "mcp.server.tool_name"
        if action_id.startswith("mcp.") and self.mcp_manager is not None:
            parts = action_id.split(".")
            if len(parts) >= 3:
                try:
                    # ``_context`` 是 execute() 为静态 handler 注入的路由内部元数据，
                    # 不是模型给 MCP 工具的参数；外发前剔掉，免得服务端按
                    # “unexpected argument” 拒收（C-1 步骤 3：B 套收缩后 mcp.* 也走本链）。
                    outbound = {k: v for k, v in dict(payload or {}).items() if k != "_context"}
                    return self.mcp_manager.invoke(action_id, outbound)
                except Exception as exc:
                    logger.warning("MCP dynamic dispatch failed for %s: %s", action_id, exc)
                    return {"success": False, "error": str(exc), "action_id": action_id}
        return None

    # 鈹?鈹? 核心编排方法 鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?
    def _process_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.environment_probe.process_list(str(payload.get("filter") or ""))

    def _process_kill(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.environment_probe.process_kill(str(payload.get("name") or ""))

    def _confirmation_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        """查询队列2 确认（confirmation_id）状态——纯读，无副作用。

        C-1 步骤 3：与 butler CONFIRM 流程配套的 confirmation.status 此前只存在于
        B 套 react_executor 内联分支，此处补注册使 A 套成为唯一执行体。
        """
        from services.confirmation_flow import get_confirmation_status

        return get_confirmation_status(str((payload or {}).get("confirmation_id") or ""))

    def _system_info(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.environment_probe.system_info(str(payload.get("category") or "all"))

    def _system_auto_backup(self, payload: dict[str, Any]) -> dict[str, Any]:
        """S3: 自动备份 — 由 cron 每周日 03:00 触发。

        备份 notes/tasks/settings/memories 到 data/backups/YYYYMMDD_HHMMSS.json.gz，
        保留最近 8 个备份，自动清理旧备份。
        """
        import asyncio
        from pathlib import Path as _Path

        try:
            # 从 data_store 获取数据目录
            data_store = getattr(self, "data_store", None)
            if data_store is None:
                return {"status": "failed", "error": "data_store not available"}
            # LocalDataStore 使用 base_dir 属性（非 data_dir）
            data_dir = getattr(data_store, "base_dir", None) or getattr(data_store, "data_dir", None)
            if data_dir is None:
                # 兜底：使用默认路径
                data_dir = _Path(__file__).resolve().parent.parent / "data"
            else:
                data_dir = _Path(data_dir)

            from services.backup_service import BackupService, BackupConfig

            # 保留最近 8 个备份（约 8 周）
            config = BackupConfig(
                backup_dir=data_dir / "backups",
                max_backups=8,
                compress=True,
                include_ai_cache=False,  # AI 缓存体积大且可重建
            )
            # 传入 data_store 使 BackupService 通过 SQLite 读写数据
            service = BackupService(data_dir=data_dir, config=config, data_store=data_store)

            # create_backup 是 async，在同步 handler 中通过 asyncio.run 运行。
            # cron handler 在线程池中执行，不会有已运行的事件循环。
            try:
                result = asyncio.run(service.create_backup())
            except RuntimeError:
                # 若 asyncio.run 失败（已有运行中的事件循环），用线程池隔离
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    result = pool.submit(asyncio.run, service.create_backup()).result(timeout=120)

            if result.success:
                logger.info(
                    "S3 自动备份成功: id=%s, size=%d bytes",
                    result.backup_id,
                    result.size_bytes,
                )
                return {
                    "status": "completed",
                    "backup_id": result.backup_id,
                    "file_path": result.file_path,
                    "size_bytes": result.size_bytes,
                    "duration_seconds": result.duration_seconds,
                }
            else:
                logger.warning("S3 自动备份失败: %s", result.error)
                return {"status": "failed", "error": result.error}
        except Exception as exc:
            logger.exception("S3 自动备份异常: %s", exc)
            return {"status": "failed", "error": str(exc)}

    def _system_memory_health(self, payload: dict[str, Any]) -> dict[str, Any]:
        """P0-4: 记忆健康巡检 — 每日由 cron 触发。

        检查三项：存储目录可写、数据层可读、记忆索引可查询。
        任一项失败返回 error，由 CronService 连续失败告警链路通知用户
        （24 小时监控不只盯进程，也要盯核心数据资产）。
        """
        import tempfile
        from pathlib import Path as _Path

        checks: dict[str, Any] = {}
        errors: list[str] = []
        data_store = getattr(self, "data_store", None)

        # 1. 存储目录存在且可写
        try:
            data_dir = getattr(data_store, "base_dir", None) or getattr(data_store, "data_dir", None)
            if data_dir is None:
                checks["storage_writable"] = "skipped_no_data_dir"
            else:
                probe = _Path(data_dir)
                if not probe.exists():
                    errors.append(f"data dir missing: {data_dir}")
                    checks["storage_writable"] = False
                else:
                    fd, tmp_name = tempfile.mkstemp(dir=str(probe), prefix=".health_")
                    import os as _os

                    _os.close(fd)
                    _os.remove(tmp_name)
                    checks["storage_writable"] = True
        except Exception as exc:
            checks["storage_writable"] = False
            errors.append(f"storage_writable: {exc}")

        # 2. 数据层可读（JSON/SQLite 读取链路健康）
        try:
            if data_store is not None and hasattr(data_store, "load_json"):
                data_store.load_json("cron_jobs", [])
                checks["data_readable"] = True
            else:
                checks["data_readable"] = "skipped_no_data_store"
        except Exception as exc:
            checks["data_readable"] = False
            errors.append(f"data_readable: {exc}")

        # 3. 记忆索引可查询（向量库/索引健康）
        try:
            svc = getattr(self, "memory_index_service", None)
            if svc is None:
                checks["memory_searchable"] = "skipped_no_service"
            elif hasattr(svc, "health_check"):
                checks["memory_searchable"] = bool(svc.health_check())
            elif hasattr(svc, "search"):
                svc.search("巡检", 1)
                checks["memory_searchable"] = True
            else:
                checks["memory_searchable"] = "skipped_no_probe_api"
        except Exception as exc:
            checks["memory_searchable"] = False
            errors.append(f"memory_searchable: {exc}")

        ok = not errors
        if ok:
            logger.info("记忆健康巡检通过: %s", checks)
            return {"success": True, "checks": checks}
        logger.warning("记忆健康巡检异常: %s", errors)
        return {"success": False, "checks": checks, "error": "; ".join(errors)[:300]}

    def _code_execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.cc_bridge is None:
            return {"success": False, "error": "CCBridge 未配置", "action": "code.execute"}
        task = str(payload.get("task") or payload.get("goal") or payload.get("description") or "")
        if not task:
            return {"success": False, "error": "缺少 task 参数", "action": "code.execute"}
        try:
            context = {
                "files": payload.get("files") or [],
                "constraints": payload.get("constraints") or [],
                "user_profile": payload.get("user_profile") or {},
            }
            coro = self.cc_bridge.execute(
                task=task,
                context=context,
                max_turns=int(payload.get("max_turns", 100)),
                model=str(payload.get("model", "sonnet")),
                allowed_tools=str(payload.get("allowed_tools", "Read,Write,Edit,Bash,Glob,Grep")),
                timeout=int(payload.get("timeout", 600)),
            )
            # B1 修复：避免在已有事件循环中调用 asyncio.run() 导致崩溃
            try:
                asyncio.get_running_loop()
                # 已在事件循环中：在新线程中运行以避免嵌套事件循环
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    result = pool.submit(asyncio.run, coro).result(timeout=int(payload.get("timeout", 600)) + 30)
            except RuntimeError:
                # 无运行中的事件循环：直接 asyncio.run
                result = asyncio.run(coro)
            return {
                "success": result.status.value in ("success", "completed"),
                "action": "code.execute",
                "status": result.status.value,
                "summary": result.summary,
                "files_created": result.files_created,
                "files_modified": result.files_modified,
                "commands_run": result.commands_run,
                "duration_seconds": result.duration_seconds,
                "error": result.error,
            }
        except Exception as exc:
            return {"success": False, "error": f"{type(exc).__name__}: {exc}", "action": "code.execute"}

    def _system_capabilities(self, payload: dict[str, Any]) -> dict[str, Any]:
        include_disabled = bool(payload.get("include_disabled", False))
        if self.capability_registry is not None:
            capabilities = self.capability_registry.build_capability_catalog(include_disabled=include_disabled)
            groups = self.capability_registry.list_groups()
            return {
                "success": True,
                "capabilities": capabilities,
                "groups": [{"id": g.group_id, "name": g.name, "description": g.description, "enabled": g.enabled} for g in groups],
                "total": len(capabilities),
            }
        return {
            "success": True,
            "capabilities": [
                {"action_id": aid, "description": getattr(h, "__doc__", "") or "", "source": "builtin"} for aid, h in self._handlers.items()
            ],
            "total": len(self._handlers),
        }

    def _system_environment(self, payload: dict[str, Any]) -> dict[str, Any]:
        category = str(payload.get("category") or "all").strip().lower()
        result: dict[str, Any] = {"success": True, "captured_at": datetime.now(timezone.utc).isoformat()}
        if category in ("all", "cpu", "memory", "disk", "network"):
            result["system"] = self.environment_probe.system_info(category if category != "all" else "all")
        if category in ("all", "processes"):
            result["processes"] = self.environment_probe.process_list()
        if category in ("all", "apps"):
            apps = []
            known = self.environment_probe._WELL_KNOWN_APPS
            for name in known:
                path = self.environment_probe.resolve_app_executable(name)
                apps.append({"name": name, "available": path is not None, "path": path or ""})
            result["apps"] = apps
        return result

    def _system_scan(self, payload: dict[str, Any]) -> dict[str, Any]:
        combined = self._system_environment({"category": "all"})
        combined["capabilities"] = self._system_capabilities({"include_disabled": False})
        return combined

    def _evolution_daily_scan(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.evolution_orchestrator is None:
            return {"status": "skipped", "reason": "evolution_orchestrator not configured"}
        try:
            briefing = self.evolution_orchestrator.scan_and_report()
            return {"status": "completed", "findings": briefing.to_dict() if hasattr(briefing, "to_dict") else str(briefing)}
        except Exception as exc:
            logger.warning("鎶?鏈?姩态扫描失璐? %s", exc)
            return {"status": "failed", "error": str(exc)}

    def _decision_compare_paths(self, payload: dict[str, Any]) -> dict[str, Any]:
        comparator = getattr(self, "path_comparator", None)
        if comparator is None:
            return {"status": "skipped", "reason": "path_comparator not configured"}
        try:
            options = payload.get("options", [])
            if not options:
                return {"status": "failed", "error": "no options provided"}
            result = comparator.compare(options)
            return {"status": "completed", "result": result.to_dict() if hasattr(result, "to_dict") else str(result)}
        except Exception as exc:
            logger.warning("鏂规?比较失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

    def _cross_device_route(self, payload: dict[str, Any]) -> dict[str, Any]:
        router = getattr(self, "cross_device_router", None)
        if router is None:
            return {"status": "skipped", "reason": "cross_device_router not configured"}
        try:
            capability = payload.get("capability", "")
            if not capability:
                return {"status": "failed", "error": "no capability specified"}
            device_type = payload.get("device_type", "")
            if device_type:
                router.set_current_device(device_type)
            result = router.route(capability)
            return {"status": "completed", "result": result.to_dict() if hasattr(result, "to_dict") else str(result)}
        except Exception as exc:
            logger.warning("璺ㄧ?璺?敱失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

    def _cross_device_save_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        mgr = getattr(self, "cross_device_session", None)
        if mgr is None:
            return {"status": "skipped", "reason": "cross_device_session not configured"}
        try:
            result = mgr.save_session(
                session_id=payload.get("session_id", ""),
                device_type=payload.get("device_type", "desktop"),
                topic=payload.get("topic", ""),
                last_message=payload.get("last_message", ""),
            )
            return {"status": "completed", "session_id": result}
        except Exception as exc:
            logger.warning("保存璺ㄧ?会话失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

    def _cross_device_upload_attachment(self, payload: dict[str, Any]) -> dict[str, Any]:
        layer = getattr(self, "attachment_layer", None)
        if layer is None:
            return {"status": "skipped", "reason": "attachment_layer not configured"}
        try:
            attach = layer.upload(
                source_device=payload.get("source_device", "desktop"),
                file_name=payload.get("file_name", "unknown"),
                description=payload.get("description", ""),
            )
            return {"status": "completed", "attachment": attach.to_dict() if hasattr(attach, "to_dict") else str(attach)}
        except Exception as exc:
            logger.warning("上传附件失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

    def _workflow_publish_article(self, payload: dict[str, Any]) -> dict[str, Any]:
        """全自动发文章：采集 → AI写文 → 配图 → 发布（异步推送进度）。"""
        try:
            import asyncio
            from services.info_collector_service import InfoCollectorService
            from services.image_generator_service import ImageGeneratorService
            from services.platform_publisher_service import PlatformPublisherService

            topics = payload.get("topics", ["AI", "前沿科技", "效率工具", "开源项目"])
            platform = payload.get("platform", "wordpress")
            status = payload.get("status", "draft")
            # B8 修复：使用 asyncio.get_running_loop() 替代私有属性 _event_bus._loop
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is None or not loop.is_running():
                return {"status": "failed", "error": "事件循环不可用"}

            total_steps = 4

            def _emit(step: int, label: str, status_text: str) -> None:
                self._event_bus.emit(
                    AppEvent(
                        event_type="workflow.progress",
                        source="capability_router",
                        payload={"step": step, "total": total_steps, "label": label, "status": status_text},
                    )
                )

            async def _run() -> dict[str, Any]:
                try:
                    _emit(1, "采集资讯", "collecting")
                    collector = InfoCollectorService(
                        ai_client=self.ai_client,
                        settings_repository=getattr(self, "settings_repository", None),
                        # R2-A1：复用 context 装配的共享注册表
                        source_registry=getattr(self, "info_source_registry", None),
                    )
                    collected = await collector.collect(topics=topics, generate_brief=True)
                    items = collected.get("items", [])
                    if not items:
                        _emit(4, "采集失败", "failed")
                        return {"status": "failed", "error": "采集不到资讯"}

                    _emit(2, "AI 撰写文章", "writing")
                    title = f"{'、'.join(topics[:2])} 今日资讯简报"
                    body = collected.get("brief", "")
                    if not body:
                        body_lines = [f"- [{i['title']}]({i['url']})" for i in items[:10]]
                        body = "\n\n".join(body_lines)

                    _emit(3, "生成配图", "generating_image")
                    img_result = None
                    try:
                        img_svc = ImageGeneratorService(
                            ai_client=self.ai_client,
                            settings_repository=self.settings_repository,
                        )
                        img_result = await img_svc.generate(
                            prompt=f"一张关于{'、'.join(topics)}的资讯插图",
                            n=1,
                        )
                    except Exception:
                        logger.warning("image generation failed", exc_info=True)
                    image_urls = []
                    if img_result and img_result.get("success"):
                        image_urls = [img["b64_data"] for img in img_result.get("images", [])]

                    _emit(4, "发布到平台", "publishing")
                    publisher = PlatformPublisherService(settings_repository=self.settings_repository)
                    pub_result = await publisher.publish(
                        platform=platform,
                        content={
                            "title": title,
                            "body": body,
                            "image_urls": image_urls,
                            "tags": topics,
                            "status": status,
                        },
                    )
                    _emit(4, "发布完成", "completed")
                    self._event_bus.emit(
                        AppEvent(
                            event_type="workflow.completed",
                            source="capability_router",
                            payload={"result": pub_result, "title": title},
                        )
                    )
                    return {
                        "status": "completed" if pub_result.get("success") else "failed",
                        "data": {
                            "platform": platform,
                            "title": title,
                            "body_preview": body[:200],
                            "image_count": len(image_urls),
                            "publish_result": pub_result,
                        },
                    }
                except Exception as exc:
                    logger.warning("workflow.publish_article failed: %s", exc)
                    _emit(4, f"失败: {exc}", "failed")
                    return {"status": "failed", "error": str(exc)}

            asyncio.run_coroutine_threadsafe(_run(), loop)
            return {"status": "started", "task": "workflow.publish_article", "total_steps": total_steps}
        except Exception as exc:
            logger.warning("workflow.publish_article launch failed: %s", exc)
            return {"status": "failed", "error": str(exc)}

    def _settings_show(self, payload: dict[str, Any]) -> dict[str, Any]:
        """打开设置（回璋冮?氳繃 ActionDispatcher 处理锛屾?处仅记录锛夈?"""
        logger.info("settings requested: %s", payload.get("action_id", "unknown"))
        return {"status": "completed", "message": "请在主窗鍙ｈ?缃?潰板中操作"}

    def _evolution_show(self, payload: dict[str, Any]) -> dict[str, Any]:
        """显示鎶?鏈?浄达发鐜般?"""
        if self.evolution_orchestrator is not None:
            briefing = self.evolution_orchestrator.get_last_briefing()
            if briefing is not None:
                return {"status": "completed", "briefing": str(briefing)}
        return {"status": "completed", "message": "暂无鎶?鏈?浄达发鐜?"}

    def _habit_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        """鏌ヨ?画像习惯学习结果。"""
        if self.user_profile_service is None:
            return {"status": "ok", "message": "画像服务鏈?氨缁?"}
        try:
            profile = self.user_profile_service.load()
            long_term = profile.get("long_term", {})
            context = profile.get("context", {})
            lines = ["## 小问对你的了瑙?n"]
            habit_hours = context.get("habit_hours", [])
            if habit_hours:
                lines.append(f"**活跃时段**：{', '.join(habit_hours)}")
            else:
                lines.append("**活跃鏃舵?**：暂鏈?彂鐜拌?律（继续观察一?級")
            topics = long_term.get("focus_topics", [])
            if topics:
                lines.append(f"**关注话题**：{', '.join(topics)}")
            tools = long_term.get("preferred_tools", [])
            if tools:
                lines.append(f"**常用能力**：{', '.join(tools)}")
            templates = long_term.get("task_templates", [])
            if templates:
                names = [t.get("name", "") for t in templates[:5] if isinstance(t, dict)]
                if names:
                    lines.append(f"**任务模板**：{', '.join(names)}")
            sources = long_term.get("common_sources", [])
            if sources:
                lines.append(f"**信息源**：{'、'.join(sources[:5])}")
            proactive = profile.get("permission", {}).get("allow_proactive_push", True)
            lines.append(f"**主动推送**：{'已开启' if proactive else '已关闭'}")
            return {"status": "ok", "message": "\n".join(lines)}
        except Exception as exc:
            logger.warning("habit.status failed: %s", exc)
            return {"status": "ok", "message": "画像读取失败锛岃?稍后再试"}

    def _review_daily(self, payload: dict[str, Any]) -> dict[str, Any]:
        """每日复盘：汇总今日活鍔ㄣ?"""
        today = datetime.now().strftime("%Y-%m-%d")
        lines = [f"## {today} 复盘\n"]
        # 笔记?
        note_count = 0
        if self.note_repository is not None:
            try:
                notes: list[Any] = getattr(self.note_repository, "get_all_notes", lambda: [])()

                note_count = len(notes) if isinstance(notes, (list, tuple)) else 0
            except Exception as exc:
                logger.debug("Failed to get note count: %s", exc)
        lines.append("- **笔记?**锛{} 鏉?".format(note_count))
        # 任务
        task_count = 0
        if self.task_repository is not None:
            try:
                tasks: list[Any] = getattr(self.task_repository, "get_all_tasks", lambda: [])()

                if isinstance(tasks, (list, tuple)):
                    task_count = len([t for t in tasks if not getattr(t, "completed", False)])
            except Exception as exc:
                logger.debug("Failed to get task count: %s", exc)
        lines.append("- **待办**：{} 项未完成".format(task_count))
        # 行为事件数（今日）
        event_today = 0
        try:
            from core.behavior_logger import EVENT_FILE
            import os

            log_path = getattr(getattr(self, "user_profile_service", None), "data_store", None)
            if log_path and hasattr(log_path, "base_dir"):
                bpath = os.path.join(log_path.base_dir, EVENT_FILE)
                if os.path.isfile(bpath):
                    with open(bpath, encoding="utf-8") as f:
                        for line in f:
                            if today in line:
                                event_today += 1
        except Exception as exc:
            logger.debug("Failed to count behavior events: %s", exc)
        if event_today > 0:
            lines.append("- **记录**：{} 条行为事件".format(event_today))
        lines.append("")
        lines.append("有什么想回顾或调整的吗？")
        return {"status": "ok", "message": "\n".join(lines)}

    def _dashboard_show(self, payload: dict[str, Any]) -> dict[str, Any]:
        from core.events import event_bus, AppEvent

        event_bus.emit(
            AppEvent(
                event_type="dashboard.show",
                payload={},
                source="capability_router",
            )
        )
        return {"success": True, "message": "正在打开工作台仪表盘。"}
