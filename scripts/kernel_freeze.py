"""执行内核冻结/解冻开关（2026-10-15 补建）。

背景：2026-09-18 探针会话把内核冻结为 ``agent_kernel.type=none`` +
``per_request_selection=false``，并在 settings 里留了 ``_freeze_note`` 指向
本脚本 —— 但脚本随当时的工作目录一起被清理掉了（注释指向不存在的文件，
解冻只能手改 settings blob，易出错）。本脚本让注释成真。

语义（为什么冻结这样做）：
- ``type=none`` → ``build_gateway`` 返回 ``NoneGateway``，其 ``submit()`` 立即
  返回 ``status="unavailable"``，**不 spawn dsh、不联网**；``kernel_ai`` 等
  调用方随即走 fallback（ai_client）。冻结只影响"用不用成熟内核"，不影响壳
  自身能力与 AI 文本端口。
- ``per_request_selection=false`` → 关闭按请求选内核（冻结期内不允许临时切换）。

用法：
    python scripts/kernel_freeze.py status     # 查看当前状态（默认）
    python scripts/kernel_freeze.py freeze     # 冻结（type=none + 选择关闭）
    python scripts/kernel_freeze.py restore    # 解冻（type=dsh + 选择开启）
    python scripts/kernel_freeze.py restore --type auto   # 解冻到 auto

写入走 settings_repository（热更 + 校验），改完立即生效，无需重启。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "思忆集app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

KEY = "agent_kernel"
DEFAULT_TYPE = "dsh"


def _ctx():
    os.chdir(APP_DIR)
    from app_runtime.app_context import AppContext

    return AppContext(headless=True, desknotes_data_dir=None, data_base_dir=None)


def _read(repo) -> dict:
    raw = repo.get_setting(KEY, "") or ""
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(raw) if raw else {}
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def cmd_status(repo) -> int:
    cfg = _read(repo)
    print(f"agent_kernel = {json.dumps(cfg, ensure_ascii=False)}")
    ktype = str(cfg.get("type") or DEFAULT_TYPE)
    frozen = ktype == "none"
    print(f"status: {'FROZEN（内核冻结）' if frozen else f'ACTIVE（内核={ktype}）'}")
    note = cfg.get("_freeze_note")
    if note:
        print(f"note: {note}")
    return 0


def cmd_freeze(repo, note: str) -> int:
    cfg = _read(repo)
    cfg["type"] = "none"
    cfg["per_request_selection"] = False
    cfg["_freeze_note"] = note
    # settings_repository 的 agent_kernel 值为 dict（仓储层负责序列化/校验），
    # 直接传 dict——传 JSON 字符串会写成错误格式。
    repo.set_setting(KEY, cfg)
    return cmd_status(repo)


def cmd_restore(repo, ktype: str) -> int:
    cfg = _read(repo)
    cfg["type"] = ktype
    cfg["per_request_selection"] = True
    cfg.pop("_freeze_note", None)
    repo.set_setting(KEY, cfg)
    return cmd_status(repo)


def main() -> int:
    ap = argparse.ArgumentParser(description="执行内核冻结/解冻开关")
    ap.add_argument("action", nargs="?", default="status", choices=["status", "freeze", "restore"])
    ap.add_argument("--type", dest="ktype", default=DEFAULT_TYPE, help="restore 时的内核类型（默认 dsh，可 auto）")
    ap.add_argument("--note", default="", help="freeze 时的说明（写入 _freeze_note）")
    args = ap.parse_args()

    repo = _ctx().settings_repository
    if repo is None:
        print("FATAL: settings_repository unavailable")
        return 1
    if args.action == "freeze":
        return cmd_freeze(repo, args.note or "manual freeze (kernel_freeze.py)")
    if args.action == "restore":
        return cmd_restore(repo, args.ktype)
    return cmd_status(repo)


if __name__ == "__main__":
    sys.exit(main())
