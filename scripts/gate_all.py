#!/usr/bin/env python3
"""gate_all.py —— 本地全量门禁汇总（2026-09-16 用户拍板⑥：「完成」定义改挂本地 gate_all）。

背景（P0-5 实测确证）：push 从未成功、远端空仓库、15 道 GitHub 门禁从未运行过，
AGENTS.md「完成 = commit SHA + CI 绿 + 已 push」结构上不可满足。本脚本把 CI 的
本地等价物一次跑齐：

  1. done_check.py  现有全部静态门禁 + 工作树干净检查（复用不重复造）
                    项数不硬编码：运行时从 done_check.CHECKS 动态取
                    （历史教训：文案曾写死「11 道」，而实际已 12→13 项，
                     2026-09-18 随「时钟纪律棘轮」入库时一并治本）
  2. kwarg 门禁     tests/test_handler_kwarg_survey_20260916.py（keyword-only 误调防复发）
  3. facade 对表    scripts/facade_attr_smoke.py（属性名漂移防复发，存在才跑）
  4. 备份新鲜度     最近 git bundle ≤7 天（拍板⑥捆绑项：本地门禁绿≠数据有兜底）

用法：
  python scripts/gate_all.py            # 全部门禁
  python scripts/gate_all.py --skip-pytest   # 跳过 pytest 项（快速模式）
退出码：0=全绿；1=任一 FAIL。

「完成」新定义：commit SHA + gate_all 绿（远端 push 修复另案，见
deliverables/fix-and-upgrade-execution-plan-2026-09-16.md S2-4）。
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gate_tooling import indent_detail, project_python, tail_detail  # noqa: E402  （scripts/ 内公共模块）

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE_GLOBS = [
    "D:/h/3.25/siyiji-local-backup-*.bundle",
    os.path.join(REPO_ROOT, "..", "siyiji-local-backup-*.bundle"),
]
BUNDLE_MAX_AGE_DAYS = 7

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _run(args: list[str], timeout: int = 900) -> tuple[bool, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            args,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"超时(>{timeout}s)"
    out = (proc.stdout or "") + (proc.stderr or "")
    # FAIL 时保留末若干行：子门禁末行常是"提示：…"尾注，真因在其上方（见 tail_detail 文档）
    return proc.returncode == 0, tail_detail(out, ok=proc.returncode == 0)


def check_done() -> tuple[bool, str]:
    return _run([sys.executable, "scripts/done_check.py"])


def done_check_label() -> str:
    """done_check 步骤名——静态门禁项数从 done_check.CHECKS 动态取。

    硬编码项数必然漂移（曾写死「11 道」而实际已是 13 项），
    故此处只描述，不存数；取不到时退化为不带数字的标签（不影响门禁结论）。
    """
    try:
        scripts_dir = os.path.join(REPO_ROOT, "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from done_check import CHECKS  # noqa: PLC0415  （延迟导入：仅为取项数）

        return f"done_check({len(CHECKS)} 静态门禁+工作树)"
    except Exception:  # noqa: BLE001  —— 标签取不到不该让门禁本身失败
        return "done_check(静态门禁+工作树)"


def check_pytest_gate(rel_path: str) -> tuple[bool, str]:
    """跑定向门禁测试（在 思忆集app/ 下、--basetemp 指向 verify_tmp，符合项目 pytest 纪律）。

    rel_path 相对 思忆集app/ 目录。
    """
    app_dir = os.path.join(REPO_ROOT, "思忆集app")
    if not os.path.exists(os.path.join(app_dir, rel_path)):
        return True, f"SKIP（思忆集app/{rel_path} 尚未落地）"
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            # 用项目 venv 而非 sys.executable：pytest 项依赖项目依赖（aiohttp 等），
            # 而 mypy 棘轮需要的解释器未必装了 pytest → 曾报 No module named pytest。
            [project_python(), "-m", "pytest", rel_path, "-q", f"--basetemp={os.path.join(REPO_ROOT, 'verify_tmp', 'pytest-gate_all')}"],
            cwd=app_dir,
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
    return proc.returncode == 0, tail_detail(out, ok=proc.returncode == 0)


def check_facade_smoke() -> tuple[bool, str]:
    script = os.path.join(REPO_ROOT, "scripts", "facade_attr_smoke.py")
    if not os.path.exists(script):
        return True, "SKIP（facade_attr_smoke.py 尚未落地）"
    return _run([sys.executable, "scripts/facade_attr_smoke.py"])


def check_bundle_fresh() -> tuple[bool, str]:
    """备份新鲜度：最近的 siyiji-local-backup-*.bundle 修改时间 ≤ 7 天。

    push 不可达的现实下，bundle 是唯一全库兜底（MEMORY git 红线）。
    完成定义改挂本地门禁后，这一项必须机器盯——不能再靠人记。
    """
    candidates: list[str] = []
    for pattern in BUNDLE_GLOBS:
        candidates.extend(glob.glob(os.path.abspath(pattern)))
    if not candidates:
        return False, "找不到任何 git bundle 备份（开工先 git bundle create ... --all）"
    newest = max(candidates, key=os.path.getmtime)
    age_days = (time.time() - os.path.getmtime(newest)) / 86400
    name = os.path.basename(newest)
    if age_days <= BUNDLE_MAX_AGE_DAYS:
        return True, f"最新 bundle: {name}（{age_days:.1f} 天前）"
    return False, (
        f"最新 bundle {name} 已 {age_days:.1f} 天（上限 {BUNDLE_MAX_AGE_DAYS} 天）——立即执行 git bundle create ... --all && git bundle verify"
    )


def main() -> int:
    skip_pytest = "--skip-pytest" in sys.argv[1:]
    results: list[tuple[str, bool, str]] = []

    steps = [(done_check_label(), check_done)]
    if not skip_pytest:
        steps.append(("kwarg 误调门禁", lambda: check_pytest_gate(os.path.join("tests", "test_handler_kwarg_survey_20260916.py"))))
        steps.append(("facade 属性对表", check_facade_smoke))
    steps.append(("bundle 备份新鲜度", check_bundle_fresh))

    print("gate_all:" + " " * 46)
    for name, fn in steps:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"门禁自身异常: {exc!r}"
        results.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} -- {indent_detail(detail)}")

    failed = [n for n, ok, _ in results if not ok]
    print("-" * 60)
    print(f"gate_all: {len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print(f"  FAIL: {', '.join(failed)}")
        print("  任一 FAIL 即不满足「完成」新定义（commit SHA + gate_all 绿）。")
        return 1
    print("  全部通过 —— 满足本地「完成」定义。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
