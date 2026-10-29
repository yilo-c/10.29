#!/usr/bin/env python3
"""守卫：核心模块完整性（debt-v2 TASK-M2-1 / A-6）。

背景：CI 引用本脚本（.github/workflows/python-tests.yml:49）但此前不存在，导致该
门禁步骤从未能绿。本实现核对关键入口模块存在且非空，防 2026-07-18 源码丢失
事故再发（与 check_no_sourceless_pyc.py 互补：那个扫全仓裸 pyc，这个钉关键路径）。

用法：
  python scripts/check_core_integrity.py [--list]
  --list：仅列出清单（供人工核对 / 更新时参照）
  返回值：0 = 全部在场且非空；1 = 存在缺失/空文件
"""

from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 关键入口清单（相对仓库根）。来源：debt-v2 TASK-M2-1 首批。
# 新增关键模块时在此登记，并在 CI 文案/文档同步提及。
CRITICAL_FILES = [
    "思忆集app/backend/headless_main.py",
    "思忆集app/backend/ws_server.py",
    "思忆集app/backend/method_registry.py",
    "思忆集app/backend/event_bridge.py",
    "思忆集app/app_runtime/app_context.py",
    "思忆集app/app_runtime/_context_builder.py",
    "思忆集app/services/cron_actions.py",
    "思忆集app/services/cron_service.py",
    "思忆集app/sync/cloud_sync_client.py",
    "思忆集app/core/audit_rollback.py",
    # 2026-09-18 新增：P0 治本基元——时钟纪律（P0-1）与隔离 fail-closed（P0-2）。
    # 二者被时间判据 / 数据目录解析模块广泛依赖，缺失会静默放大为
    # "审批窗口失效 / 隔离泄漏写穿生产"，故纳入关键路径清单。
    "思忆集app/core/clock.py",
    "思忆集app/core/isolation.py",
    # 2026-09-18 新增：P0 治本基元——进程级完整性台账（P1-2/P2-4/P1-4 共用出口）。
    # "写入失败被吞、上层不可观测"同一形状在四处独立复发（笔记解析跳过 /
    # store.json 写失败 / 笔记级联 / 任务级联），故按元判据建基元；缺失会让
    # 上述站点退回各自的静默路径，故障再次隐形。
    "思忆集app/core/integrity.py",
]


def main() -> int:
    if "--list" in sys.argv[1:]:
        for rel in CRITICAL_FILES:
            print(rel)
        return 0

    missing: list[str] = []
    empty: list[str] = []
    for rel in CRITICAL_FILES:
        path = os.path.join(REPO_ROOT, rel)
        if not os.path.isfile(path):
            missing.append(rel)
        elif os.path.getsize(path) == 0:
            empty.append(rel)

    if missing or empty:
        print("[核心模块完整性] 失败：关键模块缺失/为空（源码丢失风险）:")
        for rel in missing:
            print("  [MISSING]", rel)
        for rel in empty:
            print("  [EMPTY]  ", rel)
        print()
        print("处理指南：按 docs/PYC_REBUILD_RUNBOOK.md 重建，或确认无引用后从清单移除。")
        return 1

    print(f"[核心模块完整性] 通过：{len(CRITICAL_FILES)} 个关键模块在场且非空")
    return 0


if __name__ == "__main__":
    sys.exit(main())
