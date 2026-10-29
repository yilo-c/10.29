#!/usr/bin/env python3
"""pip-audit 依赖漏洞门禁包装（debt-v2 TASK-M2-1 / A-6，对应 CI P2-3 步骤）。

背景：CI 引用本脚本（.github/workflows/python-tests.yml）但此前不存在。本实现
以「基线豁免 + 新增漏洞才红」为策略：对 requirements-desktop.txt 列出的直接依赖
跑 pip-audit（--no-deps，基线语义清晰），与 scripts/pip-audit-baseline.txt（每行
一个已接受漏洞 ID）比较；仅**不在基线内**的新增漏洞令退出码 1。

批次5（2026-08-26）严格模式加固 — 封堵"假绿"：
  旧实现存在两类假绿:
  1) find-links/离线源导致解析不到任何包 → dependencies 为空 → 误判"干净通过"；
  2) 命令异常 / 超时 / 输出解析失败时 WARN 后静默返回 0（当作通过）。
  严格模式：**无有效审计结果即非零退出**，绝不把"没扫"当"通过"。

用法：
  python scripts/run_pip_audit.py [--verbose]        # 门禁模式（CI，严格）
  python scripts/run_pip_audit.py --gen-baseline     # 生成/刷新基线（首跑、或依赖升级已修复后）
  返回值：门禁模式 0 = 审计有效且无新增漏洞；非 0 = 存在新增漏洞 或 审计无效（严格模式）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(REPO_ROOT, "scripts", "pip-audit-baseline.txt")
REQUIREMENTS = os.path.join(REPO_ROOT, "requirements-desktop.txt")


def _audit_once() -> tuple[bool, dict | None, str | None]:
    """运行一次 pip-audit。

    返回 (ran, data, note)：
      ran=True ：命令成功执行且输出可解析，data 为 JSON dict
      ran=False：审计没能产生有效结果（执行异常/超时/解析失败/依赖缺失），
                 note 为原因说明，调用方按严格模式判「无效即失败」。
    """
    if not os.path.isfile(REQUIREMENTS):
        return False, None, "requirements-desktop.txt 不存在，无法审计"
    targets = ["-r", REQUIREMENTS]
    cmd = ["pip-audit", *targets, "--no-deps", "--format", "json"]
    if not shutil.which("pip-audit"):
        cmd = [sys.executable, "-m", "pip_audit", *targets, "--no-deps", "--format", "json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False, None, "审计超时（网络不可达？）"
    # pip-audit 有漏洞时退出码为 1，属正常路径（仍可能有有效 JSON 输出）
    if proc.returncode not in (0, 1):
        return False, None, f"执行异常（rc={proc.returncode}）: {proc.stderr[-300:]}"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False, None, f"输出解析失败（rc={proc.returncode}）; 前 200 字: {proc.stdout[:200]}"
    if not isinstance(data, dict):
        return False, None, "输出 JSON 非法（非对象）"
    return True, data, None


def _extract_vulns(data: dict) -> list[dict]:
    """从 pip-audit JSON 中提取漏洞列表（兼容 dependencies[].vulns[] 与顶层 vulnerabilities[]）。"""
    vulns: list[dict] = []
    for dep in data.get("dependencies") or []:
        for v in dep.get("vulns") or []:
            vulns.append(
                {
                    "id": v.get("id"),
                    "fix_versions": v.get("fix_versions"),
                    "description": v.get("description", ""),
                }
            )
    for v in data.get("vulnerabilities") or []:
        vulns.append(
            {
                "id": v.get("id"),
                "fix_versions": v.get("fix_versions"),
                "description": v.get("description", ""),
            }
        )
    return vulns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="打印审计明细")
    parser.add_argument("--gen-baseline", action="store_true", help="生成/刷新基线")
    args = parser.parse_args()

    ran, data, note = _audit_once()

    # ── 严格模式（批次5）：无有效审计结果 = 假绿，必须非零退出 ──
    if not ran:
        print(f"[pip-audit] FAIL（严格模式）: 审计未产生有效结果 → {note}")
        print("  处理：请在有网络的环境运行门禁，确认 pip-audit 能解析到依赖清单后再合入。")
        return 1

    deps = data.get("dependencies")
    # "dependencies" 为空/缺失且亦无顶层 vulnerabilities → 0 包可审计 → 视为无效（假绿）
    if not deps and not data.get("vulnerabilities"):
        print("[pip-audit] FAIL（严格模式）: 审计有效但 0 包可审计（找不到任何可解析依赖）。")
        print("  这可能由 find-links/离线源/清单漂移导致——空清单不能当作干净通过。")
        print("  data 键: " + ", ".join(sorted(data.keys())))
        return 1

    vulns = _extract_vulns(data)
    ids = sorted({v.get("id") or "?" for v in vulns})

    if args.gen_baseline:
        with open(BASELINE, "w", encoding="utf-8") as fh:
            fh.write("\n".join(ids) + ("\n" if ids else ""))
        print(f"[pip-audit] 基线已写入 {len(ids)} 个漏洞 ID -> scripts/pip-audit-baseline.txt")
        return 0

    baseline: set[str] = set()
    if os.path.isfile(BASELINE):
        with open(BASELINE, encoding="utf-8") as fh:
            baseline = {ln.strip() for ln in fh if ln.strip()}

    # 未标定守卫：基线为空。此时若审计出漏洞即视为「未知/新增」阻塞；无漏洞则通过。
    if not baseline and vulns:
        print("[pip-audit] FAIL: 基线为空（未标定）且审计发现漏洞——无法豁免任何漏洞。")
        print("  处理：确认这些为已知接受风险后执行 --gen-baseline 写入基线。")
        return 1

    for v in vulns:
        tag = "known" if v.get("id") in baseline else "NEW!"
        print(f"  [{tag}] {v.get('id')} fix={v.get('fix_versions')} - {v.get('description', '')[:70]}")

    new_ids = [i for i in ids if i not in baseline]
    if new_ids:
        print(f"[pip-audit] FAIL: {len(new_ids)} 个基线外新增漏洞: {', '.join(new_ids)}")
        print("  处理：升级依赖修复后，用 --gen-baseline 刷新基线；或确认为已知接受风险并写入基线。")
        return 1
    print(f"[pip-audit] 通过：审计有效，{len(vulns)} 个漏洞，{len(new_ids)} 个新增（基线 {len(baseline)} 个已接受）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
