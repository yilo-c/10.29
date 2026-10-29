"""思忆集无头后端入口 — 无 PyQt6 依赖。

启动流程：
1. 单实例检查（msvcrt 文件锁 + P0-1 僵死进程自动接管）
2. 初始化 AppContext（跳过 Qt 依赖）
3. 注册 JSON-RPC 方法
4. 启动 WebSocket 服务器
5. 启动 EventBus 桥接
6. 等待停止信号
"""

from __future__ import annotations

from typing import Any

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone

# 确保能从思忆集app/ 根目录导入
_shim_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _shim_dir not in sys.path:
    sys.path.insert(0, _shim_dir)

# 单实例锁（P0-1：实现下沉至 backend/instance_lock.py，含僵死进程接管）
from backend import instance_lock

HAS_MSVCRT = instance_lock.HAS_MSVCRT

from app_runtime.app_context import AppContext
from core.events import AppEvent, event_bus
from core.isolation import is_offline_mode

logger = logging.getLogger("siyi.backend")


def _log_effective_api_key(context) -> None:
    """记录最终生效的 DeepSeek API key 前缀，用于排查 key 丢失问题。

    P2-3: 使用 core.env_loader.mask_secret 脱敏显示 key 前缀（如 sk-a***），
    替代旧实现只输出 len(key) 的方式，便于排查 key 配置问题同时不泄露完整 key。
    """
    try:
        from core.env_loader import mask_secret

        client = getattr(context, "ai_client", None)
        if client is None:
            logger.info("API key check: ai_client is None")
            return
        _inner = getattr(client, "_inner", None)
        primary = getattr(_inner, "primary", None) if _inner else None
        if primary and getattr(primary, "api_key", None):
            key = primary.api_key
            logger.info("API key check: primary=%s key_prefix=%s len=%d", getattr(primary, "model", "?"), mask_secret(key), len(key))
        else:
            logger.info("API key check: no primary client found")
    except Exception as exc:
        logger.debug("API key check failed (non-critical): %s", exc)


# ── 配置 ──
# N-D 暴露面收口（2026-09-17）：默认仅绑定回环地址，消除「生产默认全网卡
# 监听」。Tailscale / LAN 手机直连改为显式开启：设 SIYI_WS_HOST=0.0.0.0
# （或指定网卡地址），并保证认证可用——非回环绑定 + 认证不可用会被
# security_fail_closed_gate 拒绝启动。ws_server._verify_auth 仍强制
# 非 localhost 连接必须携带 token，双保险。
WS_HOST = (os.environ.get("SIYI_WS_HOST") or "").strip() or "127.0.0.1"
WS_PORT = int(os.environ.get("SIYI_WS_PORT", "18765"))

# fail-closed 守卫的回环地址白名单（与 ws_server._verify_auth 的 localhost
# 信任集对齐；127.0.0.0/8 整段视为回环）
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})


def _is_loopback_host(host: str) -> bool:
    """绑定地址是否属于仅本机可达的回环面。"""
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def security_fail_closed_gate(ws_host: str = WS_HOST, auth_enabled: bool = True) -> None:
    """N-D auth fail-closed 守卫：认证不可用 + 非回环绑定 = 拒启（exit 8）。

    修复前：auth token 初始化失败只打 warning，auth_enabled 静默降为 False，
    服务继续启动——名义上 LAN 可达，实际非 localhost 连接全被 _verify_auth
    挡掉，Tailscale 手机端静默断连且运维无感知（静默降级）。
    修复后：该组合启动即红，把降级变成显式失败。仅回环绑定允许无认证运行
    （与 _verify_auth 的 localhost 信任语义一致；个人单机自用即此形态）。
    """
    if auth_enabled or _is_loopback_host(ws_host):
        return
    logger.error(
        "SECURITY POSTURE: refusing to start — bind=%s is non-loopback but "
        "authentication is unavailable (token init failed or "
        "SIYI_WS_AUTH_ENABLED=0). Fix the auth token, or set "
        "SIYI_WS_HOST=127.0.0.1 for local-only use.",
        ws_host,
    )
    sys.exit(8)


# SIYI_TEST_OFFLINE=1：测试隔离后端的启动离线模式。跳过启动期会触发真实
# 网络的后台任务（资讯 RSS 抓取、autonomy 到期调度），使隔离后端快速、
# 确定性地就绪，避免慢/受限网络下集成测试被后台流量拖慢或干扰。
#
# PD-ISO-b（2026-09-18）：判定收敛到 core.isolation 的**离线基元**，不再各处
# 自己写 `== "1"`——分叉的后果是"声明了但有的通道没人读"（ai_client 就漏了
# 一整代）。新增出网通道请一律读同一基元。
TEST_OFFLINE = is_offline_mode()


def acquire_lock() -> int | None:
    """单实例文件锁，返回锁文件 fd。

    P0-1：实现下沉至 ``backend/instance_lock.py``——锁文件记录持有者 PID；
    获取失败时探测旧持有者（PID 已死 → 清残留锁；PID 存活但 WS 端口持续
    不可达 → 终止僵死进程后接管），避免人工杀进程删锁。
    """
    return instance_lock.acquire()


def release_lock(fd: int | None) -> None:
    instance_lock.release(fd)


# ── auth.rotate 延迟踢连接（P2 修复，rpc-census-2026-08-30）──
# 缺陷：_rpc_auth_rotate 原实现在 rotate 后同步 close_all(1008)，把调用方
# 自己的连接也先于 RPC 响应关掉——客户端只见 "ws closed: 8"（普查 broken），
# 成功响应（含新 token）永远送不到。修复：handler 先返回响应，延迟 1s 再
# 异步踢出全部旧连接（设计意图保留：旧 token 立即失效，客户端凭响应中的
# 新 token 重连）。
# 延迟依据：dispatch 返回 → ws_server 写出响应是毫秒级窗口，1s 余量充分；
# 延迟期间旧 token 连接多存活 1s，属可接受的安全权衡。
_KICK_DELAY_SECONDS = 1.0
# 延迟任务强引用集合，防止 create_task 被 GC 回收（惯例同 _notes._pending_bg_tasks）
_deferred_kick_tasks: "set[asyncio.Task]" = set()


