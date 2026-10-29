from __future__ import annotations

import faulthandler
import logging
import os
import signal
import sys
import tempfile
import traceback

# 启用 faulthandler（仅一次）以在 segfault 时输出崩溃堆栈到文件
# 注意：文件句柄在进程退出时由 OS 自动关闭，这里不显式关闭
_fault_log_path = os.path.join(tempfile.gettempdir(), "siyi_fault.log")
_fault_file = open(_fault_log_path, "w", buffering=1)
faulthandler.enable(file=_fault_file)

# atexit 记录 — 捕获进程退出时的堆栈
_atexit_log_path = os.path.join(tempfile.gettempdir(), "siyi_atexit.log")
_atexit_logger = logging.getLogger("siyi.app.atexit")
try:
    import atexit

    def _log_exit():
        from datetime import datetime

        with open(_atexit_log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] atexit: process exiting\n")
            import traceback as tb

            tb.print_stack(file=f)
            f.flush()

    atexit.register(_log_exit)
except Exception as exc:
    _atexit_logger.warning("atexit register failed: %s", exc)

# ── .env 文件安全加载（P2-3: 使用 core.env_loader 统一接口） ──
# 旧实现直接在 main.py 内联解析 .env，且无法复用；现收敛到 core.env_loader.load_env_file
# 安全保证：不打印 value 明文，只输出 key 数量和路径
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
_env_path = os.path.abspath(_env_path)
try:
    from core.env_loader import load_env_file

    _loaded_keys = load_env_file(_env_path)
    if _loaded_keys:
        print(f"[env] loaded {len(_loaded_keys)} keys from {_env_path}")
    else:
        print(f"[env] no .env file or empty at {_env_path}")
except Exception as _exc:
    print(f"[env] failed to load {_env_path}: {_exc}")

# 额外崩溃信号捕获
_crash_log_path = os.path.join(tempfile.gettempdir(), "siyi_crash_signal.log")
_crash_logger = logging.getLogger("siyi.app.crash")
try:

    def _crash_signal_handler(sig, frame):
        import threading

        try:
            sig_name = signal.Signals(sig).name
        except (ValueError, TypeError):
            sig_name = f"signal_{sig}"
        with open(_crash_log_path, "a", encoding="utf-8") as f:
            from datetime import datetime

            f.write(f"[{datetime.now().isoformat()}] CRASH SIGNAL: {sig} ({sig_name})\n")
            f.write(f"Main thread: {threading.current_thread().name == 'MainThread'}\n")
            f.write(f"Thread: {threading.current_thread().name}\n")
            import traceback as tb

            tb.print_stack(frame, file=f)
            f.flush()

    signal.signal(signal.SIGABRT, _crash_signal_handler)
    signal.signal(signal.SIGSEGV, _crash_signal_handler)
except Exception as exc:
    _crash_logger.warning("could not set crash signal handlers: %s", exc)

try:
    from PyQt6.QtCore import QLockFile, QTimer
    from PyQt6.QtWidgets import QApplication, QMessageBox
except ImportError as e:
    print(f"导入PyQt6时出错: {e}")
    print("请确保已正确安装PyQt6:")
    print("pip install PyQt6")
    sys.exit(1)

