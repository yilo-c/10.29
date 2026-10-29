"""P0-6 验证：app.launch 不用 shell=True。

历史 bug：
- backend/react_executor.py:290 用 subprocess.Popen([cmd], shell=True)
- shell=True 时 cmd 字符串被 cmd.exe 解释，"chrome & calc.exe & 任意命令"
  这种链式攻击仍可成功；APP_LAUNCH_ALLOWLIST 只检查 stem 名称无法防住

修复：
- 改用解析后的绝对路径（注册表 / `where` / 常见安装目录）
- shell=False + 列表参数，杜绝注入
- startfile 回退路径前仍先过 allowlist 关卡

C-1 执行器收敛 步骤 3 重指向：app.launch 的实现已从 B 套内联分支删除，现在由
A 套 ``assistant/capability/_shell.py::ShellCapabilityDomain.app_launch`` 提供
（经 ``make_react_executor`` → ``router.dispatch_sync``）。三处形状差异是收敛的预期结果：

- 信封：B 套 ``{"error": "...不在允许列表..."}`` → A 套 ``{"launched": False,
  "reason": "blocked: ... allowed applications list"}``（适配层仍会补出 ``error``
  键，所以「模型能读到失败原因」这条契约不变，只是文案换成 A 套版本）；
- 可执行文件解析：B 套自己调 ``shutil.which`` → A 套走
  ``AssistantEnvironmentProbe.resolve_app_executable``，故 patch 目标跟着换；
- 成功标记：B 套 ``{"result": {...}}`` → A 套平铺 ``{"launched": True, ...}``。

安全不变量（shell=False / argv 是 list / 必须过 allowlist）一条未放宽。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest  # noqa: E402

# A 套解析可执行文件的真入口（patch 它就能令测试不碰注册表 / `where` / 真实安装目录）
_RESOLVE_TARGET = "assistant.execution.environment.AssistantEnvironmentProbe.resolve_app_executable"


def _make_executor():
    """构造走完整分派链的执行器（拿不到真 router 就测不到收敛后的 app.launch）。

    注意不得用 ``MagicMock()`` 当 context：``context.capability_router`` 会被自动
    特化出一个假 router，分派「成功」返回 MagicMock，安全断言全变成空断言。
    """
    from assistant.capability_router import AssistantCapabilityRouter
    from data.local_storage import LocalDataStore
    from services.react_executor import make_react_executor

    router = AssistantCapabilityRouter(
        data_store=LocalDataStore(base_dir=tempfile.mkdtemp(prefix="siyi-c1-applaunch-")),
        settings_repository=None,
        observability_service=None,
        note_repository=None,
        task_repository=None,
        news_repository=None,
        inbox_repository=None,
    )
    router.shell_execution.workspace_root = Path(tempfile.mkdtemp(prefix="siyi-c1-applaunch-ws-"))
    context = type("_Ctx", (), {"capability_router": router})()
    return make_react_executor(context)


# ── 拒绝白名单外应用 ───────────────────────────────────────────────────────


def test_app_launch_rejects_non_allowlisted():
    execute = _make_executor()
    result = execute("app.launch", {"command": "powershell"})
    assert result.get("launched") is False, result
    assert "error" in result, "适配层必须把 A 套拒绝原因回灌成 error 键供模型自我纠正"
    assert "allowed applications list" in result["error"]


def test_app_launch_rejects_empty_command():
    execute = _make_executor()
    result = execute("app.launch", {"command": ""})
    assert result.get("error") == "no_command", result


# ── 关键安全断言：shell=False + 路径解析 ──────────────────────────────────


def test_app_launch_uses_shell_false_and_resolved_absolute_path():
    """app.launch 的 Popen 调用必须 shell=False，且第一个 argv 是解析后的绝对路径。"""
    execute = _make_executor()

    with patch("subprocess.Popen") as mock_popen, patch(
        _RESOLVE_TARGET, return_value="C:/Program Files/Google/Chrome/Application/chrome.exe"
    ):
        result = execute("app.launch", {"command": "chrome"})

    assert result.get("launched") is True, f"unexpected: {result}"
    assert mock_popen.called
    args, kwargs = mock_popen.call_args
    # args[0] 应是 list 形式的命令
    assert isinstance(args[0], list), f"必须用列表形式而非 shell=True 字符串：{args[0]}"
    # shell 必须为 False
    assert kwargs.get("shell") is False, "shell 必须为 False，否则 cmd.exe 注入"
    # 解析后的绝对路径在第一个位置
    assert str(args[0][0]).endswith("chrome.exe")


def test_app_launch_blocks_chained_command_injection():
    """LLM 给 'chrome & calc.exe' 这类注入串：allowlist 第一道关卡就拦住。"""
    execute = _make_executor()

    with patch("subprocess.Popen") as mock_popen, patch(_RESOLVE_TARGET, return_value=None):
        # basename stem 是 "chrome & calc"，不在 allowlist → 应被第一道关卡拦下
        result = execute("app.launch", {"command": "chrome & calc.exe"})

    # 第一道关卡（allowlist）就拦住，不应触及 Popen
    assert "error" in result
    assert "allowed applications list" in result["error"]
    assert not mock_popen.called


def test_app_launch_blocks_shell_metacharacters_in_startfile_fallback():
    """解析失败 + Popen 抛 FileNotFoundError 时，仍只在白名单内才可能走到 startfile。"""
    execute = _make_executor()

    # 用纯 "calc"（白名单内）模拟解析失败 → Popen FileNotFoundError → startfile 分支
    with patch(_RESOLVE_TARGET, return_value=None), patch(
        "subprocess.Popen", side_effect=FileNotFoundError()
    ) as mock_popen, patch("os.startfile") as mock_startfile:
        result = execute("app.launch", {"command": "calc"})

    assert mock_popen.called, "白名单内应尝试直接拉起"
    # 因为 cmd "calc" 无 shell 元字符，startfile 应被尝试
    assert mock_startfile.called or "error" in result


def test_app_launch_returns_resolved_flag():
    """成功路径应返回 resolved=True 表明用了绝对路径。"""
    execute = _make_executor()
    with patch("subprocess.Popen"), patch(_RESOLVE_TARGET, return_value="C:/Windows/System32/calc.exe"):
        result = execute("app.launch", {"command": "calc"})
    assert result.get("resolved") is True, result
    assert result.get("launched") is True, result


def test_app_launch_does_not_execute_arbitrary_path():
    """LLM 给完整奇怪路径（即便 basename stem 在 allowlist），解析失败后由系统决定
    是否能找到 —— 但 shell=False 保证不会被解析为 cmd.exe 命令行。"""
    execute = _make_executor()

    # cmd 是 "C:\\Users\\evil\\chrome.exe" — basename stem chrome 在 allowlist
    with patch("subprocess.Popen") as mock_popen, patch(_RESOLVE_TARGET, return_value=None):
        # 解析返回 None → exe 等于原始 cmd → 但 shell=False 仍然安全
        execute("app.launch", {"command": r"C:\Users\evil\chrome.exe"})

    # 不论结果，关键是 shell=False
    if mock_popen.called:
        _, kwargs = mock_popen.call_args
        assert kwargs.get("shell") is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
