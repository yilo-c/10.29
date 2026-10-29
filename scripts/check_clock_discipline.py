#!/usr/bin/env python3
"""时钟纪律棘轮：时间敏感模块不得直读墙钟（P0-1 治本门禁）。

背景
====
env-drill 第三波（T2 时钟跳变，2026-09-17）实证：同一"naive 墙钟"病灶在
confirmation TTL / task_queue lease / cron 补跑 / weekly_report **四处独立复发**。

逐处打补丁只能治标——第五处、第六处还会复发。本门禁把纪律变成**机器断言**
（AGENTS.md 守则第 7 条："修复侧优先把结论固化为机器断言（结构性测试 / CI 门禁 /
攻击链 PoC 先红后绿），弱化对任何单一会话持续诚实的依赖"），与
``scripts/scan_broad_except.py`` / ``scripts/mypy_ratchet.py`` 同款**棘轮**范式：
**存量只允许下降，新增即失败**。

口径
====
对 ``TARGETS`` 中每个"时间判据模块"统计直读时间的次数：

* ``time.time()`` —— 墙钟，回拨/前进都会破坏判据
* ``datetime.now()`` / ``datetime.utcnow()`` —— 同上
* ``time.monotonic()`` —— 进程内时长，本应集中在基元里统一使用

**合规写法** = 经 ``core.clock``：
``wall_ts`` / ``mono_ts`` / ``wall_now`` / ``detect_rollback`` /
``deadline_expired`` / ``safe_timestamp_write``。

``思忆集app/core/clock.py`` 是**唯一**允许直读时间的地方，不在 TARGETS 内。

用法
====
  python scripts/check_clock_discipline.py            # 校验：新增违规 → 退出 1
  python scripts/check_clock_discipline.py --report    # 仅打印当前计数
  python scripts/check_clock_discipline.py --update    # 消减存量后收紧基线
"""

from __future__ import annotations

import ast
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 时间判据模块（env-drill T2 实证的病灶面）。新增时间敏感模块请登记于此。
TARGETS: list[str] = [
    "思忆集app/services/confirmation_store.py",
    "思忆集app/services/cron_service.py",
    "思忆集app/services/task_queue.py",
    "思忆集app/backend/weekly_report_scheduler.py",
    "思忆集app/app_runtime/_context_cron.py",
]

BASELINE_PATH = os.path.join(REPO_ROOT, "scripts", "clock_discipline_baseline.json")

# 直读时间的调用形态（AST 精确判定，避免注释/docstring 里提到函数名被误计）。
_TIME_FUNCS = {"time", "monotonic", "perf_counter", "process_time"}
_DATETIME_FUNCS = {"now", "utcnow", "today"}


def _count_reads(rel_path: str) -> int:
    """统计 ``time.X()`` / ``datetime.X()`` 形态的**真实调用**次数。"""
    abs_path = os.path.join(REPO_ROOT, rel_path)
    if not os.path.isfile(abs_path):
        return 0
    try:
        with open(abs_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=rel_path)
    except (OSError, SyntaxError):
        return 0

    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        owner = func.value
        if not isinstance(owner, ast.Name):
            continue
        if owner.id == "time" and func.attr in _TIME_FUNCS:
            count += 1
        elif owner.id == "datetime" and func.attr in _DATETIME_FUNCS:
            count += 1
    return count


def _collect() -> dict[str, int]:
    return {rel: _count_reads(rel) for rel in TARGETS}


def _load_baseline() -> dict[str, int]:
    if not os.path.isfile(BASELINE_PATH):
        return {}
    with open(BASELINE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_baseline(data: dict[str, int]) -> None:
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def main() -> int:
    argv = sys.argv[1:]
    current = _collect()

    if "--report" in argv:
        print("[时钟纪律] 当前直读计数：")
        for rel, n in current.items():
            print(f"  {n:>3}  {rel}")
        return 0

    if "--update" in argv:
        _save_baseline(current)
        print("[时钟纪律] 基线已更新：")
        for rel, n in sorted(current.items()):
            print(f"  {n:>3}  {rel}")
        return 0

    baseline = _load_baseline()
    if not baseline:
        print("[时钟纪律] 基线缺失——先跑 `python scripts/check_clock_discipline.py --update`")
        print(f"  期望路径：{BASELINE_PATH}")
        return 1

    regressions: list[str] = []
    tightened: list[str] = []

    for rel in TARGETS:
        now = current.get(rel, 0)
        was = baseline.get(rel)
        if was is None:
            # 新登记的目标模块：基线里没有 → 以其当前值为准，但要求为 0 才放行
            if now > 0:
                regressions.append(f"  [新目标未登记] {rel}: 当前 {now} 处直读（应为 0 或先 --update）")
            continue
        if now > was:
            regressions.append(f"  [新增直读] {rel}: {was} → {now}")
        elif now < was:
            tightened.append(f"  [可收紧] {rel}: {was} → {now}")

    if regressions:
        print("[时钟纪律] 失败：时间敏感模块出现新的直读墙钟/单调钟：")
        for r in regressions:
            print(r)
        print()
        print("  合规写法：经 core.clock —— wall_ts / mono_ts / deadline_expired /")
        print("            safe_timestamp_write / detect_rollback")
        print("  理由：P0-1（env-drill T2-D）——墙钟回拨会让 TTL / lease / 补跑判据失效。")
        if tightened:
            print()
            print("  另有存量已消减（消减后请跑 --update 收紧基线）：")
            for t in tightened:
                print(t)
        return 1

    total = sum(current.values())
    print(f"[时钟纪律] 通过：{len(TARGETS)} 个时间判据模块，直读计数均在基线内（合计 {total} 处存量）")
    if tightened:
        print("  以下模块存量已消减，建议 `--update` 收紧基线：")
        for t in tightened:
            print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
