import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from subprocess import TimeoutExpired


def _read_startup_log_tail(log_path: Path, limit: int = 80) -> str:
    if not log_path.is_file():
        return ""
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[-max(1, limit):])


def _remove_lock_file() -> None:
    lock_path = Path(tempfile.gettempdir()) / "siyi_desktop_app.lock"
    if lock_path.exists():
        try:
            lock_path.unlink()
        except OSError:
            pass


def _contains_blocking_marker(text: str) -> bool:
    lowered = text.lower()
    markers = [
        "unhandled startup exception",
        "traceback",
        "crash.recovery",
        "single instance lock takeover failed",
        "smoke_failed",
    ]
    return any(marker in lowered for marker in markers)


def run_check(python_executable: str, app_entry: Path, rounds: int, auto_exit_ms: int, timeout_sec: int) -> int:
    log_dir = Path(tempfile.gettempdir()) / "siyi_cold_start_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    failures = []

    for index in range(1, rounds + 1):
        _remove_lock_file()
        env = dict(os.environ)
        env["SIYI_LOG_LEVEL"] = env.get("SIYI_LOG_LEVEL", "INFO")
        env["SIYI_LAUNCH_SOURCE"] = "cold-start-check"
        env["SIYI_AUTO_EXIT_MS"] = str(auto_exit_ms)

        command = [python_executable, str(app_entry)]
        env.setdefault("SIYI_NONINTERACTIVE_STARTUP", "1")
        env.setdefault("SIYI_SKIP_SINGLE_INSTANCE_LOCK", "1")
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        startup_log = log_dir / f"startup_round_{index}.log"
        env["SIYI_STARTUP_LOG_PATH"] = str(startup_log)

        timed_out = False
        try:
            completed = subprocess.run(command, env=env, check=False, capture_output=True, text=True, timeout=timeout_sec)
        except TimeoutExpired as exc:
            timed_out = True
            completed = subprocess.CompletedProcess(
                args=command,
                returncode=124,
                stdout=(exc.stdout.decode("utf-8", errors="ignore") if isinstance(exc.stdout, bytes) else (exc.stdout or "")),
                stderr=(exc.stderr.decode("utf-8", errors="ignore") if isinstance(exc.stderr, bytes) else (exc.stderr or "")) + "\nTIMEOUT",
            )

        tail_text = _read_startup_log_tail(startup_log, limit=120)
        has_blocking_marker = _contains_blocking_marker(tail_text)
        ok = (completed.returncode == 0) and (not has_blocking_marker) and (not timed_out)
        status = "OK" if ok else "FAILED"
        print(f"COLD_START_ROUND {index}/{rounds} status={status} exit_code={completed.returncode}")

        if not ok:
            failures.append(
                {
                    "round": index,
                    "exit_code": completed.returncode,
                    "stdout_tail": "\n".join((completed.stdout or "").splitlines()[-30:]),
                    "stderr_tail": "\n".join((completed.stderr or "").splitlines()[-30:]),
                    "startup_log_tail": tail_text,
                }
            )

    print(f"COLD_START_SUMMARY rounds={rounds} failed={len(failures)} at={datetime.now().isoformat()}")
    if failures:
        first = failures[0]
        print("COLD_START_FIRST_FAILURE")
        print(f"round={first['round']} exit_code={first['exit_code']}")
        if first["stdout_tail"]:
            print("stdout_tail:")
            print(first["stdout_tail"])
        if first["stderr_tail"]:
            print("stderr_tail:")
            print(first["stderr_tail"])
        if first["startup_log_tail"]:
            print("startup_log_tail:")
            print(first["startup_log_tail"])
        return 1
    print("COLD_START_OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="思忆集桌面端冷启动稳定性检查")
    parser.add_argument("--rounds", type=int, default=30, help="执行轮次，默认 30")
    parser.add_argument("--auto-exit-ms", type=int, default=1200, help="每轮自动退出毫秒数，默认 1200")
    parser.add_argument("--timeout-sec", type=int, default=20, help="单轮启动超时时间（秒），默认 20")
    parser.add_argument(
        "--app-entry",
        # 注（§9-9）：本检查量的是**桌面 GUI 冷启动**路径（QT offscreen），入口是
        # 思忆集app/main.py（23 行兼容 shim → app_entry.main）；与 doctor 健康探针
        # （已改指 backend/headless_main.py 生产后端入口）不是同一目标，故此处**保留**
        # shim 入口。待 app_entry/main.py 随 M-1 归档时再统一处置本脚本。
        default=str(Path(__file__).resolve().parents[1] / "思忆集app" / "main.py"),
        help="桌面端入口脚本路径",
    )
    parser.add_argument("--python", default=sys.executable, help="Python 可执行文件路径")
    args = parser.parse_args()

    rounds = max(1, int(args.rounds))
    auto_exit_ms = max(0, int(args.auto_exit_ms))
    timeout_sec = max(5, int(args.timeout_sec))
    app_entry = Path(args.app_entry).resolve()

    if not app_entry.is_file():
        print(f"COLD_START_INVALID_ENTRY {app_entry}")
        return 2

    return run_check(args.python, app_entry, rounds, auto_exit_ms, timeout_sec)


if __name__ == "__main__":
    raise SystemExit(main())
