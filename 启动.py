"""思忆集 启动器 — 并行启动后端 + Electron 前端"""
import subprocess, sys, time, os, socket, threading

# 强制 stdout/stderr 使用 UTF-8（避免 GBK 编码报错）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(ROOT, ".venv3.11")


def _resolve_venv_python(venv_dir: str) -> str:
    """解析 venv 的真实解释器路径，绕过 uv redirector shim。

    uv 创建的 venv 其 ``Scripts\\python.exe`` 是 redirector launcher：
    每次用它启动都会额外 spawn 一个真实 uv 解释器子进程，导致进程树
    出现"看似双 watchdog / 双 headless 嵌套"的多层结构。这里直接从
    ``pyvenv.cfg`` 的 ``home`` 解析真实解释器并直接调用，配合调用方
    注入 ``PYTHONPATH``（指向 site-packages），后端进程树收敛为
    真实的 watchdog + headless 两层。
    """
    cfg = os.path.join(venv_dir, "pyvenv.cfg")
    home = ""
    try:
        with open(cfg, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("home") and "=" in line:
                    home = line.split("=", 1)[1].strip()
                    break
    except Exception:
        pass
    if home and os.path.isdir(home):
        exe = os.path.join(home, "python.exe")
        if os.path.isfile(exe):
            return exe
    # 无 pyvenv.cfg 或解析失败：回退到 venv 的 shim 路径（功能不受影响）
    return os.path.join(venv_dir, "Scripts", "python.exe")


PYTHON = _resolve_venv_python(VENV_DIR)
# 真实解释器不会自动携带 venv 的 site-packages，需显式注入 PYTHONPATH
SITE_PACKAGES = os.path.join(VENV_DIR, "Lib", "site-packages")
# 前端实际项目位于 desk-notes/packages/desktop（npm workspaces）
# 注意：desk-notes/package.json 是遗留文件，其 electron:dev 脚本（5175+localhost）已过时，
# 真正的脚本在 packages/desktop/package.json（5190 + 127.0.0.1 + --no-sandbox）
FRONTEND_DIR = os.path.join(ROOT, "desk-notes", "packages", "desktop")
PORT = 18765
# Vite 默认端口（启动.py 会清理这些端口的残留进程，避免 wait-on 等不到导致 Electron 不启动）
# 5175-5178: 遗留 desk-notes/package.json 脚本可能占用的端口
# 5190-5193: 当前 packages/desktop/package.json 脚本端口
VITE_PORTS = (5175, 5176, 5177, 5178, 5190, 5191, 5192, 5193)

# 后端目录
BACKEND_DIR = os.path.join(ROOT, "思忆集app")
if not os.path.isdir(BACKEND_DIR):
    print("ERROR: 找不到后端目录（思忆集app）")
    input("Press Enter to exit...")
    sys.exit(1)


def port_open(port):
    """检测本地端口是否已被监听。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except Exception:
            return False


def _get_python_processes():
    """获取所有 python.exe 进程的 PID 和命令行。

    Windows 上优先使用 PowerShell，回退到 wmic（部分系统已移除）。
    """
    if sys.platform != "win32":
        return []
    # PowerShell 方式：更现代，Windows 11 也可用
    ps_cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Select-Object ProcessId,CommandLine | "
        "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-OutputFormat", "Text", "-Command", ps_cmd],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
        if result.returncode == 0 and result.stdout:
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # 回退到 wmic
    try:
        result = subprocess.run(
            'wmic process where "Name=\'python.exe\'" get ProcessId,CommandLine /format:csv',
            shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
        lines = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("Node,"):
                continue
            parts = line.split(",")
            if len(parts) < 3:
                continue
            pid = parts[-1]
            cmdline = ",".join(parts[1:-1])
            lines.append(f"{pid}|{cmdline}")
        return lines
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


def kill_process_on_port(port):
    """杀掉占用指定端口的进程（仅处理 LISTENING 状态）。"""
    if sys.platform != "win32":
        return
    try:
        result = subprocess.run(
            f'netstat -ano | findstr ":{port}"',
            shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
        )
        killed = set()
        for line in result.stdout.splitlines():
            if "LISTENING" not in line:
                continue
            parts = line.strip().split()
            if len(parts) < 5:
                continue
            try:
                pid = int(parts[-1])
            except (ValueError, TypeError):
                continue
            if pid in killed or pid <= 4:  # 跳过系统进程
                continue
            try:
                os.kill(pid, 9)
                killed.add(pid)
                print(f"  已终止占用端口 {port} 的进程 PID={pid}")
                time.sleep(0.3)
            except (OSError, ValueError, TypeError):
                pass
    except subprocess.TimeoutExpired:
        pass


def kill_stale_backend_processes():
    """杀掉所有残留的后端 python 进程（watchdog + headless_main）。

    端口检测只能发现正在监听的进程；若后端启动失败（如锁冲突导致 sys.exit），
    进程可能残留但不监听端口。这里通过命令行匹配兜底清理。

    必须同时清理 ``backend.process_watchdog``：孤儿 watchdog 不占端口、不带锁，
    若只杀 headless_main 会残留孤儿 watchdog，再次启动就叠加成双 watchdog 嵌套——
    两个 watchdog 反复重启后端互相竞争（锁冲突 → headless_main exit 1 → 重启）。
    """
    killed = set()
    for line in _get_python_processes():
        if (
            "backend.headless_main" not in line
            and "backend.process_watchdog" not in line
        ):
            continue
        try:
            pid = int(line.split("|", 1)[0])
        except (ValueError, TypeError):
            continue
        if pid in killed:
            continue
        try:
            os.kill(pid, 9)
            killed.add(pid)
            print(f"  已终止残留后端进程 PID={pid}")
            time.sleep(0.3)
        except (OSError, ValueError, TypeError):
            pass


def remove_lock_file():
    """移除单实例锁文件（带重试）。

    msvcrt.locking 持有的文件在进程被 SIGKILL 后仍可能短暂被 OS 锁定，
    因此需要在杀进程后重试几次。
    """
    lock = os.path.join(
        os.environ.get("TEMP", os.environ.get("TMP", "/tmp")),
        "siyi_headless.lock",
    )
    if not os.path.exists(lock):
        return
    for attempt in range(5):
        try:
            os.remove(lock)
            print(f"  已移除锁文件 (尝试 {attempt + 1}/5)")
            return
        except PermissionError:
            # 文件仍被占用，等待后重试
            time.sleep(0.5)
        except OSError:
            time.sleep(0.3)
    print(f"  警告: 无法移除锁文件 {lock}（可能仍被占用）")


# === 前置检查 ===
if not os.path.isfile(PYTHON):
    print(f"ERROR: 虚拟环境 Python 未找到: {PYTHON}")
    print("请确认已创建虚拟环境: .venv3.11/")
    input("Press Enter to exit...")
    sys.exit(1)

# === 清理旧进程（顺序很重要：先杀进程，再清锁文件）===
# 1. 杀掉所有残留的 backend.headless_main 进程（无论是否监听端口）
print("检查残留后端进程...")
kill_stale_backend_processes()

# 2. 杀掉占用端口的进程（兼容非 backend 进程占用情况）
if port_open(PORT):
    print(f"端口 {PORT} 已被占用，正在清理...")
    kill_process_on_port(PORT)

# 2.1 清理残留 Vite 进程（占用 5175+ 会导致 wait-on 等不到 → Electron 不启动）
for vp in VITE_PORTS:
    if port_open(vp):
        print(f"清理残留 Vite 进程（端口 {vp}）...")
        kill_process_on_port(vp)

# 3. 等待 OS 释放 msvcrt 锁，再移除锁文件
time.sleep(1)
remove_lock_file()
time.sleep(0.5)

# ──────────────────────────────────────────────
# 并行启动：后端 + Electron 同时开始
# ──────────────────────────────────────────────

# 构造后端环境变量
env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"
# 绕过 uv venv shim：真实解释器不会自动携带 venv 的 site-packages，需显式注入
if os.path.isdir(SITE_PACKAGES):
    env["PYTHONPATH"] = SITE_PACKAGES + os.pathsep + env.get("PYTHONPATH", "")

env_file = os.path.join(BACKEND_DIR, ".env")
if os.path.isfile(env_file):
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and val:
                    env[key] = val

# 日志文件（重定向子进程输出，避免污染启动器终端）
BACKEND_LOG = os.path.join(ROOT, "backend.dev.log")
ELECTRON_LOG = os.path.join(ROOT, "electron.dev.log")
backend_log = open(BACKEND_LOG, "w", encoding="utf-8")
electron_log = open(ELECTRON_LOG, "w", encoding="utf-8")

# 启动后端（新控制台窗口）— 经 process_watchdog 包裹，崩溃自动重启
# P1.1：用看门狗替代裸 Popen，支持指数退避重启 + 熔断
print(f"[1/2] 启动后端 ({BACKEND_DIR}, watchdog-enabled) ...")
print(f"      日志 -> {BACKEND_LOG}")
backend_proc = subprocess.Popen(
    [PYTHON, "-m", "backend.process_watchdog"],
    cwd=BACKEND_DIR,
    env=env,
    stdout=backend_log,
    stderr=subprocess.STDOUT,
    encoding="utf-8",
    errors="replace",
    creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
)

# 立即启动 Electron（新控制台窗口）— 不等待后端就绪
print("[2/2] 启动 Electron 前端 (Vite 冷编译约 5-20s) ...")
print(f"      日志 -> {ELECTRON_LOG}")
electron_proc = subprocess.Popen(
    "npm run electron:dev",
    cwd=FRONTEND_DIR,
    env=env,
    stdout=electron_log,
    stderr=subprocess.STDOUT,
    shell=True,
    encoding="utf-8",
    errors="replace",
    creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
)

# 在等待后端的同时，编译已经在进行
print("\n等待后端就绪（Vite 编译与此并行进行）...")
backend_ok = False
for i in range(30):
    if port_open(PORT):
        backend_ok = True
        print(f"  [OK] 后端已启动 (耗时 ~{i+1}s)")
        break
    # 同时检查 Electron 是否过早退出
    if electron_proc.poll() is not None:
        print("ERROR: Electron 前端启动失败")
        print(f"请检查日志: {ELECTRON_LOG}")
        input("Press Enter to exit...")
        sys.exit(1)
    time.sleep(1)

if not backend_ok:
    print("ERROR: 后端启动超时（30s）")
    print("请检查后端控制台窗口中的错误信息")
    input("Press Enter to exit...")
    sys.exit(1)

# 最终检查 Electron 是否存活
if electron_proc.poll() is not None:
    print("ERROR: Electron 前端启动失败")
    print(f"请检查日志: {ELECTRON_LOG}")
    input("Press Enter to exit...")
    sys.exit(1)

print("[OK] 思忆集 启动完成！")
print(f"  后端接口 -> ws://127.0.0.1:{PORT}")
print("  Electron 前端正在 Vite 编译中（新窗口可见进度）")
print("")
print("提示：此窗口将在 3 秒后自动关闭（服务继续在后台运行）")
time.sleep(3)

# 关闭日志文件句柄，避免资源泄漏
backend_log.close()
electron_log.close()