LOG_LEVEL = os.getenv("SIYI_LOG_LEVEL", "INFO").upper()
STARTUP_LOG_PATH = os.getenv("SIYI_STARTUP_LOG_PATH") or os.path.join(tempfile.gettempdir(), "siyi_startup_latest.log")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(STARTUP_LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("siyi.app")

# 全局 excepthook — P2-2: 统一使用 core.exception_handler 的实现
# 旧实现（直接写 /tmp/siyi_excepthook.log + traceback.print_tb）已收敛到
# install_global_excepthook，避免三处入口重复实现。
try:
    from core.exception_handler import install_global_excepthook, install_log_sanitizer

    install_global_excepthook(app_name="desktop")
    install_log_sanitizer()
except Exception as _exc:
    logger.warning("failed to install global excepthook: %s", _exc)


def _emit_startup_event(event_name, severity="info", payload=None):
    try:
        from data.local_storage import LocalDataStore
        from core.observability import ObservabilityService

        service = ObservabilityService(LocalDataStore())
        service.track_event(event_name, severity=severity, payload=payload or {})
    except Exception:
        logger.debug("emit_startup_event skipped", exc_info=True)


def _resolve_launch_source():
    explicit_source = str(os.getenv("SIYI_LAUNCH_SOURCE", "")).strip()
    if explicit_source:
        return explicit_source
    if "debugpy" in sys.modules:
        return "vscode-debug"
    if os.getenv("TERM_PROGRAM") == "vscode":
        return "vscode-terminal"
    return "plain-terminal"


def _resolve_auto_exit_ms():
    raw_value = str(os.getenv("SIYI_AUTO_EXIT_MS", "")).strip()
    if not raw_value:
        return 0
    try:
        parsed = int(raw_value)
    except ValueError:
        return 0
    return max(0, parsed)


def _open_path_in_system(path):
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
            return True
    except Exception:
        return False
    return False


def _lazy_start_cron(app_context):
    """延迟启动 cron 服务（在 event loop 运行后调用）。"""
    try:
        status = app_context.start_cron_service()
        logger.info("cron service started (lazy): %s", status)
    except Exception:
        logger.warning("cron service start failed (lazy)", exc_info=True)


def _lazy_init_kb_bridge(app_context):
    """延迟初始化知识库桥接层（避免阻塞启动）。"""
    try:
        bridge = getattr(app_context, "knowledge_base_bridge", None)
        if bridge is not None:
            ok = bridge.ensure_initialized()
            logger.info("knowledge base bridge initialized (lazy): %s", ok)
    except Exception:
        logger.warning("knowledge base bridge init failed (lazy)", exc_info=True)


def _lazy_start_automation_hub(app_context):
    """延迟启动 automation-hub 后端（窗口显示后执行）。"""
    try:
        ok = app_context.start_automation_hub()
        logger.info("automation hub started (lazy): %s", ok)
    except Exception:
        logger.warning("automation hub start failed (lazy)", exc_info=True)


def _lazy_startup_phase2(app_context):
    """延迟启动非关键服务：bridge、state monitor、file watcher（窗口显示后执行）。"""
    import time as _time

    _t0 = _time.monotonic()
    try:
        # T2 修复：与 backend/headless_main.py:68 同源解析主 WS 契约端口
        # （SIYI_WS_PORT 环境变量或默认 18765），传给桥作 avoid_port，
        # 防止桥抢占/遮蔽主 WS 端口上的 /health 健康契约。
        ws_port = int(os.environ.get("SIYI_WS_PORT", "18765"))
        bridge_status = app_context.start_browser_assistant_bridge(avoid_port=ws_port)
        logger.info("browser assistant bridge status: %s", bridge_status)
    except Exception:
        logger.warning("browser assistant bridge start failed (lazy)", exc_info=True)
    try:
        from desktop.app_state_monitor import AppStateMonitor

        monitor = AppStateMonitor(behavior_logger=app_context.behavior_logger)
        monitor.install()
    except Exception:
        logger.warning("app state monitor install failed (lazy)", exc_info=True)
    try:
        _watch_dirs = [
            os.path.expanduser("~/Desktop"),
            os.path.expanduser("~/Documents"),
        ]
        app_context.start_file_watcher(_watch_dirs)
    except Exception:
        logger.warning("file watcher start failed (lazy)", exc_info=True)
    logger.info("phase2 (bridge+monitor+watcher) done in %.1fs", _time.monotonic() - _t0)


def _show_startup_failure(error_message):
    detail = f"{error_message}\n\n日志路径:\n{STARTUP_LOG_PATH}"
    try:
        action = QMessageBox.question(
            None,
            "启动失败",
            detail + "\n\n是否立即打开日志文件？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if action == QMessageBox.StandardButton.Yes:
            _open_path_in_system(STARTUP_LOG_PATH)
    except Exception:
        logger.error("startup failure dialog failed: %s", detail)


def _acquire_single_instance_lock(app):
    if str(os.getenv("SIYI_SKIP_SINGLE_INSTANCE_LOCK", "")).strip() == "1":
        logger.warning("single instance lock skipped by env SIYI_SKIP_SINGLE_INSTANCE_LOCK=1")
        return True

    noninteractive = str(os.getenv("SIYI_NONINTERACTIVE_STARTUP", "")).strip() == "1"
    lock_path = os.path.join(tempfile.gettempdir(), "siyi_desktop_app.lock")
    lock = QLockFile(lock_path)
    lock.setStaleLockTime(0)
    if lock.tryLock(100):
        app._single_instance_lock = lock
        logger.debug("single instance lock acquired: %s", lock_path)
        return True

    logger.warning("single instance lock exists, entering takeover flow: %s", lock_path)
    if noninteractive:
        logger.error("single instance lock exists in noninteractive mode, startup aborted")
        return False

    # 防止 QMessageBox 关闭时误触 quitOnLastWindowClosed
    saved_quit_on_close = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    choice = QMessageBox.question(
        None,
        "检测到已运行实例",
        '思忆集可能已在运行。\n\n选择"是"尝试接管并继续启动；选择"否"退出本次启动。',
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    app.setQuitOnLastWindowClosed(saved_quit_on_close)
    if choice != QMessageBox.StandardButton.Yes:
        return False

    removed = False
    try:
        removed = bool(lock.removeStaleLockFile())
    except Exception:
        removed = False
    if removed:
        logger.info("stale lock file removed: %s", lock_path)

    if lock.tryLock(600):
        app._single_instance_lock = lock
        logger.info("single instance lock takeover succeeded: %s", lock_path)
        return True

    QMessageBox.warning(None, "启动被阻止", "无法接管锁文件，另一个实例仍在运行。")
    logger.error("single instance lock takeover failed: %s", lock_path)
    return False


# ═══════════════════════════════════════════════════════════════
# main() — 可供外部 import 后调用的启动入口
# ═══════════════════════════════════════════════════════════════


def main() -> int:
    """启动思忆集桌面应用。返回退出码。"""
    import atexit
    import time as _time

    from app_runtime.app_context import AppContext
    from ui.alarm.dialog import AlarmDialog
    from ui.main_window import MainWindow
    from ui.notes.dialog import NotesDialog
    from ui.settings.dialog import SettingsDialog
    from ui.tasks.dialog import DailyTaskDialog
    from ui.theme import apply_desktop_theme, set_app_theme
    from ui.translate.dialog import TranslateDialog
    from ui.url_learning.dialog import UrlLearningDialog
    from ui.wordbank.dialog import WordBankDialog

    # MCPDialog 尚未实现（ui/mcp/ 为 placeholder 包），容错导入避免启动崩溃
    try:
        from ui.mcp.dialog import MCPDialog
    except ImportError:
        MCPDialog = None
        logger.warning("ui.mcp.dialog not implemented, MCP panel disabled")
    # 以下 4 个对话框模块同样尚未实现，容错导入避免启动崩溃
    try:
        from ui.knowledge_base.dialog import KnowledgeBaseDialog
    except ImportError:
        KnowledgeBaseDialog = None
        logger.warning("ui.knowledge_base.dialog not implemented, panel disabled")
    try:
        from ui.workflows.dialog import WorkflowsDialog
    except ImportError:
        WorkflowsDialog = None
        logger.warning("ui.workflows.dialog not implemented, panel disabled")
    try:
        from ui.plugins.dialog import PluginsDialog
    except ImportError:
        PluginsDialog = None
        logger.warning("ui.plugins.dialog not implemented, panel disabled")
    try:
        from ui.evolution.dialog import EvolutionDialog
    except ImportError:
        EvolutionDialog = None
        logger.warning("ui.evolution.dialog not implemented, panel disabled")
    from ui.layout_dialog import LayoutDialog
    from ui.floating_assistant import FloatingAssistantDialog
    from ui.startup_guide_dialog import DesktopDoctorDialog, StartupGuideDialog
    from ui.mobile_pairing_dialog import MobilePairingDialog
    from ui.undo_dialog import UndoDialog

    # P2-2: debug 模式复用统一 excepthook（force=True 允许重新安装覆盖非 debug 模式的实例）
    try:
        from core.exception_handler import install_global_excepthook

        install_global_excepthook(app_name="desktop_debug", force=True)
    except Exception as _dbg_exc:
        logger.warning("failed to install debug excepthook: %s", _dbg_exc)

    atexit.register(lambda: logger.debug("atexit"))
    logger.info("main entry")
    _emit_startup_event("startup.begin", severity="info", payload={"source": _resolve_launch_source(), "pid": os.getpid()})
    logger.info("official desktop entrypoint active: 思忆集app/main.py")
    logger.info(
        "launch context: source=%s pid=%s cwd=%s python=%s",
        _resolve_launch_source(),
        os.getpid(),
        os.getcwd(),
        sys.executable,
    )
    try:
        # QtWebEngineWidgets 延迟到 WebBridgeService.start() 时导入，
        # 避免 Chromium 初始化阻塞启动（在某些 Windows 配置下可能挂起）
        from PyQt6.QtCore import Qt

        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
        app = QApplication(sys.argv)
        if not _acquire_single_instance_lock(app):
            return 0
        apply_desktop_theme(app)
        logger.debug("QApplication created")

        # 显示启动遮罩层（在耗时初始化前显示）
        from ui.loading_overlay import LoadingOverlay

        _overlay = LoadingOverlay()
        _overlay.show_with_message("正在启动...")

        dialog_classes = {
            "tasks": DailyTaskDialog,
            "notes": NotesDialog,
            "alarms": AlarmDialog,
            "translate": TranslateDialog,
            "settings": SettingsDialog,
            "url_learning": UrlLearningDialog,
            "wordbank": WordBankDialog,
            "layout": LayoutDialog,
            "floating_assistant": FloatingAssistantDialog,
            "doctor": DesktopDoctorDialog,
            "startup_guide": StartupGuideDialog,
            "mobile_pairing": MobilePairingDialog,
            "undo": UndoDialog,
        }
        # 可选对话框：模块未实现时为 None，跳过注册
        for _key, _cls in [
            ("mcp", MCPDialog),
            ("knowledge_base", KnowledgeBaseDialog),
            ("workflows", WorkflowsDialog),
            ("plugins", PluginsDialog),
            ("evolution", EvolutionDialog),
        ]:
            if _cls is not None:
                dialog_classes[_key] = _cls
        # 启动时验证对话框类字典，确保所有键值有效
        from ui.dialog_factory import MainWindowDialogMixin

        invalid_dialog_keys = MainWindowDialogMixin.validate_dialog_classes(dialog_classes)
        if invalid_dialog_keys:
            logger.warning("发现 %d 个无效对话框类，请检查 dialog_classes 配置", len(invalid_dialog_keys))

        _t0 = _time.monotonic()
        from app_runtime.desknotes_path import resolve_desknotes_dir

        app_context = AppContext(
            desknotes_data_dir=resolve_desknotes_dir(),
            # R2-C1：honor SIYI_DATA_DIR 实现数据目录隔离（env 未设时与旧行为一致）
            data_base_dir=os.environ.get("SIYI_DATA_DIR") or None,
        )
        _t_appctx = _time.monotonic()
        logger.info("AppContext created in %.1fs", _t_appctx - _t0)
        _overlay.set_message("正在初始化服务...")
        # 读取已保存的主题设置
        _saved_theme = app_context.settings_repository.get_setting("ui_theme", "light")
        if _saved_theme == "dark":
            set_app_theme(app, "dark")
        # 延迟到窗口显示后启动非关键服务（bridge、state monitor、file watcher）
        QTimer.singleShot(0, lambda: _lazy_startup_phase2(app_context))
        # Defer startup AI event to avoid blocking the UI thread (~57s timeout)
        QTimer.singleShot(0, lambda: app_context.autonomous_assistant_manager.run_system_event("startup", {"source": _resolve_launch_source()}))
        logger.info("creating MainWindow...")
        _overlay.set_message("正在加载界面...")
        window = MainWindow(dialog_classes, app_context)
        _t_mainwin = _time.monotonic()
        logger.info("MainWindow created in %.1fs (AppContext+MainWindow cumulative: %.1fs)", _t_mainwin - _t_appctx, _t_mainwin - _t0)

        def _on_about_to_quit():
            logger.debug("aboutToQuit")
            try:
                app_context.stop_browser_assistant_bridge()
            except Exception:
                logger.debug("browser assistant bridge stop skipped", exc_info=True)
            try:
                app_context.stop_web_bridge_service()
            except Exception:
                logger.debug("web bridge service stop skipped", exc_info=True)
            try:
                app_context.stop_background_services()
            except Exception:
                logger.debug("background services stop skipped", exc_info=True)

        app.aboutToQuit.connect(_on_about_to_quit)
        logger.info("showing window directly (no timer)")
        window.showNormal()
        window.raise_()
        window.activateWindow()
        # 窗口显示后再隐藏遮罩，避免出现空白闪烁
        _overlay.hide_and_cleanup()
        # 防止 quitOnLastWindowClosed 在窗口尚未显示时误触发退出
        app.setQuitOnLastWindowClosed(False)
        # 首次启动：若引导未完成，延迟 500ms 自动弹出
        _guide_done = app_context.settings_repository.get_setting("startup_guide_completed", False)
        if not _guide_done:
            QTimer.singleShot(500, lambda: window._open_dialog(key="startup_guide"))
        # 延迟启动 cron 服务（窗口已就绪后启动，减少启动竞态）
        QTimer.singleShot(1000, lambda: _lazy_start_cron(app_context))
        QTimer.singleShot(2000, lambda: _lazy_start_automation_hub(app_context))
        QTimer.singleShot(3000, lambda: _lazy_init_kb_bridge(app_context))
        # 诊断定时器：确认 event loop 在运行
        QTimer.singleShot(2000, lambda: logger.info("event loop still running at 2s"))
        QTimer.singleShot(5000, lambda: logger.info("event loop still running at 5s"))
        # Web 桥接（QWebEngineView）在 event loop 中由 _lazy_init_phase4 延迟启动
        auto_exit_ms = _resolve_auto_exit_ms()
        if auto_exit_ms > 0:
            logger.info("auto-exit enabled: %sms", auto_exit_ms)
            QTimer.singleShot(auto_exit_ms, app.quit)
        _emit_startup_event("startup.window_shown", severity="info", payload={"entry": "official"})
        logger.info("window shown in %.1fs total, entering event loop", _time.monotonic() - _t0)
        exit_code = app.exec()
        try:
            app_context.autonomous_assistant_manager.run_system_event("shutdown", {"exit_code": exit_code})
        except Exception:
            logger.debug("assistant shutdown system event skipped", exc_info=True)
        logger.info("event loop exited with code %s", exit_code)
        return exit_code
    except SystemExit:
        raise
    except Exception as e:
        logger.exception("Unhandled exception: %s", e)
        _emit_startup_event(
            "crash.recovery",
            severity="blocking",
            payload={
                "error_type": e.__class__.__name__,
                "error_message": str(e),
            },
        )
        try:
            with open(STARTUP_LOG_PATH, "a", encoding="utf-8") as file_handle:
                file_handle.write("\n===== UNHANDLED STARTUP EXCEPTION =====\n")
                file_handle.write(traceback.format_exc())
                file_handle.write("\n")
        except Exception:
            logger.debug("failed to write crash log to file", exc_info=True)
        _show_startup_failure(f"应用启动异常: {e}")
        return 1


# ═══════════════════════════════════════════════════════════════
# 入口点
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    sys.exit(main())
