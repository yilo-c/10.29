#!/usr/bin/env python
"""换内核演练（kernel-migration-plan §9.4 / A7 终验预置）。

对三种内核状态（dsh / claude_code / codex / none）逐一：
- 经统一端口 ``build_gateway`` 构造适配器（**换内核零代码改动** = Zone 契约 A7）；
- ``health()`` 探测可用性；
- 可用则轻量 ``submit("Reply with just: OK")`` 冒烟（默认跳过真实 LLM 调用，
  设 ``KERNEL_DRILL_RUN=1`` 才实跑，避免观察期外消耗）。

用法：
    python scripts/kernel_swap_drill.py            # 仅健康检查 + none 冒烟
    python scripts/kernel_swap_drill.py --run      # 真实内核 submit 冒烟（dsh 需 API key）
    python scripts/kernel_swap_drill.py --type dsh # 只测指定类型
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "思忆集app"
sys.path.insert(0, str(APP_DIR))

from services.agent_gateway.factory import build_gateway  # noqa: E402

TYPES = ["dsh", "claude_code", "codex", "none"]


def _drill_one(ktype: str, run_submit: bool) -> dict:
    """对单个内核状态跑演练，返回结果 dict。"""
    gw = build_gateway({"type": ktype})
    health = {}
    try:
        health = gw.health()
    except Exception as exc:  # noqa: BLE001
        health = {"available": False, "error": str(exc)[:200]}
    result = {
        "type": ktype,
        "adapter": type(gw).__name__,
        "health": health,
    }
    if not health.get("available"):
        result["status"] = "unavailable"
        return result
    if not run_submit:
        result["status"] = "available"
        return result
    try:
        run = gw.submit("Reply with just: OK", timeout_s=30)
        result["status"] = run.status
        result["result"] = (run.result or "")[:120]
        result["error"] = run.error
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = str(exc)[:200]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="换内核三态演练（A7 终验预置）")
    parser.add_argument("--type", default=None, help="只测指定类型；默认全部")
    parser.add_argument("--run", action="store_true", help="真实内核 submit 冒烟（dsh 需 API key，消耗额度）")
    args = parser.parse_args()

    targets = [args.type] if args.type else TYPES
    all_ok = True
    for ktype in targets:
        r = _drill_one(ktype, args.run)
        # none 内核的正确状态就是 unavailable（内核关闭）；其余需 available/succeeded
        if ktype == "none":
            ok = r["status"] == "unavailable"
        else:
            ok = r["status"] in ("available", "succeeded")
        all_ok = all_ok and ok
        print(f"[drill] {ktype:<12} status={r['status']:<12} adapter={r['adapter']}")
        if r["status"] == "succeeded":
            print(f"         result={r['result']!r}")
        elif r["status"] == "error":
            print(f"         error={r['error']!r}")
    print(f"[drill] SUMMARY all_ok={all_ok}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
