#!/usr/bin/env python3
"""RPC 参数 schema 覆盖率棘轮（S2-1，2026-09-16）。

基线锁定 + 只阻塞新增（模式与 kernel_boundary_ratchet.py 同构——全项目唯一
把「棘轮」做对的范例，本脚本照它复制）。

背景（八轮实测 D-2 / Z-2 横切根因）：`method_registry._validate_params` 的
jsonschema 校验链路是建好的，但 376 个静态注册方法只有 20 个带 schema
（5.3%）→ 94.7% 的 RPC 对未知参数/错类型完全无感（`notes.list limit="abc"`
直接 -32603）。参数契约是「机制建成不启用」共因 B 的最大存量面。

本棘轮不要求一次性补 356 个 schema（那是按前端真实消费面 219 清单逐批补的
施工活），只锁死一条：**从现在起新增注册方法不带 schema 即 CI 红**。
存量 356 个冻结在基线里，修一个少一个，消减后用 --update 收紧。

指纹口径（与 I1 棘轮的差异，故意更严）：
  - I1 按「每文件每类别行数」——行号漂移不阻塞；
  - 本脚本按「方法名集合」——方法名是唯一 RPC 标识，改名重注册=新方法，
    必须带 schema。基线内存量名字从扫描结果消失视为改善（提示 --update），
    绝不阻塞。

动态注册豁免（第一参不是字符串字面量，静态扫描不可见）：
  - backend/method_registry.py 内部转发（register(name)——定义处本身）；
  - backend/handlers/_tools.py 的 register(spec)——tool.* 族由
    tool_specs.json 声明驱动，schema 在 JSON 里，不属本棘轮口径；
  - backend/handlers/__init__.py 的 wrapper 转发。
  上述位置如新增其它动态注册形态，扫描会漏——与 I1 棘轮同款局限，
  靠 code review 兜底（动态注册在本仓是罕见形态）。

用法：
    python scripts/schema_coverage_ratchet.py             # 检查（CI 门禁）
    python scripts/schema_coverage_ratchet.py --update    # 消减存量后收紧基线
    python scripts/schema_coverage_ratchet.py --report    # 只打覆盖率统计

退出码：0=无基线外新增；1=发现新增无 schema 方法（阻塞）。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "思忆集app")
BASELINE_PATH = os.path.join(REPO_ROOT, "scripts", "schema-coverage-baseline.json")

SCAN_ROOTS = ("backend",)  # RPC 注册面全部在 backend/（handlers 63 模块 + registry）
SKIP_DIR_PARTS = ("__pycache__", "_deprecated")


def scan() -> tuple[dict[str, bool], list[tuple[str, str]]]:
    """AST 扫描全部 registry.register / register_with_schema 调用。

    返回 (methods, dynamic):
      methods: {方法名: 是否任一注册点带 schema}
      dynamic: [(文件, 调用形状)] 动态注册点清单（豁免口径，仅报告）
    """
    methods: dict[str, bool] = {}
    dynamic: list[tuple[str, str]] = []
    for zone in SCAN_ROOTS:
        zone_root = os.path.join(APP_DIR, zone)
        if not os.path.isdir(zone_root):
            continue
        for root, dirs, files in os.walk(zone_root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIR_PARTS and not d.startswith(".")]
            for f in files:
                if not f.endswith(".py"):
                    continue
                abs_path = os.path.join(root, f)
                rel = os.path.relpath(abs_path, APP_DIR).replace("\\", "/")
                try:
                    tree = ast.parse(open(abs_path, encoding="utf-8").read())
                except (SyntaxError, UnicodeDecodeError):
                    print(f"  [warn] parse skip: {rel}", file=sys.stderr)
                    continue
                for node in ast.walk(tree):
                    if not (
                        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("register", "register_with_schema")
                    ):
                        continue
                    if not node.args:
                        continue
                    a0 = node.args[0]
                    has_schema = node.func.attr == "register_with_schema"
                    # register_with_schema 的 schema 是第 3 位置参（method_registry.py:131）
                    if has_schema and len(node.args) >= 3 and isinstance(node.args[2], ast.Dict):
                        has_schema = bool(node.args[2].keys)  # 空 {} 不算覆盖
                    for kw in node.keywords:
                        if kw.arg == "schema" and isinstance(kw.value, ast.Dict):
                            # 空 dict schema（schema={}）不算覆盖——校验器对空
                            # schema 恒放行，等于没写（防「形式合规实质空转」）
                            has_schema = bool(kw.value.keys)
                    if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                        name = a0.value
                        # 同名多注册点：任一带 schema 即视为覆盖
                        methods[name] = methods.get(name, False) or has_schema
                    else:
                        dynamic.append((rel, f"{node.func.attr}({ast.unparse(a0)[:40]})"))
    return methods, dynamic


def _load_baseline() -> dict | None:
    if not os.path.isfile(BASELINE_PATH):
        return None
    try:
        with open(BASELINE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="用当前扫描结果重写基线（收紧）")
    ap.add_argument("--report", action="store_true", help="只打统计不做门禁")
    args = ap.parse_args()

    methods, dynamic = scan()
    uncovered = sorted(n for n, has in methods.items() if not has)
    covered = len(methods) - len(uncovered)
    pct = covered / len(methods) * 100 if methods else 0.0
    print(f"静态注册方法 {len(methods)} | 带 schema {covered} ({pct:.1f}%) | 无 schema {len(uncovered)} | 动态注册点 {len(dynamic)}（豁免口径）")

    if args.report:
        return 0

    if args.update:
        payload = {
            "_comment": "schema 覆盖率棘轮基线：无 schema 的存量方法名清单，只减不增（scripts/schema_coverage_ratchet.py）",
            "generated": "2026-09-16",
            "total_static_methods": len(methods),
            "uncovered_methods": uncovered,
        }
        with open(BASELINE_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
        print(f"[update] 基线已写入 {BASELINE_PATH}（uncovered={len(uncovered)}）")
        return 0

    baseline = _load_baseline()
    if baseline is None:
        print("[FAIL] 基线不存在——先跑 --update 生成")
        return 1
    base_uncovered = set(baseline.get("uncovered_methods", []))
    # 新增违规 = 现在无 schema 且不在基线里的方法名
    new_violations = [n for n in uncovered if n not in base_uncovered]
    fixed = [n for n in sorted(base_uncovered) if n not in set(uncovered)]

    if new_violations:
        print(f"[FAIL] 基线外新增无 schema 注册 {len(new_violations)} 个（新 RPC 必须 register_with_schema）:")
        for n in new_violations:
            print(f"  + {n}")
        return 1
    if fixed:
        print(f"[OK] 无新增违规；另有 {len(fixed)} 个基线内存量已消减/移除——建议 --update 收紧基线:")
        for n in fixed[:20]:
            print(f"  - {n}")
    else:
        print("[OK] 无基线外新增违规")
    return 0


if __name__ == "__main__":
    sys.exit(main())