async def _deferred_close_all(
    connection_manager: Any,
    *,
    code: int,
    message: bytes,
    delay: float = _KICK_DELAY_SECONDS,
) -> None:
    """延迟关闭全部连接（auth.rotate 踢出旧 token 连接）。"""
    await asyncio.sleep(delay)
    try:
        # P3 修复（2026-08-31）：auth 踢人场景服务端继续存活，close_all 停掉
        # 的心跳必须复活，否则死连接永不清理、无 ping 保活。
        await connection_manager.close_all(code=code, message=message, restart_heartbeat=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("deferred close_all(%s) failed: %s", message, exc)


def _schedule_deferred_close_all(
    connection_manager: Any,
    *,
    code: int,
    message: bytes,
    delay: float = _KICK_DELAY_SECONDS,
) -> "asyncio.Task | None":
    """调度延迟踢连接；无运行中事件循环时返回 None。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(_deferred_close_all(connection_manager, code=code, message=message, delay=delay))
    _deferred_kick_tasks.add(task)
    task.add_done_callback(_deferred_kick_tasks.discard)
    return task


async def auth_rotate_impl(server: Any) -> dict[str, Any]:
    """auth.rotate 主体（模块级便于测试）：换 token → 调度延迟踢连接。

    注意：本函数返回即代表 RPC 响应可写回调用方，连接关闭严格晚于响应。
    """
    try:
        new_token = await asyncio.to_thread(server.rotate_auth_token)
        # 踢出所有活跃连接（旧 token 立即失效）——但延迟到响应送达之后
        _schedule_deferred_close_all(server.connection_manager, code=1008, message=b"token rotated")
        return {"success": True, "rotated": True, "token": new_token}
    except Exception as exc:  # noqa: BLE001
        logger.warning("auth.rotate failed: %s", exc)
        return {"success": False, "error": f"rotate failed: {exc}"}


async def auth_revoke_impl(server: Any, *, rotate: bool = True) -> dict[str, Any]:
    """auth.revoke 主体（模块级便于测试）：吊销全部会话 → 调度延迟踢连接。

    注意：本函数返回即代表 RPC 响应可写回调用方，连接关闭严格晚于响应。
    与 auth.rotate 同款契约（P1-1 修复）：不再内联 await close_all 吞掉响应。
    """
    try:
        if rotate:
            server.rotate_auth_token()
        server.revoke_all()
        _schedule_deferred_close_all(server.connection_manager, code=1008, message=b"auth revoked")
        return {"success": True, "revoked": True, "rotated": rotate}
    except Exception as exc:  # noqa: BLE001
        logger.warning("auth.revoke failed: %s", exc)
        return {"success": False, "error": f"revoke failed: {exc}"}


async def async_main() -> None:
    """Asynchronous main flow."""
    # 设置 asyncio 未处理异常处理器
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(
        lambda l, ctx: logging.getLogger("siyi.crash").error(
            "asyncio unhandled: %s",
            ctx.get("message", "unknown"),
            exc_info=ctx.get("exception"),
        )
    )

    from backend.ws_server import WebSocketServer
    from backend.event_bridge import EventBridge
    from backend.method_registry import MethodRegistry
    from backend import info_scheduler
    from backend.handlers import register_all as register_methods

    # ── 1. 初始化 AppContext（无头模式）──
    logger.info("initializing AppContext in headless mode...")
    from app_runtime.desknotes_path import resolve_desknotes_dir

    context = AppContext(
        headless=True,
        desknotes_data_dir=resolve_desknotes_dir(),
        # R2-C1：honor SIYI_DATA_DIR 实现数据目录隔离（env 未设时与旧行为一致）
        data_base_dir=os.environ.get("SIYI_DATA_DIR") or None,
    )
    # 注册 backend i18n 的 locale 解析器：读 settings.ui_language（默认 zh-CN）
    try:
        from backend import i18n as _backend_i18n

        _settings_repo = context.settings_repository

        def _resolve_locale() -> str:
            if _settings_repo is None:
                return _backend_i18n.DEFAULT_LOCALE
            try:
                return str(_settings_repo.get_setting("ui_language", _backend_i18n.DEFAULT_LOCALE))
            except Exception:
                return _backend_i18n.DEFAULT_LOCALE

        _backend_i18n.register_locale_provider(_resolve_locale)
    except Exception as exc:
        logger.debug("backend i18n locale provider wiring skipped: %s", exc)
    # 启动时校验 provider_configs 合理性（提醒用户修正不存在的模型名或缺 /v1 的 api_base）
    try:
        from core.provider_config import load_providers
        from ai.client.config import validate_provider_configs

        providers = load_providers(context.settings_repository)
        result = validate_provider_configs(providers)
        if result.get("errors"):
            for err in result["errors"]:
                logger.error("[启动校验] %s", err)
        if result.get("warnings"):
            for warn in result["warnings"]:
                logger.warning("[启动校验] %s", warn)
    except Exception as exc:
        logger.debug("provider_configs 启动校验跳过（非关键）: %s", exc)
    # 记录最终生效的 API key（前 10 位）用于调试 key 丢失问题
    _log_effective_api_key(context)
    logger.info("AppContext initialized successfully")

    # ── 1.3 P2-4: 启动门禁 — 关键子系统不可用时阻塞启动 ──
    # data_store 已由 AppContextBuilder.build_data_store 初始化完毕（含首启迁移），
    # 此时探针读 settings 不会触发 _legacy_migrate_once / 建库副作用。
    # 失败时抛 StartupGateError，main() 会捕获并退出。
    try:
        from core.health_check import run_startup_gate, get_health_checker

        gate_report = await run_startup_gate(
            data_store=getattr(context, "local_data_store", None),
        )
        _gate_status = gate_report.get("status", "unknown")
        if _gate_status != "healthy":
            logger.warning("startup gate passed with non-healthy status: %s", _gate_status)
        else:
            logger.info("startup gate passed (status=healthy)")
        # 初始化全局健康检查器单例（注入 data_store，供 ws_server /health 端点复用）
        get_health_checker(
            data_store=getattr(context, "local_data_store", None),
            reset=True,
        )
    except Exception as exc:
        logger.error("startup gate failed: %s", exc)
        # 不继续启动，让 main() 的外层 try 捕获并退出
        raise

    # ── 1.4 P2.7: 初始化 UsageTracker（成本可观测基础设施）──
    # 修复关键缺陷：BaseOpenAIClient._record_usage 已就位但单例从未初始化，
    # 导致 usage.* RPC 全部空跑。这里注入单例并启动后台告警循环。
    try:
        from ai.usage_tracker import init_usage_tracker_from_context

        tracker = init_usage_tracker_from_context(context)
        # 启动后台预算告警循环（每 5 分钟检查一次）；start_alert_loop 是同步方法，
        # 内部用 asyncio.get_running_loop() 创建 task，需在事件循环内调用
        tracker.start_alert_loop()
        logger.info("UsageTracker initialized and alert loop started")
    except Exception as exc:
        logger.warning("UsageTracker initialization failed (non-critical): %s", exc, exc_info=True)

    # ── 1.4b P2.9: 初始化 NotificationDispatcher（统一通知推送通道）──
    # 注入 context.notification_dispatcher 单例并订阅 EventBus 事件。
    # handler 通过 _get_dispatcher(context) 优先使用此单例，缺失时懒构建。
    try:
        from services.notification_dispatcher import NotificationDispatcher

        # local_data_store 由 AppContextBuilder.build_data_store 初始化，必然存在；
        # 此前 `or getattr(context, "data_store", None)` 是死代码（context 无 data_store 属性）。
        data_store = context.local_data_store
        if data_store is not None:
            dispatcher = NotificationDispatcher(
                data_store=data_store,
                settings_repository=context.settings_repository,
                event_bus=getattr(context, "event_bus", None),
            )
            dispatcher.start()
            # 显式注入到 context，避免 handler 路径懒构建
            setattr(context, "notification_dispatcher", dispatcher)
            logger.info("NotificationDispatcher initialized and subscribed %d events", len(getattr(dispatcher, "_subscriptions", [])))
    except Exception as exc:
        logger.warning("NotificationDispatcher initialization failed (non-critical): %s", exc, exc_info=True)

    # ── 1.4c P3.2: 显式注入 FeedbackService（P2.8 用户反馈学习闭环）──
    # 修复调研发现的 DI 不一致：_chat_context_anchor._preference_summary() 之前
    # 在函数内 import + 构造 FeedbackService，违反分层。这里统一注入到 context。
    try:
        from services.feedback_service import FeedbackService

        # local_data_store 由 AppContextBuilder.build_data_store 初始化，必然存在；
        # 此前 `or getattr(context, "data_store", None)` 是死代码（context 无 data_store 属性）。
        data_store = context.local_data_store
        if data_store is not None:
            context.feedback_service = FeedbackService(
                data_store=data_store,
                settings_repository=context.settings_repository,
                ai_client=getattr(context, "ai_client", None),
                event_bus=getattr(context, "event_bus", None),
                ai_text_service=getattr(context, "ai_text_service", None),
            )
            logger.info("FeedbackService initialized")
    except Exception as exc:
        logger.warning("FeedbackService initialization failed (non-critical): %s", exc, exc_info=True)

    # ── 1.4d M1: 显式注入 AgentGateway（执行内核可替换端口）──
    # 功能路径经 context.agent_gateway 提交 agent 任务；具体内核由 settings
    # 键 agent_kernel.type 选择（dsh/claude_code/codex/none/auto），换内核零改动。
    # 见 docs/kernel-layering-design-2026-08-15.md。
    try:
        from services.agent_gateway import build_gateway_from_repository

        context.agent_gateway = build_gateway_from_repository(context.settings_repository)
        logger.info("AgentGateway injected: kernel=%s", getattr(context.agent_gateway, "name", "?"))
    except Exception as exc:
        logger.warning("AgentGateway injection failed (non-critical): %s", exc, exc_info=True)

    # ── 1.4e M1.5: 注入壳内 AI 端口（稳定区与 ai_client 唯一接缝）──
    # services/desktop/memory 经 context.ai_text_service 调用（不再直调 chat_completion）。
    try:
        from services.ai_text_service import LegacyLlmTextService

        setattr(context, "ai_text_service", LegacyLlmTextService(context.ai_client))
        logger.info("AiTextService injected: available=%s", getattr(context.ai_client, "is_available", lambda: None)())
    except Exception as exc:
        logger.warning("AiTextService injection failed (non-critical): %s", exc, exc_info=True)

    # ── 1.5 设置迁移：确保版本升级时设置兼容 ──
    try:
        from core.settings_migration import migrate_settings_repository

        migration_result = migrate_settings_repository(context.settings_repository)
        if migration_result.get("changed"):
            logger.info("settings migrated to v%s (changes applied)", migration_result.get("version"))
        else:
            logger.debug("settings migration: no changes needed (v%s)", migration_result.get("version"))
    except Exception as exc:
        logger.warning("settings migration failed (non-critical): %s", exc, exc_info=True)

    # ── 2. 设置方法注册 + 事件桥接 + WebSocket ──
    # 频率限制：每客户端 60 秒内最多 200 次请求
    from core.rate_limiter import RateLimiter

    rate_limiter = RateLimiter(max_requests=200, window=60.0, cleanup_interval=300.0)

    registry = MethodRegistry(rate_limiter=rate_limiter)
    bridge = EventBridge()

    # ── 2.5 S15: 初始化操作审计日志（AuditTrail + WriteGuard + RollbackManager）──
    # 挂到 context 上，供各 handler 闭包通过 context.write_guard / context.audit_trail 访问
    try:
        from core.audit_rollback import AuditTrail, RollbackManager, WriteGuard

        audit_trail = AuditTrail(
            data_store=context.local_data_store,
            observability_service=getattr(context, "observability_service", None),
        )
        setattr(context, "audit_trail", audit_trail)
        setattr(
            context,
            "write_guard",
            WriteGuard(
                audit_trail=audit_trail,
                observability_service=getattr(context, "observability_service", None),
            ),
        )
        setattr(
            context,
            "rollback_manager",
            RollbackManager(
                data_store=context.local_data_store,
                audit_trail=audit_trail,
                note_repository=getattr(context, "personal_note_repository", None),
                observability_service=getattr(context, "observability_service", None),
            ),
        )
        logger.info("audit trail initialized (S15)")
    except Exception as exc:
        # 审计日志初始化失败不应阻止后端启动，仅记录警告
        logger.warning("audit trail init failed (non-critical): %s", exc, exc_info=True)

    # ── 2.6 P1-2: 统一执行观测（TraceRecorder） ──
    try:
        from services.trace_recorder import TraceRecorder

        _ds = context.local_data_store
        _data_dir = getattr(_ds, "base_dir", None) or getattr(_ds, "data_dir", None)
        if _data_dir:
            recorder = TraceRecorder(os.path.join(str(_data_dir), "trace.db"))
            setattr(context, "trace_recorder", recorder)
            recorder.subscribe(event_bus)
            cron = getattr(context, "cron_service", None)
            if cron is not None and hasattr(cron, "set_trace_recorder"):
                cron.set_trace_recorder(recorder)
            # AG-C1①：启动即收敛悬挂 run——上一进程被强杀/崩溃时 exec.step 收尾
            # 事件丢失，这些 run 会以 running 无限滞留。超龄阈值 6h（默认）内的
            # 在跑任务不受影响；本调用只动本机 trace.db，不触碰 data/data 迁移域。
            stale = recorder.reconcile_stale_runs()
            if stale:
                logger.info("trace 悬挂收敛：超龄 running → interrupted 共 %d 条 (P1-2)", stale)
            logger.info("trace recorder initialized (P1-2)")
    except Exception as exc:
        logger.warning("trace recorder init failed (non-critical): %s", exc, exc_info=True)

    # ── AG-C1③: 沙箱审批历史 kv 后端注入 ──
    # 拿不到 local_data_store 就跳过（SandboxManager 自动退化为进程内环形），不报错。
    try:
        from services.sandbox_manager import get_sandbox_manager

        _sb_ds = getattr(context, "local_data_store", None)
        if _sb_ds is not None:
            get_sandbox_manager().set_history_store(_sb_ds)
            logger.info("sandbox approval history store injected (AG-C1)")
    except Exception as exc:
        logger.warning("sandbox history store inject failed (non-critical): %s", exc, exc_info=True)

    # ── P2-C/P0-D（2026-09-18 取证）：注入沙箱工具执行器 ──
    # 事故：ReAct 引擎把破坏性工具 add 成 kind="tool" 的 pending，用户
    # sandbox.approve 后走 mgr.approve → `if self._tool_executor`。全仓 set_tool_executor
    # 仅测试调用、生产从未注入 → 落到无 executor 分支，弹条目+记 "approved"+返回
    # success=True，但动作**从未执行** = 假成功（用户见"已批准"，笔记其实没删）。
    # 注入 make_react_executor(context)：与被拦截时同一个执行器，在 _in_approval_var
    # 上下文内重放即真正落地（与 RPC 路径 registry.dispatch(skip_approval=True) 对称）。
    try:
        from services.react_executor import make_react_executor

        get_sandbox_manager().set_tool_executor(make_react_executor(context))
        logger.info("sandbox tool executor injected (P2-C)")
    except Exception as exc:
        logger.warning("sandbox tool executor inject failed (non-critical): %s", exc, exc_info=True)

    await register_methods(registry, context, ws_port=WS_PORT)

    # ── 2.7 批次5 基建9：启动自检（反射扫描接缝一致性，fail-fast）──
    # 捕获事件总线旧式双参 emit / handler 模块导入断裂等回归，启动即红，
    # 避免 P1-8/P1-9 同类「方法名/契约漂移」再次静默漏网。SIYI_SKIP_SELFCHECK=1 仅紧急逃生。
    try:
        from backend.startup_selfcheck import run_startup_selfcheck

        _selfcheck = run_startup_selfcheck()
        logger.info(
            "启动自检完成：handler 导入 %d 成功，emit 违例 %d，context 接线告警 %d",
            _selfcheck.get("handler_import_ok", 0),
            len(_selfcheck.get("emit_violations", [])),
            len(_selfcheck.get("context_wiring_warnings", [])),
        )
    except Exception as exc:  # SelfCheckError 等
        logger.error("启动自检失败，阻断启动：%s", exc)
        sys.exit(7)

    # ── V2.1 批次1#1：认证 token 持久化与启用 ──
    # P0-1 CSWSH 修复：显式选择非回环绑定（LAN/Tailscale）时必须认证可用，
    # 否则 security_fail_closed_gate 拒启（N-D fail-closed 守卫）。
    # 数据根 data_dir 解析：headless_conf_auth 依据 local_data_store.base_dir。
    _auth_data_dir = None
    _ds0 = getattr(context, "local_data_store", None)
    _auth_data_dir = getattr(_ds0, "base_dir", None) or getattr(_ds0, "data_dir", None)
    auth_token = None
    auth_token_store = None
    if _auth_data_dir:
        try:
            from backend import auth_token_store as _ats

            auth_token, created = _ats.load_or_create(_auth_data_dir)
            auth_token_store = _auth_data_dir
            if created:
                logger.info("Generated new auth token at %s", _ats.get_token_path(_auth_data_dir))
        except Exception as exc:
            logger.warning("auth token init failed (authentication may be limited): %s", exc)
    AUTH_ENABLED_BY_DEFAULT = os.environ.get("SIYI_WS_AUTH_ENABLED", "1") == "1"
    _auth_effective = AUTH_ENABLED_BY_DEFAULT and bool(auth_token)
    # N-D fail-closed：非回环绑定 + 认证不可用 → 拒启，不再静默降级
    security_fail_closed_gate(WS_HOST, _auth_effective)

    # ── 对外 LLM 中继网关（OpenAI / Anthropic 兼容端点）──
    # 复用 context.ai_client._inner（FallbackOpenAIClient）：对外 agent 调用
    # 不注入"小问"persona、也不被隐私分类长期路由到本地腿，仅复用池/回退/用量统计。
    relay_gateway = None
    try:
        from backend.relay_gateway import RelayGateway
        from core.settings_keys import KEY_RELAY_API_KEY, KEY_RELAY_MODEL_MAP
        from core.env_loader import mask_secret
        import json as _json
        import secrets as _secrets

        _repo = context.settings_repository
        relay_key = str(_repo.get_setting(KEY_RELAY_API_KEY, "") or "").strip() if _repo else ""
        if not relay_key:
            relay_key = os.environ.get("SIYI_RELAY_API_KEY", "").strip()
        if not relay_key:
            relay_key = _secrets.token_urlsafe(32)
            if _repo is not None:
                try:
                    _repo.set_setting(KEY_RELAY_API_KEY, relay_key)
                except Exception as _exc:
                    logger.warning("relay key persist failed: %s", _exc)
            logger.warning("Relay API key auto-generated— 请复制使用: %s", relay_key)
        model_map = _repo.get_setting(KEY_RELAY_MODEL_MAP, {}) if _repo else {}
        if isinstance(model_map, str):
            try:
                model_map = _json.loads(model_map)
            except (TypeError, ValueError) as _exc:
                logger.warning("relay_model_map parse failed: %s", _exc)
                model_map = {}
        if not isinstance(model_map, dict):
            model_map = {}
        _inner = getattr(context.ai_client, "_inner", None)
        if _inner is None:
            logger.warning("relay gateway skipped: ai_client unavailable")
        else:
            relay_gateway = RelayGateway(_inner, relay_key, model_map, settings_repository=_repo)
            logger.info("Relay gateway enabled, relay key prefix=%s", mask_secret(relay_key))
    except Exception as exc:
        relay_gateway = None
        logger.warning("relay gateway init failed (non-critical): %s", exc)

    server = WebSocketServer(
        registry,
        bridge,
        host=WS_HOST,
        port=WS_PORT,
        auth_token=auth_token if auth_token else None,
        auth_enabled=_auth_effective,
        auth_token_store=auth_token_store,
        relay=relay_gateway,
    )

    # ── V2.1 批次1#1：auth.rotate / auth.revoke RPC（token 生命周期）──
    # P2 修复（rpc-census-2026-08-30）：主体抽到模块级 auth_rotate_impl——
    # 先返回响应、延迟踢连接（见模块级注释）；空参 {} 是本方法的合法调用
    # （无参数），不再随响应一起被 close_all 吞掉。
    async def _rpc_auth_rotate(**kwargs: Any) -> dict[str, Any]:
        return await auth_rotate_impl(server)

    async def _rpc_auth_revoke(rotate: bool = True, **kwargs: Any) -> dict[str, Any]:
        # P1-1 修复（同 auth.rotate）：主体抽到模块级 auth_revoke_impl——
        # 先返回响应、延迟踢连接，close_all 不再吞掉本次 RPC 响应。
        return await auth_revoke_impl(server, rotate=rotate)

    registry.register("auth.rotate", _rpc_auth_rotate)
    # V2.1 批次2：auth.revoke 吊销所有连接+主 token，属破坏性操作 → R4 强制审批
    registry.register("auth.revoke", _rpc_auth_revoke, risk="R4")

    # ── 对外 LLM 中继网关：状态 / 重新生成 key（前端配置界面用）──
    if relay_gateway is not None:
        from core.env_loader import mask_secret as _mask_secret
        from core.settings_keys import KEY_RELAY_API_KEY
        import secrets as _secrets

        _relay_base = f"http://{WS_HOST}:{WS_PORT}"
        _relay_active_key = str(getattr(relay_gateway, "_relay_key", "") or "")
        _relay_active_map = dict(getattr(relay_gateway, "_model_map", None) or {})

        async def _rpc_relay_status(**kwargs: Any) -> dict[str, Any]:
            return {
                "enabled": True,
                "has_key": bool(_relay_active_key),
                "api_key_masked": _mask_secret(_relay_active_key) if _relay_active_key else "",
                "model_map": _relay_active_map,
                "base_url": _relay_base,
                "endpoints": {
                    "openai_chat": f"{_relay_base}/v1/chat/completions",
                    "anthropic_messages": f"{_relay_base}/v1/messages",
                },
            }

        registry.register("relay.status", _rpc_relay_status)

        async def _rpc_relay_regenerate_key(**kwargs: Any) -> dict[str, Any]:
            _new_key = _secrets.token_urlsafe(32)
            _repo = context.settings_repository
            if _repo is not None:
                await asyncio.to_thread(_repo.set_setting, KEY_RELAY_API_KEY, _new_key)
            return {
                "relay_api_key": _new_key,
                "masked": _mask_secret(_new_key),
                "restart_required": True,
            }

        registry.register("relay.regenerate_key", _rpc_relay_regenerate_key)

    # ── P1-4：配对握手 RPC（需鉴权；桌面 loopback 经 /auth 引导后可调用）──
    async def _rpc_pairing_pending(**kwargs: Any) -> dict[str, Any]:
        try:
            server._cleanup_expired_pairing()
            pending = [
                {
                    "request_id": rid,
                    "device_name": e["device_name"],
                    "device_id": e["device_id"],
                    "created": e["created"],
                }
                for rid, e in server._pairing_requests.items()
                if e["state"] == "pending"
            ]
            return {"success": True, "pending": pending}
        except Exception as exc:
            logger.warning("pairing.pending failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def _rpc_pairing_approve(request_id: str = "", **kwargs: Any) -> dict[str, Any]:
        try:
            entry = server._pairing_requests.get(request_id)
            if not entry or entry["state"] != "pending":
                return {"success": False, "error": "no pending pairing request"}
            entry["state"] = "approved"
            entry["token"] = server.get_auth_token()  # 下发主 token 给已批准设备
            logger.info("Pairing approved: %s (%s)", request_id, entry["device_name"])
            return {"success": True, "token": entry["token"]}
        except Exception as exc:
            logger.warning("pairing.approve failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("pairing.pending", _rpc_pairing_pending)
    registry.register("pairing.approve", _rpc_pairing_approve)

    # ── V2.1 批次1#5：通电自检断言（安全态势摘要）──
    _acl_ok = None  # None=由数据目录缺失等原因跳过；bool=ACL 检查结果
    try:
        if auth_token_store:
            from backend import auth_token_store as _ats

            _acl_ok = _ats.acl_is_hardened(_ats.get_token_path(auth_token_store)) if bool(auth_token) else False
    except Exception as exc:
        logger.debug("ACL self-check skipped: %s", exc)
    if not getattr(server, "auth_enabled", False):
        # B-12：实际行为是「继续启动 + 由 _verify_auth 非 loopback 拒绝兜底」。
        # N-D（2026-09-17）：security_fail_closed_gate 已保证此分支只在回环
        # 绑定下可达——非回环 + 无认证的组合在更早处拒启（exit 8），
        # 「仅本机可访问」是本告警成立的硬前提。
        logger.warning(
            "SECURITY POSTURE: AUTH DISABLED — serving loopback-only (%s) + origin whitelist. "
            "Non-localhost connections are impossible here: this bind is loopback "
            "(non-loopback binds refuse to start without auth). "
            "Set SIYI_WS_HOST to a LAN/Tailscale address only after fixing the auth token "
            "for LAN/remote access.",
            WS_HOST,
        )
    else:
        logger.info(
            "SECURITY POSTURE: auth=ON bind=%s:%d origin=whitelist token_persisted=%s acl_hardened=%s",
            WS_HOST,
            WS_PORT,
            bool(auth_token),
            "N/A" if _acl_ok is None else str(_acl_ok),
        )

    # ── 3. 启动 ──
    bridge.start_bridging()
    # B4 修复：将当前事件循环注册到 bridge，以便 flush 缓存事件
    bridge.set_loop(asyncio.get_running_loop())

    # 审批补发 watcher：弥合 MCP 内核审批（siyi_confirm_request 只写共享文件、
    # 不发射事件）与前端 action.confirm_required 订阅之间的跨进程缺口。
    confirmation_watcher = None
    try:
        from services.confirmation_store import get_confirmation_store
        from backend.confirmation_watcher import ConfirmationWatcher

        confirmation_watcher = ConfirmationWatcher(
            get_confirmation_store(),
            getattr(context, "event_bus", None),
        )
        confirmation_watcher.start()
    except Exception as exc:
        logger.warning("confirmation watcher start failed (non-critical): %s", exc)

    # V1 修复（2026-09-16）：启动 provider 健康监控后台循环。
    # 此前 ProviderHealthMonitor.start() 全仓无人调用 → 后台周期检查从不运行，
    # "模型红绿灯"面板只在手动 check_now 或重启恢复时才更新 —— 属"机制建成却未启用"。
    # 前置：_get_or_create_monitor 的构造参数错位已修（见 backend/handlers/_provider_health.py）。
    try:
        from backend.handlers._provider_health import _get_or_create_monitor

        _ph_monitor = await _get_or_create_monitor(context)
        if _ph_monitor is not None:
            await _ph_monitor.start()  # 内部 create_task，不阻塞启动链
            logger.info(
                "provider health monitor started (check_interval=%ss)",
                getattr(_ph_monitor, "check_interval", "?"),
            )
    except Exception as exc:
        logger.warning("provider health monitor start failed (non-critical): %s", exc)

    # P1-1: 启动后扫描 checkpoint 可续跑任务，经 task.resumable 事件通知前端
    try:
        autonomy = getattr(context, "autonomous_assistant_manager", None)
        if autonomy is not None and hasattr(autonomy, "scan_unfinished_tasks"):
            unfinished = await asyncio.to_thread(autonomy.scan_unfinished_tasks)
            if unfinished:
                event_bus.emit(
                    AppEvent(
                        "task.resumable",
                        payload={
                            "count": len(unfinished),
                            "tasks": [{"task_id": str(u.get("task_id", "")), "state": str(u.get("state", ""))} for u in unfinished[:20]],
                        },
                        source="headless_main",
                    )
                )
                logger.info("found %d resumable task(s) after startup (P1-1)", len(unfinished))
    except Exception as exc:
        logger.warning("resumable task scan failed (non-critical): %s", exc, exc_info=True)

    # 启动浏览器助手桥接服务（供浏览器扩展调用）
    # 传入 WS_PORT 让 bridge 自动避开端口冲突
    try:
        context.start_browser_assistant_bridge(avoid_port=WS_PORT)
        logger.info("browser assistant bridge started")
    except Exception:
        logger.exception("browser assistant bridge start failed (non-critical)")

    # ── 3.5 启动后台资讯定时采集 ──
    # P1-3 修复：之前只调 set_collector + restart()，但 info_scheduler._enabled
    # 默认 False，restart() 在 _enabled=False 时不会创建任务 → 调度器永不启动。
    # 现在从 settings 读取配置（默认 enabled=True, interval=3600s），并调 configure()
    # 以保证 _enabled 被正确设置。
    from services.info_collector_service import InfoCollectorService

    info_scheduler.set_collector(
        InfoCollectorService(
            ai_client=context.ai_client,
            info_item_repository=getattr(context, "info_item_repository", None),
            settings_repository=getattr(context, "settings_repository", None),
            # R2-A1：共享资讯源注册表，使定时采集与 info.sources.* RPC 配置联动
            source_registry=getattr(context, "info_source_registry", None),
        )
    )
    info_scheduler.set_context(context)  # P2: 用于定时抓取后推送收件箱
    # 持久化接线（2026-10-16）：configure 落盘到 info_scheduler_* 键，
    # 使前端开关（info.auto_config）跨后端重启不丢失
    info_scheduler.set_settings(context.settings_repository)

    # P4: 从 settings 恢复分类关键词与用户自定义板块
    settings = context.settings_repository
    if settings:
        _section_kw = settings.get_setting("info_section_keywords", None)
        if isinstance(_section_kw, dict) and _section_kw:
            from services.info_collector_service import set_section_keywords as _sk_set

            _sk_set(_section_kw)
        _user_sections = settings.get_setting("user_info_sections", None)
        if isinstance(_user_sections, list) and _user_sections:
            from services.info_constants import set_user_sections as _us_set

            _us_set(_user_sections)

    _info_enabled = bool(settings.get_setting("info_scheduler_enabled", True)) if settings else True
    _info_interval = int(settings.get_setting("info_scheduler_interval", 3600)) if settings else 3600
    _info_topics_raw = settings.get_setting("info_scheduler_topics", None) if settings else None
    _info_topics = _info_topics_raw if isinstance(_info_topics_raw, list) else None
    info_scheduler.configure(
        # SIYI_TEST_OFFLINE=1：测试环境跳过后台资讯采集（RSS 真实抓取）
        enabled=_info_enabled and not TEST_OFFLINE,
        interval=_info_interval,
        topics=_info_topics,
    )
    # ──

    # ── 3.6 启动单词复习心情联动 ──
    from backend.vocab_mood import vocab_mood_coordinator

    vocab_mood_coordinator.start()
    # ──

    # ── 3.7 启动单词到期提醒（每天首次启动主动提示）──
    from backend.due_reminder import due_reminder_scheduler

    due_reminder_scheduler.start(context)
    # ──

    # ── 3.8 启动云同步（Cloudflare Worker，与手机端协议兼容）──
    # 仅当用户在 settings 中显式启用且配置完整时才会启动；否则跳过。
    sync_bridge = None
    try:
        from sync.cloud_sync_client import SyncBridge

        sync_bridge = SyncBridge(context)
        # 波次④包 C（§9-4 双开互斥守卫显式化）：登记 B 栈启动态（判定资源集 =
        # notes/todos/settings，_cloud_sync_crypto RESOURCE_*；「开」= cloud_sync_enabled）。
        # 与 A 栈（_tooling_provider.py 构造点）登记合流后，两栈对同一 canonical
        # 资源同开 → 启动 WARN + 诊断标记；可同开（产品决策不变）、但必告警。
        try:
            from api.sync_orchestrator import CLOUD_BRIDGE_SYNC_RESOURCES, STACK_CLOUD_BRIDGE, note_stack_launch

            note_stack_launch(
                STACK_CLOUD_BRIDGE,
                CLOUD_BRIDGE_SYNC_RESOURCES,
                enabled=sync_bridge.is_enabled(),
            )
        except Exception:
            logger.debug("dual-stack launch guard skipped", exc_info=True)
        # 附加到 context，供 _sync handler 访问
        setattr(context, "cloud_sync_bridge", sync_bridge)
        await sync_bridge.start()
    except Exception:
        logger.exception("cloud sync bridge start failed (non-critical)")
    # ──

    # ── 3.9 启动周报生成器（每周一 09:00 CST 自动生成周报投递到 inbox）──
    try:
        from backend import weekly_report_scheduler

        weekly_report_scheduler.set_context(context)
        _weekly_enabled = bool(settings.get_setting("weekly_report_enabled", True)) if settings else True
        weekly_report_scheduler.configure(enabled=_weekly_enabled)
        weekly_report_scheduler.restart()
        logger.info("weekly report scheduler started (enabled=%s)", _weekly_enabled)
    except Exception:
        logger.debug("weekly report scheduler start failed (non-critical)", exc_info=True)
    # ──

    # ── 3.9a 启动日报生成器（F-3：每天配置时间生成日报投递到 inbox）──
    try:
        from backend import weekly_report_scheduler as _report_scheduler

        _report_scheduler.set_context(context)
        _daily_enabled = bool(settings.get_setting("daily_summary_enabled", False)) if settings else False
        _daily_time = str(settings.get_setting("daily_summary_time", "09:00")) if settings else "09:00"
        _report_scheduler.configure_daily(enabled=_daily_enabled, time=_daily_time)
        logger.info("daily summary scheduler started (enabled=%s, time=%s)", _daily_enabled, _daily_time)
    except Exception:
        logger.debug("daily summary scheduler start failed (non-critical)", exc_info=True)
    # ──

    # ── 3.9b 启动个人画像自动提取（每日扫描未处理 raw items → 画像）──
    try:
        from backend import personal_profile_scheduler

        personal_profile_scheduler.set_context(context)
        _pp_enabled = bool(settings.get_setting("personal_profile_scheduler_enabled", True)) if settings else True
        _pp_time = str(settings.get_setting("personal_profile_scheduler_time", "02:00")) if settings else "02:00"
        personal_profile_scheduler.configure(enabled=_pp_enabled, time=_pp_time)
        logger.info("personal profile scheduler started (enabled=%s, time=%s)", _pp_enabled, _pp_time)
    except Exception:
        logger.debug("personal profile scheduler start failed (non-critical)", exc_info=True)
    # ──

    # ── 3.9c 启动周度叙事生成调度器（"你的轨迹"）──
    try:
        from backend import narrative_scheduler

        narrative_scheduler.set_context(context)
        _nar_enabled = bool(settings.get_setting("narrative_scheduler_enabled", True)) if settings else True
        narrative_scheduler.configure(enabled=_nar_enabled)
        logger.info("narrative scheduler started (enabled=%s)", _nar_enabled)
    except Exception:
        logger.debug("narrative scheduler start failed (non-critical)", exc_info=True)
    # ──

    # ── 3.9d 启动行为日志归档调度器（T5.3：每日 180 天滚动归档）──
    try:
        from backend import behavior_archive_scheduler

        behavior_archive_scheduler.set_context(context)
        _ba_enabled = bool(settings.get_setting("behavior_archive_enabled", True)) if settings else True
        behavior_archive_scheduler.configure(enabled=_ba_enabled)
        logger.info("behavior archive scheduler started (enabled=%s)", _ba_enabled)
    except Exception:
        logger.debug("behavior archive scheduler start failed (non-critical)", exc_info=True)
    # ──

    # ── 3.10 启动求职猎手定时调度器 ──
    try:
        from backend import job_hunter_scheduler

        job_hunter_scheduler.set_service(context.job_hunter_service)
        _jh_enabled = bool(settings.get_setting("job_hunter_scheduler_enabled", False)) if settings else False
        _jh_interval = int(settings.get_setting("job_hunter_scheduler_interval", 6 * 3600)) if settings else 6 * 3600
        _jh_keywords = settings.get_setting("job_hunter_keywords", None) if settings else None
        _jh_city = str(settings.get_setting("job_hunter_city", "")) if settings else ""
        _jh_platforms = settings.get_setting("job_hunter_platforms", None) if settings else None
        job_hunter_scheduler.configure(
            enabled=_jh_enabled,
            interval=_jh_interval,
            keywords=_jh_keywords if isinstance(_jh_keywords, list) else None,
            city=_jh_city,
            platforms=_jh_platforms if isinstance(_jh_platforms, list) else None,
        )
        logger.info("job hunter scheduler started (enabled=%s)", _jh_enabled)
    except Exception:
        logger.debug("job hunter scheduler start failed (non-critical)", exc_info=True)
    # ──

    await server.start()

    # ── 3.11 启动持久任务队列 + Worker（P1.2）──
    # 把一次性 retryable 工作（资讯抓取、URL 学习、外部 API 调用等）从散落的
    # asyncio.create_task 收敛到 SQLite 持久队列：进程崩溃后未完成任务自动恢复，
    # 失败按指数退避重试，超过 max_retries 进入 dead_letter。
    # Worker executor 接 ActionBroker：所有副作用操作经统一闸门审批 + 产出 receipt。
    task_queue_worker = None
    task_queue_workers: list = []
    task_queue = None
    try:
        from services.task_queue import PersistentTaskQueue, TaskQueueWorker, TaskRecord
        from services.action_broker import ActionRequest

        ds = getattr(context, "local_data_store", None)
        base_dir = getattr(ds, "base_dir", None) if ds else None
        if base_dir:
            tq_db_path = os.path.join(base_dir, "task_queue.db")
            task_queue = PersistentTaskQueue(db_path=tq_db_path)
            setattr(context, "task_queue", task_queue)  # 供 _task_queue handler 访问

            # 个人知识体系底座：raw items 存储（L2 统一信息捕获）
            try:
                from repositories._raw_item import RawItemRepository

                setattr(context, "raw_item_repo", RawItemRepository(db_path=os.path.join(base_dir, "raw_items.db")))
                logger.info("raw_item_repo 就绪 (db=raw_items.db)")
            except Exception:
                logger.exception("raw_item_repo 初始化失败（非致命，personal.* 将不可用）")

            # P0.1: 将 TaskQueue 注入 CronService，使所有定时任务走持久队列
            if hasattr(context, "cron_service") and context.cron_service is not None:
                context.cron_service.set_task_queue(task_queue)
                logger.info("CronService 已接入 TaskQueue（headless_main 注入）")

            # executor：通过 ActionBroker 走统一闸门
            # P0-fix-3: 真正注入 action_broker 到 context —— 优先复用
            # autonomous_assistant_manager.router 自建的 broker（capability_router 在
            # capability_registry 可用时会自动创建），避免重复构造。
            broker = getattr(context, "action_broker", None)
            if broker is None:
                _am = getattr(context, "autonomous_assistant_manager", None)
                _router = getattr(_am, "router", None) if _am is not None else None
                if _router is not None:
                    broker = getattr(_router, "action_broker", None)
                if broker is not None:
                    setattr(context, "action_broker", broker)
                    logger.info("ActionBroker 已注入 context（复用 router.action_broker）")
                else:
                    logger.warning("action_broker 未注入：router.action_broker 不可用，走降级路径")

            def _task_executor(task: "TaskRecord") -> dict:
                if broker is not None:
                    req = ActionRequest(
                        capability_id=task.capability_id,
                        input=dict(task.payload),
                        source="task_queue",
                    )
                    result = broker.execute_sync(req)
                    return {
                        "success": bool(result.success),
                        "receipt_id": result.receipt_id,
                        "error": result.error or result.blocked_reason,
                    }
                # 降级：复用 cron_service 的 executor（直接调 RPC handler）
                cron_executor = getattr(context, "_execute_cron_action", None)
                if cron_executor is not None:
                    try:
                        out = cron_executor(task.capability_id, dict(task.payload)) or {}
                        error_str = str(out.get("error", ""))
                        blocked = bool(out.get("blocked")) or "blocked" in error_str.lower()
                        return {
                            "success": not bool(out.get("error")),
                            "blocked": blocked,
                            "error": error_str,
                        }
                    except Exception as exc:
                        exc_str = str(exc)
                        return {
                            "success": False,
                            "blocked": "blocked" in exc_str.lower(),
                            "error": exc_str,
                        }
                return {"success": False, "error": "no executor available (action_broker and _execute_cron_action both missing)"}

            # ── cron 队列专用 executor ──
            # 与 _task_executor 相反：cron 任务优先走 _execute_cron_action（cron_actions
            # 注册表，含 kernel.submit / 技能卡 / info.collect 等 cron 专用 action），
            # ActionBroker 仅作兜底。修"cron action 不在 broker 白名单 → 全走进死信"。
            def _cron_task_executor(task: "TaskRecord") -> dict:
                cron_executor = getattr(context, "_execute_cron_action", None)
                if cron_executor is not None:
                    try:
                        out = cron_executor(task.capability_id, dict(task.payload)) or {}
                    except Exception as exc:
                        exc_str = str(exc)
                        return {
                            "success": False,
                            "blocked": "blocked" in exc_str.lower(),
                            "error": exc_str,
                        }
                    out = dict(out)
                    status = str(out.get("status", "")).strip().lower()
                    explicit_success = out.get("success")
                    err = out.get("error")
                    if isinstance(explicit_success, bool):
                        ok = explicit_success
                    elif status in ("completed", "success", "succeeded"):
                        ok = True
                    elif status in ("failed", "error"):
                        ok = False
                    else:
                        ok = not bool(err)
                    # skipped（如 capability.run 需审批 fail-fast）的处置原因在
                    # reason 字段而非 error——透传到通知正文，否则"定时任务重试
                    # 耗尽"通知空泛，用户不知道该去点「始终允许」（2026-09-11）。
                    # D-2 修复（2026-09-16）：扩为"任何失败且 error 缺失/空白"——
                    # 按 reason→hint→status→占位逐级兜底，杜绝再产生 last_error
                    # 空白的死信（生产实测 12 条：subagent.spawn/journal.daily_draft）。
                    if not ok and not (str(err).strip() if err else ""):
                        err = out.get("reason") or out.get("hint") or (status or "unknown_failure")
                    error_str = str(err) if err else ""
                    return {
                        "success": ok,
                        "blocked": bool(out.get("blocked")) or "blocked" in error_str.lower(),
                        "error": error_str,
                    }
                # 兜底：cron_executor 不可用时才走 ActionBroker（极少见）
                if broker is not None:
                    try:
                        req = ActionRequest(
                            capability_id=task.capability_id,
                            input=dict(task.payload),
                            source="task_queue",
                        )
                        result = broker.execute_sync(req)
                        return {
                            "success": bool(result.success),
                            "receipt_id": result.receipt_id,
                            "error": result.error or result.blocked_reason,
                        }
                    except Exception as exc:
                        exc_str = str(exc)
                        return {
                            "success": False,
                            "blocked": "blocked" in exc_str.lower(),
                            "error": exc_str,
                        }
                return {"success": False, "error": "no cron executor available"}

            task_queue_worker = TaskQueueWorker(
                task_queue,
                executor=_task_executor,
                queue_name="default",
                poll_interval=0.5,
                lease_seconds=360.0,
                reaper_interval=60.0,
            )
            await task_queue_worker.start()
            logger.info("task_queue worker started (db=%s)", tq_db_path)

            # P0-fix-1: 启动 cron 队列 worker —— cron_service 入队到 queue_name="cron"，
            # 必须由独立 worker 消费（default worker 只消费 default 队列），否则定时任务永不执行。
            # AC-2（2026-09-16）：on_task_final 把 worker 真终态（completed/dead_letter）
            # 回写到 cron_service —— 翻转 dispatch 入队时记的 dispatched 历史占位，
            # 使 cron.history 能看到真实执行结果；非 cron 任务由索引自然过滤。
            # 回退开关：SIYI_CRON_QUEUE_WRITEBACK=0 关闭回写（退回"历史停在
            # dispatched"的旧行为）。回写属观测旁路，异常时可一键降级，不影响执行。
            _writeback_on = os.environ.get("SIYI_CRON_QUEUE_WRITEBACK", "1").strip() != "0"
            cron_svc_for_writeback = getattr(context, "cron_service", None) if _writeback_on else None
            cron_writeback_cb = (
                (lambda tid, final_status, error: cron_svc_for_writeback.on_queue_task_final(tid, final_status=final_status, error=error))
                if cron_svc_for_writeback is not None
                else None
            )
            cron_queue_worker = TaskQueueWorker(
                task_queue,
                executor=_cron_task_executor,
                queue_name="cron",
                poll_interval=0.5,
                # M-cron：内核路径（dsh agent）天然较慢，超时预算需覆盖
                # kernel(≤240s) + fallback(≤90s)；lease 必须 > executor_timeout
                lease_seconds=660.0,
                executor_timeout=600.0,
                reaper_interval=60.0,
                on_task_final=cron_writeback_cb,
            )
            await cron_queue_worker.start()
            logger.info("cron task_queue worker started (db=%s)", tq_db_path)
            task_queue_workers = [task_queue_worker, cron_queue_worker]
        else:
            logger.warning("task_queue skipped: local_data_store.base_dir unavailable")
    except Exception:
        logger.exception("task_queue worker start failed (non-critical)")

    # CronService 必须在 TaskQueue worker 就绪后启动，避免任务入队后无人消费。
    try:
        cron_status = context.start_cron_service()
        if not cron_status.get("running"):
            raise RuntimeError(f"CronService failed to start: {cron_status}")
        logger.info("CronService started in headless mode: %s", cron_status)
    except Exception:
        logger.exception("CronService start failed")
        for _worker in task_queue_workers:
            try:
                await _worker.stop(timeout=1.0)
            except Exception:
                logger.debug("task_queue worker startup cleanup failed", exc_info=True)
        if task_queue is not None:
            try:
                task_queue.close_all()
            except Exception:
                logger.debug("task_queue startup cleanup failed", exc_info=True)
        raise

    # ──

    # ── 3.12 启动 autonomy 调度驱动循环（P0-fix-5）──
    # 委托链：autonomy.run_due_schedules() → scheduler.run_due_schedules()
    # → events.collect_due_events() + planner + task_lifecycle.execute_and_record()
    autonomy_loop_task = None
    try:
        autonomy_manager = getattr(context, "autonomous_assistant_manager", None)
        if autonomy_manager is not None:
            AUTONOMY_TICK_INTERVAL = 60.0

            async def _autonomy_due_scheduler_loop() -> None:
                """周期性扫描到期事件并执行。

                同步操作（events/planner/task_lifecycle）通过 asyncio.to_thread 包裹，
                避免阻塞 WebSocket 事件循环。异常非致命，单次失败不中断循环。
                """
                logger.info("autonomy due-scheduler loop started (interval=%ss)", AUTONOMY_TICK_INTERVAL)
                while True:
                    try:
                        # 维护 last_checked 时间戳（诊断用，标识驱动循环是否在运行）
                        ds = getattr(context, "local_data_store", None)
                        if ds is not None:
                            try:
                                ds.update_json(
                                    "assistant_rules",
                                    {},
                                    lambda mark: {
                                        **(dict(mark) if isinstance(mark, dict) else {}),
                                        "last_checked": datetime.now(timezone.utc).isoformat(),
                                    },
                                )
                            except Exception:
                                logger.debug("last_checked timestamp update failed", exc_info=True)
                        results = await asyncio.to_thread(autonomy_manager.run_due_schedules)
                        if results:
                            logger.info("autonomy run_due_schedules fired %d event(s)", len(results))
                    except asyncio.CancelledError:
                        logger.info("autonomy due-scheduler loop cancelled")
                        raise
                    except Exception as exc:
                        logger.warning("autonomy run_due_schedules failed (non-fatal): %s", exc, exc_info=True)
                    await asyncio.sleep(AUTONOMY_TICK_INTERVAL)

            if TEST_OFFLINE:
                # SIYI_TEST_OFFLINE=1：跳过 autonomy 到期调度循环
                # （run_due_schedules 会触发 planner / AI 调用），测试环境不执行。
                logger.info("autonomy due-scheduler loop skipped (SIYI_TEST_OFFLINE=1)")
            else:
                autonomy_loop_task = asyncio.create_task(_autonomy_due_scheduler_loop())
                logger.info("autonomy due-scheduler driver task created")
        else:
            logger.warning("autonomous_assistant_manager unavailable, run_due_schedules driver skipped")
    except Exception:
        logger.exception("autonomy scheduler start failed (non-critical)")

    # ── 4. 等待停止信号 ──
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)
    else:
        # Windows: 通过 Ctrl+C 处理
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                # Windows 上 add_signal_handler 可能不支持
                pass

    logger.info("backend ready on ws://%s:%d", WS_HOST, WS_PORT)
    print(f"\n 思忆集 backend ready -> ws://{WS_HOST}:{WS_PORT}\n", flush=True)

    await stop_event.wait()

    # ── 5. 优雅关闭 ──
    logger.info("shutting down...")
    info_scheduler.cancel()
    try:
        from backend.vocab_mood import vocab_mood_coordinator

        vocab_mood_coordinator.stop()
    except Exception:
        logger.debug("vocab mood stop failed", exc_info=True)
    try:
        from backend.due_reminder import due_reminder_scheduler

        due_reminder_scheduler.stop()
    except Exception:
        logger.debug("due reminder stop failed", exc_info=True)
    try:
        from backend import weekly_report_scheduler

        weekly_report_scheduler.cancel()
        weekly_report_scheduler.cancel_daily()
    except Exception:
        logger.debug("weekly report scheduler stop failed (non-critical)", exc_info=True)
    try:
        from backend import job_hunter_scheduler

        job_hunter_scheduler.cancel()
    except Exception:
        logger.debug("job hunter scheduler stop failed (non-critical)", exc_info=True)
    try:
        from backend import personal_profile_scheduler

        personal_profile_scheduler.cancel()
    except Exception:
        logger.debug("personal profile scheduler stop failed (non-critical)", exc_info=True)
    if sync_bridge is not None:
        try:
            await sync_bridge.stop()
        except Exception:
            logger.debug("sync bridge stop failed", exc_info=True)
    for _worker in task_queue_workers:
        try:
            await _worker.stop(timeout=5.0)
        except Exception:
            logger.debug("task_queue worker stop failed", exc_info=True)
    if task_queue is not None:
        try:
            task_queue.close_all()  # 关闭 SQLite 连接，避免 WAL 残留
        except Exception:
            logger.debug("task_queue close_all failed", exc_info=True)
    if autonomy_loop_task is not None:
        autonomy_loop_task.cancel()
    if confirmation_watcher is not None:
        await confirmation_watcher.stop()
    await server.stop()
    bridge.stop_bridging()
    context.stop_background_services()
    logger.info("shutdown complete")


def _resolve_log_dir() -> str:
    """日志目录解析：``SIYI_DATA_DIR`` 优先，否则回落 ``<repo>/思忆集app/data/logs``。

    ★ 2026-09-18 修复隔离泄漏（风险台账 #180）：原实现**硬编码**
    ``<repo>/思忆集app/data/logs``，无视 ``SIYI_DATA_DIR`` → 隔离实例
    （集成测试 / AUTOSTART 临时后端 / 并行 pytest）的日志仍 append 进
    **真实** ``data/logs/siyi.log``，多实例并发时 RotatingFileHandler
    rollover 撞 WinError 32。改与 ``ai/client/fallback.py:161`` 同范式。
    """
    data_dir_env = os.environ.get("SIYI_DATA_DIR", "").strip()
    base = data_dir_env or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    return os.path.join(base, "logs")


def _setup_logging() -> None:
    """配置日志：控制台 + 轮转文件（7 天保留）+ 全局异常钩子。"""
    level = logging.DEBUG if os.environ.get("SIYI_DEBUG") else logging.INFO
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # 控制台输出
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    # 文件持久化：<data_dir>/logs/siyi.log，每个 5MB，保留 7 个备份
    # ★ log_dir 在 try **之前**定好：文件日志失败时下面装异常钩子仍要用它，
    # 否则 NameError 会被宽 except 吞掉 → 全局异常钩子**静默消失**（2026-09-18）。
    log_dir = _resolve_log_dir()
    try:
        from logging.handlers import RotatingFileHandler

        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=os.path.join(log_dir, "siyi.log"),
            maxBytes=5 * 1024 * 1024,
            backupCount=7,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
        logger.info("log file handler initialized: %s", os.path.join(log_dir, "siyi.log"))
    except Exception as exc:
        logger.warning("failed to init file logging (falling back to console only): %s", exc)

    # 全局异常钩子：P2-2 统一使用 core.exception_handler 的实现
    try:
        from core.exception_handler import install_global_excepthook, install_log_sanitizer

        install_global_excepthook(log_dir=log_dir, app_name="headless")
        install_log_sanitizer()
    except Exception as _hook_exc:
        crash_logger = logging.getLogger("siyi.crash")
        crash_logger.warning("failed to install global excepthook: %s", _hook_exc)


def main() -> None:
    """Synchronous entry point."""
    _setup_logging()

    lock_fd = acquire_lock()
    if lock_fd is None and HAS_MSVCRT:
        sys.exit(1)

    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("interrupted by user")
    except Exception:
        logger.exception("backend crashed with unhandled exception")
        raise
    finally:
        release_lock(lock_fd)


if __name__ == "__main__":
    main()
