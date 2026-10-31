#!/usr/bin/env python3
"""一键全门禁汇总（debt-v2 TASK-M1-1 / M1 完成定义机械化）。

「完成」的唯一合法形态 = commit SHA + 该 commit CI 绿 + 已 push。本脚本是
CI 绿的本机预演：顺序跑本地可执行的门禁，逐项 PASS/FAIL + 汇总 + 总退出码，
并检查工作树干净（无 staged/未提交残留）。

用法：
  python scripts/done_check.py [--with-coverage]
  --with-coverage：额外跑 coverage_gate.py（很慢，默认跳过）
  返回值：0 = 全部通过；1 = 任一 FAIL
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gate_tooling import indent_detail, tail_detail  # noqa: E402  （scripts/ 内公共模块）

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 保证自身输出不因中文 Windows 控制台 GBK 编码崩溃（子进程 detail 可能含 ✅/emoji）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# (名称, 参数) 列表，均在仓库根以当前解释器执行
CHECKS: list[tuple[str, list[str]]] = [
    # 2026-09-16 新增：工具链预检 —— 先确认"尺子准不准"，再谈门禁结果。
    # 动机：ruff 与 mypy 2.3.1 分居两个解释器环境时，任何单一解释器都必然红
    # 一道，且红的是哪道随解释器切换（.venv3.11 红 mypy / 2.3.1 环境红 ruff），
    # 多个会话各自把假红当"环境性欠债"上报，真实信号被淹没。
    # 见 .workbuddy/memory/MEMORY.md「门禁解释器坑」。
    ("工具链预检", ["scripts/check_gate_toolchain.py"]),
    # 2026-09-18 新增：git 仓库完整性（对象库 + ref 清单）——P0 事故的直接产物。
    # 动机：主仓 .git 对象库曾被静默清空（批1 的 7 个提交对象永久丢失），而**发现方式是
    # "碰巧某条 git stash 报错"** —— 对象库完整性此前没有任何机器门禁。同一次事故还
    # 静默清掉了 refs/heads/main，而 git status 干净、git log 正常，完全看不出来。
    # 故本门禁查两件事：① fsck 不得有 missing/无效 sha1 指针；② 必需 ref 必须存在
    # （棘轮基线见 scripts/git_integrity_baseline.json）。放在工具链预检之后、其余
    # 门禁之前：仓库本身坏了的话，后面所有门禁的结果都没有意义。
    ("git 完整性", ["scripts/check_git_integrity.py"]),
    ("doc 一致性", ["scripts/check_doc_index_consistency.py"]),
    ("inventory 防漂移", ["scripts/gen_inventory.py", "--check"]),
    ("无裸 pyc", ["scripts/check_no_sourceless_pyc.py"]),
    ("层边界", ["scripts/check-layer-boundaries.py"]),
    # 2026-10-31 补：CI 的 quality-gates 在「Layer boundary check」之后还有一步
    # 「Kernel boundary ratchet gate (I1 / C-7)」跑的是 kernel_boundary_ratchet.py，
    # 而本地 done_check 只跑了 check-layer-boundaries.py —— 两者不是同一道门，
    # 导致 gate_all 绿 ≠ CI 绿（实测：本地 gate_all 4/4 绿时，CI 仍因
    # services/clipboard_context.py 的 I1 违规而红）。补齐。
    ("内核边界棘轮", ["scripts/kernel_boundary_ratchet.py"]),
    ("核心模块完整性", ["scripts/check_core_integrity.py"]),
    ("pip-audit 基线", ["scripts/run_pip_audit.py"]),
    ("ruff 棘轮(改)", ["scripts/gen_quality_baseline.py", "--check-changed", "--base", "main"]),
    ("mypy 棘轮(改)", ["scripts/mypy_ratchet.py", "--check-changed", "--base", "main"]),
    ("broad-except 棘轮(改)", ["scripts/scan_broad_except.py", "--check-changed", "--base", "main"]),
    # 2026-09-18 新增：时钟纪律棘轮（P0-1 治本）——时间敏感模块不得直读墙钟。
    # 动机：env-drill 第三波（T2 时钟跳变）实证同一"naive 墙钟"病灶在
    # confirmation TTL / task_queue lease / cron 补跑 / weekly_report 四处独立
    # 复发；逐处打补丁只能治标。本门禁把纪律固化为机器断言（AGENTS.md 守则 #7），
    # 与上面三道棘轮同款：存量只允许下降，新增即失败。
    # 合规写法 = 经 思忆集app/core/clock.py（wall_ts / mono_ts / deadline_expired /
    # safe_timestamp_write / detect_rollback）。
    ("时钟纪律棘轮", ["scripts/check_clock_discipline.py"]),
    # 2026-09-18 P1-A 新增：子进程编码棘轮——裸 text=True（无 encoding=）在
    # PYTHONUTF8=1 下会把 stdout 静默置 None（实测 27 处中招，system.info/
    # clipboard.read 恒炸 'NoneType'.strip）。合规 = core.subprocess_text.run_text
    # 或显式 encoding=。基线 4 处（PIPE 流式形态，待人工确认）只降不升。
    ("子进程编码棘轮", ["scripts/check_subprocess_encoding.py"]),
    # 2026-08-24 新增：gitignore 吞源守卫——生产 .py 不得被忽略（曾吞 78 个模块，
    # HEAD 快照残缺、fresh clone 必坏，git status 又看不到，故加专门守卫）
    ("无被忽略源码", ["scripts/check_ignored_source.py"]),
    # 2026-08-27 R2 新增：F2 干净会话复核报告归档门禁（原则 P-3，堵「F2 零执行」）
    ("F2 复核归档", ["scripts/check_f2_archives.py"]),
]


def run_check(name: str, args: list[str]) -> tuple[bool, str]:
    # 强制子进程 UTF-8 输出：避免中文 Windows 控制台 GBK 编码炸掉含 ✅/emoji 的输出
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [sys.executable, *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "超时(>600s)"
    out = (proc.stdout or "") + (proc.stderr or "")
    # FAIL 时保留末若干行：各门禁脚本末行常是"提示/结论"尾注，真因在其上方
    return proc.returncode == 0, tail_detail(out, ok=proc.returncode == 0)


def git_status_clean() -> tuple[bool, str]:
    proc = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]

    # desk-notes 为独立 gitlink（无 .gitmodules），其内部改动不属于本仓工作树，忽略
    def _is_desknotes_gitlink(ln: str) -> bool:
        path = ln[3:].strip() if len(ln) >= 3 else ln.strip()
        return path == "desk-notes" or path.startswith("desk-notes/")

    dirty = [ln for ln in lines if not _is_desknotes_gitlink(ln)]
    if not dirty:
        return True, "工作树干净"
    return False, f"工作树有 {len(dirty)} 项未提交: {dirty[0]} ..."


def main() -> int:
    with_coverage = "--with-coverage" in sys.argv[1:]
    results: list[tuple[str, bool, str]] = []
    for name, args in CHECKS:
        ok, detail = run_check(name, args)
        results.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} -- {indent_detail(detail)}")

    if with_coverage:
        ok, detail = run_check("覆盖率", ["scripts/coverage_gate.py"])
        results.append(("覆盖率(预期可能红，见 TASK-M2-2 豁免)", ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] 覆盖率 -- {detail}")

    clean, status = git_status_clean()
    results.append(("git 工作树干净", clean, status))
    print(f"  [{'PASS' if clean else 'FAIL'}] {status}")

    failed = [n for n, ok, _ in results if not ok]
    print("-" * 60)
    print(f"done_check: {len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print(f"  FAIL: {', '.join(failed)}")
        print("  提示：任一 FAIL 即不满足 M1『完成』定义；修复后再 commit/push。")
        return 1
    print("  全部通过 -- 满足 M1 货币的本地预演条件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
