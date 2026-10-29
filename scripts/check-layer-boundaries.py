#!/usr/bin/env python3
"""层边界检查：确保思忆集各层之间 import 方向正确。

铁律：
  1. ai/client/（底层 HTTP）禁止 import assistant/（编排层）
  2. memory/ 禁止 import services/（memory 不应依赖 services）
  3. app_runtime/ 禁止 import ui/（DI 容器不应依赖 UI 层）

内核契约 I1（docs/kernel-contract.md v1.3，2026-09-02 增补）：
  4. 四区（backend/services/repositories/app_runtime）禁止 import assistant/
     ——壳稳定区不得反向依赖旧内核（I1 反向依赖禁止）
  5. 四区禁止 import agent_gateway.adapters/
     ——壳只认端口，不得直连具体内核适配器（I4 配置即切换）
  6. 四区禁止 import smolagents/
     ——旧内核执行器残留，壳内禁止出现任何旧内核 import

用法：
  python scripts/check-layer-boundaries.py
  返回值：0 表示无违规，1 表示存在违规
  存量违规由 scripts/kernel_boundary_ratchet.py 棘轮管理（基线锁定，
  只阻塞新增）；本脚本对 I1 三条规则同步输出棘轮指引。
"""

from __future__ import annotations

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "思忆集app")

# 契约四区（壳稳定区）：backend / services / repositories / app_runtime
_FOUR_ZONES = r"(?:backend|services|repositories|app_runtime)/.+\.py$"

RULES: list[tuple[re.Pattern, re.Pattern, str]] = [
    # (文件匹配模式, 禁止的 import 模式, 违规描述)
    (
        re.compile(r"^ai/client/.+\.py$"),
        re.compile(r"^(from|import)\s+assistant\b"),
        "P0: ai/client/（HTTP 客户端层）禁止 import assistant/（编排层）",
    ),
    (
        re.compile(r"^memory/.+\.py$"),
        re.compile(r"^(from|import)\s+services\b"),
        "P0: memory/（记忆层）禁止 import services/（服务层）",
    ),
    (
        re.compile(r"^app_runtime/.+\.py$"),
        re.compile(r"^(from|import)\s+ui\b"),
        "P0: app_runtime/（DI 容器）禁止 import ui/（表现层）",
    ),
    # ── 内核契约 I1（2026-09-02 增补，存量走棘轮 scripts/kernel_boundary_ratchet.py）──
    (
        re.compile(_FOUR_ZONES),
        re.compile(r"^(from|import)\s+assistant\b"),
        "I1: 四区（backend/services/repositories/app_runtime）禁止 import assistant/（旧内核反向依赖，契约 I1）",
    ),
    (
        re.compile(_FOUR_ZONES),
        re.compile(r"^(from|import)\s+agent_gateway\.adapters\b"),
        "I1: 四区禁止 import agent_gateway.adapters/（壳只认端口，不直连内核适配器，契约 I4）",
    ),
    (
        re.compile(_FOUR_ZONES),
        re.compile(r"^(from|import)\s+smolagents\b"),
        "I1: 四区禁止 import smolagents/（旧内核执行器残留，契约 I1）",
    ),
]

# I1 规则的描述前缀（用于区分「立即失败」与「存量棘轮管理」的规则）
I1_PREFIX = "I1:"


def check_file(rel_path: str) -> list[str]:
    violations: list[str] = []
    abs_path = os.path.join(APP_DIR, rel_path)

    if not os.path.isfile(abs_path):
        return violations

    # 跳过 __init__.py 兼容性 re-export（已知 shim）
    if "__init__.py" in rel_path:
        return violations

    try:
        with open(abs_path, encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return violations

    for file_pattern, import_pattern, message in RULES:
        if not file_pattern.search(rel_path):
            continue
        in_type_checking = False
        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            # 跳过注释
            if stripped.startswith("#"):
                continue
            # 跟踪 TYPE_CHECKING 块：遇到 "if TYPE_CHECKING:" 标记开始，
            # 遇到相同缩进级别的非空行或更低缩进则结束。
            if "TYPE_CHECKING" in stripped and stripped.rstrip().endswith(":"):
                in_type_checking = True
                continue
            if in_type_checking:
                # 如果行缩进小于等于 TYPE_CHECKING 行的缩进（即不再属于该块）
                stripped_line = line.rstrip()
                if stripped_line and not stripped_line.startswith((" ", "\t")):
                    in_type_checking = False
                else:
                    # 检查是否有缩进：TYPE_CHECKING 块内的行应有更大缩进
                    leading = len(line) - len(line.lstrip())
                    if leading <= 0:
                        in_type_checking = False
                    else:
                        continue  # 跳过 TYPE_CHECKING 块内的所有行
            if import_pattern.search(stripped):
                # 条目格式：  <path>:<line>  [I1: 规则名] <代码>
                rule_tag = "I1: " if message.startswith(I1_PREFIX) else ""
                violations.append(f"  {rel_path}:{lineno}  {rule_tag}{stripped}")
                break  # 每文件每规则只报第一次

    return violations


def main() -> int:
    blocking: list[str] = []  # P0 规则：立即失败
    ratchet: list[str] = []  # I1 规则：存量由棘轮管理，只阻塞新增

    for root, _dirs, files in os.walk(APP_DIR):
        for f in files:
            if not f.endswith(".py"):
                continue
            # 跳过 .venv 和 tests
            rel = os.path.relpath(os.path.join(root, f), APP_DIR)
            if rel.startswith("..") or rel.startswith(".venv") or rel.startswith("tests"):
                continue
            # 豁免 _deprecated/ 存档目录：其中为历史版本忠实重建，可能保留
            # 已不适用的跨层 import，不参与现行层边界约束
            if "/_deprecated/" in rel.replace("\\", "/"):
                continue
            # Windows 反斜杠 → 统一正斜杠
            rel = rel.replace("\\", "/")
            for v in check_file(rel):
                (ratchet if I1_PREFIX in v else blocking).append(v)

    exit_code = 0
    if blocking:
        print("[层边界检查] 发现 import 违规:")
        for v in blocking:
            print(v)
        print()
        print("修复指南:")
        print("  1. ai/client/ → 将路由常量提取到 ai/routing_constants.py")
        print("  2. memory/ → 通过构造器注入 HybridSearchService，不要直接 import")
        print("  3. app_runtime/ → 通过 UiBridgeContext 延迟注入，不要直接 import ui")
        exit_code = 1

    if ratchet:
        print()
        print(f"[层边界检查] I1 存量违规 {len(ratchet)} 处（棘轮基线管理，非本次阻塞）：")
        for v in ratchet:
            print(v)
        print()
        print("  存量消减由 scripts/kernel_boundary_ratchet.py 管理：")
        print("  - 新增违规 → 棘轮脚本退出 1（阻塞）")
        print("  - 消减存量后 → 运行 `python scripts/kernel_boundary_ratchet.py --update` 收紧基线")

    if exit_code == 0 and not ratchet:
        print("[层边界检查] 通过，无违规")
    elif exit_code == 0:
        print("[层边界检查] P0 规则通过；I1 存量见上方棘轮清单")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
