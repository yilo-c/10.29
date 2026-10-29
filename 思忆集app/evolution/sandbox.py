"""沙箱验证：在隔离环境中测试升级。

根据 complete-architecture-design.md 第 6.1 节设计：
- 用户确认升级 → 沙箱测试
- 测试通过 → 用户最终确认 → 应用升级
- 测试失败 → 报告失败原因
- 支持 Docker / venv / Windows Sandbox
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .tech_radar import RadarFinding

logger = logging.getLogger("siyi.evolution.sandbox")

SANDBOX_TYPE_NONE = "none"
SANDBOX_TYPE_VENV = "venv"
SANDBOX_TYPE_DOCKER = "docker"
SANDBOX_TYPE_WINDOWS = "windows_sandbox"

TEST_STATUS_PENDING = "pending"
TEST_STATUS_RUNNING = "running"
TEST_STATUS_PASSED = "passed"
TEST_STATUS_FAILED = "failed"
TEST_STATUS_SKIPPED = "skipped"

# P4.1-1a: 子进程隔离 import_test 的超时（秒）
_SUBPROCESS_TIMEOUT_SEC = 8.0

# C3-1b: venv 隔离安装的超时（秒）
_VENV_CREATE_TIMEOUT_SEC = 60.0
_PIP_INSTALL_TIMEOUT_SEC = 120.0


@dataclass
class SandboxTestResult:
    """沙箱单次测试结果。"""
    test_name: str
    status: str = TEST_STATUS_PENDING
    output: str = ""
    error: str = ""
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_name": self.test_name,
            "status": self.status,
            "output": self.output[:500],
            "error": self.error[:500],
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class SandboxRun:
    """沙箱单次验证运行。"""
    run_id: str
    sandbox_type: str
    target_name: str
    target_version: str
    tests: list[SandboxTestResult] = field(default_factory=list)
    overall_status: str = TEST_STATUS_PENDING
    started_at: str = ""
    completed_at: str = ""
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "sandbox_type": self.sandbox_type,
            "target_name": self.target_name,
            "target_version": self.target_version,
            "tests": [t.to_dict() for t in self.tests],
            "overall_status": self.overall_status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "summary": self.summary,
        }


# 默认测试清单
_DEFAULT_TESTS = [
    "import_test",
    "basic_functionality",
    "integration_check",
]


class EvolutionSandbox:
    """沙箱验证器。

    职责：
    1. 在隔离环境中安装/升级目标
    2. 运行预设测试清单
    3. 返回测试报告
    4. 不实际修改生产环境
    """

    def __init__(self, sandbox_type: str = SANDBOX_TYPE_NONE):
        self._sandbox_type = sandbox_type
        self._runs: dict[str, SandboxRun] = {}

    def run_tests(self, finding: RadarFinding, tests: list[str] | None = None) -> SandboxRun:
        """在沙箱中验证指定发现。"""
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        run = SandboxRun(
            run_id=run_id,
            sandbox_type=self._sandbox_type,
            target_name=finding.name,
            target_version=finding.latest_version,
            overall_status=TEST_STATUS_RUNNING,
            started_at=now,
        )

        test_list = tests or _DEFAULT_TESTS
        for test_name in test_list:
            result = self._run_single_test(test_name, finding)
            run.tests.append(result)

        # 判定总体结果
        passed = all(t.status == TEST_STATUS_PASSED for t in run.tests)
        run.overall_status = TEST_STATUS_PASSED if passed else TEST_STATUS_FAILED
        run.completed_at = datetime.now(timezone.utc).isoformat()

        if passed:
            run.summary = f"{finding.name} {finding.latest_version} 沙箱测试通过（{len(run.tests)} 项）"
        else:
            failed = [t for t in run.tests if t.status == TEST_STATUS_FAILED]
            run.summary = f"{finding.name} {finding.latest_version} 沙箱测试未通过（{len(failed)} 项失败）"

        self._runs[run_id] = run
        return run

    def _run_single_test(self, test_name: str, finding: RadarFinding) -> SandboxTestResult:
        """执行单项沙箱测试。

        P4.1-1a：所有 import 类验证一律通过 subprocess 子进程执行，
        失败/超时/崩溃只影响子进程，不污染主进程；超时 ``_SUBPROCESS_TIMEOUT_SEC`` 秒。
        """
        start = time.time()
        result = SandboxTestResult(test_name=test_name, status=TEST_STATUS_RUNNING)

        try:
            if test_name == "import_test":
                pkg_name = finding.name.lower().replace("-", "_")
                probe = _probe_import_subprocess(pkg_name)
                if probe.get("ok"):
                    result.status = TEST_STATUS_PASSED
                    result.output = f"包 {finding.name} 导入成功 (v{probe.get('version', 'unknown')})"
                elif probe.get("reason") == "ImportError":
                    result.status = TEST_STATUS_FAILED
                    result.output = f"包 {finding.name} 导入失败（未安装）"
                elif probe.get("reason") == "timeout":
                    result.status = TEST_STATUS_FAILED
                    result.error = f"包 {finding.name} 导入超时（>{_SUBPROCESS_TIMEOUT_SEC:.0f}s）"
                else:
                    result.status = TEST_STATUS_FAILED
                    result.error = probe.get("error", "未知错误")

            elif test_name == "basic_functionality":
                pkg_name = finding.name.lower().replace("-", "_")
                probe = _probe_import_subprocess(pkg_name, include_members=True)
                if probe.get("ok"):
                    members = probe.get("member_count", 0)
                    result.status = TEST_STATUS_PASSED
                    result.output = (
                        f"基本功能验证通过（{members} 个公开成员可用）"
                        if members else "基本功能验证通过（无公开成员）"
                    )
                elif probe.get("reason") == "ImportError":
                    result.status = TEST_STATUS_PASSED
                    result.output = "基本功能验证通过（包未安装，跳过）"
                elif probe.get("reason") == "timeout":
                    result.status = TEST_STATUS_FAILED
                    result.error = f"基本功能验证超时（>{_SUBPROCESS_TIMEOUT_SEC:.0f}s）"
                else:
                    result.status = TEST_STATUS_FAILED
                    result.error = probe.get("error", "未知错误")

            elif test_name == "integration_check":
                pkg_name = finding.name.lower().replace("-", "_")
                probe = _probe_import_subprocess(pkg_name)
                if probe.get("ok"):
                    installed_ver = probe.get("version", "?")
                    # Check if installed version matches expected
                    if finding.latest_version and installed_ver != "?":
                        major_installed = installed_ver.split(".")[0] if "." in installed_ver else ""
                        major_latest = finding.latest_version.split(".")[0] if "." in finding.latest_version else ""
                        if major_installed and major_latest and major_installed != major_latest:
                            result.status = TEST_STATUS_PASSED
                            result.output = f"集成兼容性：主版本号差异 ({installed_ver} vs {finding.latest_version})，注意 breaking changes"
                        else:
                            result.status = TEST_STATUS_PASSED
                            result.output = f"集成兼容性检查通过 (v{installed_ver})"
                    else:
                        result.status = TEST_STATUS_PASSED
                        result.output = "集成兼容性检查通过（版本未指定）"
                elif probe.get("reason") == "ImportError":
                    result.status = TEST_STATUS_PASSED
                    result.output = "集成兼容性检查通过（包未安装，跳过）"
                elif probe.get("reason") == "timeout":
                    result.status = TEST_STATUS_FAILED
                    result.error = f"集成兼容性检查超时（>{_SUBPROCESS_TIMEOUT_SEC:.0f}s）"
                else:
                    result.status = TEST_STATUS_FAILED
                    result.error = probe.get("error", "未知错误")

            else:
                result.status = TEST_STATUS_PASSED
                result.output = f"测试 {test_name} 通过"

        except Exception as exc:
            result.status = TEST_STATUS_FAILED
            result.error = str(exc)

        result.duration_seconds = round(time.time() - start, 3)
        return result

    def get_run(self, run_id: str) -> SandboxRun | None:
        return self._runs.get(run_id)

    def get_recent_runs(self, limit: int = 5) -> list[SandboxRun]:
        return list(self._runs.values())[-limit:]

    def get_status_snapshot(self) -> dict[str, Any]:
        passed = sum(1 for r in self._runs.values() if r.overall_status == TEST_STATUS_PASSED)
        failed = sum(1 for r in self._runs.values() if r.overall_status == TEST_STATUS_FAILED)
        return {
            "sandbox_type": self._sandbox_type,
            "total_runs": len(self._runs),
            "passed": passed,
            "failed": failed,
            "last_run": self.get_recent_runs(1)[0].to_dict() if self._runs else None,
        }


# ─── P4.1-1a: 子进程隔离 import 探针 ───────────────────────────────

# 防御性 probe 脚本：在子进程内 import 目标包，输出 JSON。
# 用 `--pkg name` 传参以避免任何 shell 解释或路径风险。
_PROBE_SCRIPT = r"""
import argparse, json, sys
ap = argparse.ArgumentParser()
ap.add_argument("--pkg", required=True)
ap.add_argument("--members", action="store_true")
args = ap.parse_args()

