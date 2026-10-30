from __future__ import annotations

import os
import subprocess
import threading

from core.observability import SEVERITY_DEGRADED
from core.subprocess_text import run_text
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMessageBox


class DesktopPetController:
    def __init__(
        self,
        host_window,
        action_guard,
        logger,
        nana_pet_dirname,
        nana_pet_launcher,
        nana_status_check_interval_ms,
        neuro_pet_live2d_dir,
        neuro_pet_emotion_port,
        neuro_status_check_interval_ms,
        neuro_mood_to_emotion,
    ):
        self._host_window = host_window
        self._action_guard = action_guard
        self._logger = logger
        self._nana_pet_dirname = nana_pet_dirname
        self._nana_pet_launcher = nana_pet_launcher
        self._neuro_pet_live2d_dir = neuro_pet_live2d_dir
        self._neuro_pet_emotion_port = neuro_pet_emotion_port
        self._neuro_mood_to_emotion = dict(neuro_mood_to_emotion)

        self._nana_pet_actions = []
        self._nana_pet_running = False
        self._nana_status_timer = QTimer(host_window)
        self._nana_status_timer.setInterval(nana_status_check_interval_ms)
        self._nana_status_timer.timeout.connect(self.refresh_nana_state)

        self._neuro_pet_actions = []
        self._neuro_pet_running = False
        self._neuro_pet_process = None
        self._neuro_status_timer = QTimer(host_window)
        self._neuro_status_timer.setInterval(neuro_status_check_interval_ms)
        self._neuro_status_timer.timeout.connect(self.refresh_neuro_state)

    def stop(self):
        self._nana_status_timer.stop()
        self._neuro_status_timer.stop()

    def create_nana_action(self, parent=None):
        owner = parent if parent is not None else self._host_window
        action = QAction("启动七奈桌宠", owner)
        action.triggered.connect(self.launch_nana_desktop_pet)
        self._nana_pet_actions.append(action)
        self._apply_nana_action_state(action)
        return action

    def create_neuro_action(self, parent=None):
        owner = parent if parent is not None else self._host_window
        action = QAction("启动肥牛桌宠", owner)
        action.triggered.connect(self.launch_neuro_desktop_pet)
        self._neuro_pet_actions.append(action)
        self._apply_neuro_action_state(action)
        return action

    def setup_nana_monitor(self):
        self.refresh_nana_state(force=True)
        self._nana_status_timer.start()

    def setup_neuro_monitor(self):
        self.refresh_neuro_state(force=True)
        self._neuro_status_timer.start()

    def refresh_nana_state(self, force=False):
        running = self._detect_nana_process()
        if force or running != self._nana_pet_running:
            self._set_nana_running(running)
        return self._nana_pet_running

    def refresh_neuro_state(self, force=False):
        running = self._detect_neuro_process()
        if force or running != self._neuro_pet_running:
            self._set_neuro_running(running)
        return self._neuro_pet_running

    def is_nana_desktop_pet_running(self):
        return self._nana_pet_running

    def launch_nana_desktop_pet(self):
        if self.refresh_nana_state(force=True):
            QMessageBox.information(self._host_window, "Pet Status", "Launcher is already running.")
            return False
        launcher_path = self._get_nana_launcher()
        if not os.path.isfile(launcher_path):
            QMessageBox.warning(self._host_window, "七奈桌宠", f"未找到启动文件:\n{launcher_path}")
            return False
        command_check = self._action_guard.validate_command(
            command_id="launch_nana_pet",
            payload={"launcher_path": launcher_path, "command": "os.startfile"},
            source="ui.command.launch_nana_pet",
        )
        if not command_check.allowed:
            self._track_blocked_event("launch_nana_pet", command_check.reason, command_check.risk_level)
            QMessageBox.warning(self._host_window, "七奈桌宠", "当前安全策略已阻止启动命令，请在设置中调整动作治理档位后重试。")
            return False
        for warning in command_check.warnings:
            self._append_assistant_message(f"命令风险提醒：{warning}")
        try:
            if os.name == "nt":
                os.startfile(launcher_path)  # type: ignore[attr-defined]
            else:
                return False
        except Exception as exc:
            QMessageBox.warning(self._host_window, "七奈桌宠", f"启动失败: {exc}")
            return False
        QTimer.singleShot(1500, lambda: self.refresh_nana_state(force=True))
        return True

    def launch_neuro_desktop_pet(self):
        if self.refresh_neuro_state(force=True):
            QMessageBox.information(self._host_window, "肥牛桌宠", "肥牛桌宠已经在运行，无需重新启动。")
            return False
        live2d_dir = self._get_neuro_live2d_dir()
        package_json = os.path.join(live2d_dir, "package.json")
        if not os.path.isfile(package_json):
            QMessageBox.warning(self._host_window, "肥牛桌宠", f"未找到 Live2D 项目:\n{live2d_dir}")
            return False
        node_modules = os.path.join(live2d_dir, "node_modules")
        if not os.path.isdir(node_modules):
            QMessageBox.warning(self._host_window, "肥牛桌宠", f"依赖未安装，请先在 live-2d 目录执行 npm install:\n{live2d_dir}")
            return False
        try:
            npx_path = "npx.cmd" if os.name == "nt" else "npx"
            self._neuro_pet_process = subprocess.Popen(
                [npx_path, "electron", "."],
                cwd=live2d_dir,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            QMessageBox.warning(self._host_window, "肥牛桌宠", f"启动失败: {exc}")
            return False
        QTimer.singleShot(2000, lambda: self.refresh_neuro_state(force=True))
        return True

    def push_neuro_emotion(self, mood):
        if not self._neuro_pet_running:
            return
        emotion_name = self._neuro_mood_to_emotion.get(mood, "")
        if not emotion_name:
            return

        def _do_push():
            try:
                import json as _json
                import urllib.request

                data = _json.dumps({"action": "trigger_emotion", "emotion_name": emotion_name}).encode()
                req = urllib.request.Request(
                    f"http://127.0.0.1:{self._neuro_pet_emotion_port}/control-motion",
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=2)
            except Exception:
                self._logger.warning("failed to push motion to neuro pet")

        threading.Thread(target=_do_push, daemon=True).start()

    def _apply_nana_action_state(self, action):
        if self._nana_pet_running:
            action.setText("七奈桌宠运行中")
            action.setEnabled(False)
            action.setToolTip("七奈桌宠已在运行")
            action.setStatusTip("七奈桌宠已在运行")
        else:
            action.setText("启动七奈桌宠")
            action.setEnabled(True)
            action.setToolTip("启动七奈桌宠")
            action.setStatusTip("启动七奈桌宠")

    def _apply_neuro_action_state(self, action):
        if self._neuro_pet_running:
            action.setText("肥牛桌宠运行中")
            action.setEnabled(False)
            action.setToolTip("肥牛 Live2D 桌宠已在运行")
        else:
            action.setText("启动肥牛桌宠")
            action.setEnabled(True)
            action.setToolTip("启动肥牛 Live2D 桌宠")

    def _prune_nana_actions(self):
        alive_actions = []
        for action in self._nana_pet_actions:
            if action is None:
                continue
            try:
                action.text()
            except RuntimeError:
                continue
            alive_actions.append(action)
        self._nana_pet_actions = alive_actions

    def _prune_neuro_actions(self):
        alive_actions = []
        for action in self._neuro_pet_actions:
            if action is None:
                continue
            try:
                action.text()
            except RuntimeError:
                continue
            alive_actions.append(action)
        self._neuro_pet_actions = alive_actions

    def _set_nana_running(self, running):
        self._nana_pet_running = bool(running)
        self._prune_nana_actions()
        for action in self._nana_pet_actions:
            self._apply_nana_action_state(action)

    def _set_neuro_running(self, running):
        self._neuro_pet_running = bool(running)
        self._prune_neuro_actions()
        for action in self._neuro_pet_actions:
            self._apply_neuro_action_state(action)

    def _get_neuro_live2d_dir(self):
        workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(workspace_root, self._neuro_pet_live2d_dir)

    def _get_nana_launcher(self):
        workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(workspace_root, self._nana_pet_dirname, self._nana_pet_launcher)

    def _detect_neuro_process(self):
        if self._neuro_pet_process is not None:
            if self._neuro_pet_process.poll() is None:
                return True
            self._neuro_pet_process = None
            return False
        if os.name != "nt":
            return False

        # 优先使用 psutil（跨平台、高效，无需启动 PowerShell 子进程）
        try:
            import psutil
            for proc in psutil.process_iter(["name", "cmdline"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                    if name == "electron.exe" and "my-neuro" in cmdline and "live-2d" in cmdline:
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return False
        except ImportError:
            pass

        # 回退：PowerShell（psutil 不可用时）
        script = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -eq 'electron.exe' -and $_.CommandLine -like '*my-neuro*live-2d*' } | "
            "Select-Object -First 1 -ExpandProperty ProcessId"
        )
        try:
            result = run_text(
                ["powershell", "-NoProfile", "-Command", script],
                timeout=5,
                extra_kwargs={"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)},
            )
        except Exception:
            return self._neuro_pet_running
        return bool((result.stdout or "").strip())

    def _detect_nana_process(self):
        if os.name != "nt":
            return False
        check_result = self._action_guard.validate_command(
            command_id="detect_nana_pet_process",
            payload={"command": "psutil process detect"},
            source="ui.command.detect_nana_pet",
        )
        if not check_result.allowed:
            self._track_blocked_event("detect_nana_pet_process", check_result.reason, check_result.risk_level)
            return self._nana_pet_running

        # 优先使用 psutil（跨平台、高效，无需启动 PowerShell 子进程）
        try:
            import psutil
            for proc in psutil.process_iter(["name", "cmdline"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                    # 匹配 java.exe / javaw.exe 且命令行包含 Shimeji-ee.jar
                    if name in ("java.exe", "javaw.exe") and "shimeji-ee.jar" in cmdline:
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return False
        except ImportError:
            pass

        # 回退：PowerShell（psutil 不可用时）
        script = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -match '^javaw?\\.exe$' -and $_.CommandLine -like '*Shimeji-ee.jar*' } | "
            "Select-Object -First 1 -ExpandProperty ProcessId"
        )
        try:
            result = run_text(
                ["powershell", "-NoProfile", "-Command", script],
                timeout=5,
                extra_kwargs={"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)},
            )
        except Exception as exc:
            self._logger.warning("failed to detect nana desktop pet process", exc_info=True)
            return self._nana_pet_running
        if result.returncode != 0:
            self._logger.debug("nana desktop pet status command failed: %s", (result.stderr or "").strip())
            return self._nana_pet_running
        return bool((result.stdout or "").strip())

    def _track_blocked_event(self, command_id, reason, risk_level):
        track_event = getattr(self._host_window, "_track_event", None)
        if callable(track_event):
            track_event(
                "assistant.command.blocked",
                SEVERITY_DEGRADED,
                {"command_id": command_id, "reason": reason, "risk_level": risk_level},
            )

    def _append_assistant_message(self, message):
        append_message = getattr(self._host_window, "_append_assistant_message", None)
        if callable(append_message):
            append_message("助手", message)
