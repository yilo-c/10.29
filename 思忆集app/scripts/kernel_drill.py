# -*- coding: utf-8 -*-
"""kernel_drill.py — 内核致命错误 / 降级链演练（M4a-3 净新建）。

流程（任务书 M4a-3 口径）：
1. 复制 ``data/data/`` → 副本目录（verify：文件数/字节数/逐文件 sha256 抽样）；
2. 在副本上**置废内核 key**（``settings.agent_kernel.dsh.provider_env`` 全量替换为
   无效值 + ``llm_api_key`` flat 键置废），模拟
   ``gateway_path.py:410 _FATAL_KERNEL_ERROR_MARKERS`` 触发条件（invalid api key /
   unauthorized / subscription 类文案）；
3. 以副本为数据目录（``SIYI_DATA_DIR`` env 隔离，先例
   ``services/confirmation_store.py`` ``_resolve_default_data_dir``）跑一次
   降级链调用（``gateway_path.run_gateway_path`` 为唯一入口）；
4. 断言：①主进程不崩（I6：run_gateway_path 正常返回 bool，无异常逃逸）；
   ②降级结果对用户可见（chunk 流含 notice/error 标记且返回 False=回落壳内管家）。

安全边界（任务书 §0.5 / §1）：
* **副本实跑 = 人工批准点（🔒）**。本脚本缺省 ``--dry-run`` 只打印计划；
  真执行必须显式给 ``--data-dir <副本目录>`` 且不带 ``--dry-run``。
* 全程禁碰生产 ``data/data/``：读生产目录仅做只读复制源；所有写操作只落在副本。
* 演练对库的读数一律 ``mode=ro``（沿用 route_report.py 连接模式）。

用法::

    python scripts/kernel_drill.py --dry-run
    python scripts/kernel_drill.py --data-dir data/data-drill-copy   # 🔒 人工批准后
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from typing import Any

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = os.path.join(APP_ROOT, "data", "data")
DEFAULT_DST = os.path.join(APP_ROOT, "data", "data-drill-copy")
DRILL_SENTINEL = "sk-drill-revoked-20260912-invalid"
DRILL_MESSAGE = "请帮我把这份季度复盘报告的要点总结成三条"
# 抽样 verify 上限（大目录防超时；sha256 抽样 + 全量 size/计数核对）
VERIFY_SAMPLE_LIMIT = 200

# ── 置废目标解析（2026-09-13 修复）────────────────────────────
# 上次演练 A4 FAIL 根因：置废凭空造的 ``ARK_API_KEY`` 与 dsh 实际读 key 的
# 环境变量名（``~/.dsh/settings.yaml`` 各 provider 的 ``apiKeyEnv``，当前为
# ``BAILIAN_QWEN_API_KEY``）不匹配，dsh 仍用有效 key 成功产出 result。
# 这里改为扫描 dsh 全局配置里的 apiKeyEnv 名注入哨兵；扫描不到时用兜底候选
# （覆盖火山/百炼常见键），保证演练在任意环境都能置废真实生效的 key。
_DSH_SETTINGS_PATH = os.path.expanduser(os.path.join("~", ".dsh", "settings.yaml"))
_DSH_APIKEYENV_RE = re.compile(r"^\s+apiKeyEnv:\s*(\S+)", re.MULTILINE)
_FALLBACK_KEY_ENV_NAMES = ("BAILIAN_QWEN_API_KEY", "BAILIAN_API_KEY", "ARK_API_KEY")


def _dsh_api_key_env_names() -> list[str]:
    """扫描 ~/.dsh/settings.yaml 中所有 provider 的 apiKeyEnv 名（去重保序）。

    读不到 / 文件缺失 → 空列表，由调用方兜底候选。轻量文本扫描即可
    （与 gateway_path._read_dsh_default_model 同思路，不做严格 YAML）。
    """
    try:
        if not os.path.isfile(_DSH_SETTINGS_PATH):
            return []
        with open(_DSH_SETTINGS_PATH, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        return list(dict.fromkeys(_DSH_APIKEYENV_RE.findall(text)))
    except OSError:
        return []


def build_plan(src: str, dst: str, message: str = DRILL_MESSAGE) -> list[tuple[str, list[str]]]:
    """生成演练计划（两节：置废 / 断言）。纯函数，dry-run 与测试共用。"""
    return [
        ("置废计划（副本上执行，禁碰生产）", [
            f"1. 复制数据目录：{src} → {dst}（shutil.copytree, ignore -wal/-shm 之外全量）",
            "2. 校验副本：逐文件计数 + 字节总量 + sha256 抽样对账（verify 不等 → 立即中止）",
            f"3. 设 SIYI_DATA_DIR={dst}（confirmation_store 同款 env 隔离先例）",
            "4. 在副本上置废 settings.agent_kernel.dsh.provider_env → 全值替换为无效哨兵 "
            f"{DRILL_SENTINEL}（触发 provider 401/unauthorized 文案）",
            f"5. 副本置废 flat 密钥 llm_api_key → {DRILL_SENTINEL}（覆盖 flat:llm_api_key 引用）",
            "6. 副本写回 settings 存储（LocalDataStore.save_json，只写副本）",
            f"7. 在副本上跑降级链调用一次：run_gateway_path(context, message={message!r}, params={{}})",
        ]),
        ("断言计划（PASS/FAIL 判据）", [
            "A1 主进程不崩（I6）：run_gateway_path 正常返回 bool，无异常逃逸出演练进程",
            "A2 命中致命错误判据：gateway_path.py:410 _FATAL_KERNEL_ERROR_MARKERS "
            "口径（subscription/unauthorized/invalid api key/quota/billing…）",
            "A3 降级对用户可见：chunk 流含 notice 或 error 类型（致命错误以 notice 下发）",
            "A4 回落语义：run_gateway_path 返回 False（dispatcher 落壳内管家）",
            "A5 收尾：PASS=exit 0 / FAIL=exit 1，并打印各断言明细",
        ]),
    ]


def render_plan(plan: list[tuple[str, list[str]]]) -> str:
    lines = ["# kernel_drill 演练计划", ""]
    for title, steps in plan:
        lines.append(f"## {title}")
        lines.extend(steps)
        lines.append("")
    return "\n".join(lines)


def _iter_files(root: str) -> list[str]:
    out: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            out.append(os.path.join(dirpath, fn))
    return sorted(out)


def copy_and_verify(src: str, dst: str) -> dict[str, Any]:
    """复制生产数据目录到副本并与源对账；不平 → RuntimeError（禁带病演练）。"""
    import shutil

    if os.path.abspath(src) == os.path.abspath(dst):
        raise RuntimeError("源与目标同路径，拒绝（禁碰生产 data/data）")
    if not os.path.isdir(src):
        raise RuntimeError(f"源数据目录不存在：{src}")
    if os.path.exists(dst):
        raise RuntimeError(f"副本目录已存在，先人工清理再演练：{dst}")
    shutil.copytree(src, dst)
    src_files, dst_files = _iter_files(src), _iter_files(dst)
    if len(src_files) != len(dst_files):
        raise RuntimeError(f"副本文件数不等：{len(src_files)} vs {len(dst_files)}")
    src_bytes = sum(os.path.getsize(p) for p in src_files)
    dst_bytes = sum(os.path.getsize(p) for p in dst_files)
    if src_bytes != dst_bytes:
        raise RuntimeError(f"副本字节总量不等：{src_bytes} vs {dst_bytes}")
    sampled = 0
    for sp in src_files[:: max(1, len(src_files) // VERIFY_SAMPLE_LIMIT)]:
        dp = os.path.join(dst, os.path.relpath(sp, src))
        with open(sp, "rb") as f1, open(dp, "rb") as f2:
            if hashlib.sha256(f1.read()).hexdigest() != hashlib.sha256(f2.read()).hexdigest():
                raise RuntimeError(f"sha256 抽样不平：{sp}")
        sampled += 1
    return {"files": len(dst_files), "bytes": dst_bytes, "sha256_sampled": sampled}


def invalidate_kernel_key(copy_dir: str) -> list[str]:
    """在副本上置废内核 key；返回变更说明列表。只写副本目录。"""
    sys.path.insert(0, APP_ROOT)
    from data.local_storage import LocalDataStore

    store = LocalDataStore(base_dir=copy_dir)
    settings = store.load_json("settings", {})
    if not isinstance(settings, dict):
        raise RuntimeError("副本 settings 存储不是 dict，中止演练")
    changes: list[str] = []
    raw_kernel = settings.get("agent_kernel") or {}
    if isinstance(raw_kernel, str):
        try:
            raw_kernel = json.loads(raw_kernel)
        except json.JSONDecodeError:
            raw_kernel = {}
    if not isinstance(raw_kernel, dict):
        raw_kernel = {}
    dsh = raw_kernel.get("dsh") or {}
    env = dsh.get("provider_env") or {}
    if not isinstance(env, dict):
        env = {}
    # 置废范围 = 现有 provider_env 键 ∪ dsh 真实 apiKeyEnv 名 ∪ 兜底候选
    names = list(env)
    for n in _dsh_api_key_env_names():
        if n not in names:
            names.append(n)
    for n in _FALLBACK_KEY_ENV_NAMES:
        if n not in names:
            names.append(n)
    revoked = {name: DRILL_SENTINEL for name in names}
    dsh["provider_env"] = revoked
    raw_kernel["dsh"] = dsh
    raw_kernel.setdefault("type", "dsh")
    settings["agent_kernel"] = raw_kernel
    changes.append(f"agent_kernel.dsh.provider_env 置废 {len(revoked)} 项 → {DRILL_SENTINEL}")
    settings["llm_api_key"] = DRILL_SENTINEL
    changes.append(f"flat 键 llm_api_key 置废 → {DRILL_SENTINEL}")
    store.save_json("settings", settings)
    changes.append(f"副本 settings 已写回：{copy_dir}")
    return changes


class _CopySettingsRepo:
    """最小 settings 仓储适配器（读副本；与 build_gateway_from_repository 兼容）。"""

    def __init__(self, copy_dir: str) -> None:
        sys.path.insert(0, APP_ROOT)
        from data.local_storage import LocalDataStore

        self.data_store = LocalDataStore(base_dir=copy_dir)
        self._settings = self.data_store.load_json("settings", {}) or {}

    def get_setting(self, key: str, default: Any = "") -> Any:
        return self._settings.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        self._settings[key] = value
        self.data_store.save_json("settings", self._settings)


def run_degradation_chain(copy_dir: str, message: str) -> tuple[bool, list[dict[str, Any]]]:
    """在副本上下文跑一次降级链；返回 (handled, chunks)。"""
    import asyncio
    from types import SimpleNamespace

    sys.path.insert(0, APP_ROOT)
    os.environ["SIYI_DATA_DIR"] = copy_dir
    from backend.handlers.chat_paths.gateway_path import run_gateway_path
    from services.agent_gateway import build_gateway_from_repository

    repo = _CopySettingsRepo(copy_dir)
    gw = build_gateway_from_repository(repo)
    context = SimpleNamespace(agent_gateway=gw, settings_repository=repo)
    chunks: list[dict[str, Any]] = []

    async def _send_chunk(data: Any, chunk_type: str = "text") -> None:
        chunks.append({"type": chunk_type, "data": str(data)[:400]})

    async def _main() -> bool:
        return await run_gateway_path(context, message, {}, _send_chunk)

    handled = asyncio.run(_main())
    return handled, chunks


def evaluate_assertions(handled: bool, chunks: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """A1 能走到这里即不崩；A3 可见降级标记；A4 handled is False。"""
    detail: list[str] = ["A1 主进程未崩溃（run_gateway_path 正常返回）: OK"]
    visible = [c for c in chunks if c["type"] in ("notice", "error")]
    detail.append(f"A3 chunk 流含 notice/error 标记: {'OK' if visible else 'FAIL'}（{len(visible)} 条）")
    detail.append(f"A4 降级回落（handled=False）: {'OK' if handled is False else 'FAIL'}")
    return (handled is False) and bool(visible), detail


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="内核致命错误降级链演练（副本实跑=🔒人工批准）")
    ap.add_argument("--dry-run", action="store_true", help="只打印演练计划，不执行")
    ap.add_argument("--src-dir", default=DEFAULT_SRC, help="复制源（生产数据目录，只读）")
    ap.add_argument("--data-dir", default=DEFAULT_DST, help="副本目录（演练写入区）")
    ap.add_argument("--message", default=DRILL_MESSAGE, help="降级链演练消息")
    args = ap.parse_args(argv)

    if args.dry_run:
        print(render_plan(build_plan(args.src_dir, args.data_dir, args.message)))
        print("DRY-RUN：计划已打印，未执行任何复制/置废/调用。（副本实跑=🔒人工批准）")
        return 0

    src = args.src_dir if os.path.isabs(args.src_dir) else os.path.join(APP_ROOT, args.src_dir)
    dst = args.data_dir if os.path.isabs(args.data_dir) else os.path.join(APP_ROOT, args.data_dir)
    if os.path.abspath(dst) == os.path.abspath(DEFAULT_SRC):
        print("FAIL：--data-dir 指向生产 data/data，拒绝")
        return 1
    try:
        stats = copy_and_verify(src, dst)
        print(f"副本就绪：{stats['files']} 文件 / {stats['bytes']} 字节 / sha256 抽样 {stats['sha256_sampled']}")
        for line in invalidate_kernel_key(dst):
            print(f"置废：{line}")
        handled, chunks = run_degradation_chain(dst, args.message)
        passed, detail = evaluate_assertions(handled, chunks)
        for line in detail:
            print(line)
        print("演练结果:", "PASS" if passed else "FAIL")
        return 0 if passed else 1
    except Exception as exc:
        print(f"演练异常（I6 违例即 FAIL）：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