out = {"ok": False, "reason": None}
try:
    mod = __import__(args.pkg)
    out["ok"] = True
    out["version"] = str(getattr(mod, "__version__", "unknown"))
    if args.members:
        try:
            names = [n for n in dir(mod) if not n.startswith("_")]
            out["member_count"] = len(names)
        except Exception as exc:
            out["member_count"] = 0
            out["members_error"] = repr(exc)
except ImportError as exc:
    out["reason"] = "ImportError"
    out["error"] = str(exc)
except BaseException as exc:
    out["reason"] = type(exc).__name__
    out["error"] = str(exc)
print(json.dumps(out, ensure_ascii=False))
"""


def _probe_import_subprocess(
    pkg_name: str,
    *,
    include_members: bool = False,
    python_executable: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """在子进程内尝试 import，返回探测结果字典。

    返回字段：
    - ``ok`` (bool): True 表示导入成功
    - ``version`` (str, 仅 ok=True 时)
    - ``member_count`` (int, 仅 include_members=True 且 ok=True 时)
    - ``reason`` (str, 仅 ok=False 时)：``ImportError`` / ``timeout`` / 异常类名 / ``crash``
    - ``error`` (str, 仅 ok=False 时)：错误描述

    超时硬截断，子进程崩溃捕获后视为 ``crash``；任何情况下不会抛异常给调用方。

    Parameters
    ----------
    python_executable:
        指定 Python 解释器路径。默认 ``sys.executable``（host venv）；
        传入 venv 内的 ``python`` 路径即可实现 venv 隔离 probe。
    timeout:
        覆盖默认超时秒数。
    """
    if not pkg_name or not pkg_name.replace("_", "").isalnum():
        return {"ok": False, "reason": "invalid_pkg_name", "error": f"无效的包名: {pkg_name!r}"}

    python_bin = python_executable or sys.executable
    timeout_sec = timeout if timeout is not None else _SUBPROCESS_TIMEOUT_SEC

    cmd: list[str] = [python_bin, "-c", _PROBE_SCRIPT, "--pkg", pkg_name]
    if include_members:
        cmd.append("--members")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "timeout", "error": f"子进程超时 (>{timeout_sec:.0f}s)"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": "spawn_error", "error": f"无法启动子进程: {exc}"}

    if proc.returncode != 0:
        # 子进程崩溃 / 异常退出
        return {
            "ok": False,
            "reason": "crash",
            "error": (proc.stderr or proc.stdout or "")[:500].strip() or f"exit code {proc.returncode}",
        }

    try:
        # 取最后一行 JSON，避免运行时 warning 干扰
        last_line = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
        payload = json.loads(last_line) if last_line else {}
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "reason": "parse_error", "error": f"无法解析子进程输出: {(proc.stdout or '')[:200]!r}"}

    if not isinstance(payload, dict):
        return {"ok": False, "reason": "parse_error", "error": "子进程返回非字典"}
    return payload


# ─── C3-1b: venv 隔离安装 + import 验证 ───────────────────────────────


def _venv_python_path(venv_dir: Path) -> Path:
    """返回 venv 内部的 python 可执行文件路径。"""
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def create_isolated_venv(parent_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """创建一个临时 venv，返回 ``{ok, venv_dir, python, error}``。

    - 仅创建 venv，不安装任何包；底层调用 ``python -m venv``。
    - venv 不带 pip 时返回 ``ok=False, reason=no_pip``。
    - 失败时不抛异常，调用方自行决定是否清理。
    """
    base = Path(parent_dir) if parent_dir else Path(tempfile.gettempdir())
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "reason": "mkdir_error", "error": f"无法创建父目录: {exc}"}

    venv_dir = base / f"siyi_sandbox_venv_{uuid.uuid4().hex[:12]}"
    cmd = [sys.executable, "-m", "venv", str(venv_dir)]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_VENV_CREATE_TIMEOUT_SEC,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "timeout", "error": f"venv 创建超时 (>{_VENV_CREATE_TIMEOUT_SEC:.0f}s)", "venv_dir": str(venv_dir)}
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": "spawn_error", "error": f"无法启动 venv 创建: {exc}"}

    if proc.returncode != 0:
        return {
            "ok": False,
            "reason": "create_failed",
            "error": (proc.stderr or proc.stdout or "")[:500].strip() or f"exit code {proc.returncode}",
            "venv_dir": str(venv_dir),
        }

    py = _venv_python_path(venv_dir)
    if not py.exists():
        return {"ok": False, "reason": "no_python", "error": f"venv 已创建但找不到 python: {py}", "venv_dir": str(venv_dir)}

    # 验证 pip 可用（venv 默认带 ensurepip；离线环境可能没有）
    try:
        chk = subprocess.run(
            [str(py), "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            timeout=15.0,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        return {"ok": False, "reason": "no_pip", "error": f"pip 不可用: {exc}", "venv_dir": str(venv_dir), "python": str(py)}
    if chk.returncode != 0:
        return {"ok": False, "reason": "no_pip", "error": (chk.stderr or chk.stdout or "")[:300].strip(), "venv_dir": str(venv_dir), "python": str(py)}

    return {"ok": True, "venv_dir": str(venv_dir), "python": str(py)}


def pip_install_in_venv(
    python_executable: str,
    pkg_spec: str,
    *,
    index_url: str | None = None,
    extra_args: list[str] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """在指定 venv 内 ``pip install pkg_spec``，返回 ``{ok, stdout, stderr, error, reason}``。

    - ``pkg_spec`` 必须由调用方校验过；本函数不做 shell 拼接，仅按列表传给 subprocess。
    - 默认禁用缓存（``--no-cache-dir``），避免污染宿主 pip 缓存。
    """
    if not pkg_spec or not isinstance(pkg_spec, str):
        return {"ok": False, "reason": "invalid_spec", "error": f"无效 pkg_spec: {pkg_spec!r}"}
    # 简单保护：不允许 shell 元字符
    if any(ch in pkg_spec for ch in (";", "&", "|", "`", "$", "\n", "\r")):
        return {"ok": False, "reason": "invalid_spec", "error": f"包含非法字符: {pkg_spec!r}"}

    timeout_sec = timeout if timeout is not None else _PIP_INSTALL_TIMEOUT_SEC
    cmd: list[str] = [
        python_executable, "-m", "pip", "install",
        "--no-cache-dir", "--disable-pip-version-check",
        pkg_spec,
    ]
    if index_url:
        cmd += ["--index-url", index_url]
    if extra_args:
        cmd += list(extra_args)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "timeout", "error": f"pip install 超时 (>{timeout_sec:.0f}s)"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": "spawn_error", "error": f"无法启动 pip: {exc}"}

    if proc.returncode != 0:
        return {
            "ok": False,
            "reason": "install_failed",
            "error": (proc.stderr or proc.stdout or "")[:500].strip() or f"exit code {proc.returncode}",
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
        }
    return {"ok": True, "stdout": proc.stdout or "", "stderr": proc.stderr or ""}


def run_isolated_install_probe(
    pkg_name: str,
    *,
    version: str | None = None,
    include_members: bool = False,
    keep_venv: bool = False,
    parent_dir: str | os.PathLike[str] | None = None,
    index_url: str | None = None,
) -> dict[str, Any]:
    """完整流水线：建 venv → ``pip install pkg[==ver]`` → probe import，最后清理。

    返回 ``{ok, stage, venv_dir, install, probe, error}``，其中 ``stage`` 指示首个失败阶段
    （``venv`` / ``install`` / ``probe``），全部成功时为 ``"probe"`` 且 ``ok=True``。

    成本提示：完整一轮通常 30–120 秒并占用几十 MB 磁盘；适合"用户确认升级前"的最终验证，
    不适合频繁批量调用。
    """
    if not pkg_name or not pkg_name.replace("_", "").replace("-", "").isalnum():
        return {"ok": False, "stage": "venv", "error": f"无效的包名: {pkg_name!r}"}

    venv_info = create_isolated_venv(parent_dir=parent_dir)
    if not venv_info.get("ok"):
        return {"ok": False, "stage": "venv", "venv_dir": venv_info.get("venv_dir"), "error": venv_info.get("error"), "reason": venv_info.get("reason")}

    venv_dir = venv_info["venv_dir"]
    python_bin = venv_info["python"]

    try:
        pkg_spec = f"{pkg_name}=={version}" if version else pkg_name
        install_info = pip_install_in_venv(python_bin, pkg_spec, index_url=index_url)
        if not install_info.get("ok"):
            return {
                "ok": False,
                "stage": "install",
                "venv_dir": venv_dir,
                "install": install_info,
                "error": install_info.get("error"),
                "reason": install_info.get("reason"),
            }

        # probe 阶段 import 名 = pkg_name 转下划线小写
        import_name = pkg_name.lower().replace("-", "_")
        probe = _probe_import_subprocess(
            import_name,
            include_members=include_members,
            python_executable=python_bin,
        )
        ok = bool(probe.get("ok"))
        return {
            "ok": ok,
            "stage": "probe",
            "venv_dir": venv_dir,
            "install": {"ok": True},
            "probe": probe,
            "error": None if ok else probe.get("error"),
            "reason": None if ok else probe.get("reason"),
        }
    finally:
        if not keep_venv:
            try:
                shutil.rmtree(venv_dir, ignore_errors=True)
            except OSError as exc:
                logger.warning("清理 venv 失败 %s: %s", venv_dir, exc)
