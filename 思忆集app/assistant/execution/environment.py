from __future__ import annotations

import csv
import io
import logging
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

from core.subprocess_text import run_text

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows fallback
    winreg = None  # type: ignore[assignment]

logger = logging.getLogger("siyi.assistant.environment")


class AssistantEnvironmentProbe:
    """环境探测与本机能力解析（跨平台 Windows / macOS / Linux）。"""

    _WELL_KNOWN_APPS: dict[str, list[str]] = {
        "微信": ["Weixin.exe", "WeChat.exe", "XWeChat.exe"],
        "weixin": ["Weixin.exe", "WeChat.exe", "XWeChat.exe"],
        "wechat": ["Weixin.exe", "WeChat.exe", "XWeChat.exe"],
        "qq": ["QQ.exe"],
        "钉钉": ["DingTalk.exe", "DingtalkLauncher.exe"],
        "飞书": ["Feishu.exe", "Lark.exe"],
        "企业微信": ["WXWork.exe"],
        "vscode": ["Code.exe"],
        "code": ["Code.exe"],
        "记事本": ["notepad.exe"],
        "notepad": ["notepad.exe"],
        "计算器": ["calc.exe"],
        "画图": ["mspaint.exe"],
        "浏览器": ["msedge.exe", "chrome.exe", "firefox.exe"],
        "chrome": ["chrome.exe"],
        "edge": ["msedge.exe"],
        "firefox": ["firefox.exe"],
        "word": ["WINWORD.EXE"],
        "excel": ["EXCEL.EXE"],
        "powerpoint": ["POWERPNT.EXE"],
        "ppt": ["POWERPNT.EXE"],
        "cmd": ["cmd.exe"],
        "终端": ["wt.exe", "cmd.exe"],
        "powershell": ["powershell.exe"],
        "资源管理器": ["explorer.exe"],
        "explorer": ["explorer.exe"],
    }

    _WELL_KNOWN_APPS_UNIX: dict[str, list[str]] = {
        "vscode": ["code", "code-oss"],
        "code": ["code", "code-oss"],
        "chrome": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
        "edge": ["microsoft-edge", "microsoft-edge-stable"],
        "firefox": ["firefox"],
        "浏览器": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "firefox", "microsoft-edge"],
        "terminal": ["gnome-terminal", "konsole", "xfce4-terminal", "alacritty", "kitty", "iterm2", "terminal"],
        "终端": ["gnome-terminal", "konsole", "xfce4-terminal", "alacritty", "kitty", "iterm2", "terminal"],
        "iterm2": ["iterm2"],
        "safari": ["safari"],
        "notes": ["notes"],
        "计算器": ["gnome-calculator", "kcalc"],
        "记事本": ["gedit", "mousepad", "kate", "xed", "leafpad"],
        "notepad": ["gedit", "mousepad", "kate", "xed", "leafpad"],
        "cmd": ["bash", "zsh", "sh"],
    }

    _COMMON_INSTALL_DIRS: list[str] = [
        os.path.expandvars(r"%ProgramFiles%"),
        os.path.expandvars(r"%ProgramFiles(x86)%"),
        os.path.expandvars(r"%LOCALAPPDATA%"),
        os.path.expandvars(r"%APPDATA%"),
        os.path.expandvars(r"%ProgramFiles%\Tencent"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Tencent"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
    ]

    _START_MENU_DIRS: list[str] = [
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
    ]

    def __init__(self) -> None:
        self._platform = platform.system().lower()

    def usage_snapshot(self, top_n: int = 10) -> dict[str, Any]:
        safe_top_n = max(3, min(int(top_n or 10), 30))
        stats: dict[str, int] = {}
        if self._platform == "windows":
            completed = run_text(["tasklist", "/fo", "csv", "/nh"], timeout=8)
            if completed.returncode != 0:
                raise RuntimeError("tasklist command failed")
            reader = csv.reader(io.StringIO(completed.stdout))
            for row in reader:
                if not row:
                    continue
                image_name = str(row[0] or "").strip().lower()
                if not image_name:
                    continue
                stats[image_name] = stats.get(image_name, 0) + 1
        else:
            # POSIX fallback: ps -eo comm
            completed = run_text(["ps", "-eo", "comm="], timeout=8)
            if completed.returncode != 0:
                raise RuntimeError("ps command failed")
            for line in completed.stdout.splitlines():
                image_name = line.strip().lower()
                if not image_name:
                    continue
                # 取可执行文件名
                image_name = os.path.basename(image_name)
                stats[image_name] = stats.get(image_name, 0) + 1

        top = sorted(stats.items(), key=lambda item: item[1], reverse=True)[:safe_top_n]
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "top_processes": [{"process": name, "instances": count} for name, count in top],
        }

    def process_list(self, filter_name: str = "") -> dict[str, Any]:
        normalized_filter = str(filter_name or "").strip().lower()
        processes = []
        try:
            if self._platform == "windows":
                result = run_text(["tasklist", "/FO", "CSV", "/NH"], timeout=10)
                lines = result.stdout.strip().split("\n")
                for line in lines[:200]:
                    try:
                        reader = csv.reader(io.StringIO(line))
                        cols = next(reader)
                        name, pid, *rest = cols
                        name = name.strip('"')
                        if normalized_filter and normalized_filter not in name.lower():
                            continue
                        mem = rest[2].strip('"') if len(rest) > 2 else ""
                        processes.append({"name": name, "pid": pid.strip('"'), "memory": mem})
                    except Exception:
                        continue
            else:
                # POSIX: ps -eo pid,comm,rss
                result = run_text(["ps", "-eo", "pid,comm,rss"], timeout=10)
                for line in result.stdout.strip().split("\n")[1:]:
                    parts = line.strip().split(None, 2)
                    if len(parts) < 2:
                        continue
                    pid, comm = parts[0], parts[1]
                    mem = parts[2] if len(parts) > 2 else ""
                    name = os.path.basename(comm)
                    if normalized_filter and normalized_filter not in name.lower():
                        continue
                    processes.append({"name": name, "pid": pid, "memory": mem})
            return {"success": True, "count": len(processes), "processes": processes[:50]}
        except Exception as exc:
            return {"success": False, "reason": str(exc)}

    def process_kill(self, name: str) -> dict[str, Any]:
        process_name = str(name or "").strip()
        if not process_name:
            return {"success": False, "reason": "no_process_name"}
        protected_win = {
            "csrss.exe",
            "smss.exe",
            "wininit.exe",
            "services.exe",
            "lsass.exe",
            "svchost.exe",
            "winlogon.exe",
            "explorer.exe",
            "system",
            "system idle process",
            "dwm.exe",
        }
        protected_unix = {"kernel", "launchd", "systemd", "init", "loginwindow", "Dock", "Finder"}
        if self._platform == "windows":
            if process_name.lower() in protected_win:
                return {"success": False, "reason": f"protected_process: {process_name} 是系统关键进程，不允许结束。"}
            try:
                if process_name.isdigit():
                    cmd = ["taskkill", "/PID", process_name, "/F"]
                else:
                    cmd = ["taskkill", "/IM", process_name, "/F"]
                result = run_text(cmd, timeout=10)
                return {"success": result.returncode == 0, "output": result.stdout.strip() or result.stderr.strip()}
            except Exception as exc:
                return {"success": False, "reason": str(exc)}
        else:
            if process_name.lower() in protected_unix:
                return {"success": False, "reason": f"protected_process: {process_name} 是系统关键进程，不允许结束。"}
            try:
                if process_name.isdigit():
                    cmd = ["kill", "-9", process_name]
                else:
                    cmd = ["pkill", "-f", process_name]
                result = run_text(cmd, timeout=10)
                return {"success": result.returncode == 0, "output": result.stdout.strip() or result.stderr.strip()}
            except Exception as exc:
                return {"success": False, "reason": str(exc)}

    @staticmethod
    def _run_windows_cmd(ps_cmd: str, wmic_cmd: list[str]) -> str:
        """优先用 PowerShell CIM（Win11 可用），失败回退 wmic（旧系统）。"""
        try:
            r = run_text(["powershell", "-NoProfile", "-Command", ps_cmd], timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except FileNotFoundError:
            pass
        except subprocess.TimeoutExpired:
            pass
        try:
            r = run_text(wmic_cmd, timeout=10)
            return r.stdout.strip()
        except FileNotFoundError:
            return "(wmic 已在此 Windows 版本移除，且 PowerShell 不可用)"
        except Exception as exc:
            return f"(查询失败: {exc})"

    def system_info(self, category: str = "all") -> dict[str, Any]:
        target = str(category or "all").strip().lower()
        info: dict[str, Any] = {}
        try:
            if self._platform == "windows":
                # wmic 在 Win11 24H2+ 已被移除；优先 PowerShell CIM，wmic 作为 fallback
                if target in ("cpu", "all"):
                    cpu_result = self._run_windows_cmd(
                        ps_cmd="Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors | Format-Table -HideTableHeaders",
                        wmic_cmd=["wmic", "cpu", "get", "Name,NumberOfCores,NumberOfLogicalProcessors", "/FORMAT:CSV"],
                    )
                    info["cpu"] = cpu_result[:2000]
                if target in ("memory", "all"):
                    mem_result = self._run_windows_cmd(
                        ps_cmd="Get-CimInstance Win32_OperatingSystem | Select-Object FreePhysicalMemory,TotalVisibleMemorySize | Format-Table -HideTableHeaders",
                        wmic_cmd=["wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize", "/FORMAT:CSV"],
                    )
                    info["memory"] = mem_result[:2000]
                if target in ("disk", "all"):
                    disk_result = self._run_windows_cmd(
                        ps_cmd="Get-CimInstance Win32_LogicalDisk | Select-Object DeviceID,Size,FreeSpace | Format-Table -HideTableHeaders",
                        wmic_cmd=["wmic", "logicaldisk", "get", "Name,Size,FreeSpace", "/FORMAT:CSV"],
                    )
                    info["disk"] = disk_result[:2000]
                if target in ("network", "all"):
                    net_result = run_text(["ipconfig", "/all"], timeout=10)
                    info["network"] = net_result.stdout.strip()[:4000]
            elif self._platform == "darwin":
                if target in ("cpu", "all"):
                    cpu_cp = run_text(["sysctl", "-n", "machdep.cpu.brand_string", "hw.ncpu"], timeout=10)
                    info["cpu"] = cpu_cp.stdout.strip()[:2000]
                if target in ("memory", "all"):
                    mem_cp = run_text(["vm_stat"], timeout=10)
                    info["memory"] = mem_cp.stdout.strip()[:2000]
                if target in ("disk", "all"):
                    disk_cp = run_text(["df", "-h"], timeout=10)
                    info["disk"] = disk_cp.stdout.strip()[:2000]
                if target in ("network", "all"):
                    net_result = run_text(["ifconfig"], timeout=10)
                    info["network"] = net_result.stdout.strip()[:4000]
            else:
                # Linux / generic POSIX
                if target in ("cpu", "all"):
                    cpu_cp = run_text(["cat", "/proc/cpuinfo"], timeout=10)
                    info["cpu"] = cpu_cp.stdout.strip()[:2000]
                if target in ("memory", "all"):
                    mem_cp = run_text(["cat", "/proc/meminfo"], timeout=10)
                    info["memory"] = mem_cp.stdout.strip()[:2000]
                if target in ("disk", "all"):
                    disk_cp = run_text(["df", "-h"], timeout=10)
                    info["disk"] = disk_cp.stdout.strip()[:2000]
                if target in ("network", "all"):
                    net_result = run_text(["ip", "addr"], timeout=10)
                    if net_result.returncode != 0:
                        net_result = run_text(["ifconfig"], timeout=10)
                    info["network"] = net_result.stdout.strip()[:4000]
        except Exception as exc:
            info["error"] = str(exc)
        info["success"] = "error" not in info
        return info

    def resolve_app_executable(self, app_name: str) -> str | None:
        name_lower = app_name.lower().strip()
        if self._platform == "windows":
            candidates = self._WELL_KNOWN_APPS.get(name_lower, [app_name])
            registry_target = self._resolve_from_registry(app_name, candidates)
            if registry_target:
                return registry_target
            for exe_name in candidates:
                normalized = self._strip_windows_path(exe_name)
                if normalized and os.path.isfile(normalized):
                    return normalized
                try:
                    result = run_text(["where", exe_name], timeout=5)
                    if result.returncode == 0:
                        path = result.stdout.strip().splitlines()[0]
                        if path and os.path.isfile(path):
                            return path
                except Exception:
                    logger.warning("failed to locate executable via `where`: %s", exe_name)
                for base_dir in self._COMMON_INSTALL_DIRS:
                    if not os.path.isdir(base_dir):
                        continue
                    try:
                        for root, _dirs, files in os.walk(base_dir):
                            depth = root.replace(base_dir, "").count(os.sep)
                            if depth > 3:
                                continue
                            for filename in files:
                                if filename.lower() == exe_name.lower():
                                    return os.path.join(root, filename)
                    except PermissionError:
                        continue
            return self._resolve_from_start_menu(app_name, candidates)

        # macOS / Linux
        candidates = self._WELL_KNOWN_APPS_UNIX.get(name_lower, [app_name])
        for exe_name in candidates:
            # 1. PATH 中查找
            which_path = shutil.which(exe_name)
            if which_path:
                return which_path
            # 2. macOS Applications / Linux /usr/share/applications
            if self._platform == "darwin":
                app_bundle = self._resolve_macos_app(exe_name)
                if app_bundle:
                    return app_bundle
        return None

    def _strip_windows_path(self, raw_value: str) -> str | None:
        value = str(raw_value or "").strip().strip('"')
        if not value:
            return None
        if "," in value:
            value = value.split(",", 1)[0].strip().strip('"')
        return value if value else None

    def _resolve_from_registry(self, app_name: str, candidates: list[str]) -> str | None:
        if winreg is None:
            return None
        app_path_roots = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
        ]
        uninstall_roots = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for exe_name in candidates:
            for root, subkey in app_path_roots:
                try:
                    with winreg.OpenKey(root, subkey + "\\" + exe_name) as key:
                        path, _ = winreg.QueryValueEx(key, None)  # type: ignore[arg-type]
                        normalized = self._strip_windows_path(path)
                        if normalized and os.path.isfile(normalized):
                            return normalized
                except OSError:
                    continue
        match_tokens = {app_name.lower().strip()}
        match_tokens.update(candidate.lower().replace(".exe", "") for candidate in candidates)
        for root, subkey in uninstall_roots:
            try:
                with winreg.OpenKey(root, subkey) as uninstall_key:
                    index = 0
                    while True:
                        try:
                            child_name = winreg.EnumKey(uninstall_key, index)
                            index += 1
                        except OSError:
                            break
                        try:
                            with winreg.OpenKey(uninstall_key, child_name) as child_key:
                                values = {}
                                for value_name in ("DisplayName", "DisplayIcon", "InstallLocation"):
                                    try:
                                        values[value_name], _ = winreg.QueryValueEx(child_key, value_name)
                                    except OSError:
                                        values[value_name] = ""
                                haystacks = [str(values.get("DisplayName") or "").lower(), child_name.lower()]
                                if not any(token and token in hay for token in match_tokens for hay in haystacks):
                                    continue
                                display_icon = self._strip_windows_path(values.get("DisplayIcon") or "")
                                if display_icon and os.path.isfile(display_icon):
                                    return display_icon
                                install_dir = self._strip_windows_path(values.get("InstallLocation") or "")
                                if install_dir and os.path.isdir(install_dir):
                                    for exe_name in candidates:
                                        target = os.path.join(install_dir, exe_name)
                                        if os.path.isfile(target):
                                            return target
                        except OSError:
                            continue
            except OSError:
                continue
        return None

    def _resolve_from_start_menu(self, app_name: str, candidates: list[str]) -> str | None:
        tokens = {app_name.lower().strip()}
        tokens.update(candidate.lower().replace(".exe", "") for candidate in candidates)
        for base_dir in self._START_MENU_DIRS:
            if not os.path.isdir(base_dir):
                continue
            try:
                for root, dirs, files in os.walk(base_dir):
                    depth = root.replace(base_dir, "").count(os.sep)
                    if depth > 4:
                        dirs[:] = []
                        continue
                    for filename in files:
                        lower_name = filename.lower()
                        if not lower_name.endswith(".lnk"):
                            continue
                        stem = lower_name[:-4]
                        if any(token and token in stem for token in tokens):
                            return os.path.join(root, filename)
            except PermissionError:
                continue
        return None

    def _resolve_macos_app(self, exe_name: str) -> str | None:
        """通过 mdfind 和常见 Applications 目录定位 macOS app bundle 或可执行文件。"""
        tokens = {exe_name.lower().strip()}
        # 常见 .app bundle 名称映射
        bundle_map = {
            "safari": "Safari",
            "notes": "Notes",
            "code": "Visual Studio Code",
            "vscode": "Visual Studio Code",
            "chrome": "Google Chrome",
            "edge": "Microsoft Edge",
            "firefox": "Firefox",
            "iterm2": "iTerm",
            "terminal": "Terminal",
        }
        bundle_name = bundle_map.get(exe_name.lower())

        # 1. mdfind
        try:
            query = f"kMDItemKind == 'Application' && kMDItemDisplayName == '{bundle_name or exe_name}*'"
            result = run_text(["mdfind", query], timeout=5)
            for line in result.stdout.strip().splitlines():
                if line.strip().endswith(".app") and os.path.isdir(line.strip()):
                    return line.strip()
        except Exception:
            pass

        # 2. 常见 Applications 目录
        app_dirs = [
            "/Applications",
            os.path.expanduser("~/Applications"),
            "/System/Applications",
        ]
        for base_dir in app_dirs:
            if not os.path.isdir(base_dir):
                continue
            try:
                for item in os.listdir(base_dir):
                    lower = item.lower()
                    if not lower.endswith(".app"):
                        continue
                    stem = lower[:-4]
                    if any(token and token in stem for token in tokens):
                        return os.path.join(base_dir, item)
                    # 匹配 bundle_name
                    if bundle_name and bundle_name.lower().replace(" ", "") in stem.replace(" ", ""):
                        return os.path.join(base_dir, item)
            except PermissionError:
                continue
        return None
