"""思忆集 Core Smoke Check — 快速冒烟，验证核心模块可导入且基础功能正常。

用法:
    python 思忆集app/smoke_check.py                # 全部
    python 思忆集app/smoke_check.py --suite core   # 核心模块
    python 思忆集app/smoke_check.py --suite api    # API 模块
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

PASS, FAIL = "PASS", "FAIL"


def check_import(module_name: str) -> tuple[str, str]:
    try:
        __import__(module_name)
        return PASS, ""
    except Exception as exc:
        return FAIL, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="思忆集 Core Smoke Check")
    parser.add_argument("--suite", choices=["core", "api", "all"], default="all", help="检查套件 (默认 all)")
    args = parser.parse_args()

    core_modules = [
        "core.env_loader",
        "core.errors",
        "core.events",
        "backend.method_registry",
        "backend.connection_manager",
        "backend.event_bridge",
        "repositories._protocols",
        "sync.note_sync",
    ]

    api_modules = [
        "api.integration_hub",
        "api.oauth_security",
        "api.provider_registry",
        "api.sync_orchestrator",
    ]

    all_modules: list[str] = []
    if args.suite in ("core", "all"):
        all_modules.extend(core_modules)
    if args.suite in ("api", "all"):
        all_modules.extend(api_modules)

    if not all_modules:
        print("No modules selected")
        return 0

    print(f"Smoke check ({args.suite}): {len(all_modules)} modules")
    failed = 0
    for mod in all_modules:
        status, detail = check_import(mod)
        if status == PASS:
            print(f"  [{status}] {mod}")
        else:
            print(f"  [{status}] {mod} — {detail}")
            failed += 1

    print(f"\nResult: {len(all_modules) - failed}/{len(all_modules)} passing")
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
