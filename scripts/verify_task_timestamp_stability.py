"""验证 desknotes_task_adapter 的时间戳 / reminder_time 行为（只读，不碰用户数据）。

同源问题（与 note_adapter 同一模子）：
  - :102-103 created_at / updated_at 缺字段时回退 _now_iso() → 伪造成读取时刻
  - :95      reminder_time 无 dueDate 时回退 _now_iso() → 等于给待办挂一个
             "立即触发"的提醒，语义上比时间戳伪造更离谱

本脚本同时加载 **修复前（git HEAD 版本）** 与 **修复后（工作区版本）** 两个
adapter，在同一个临时数据副本上跑，直接对比。原始数据全程只读。

用法：
  思忆集app/.venv/Scripts/python.exe scripts/verify_task_timestamp_stability.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = REPO / "思忆集app"
sys.path.insert(0, str(APP_DIR))

from data.desknotes_id_map import IDMapper  # noqa: E402
from data.repositories.desknotes_task_adapter import (  # noqa: E402
    DeskNotesTaskAdapter as FixedAdapter,
)

DEFAULT_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "desk-notes" / "siyi-notes"
REL_ADAPTER = "思忆集app/data/repositories/desknotes_task_adapter.py"


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).isoformat()


def load_head_adapter():
    """从 git HEAD 取出修复前的 adapter，作为基线对照。"""
    src = subprocess.run(
        ["git", "show", f"HEAD:{REL_ADAPTER}"],
        cwd=str(REPO), capture_output=True, check=True,
    ).stdout.decode("utf-8")
    tmp_mod = Path(tempfile.mkdtemp(prefix="siyi_head_")) / "_orig_task_adapter.py"
    tmp_mod.write_text(src, encoding="utf-8")
    name = "data.repositories._orig_task_adapter"
    spec = importlib.util.spec_from_file_location(name, tmp_mod)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod.DeskNotesTaskAdapter


def build_isolated(data_dir: Path) -> tuple[Path, dt.datetime]:
    """复制 store.json / id map 到临时目录，并注入两条合成待办。

    真实数据里 32 条待办**全部**带 createdAt、只有 2 条带 dueDate，
    所以"缺 createdAt"这条路径必须造样本才能验证。
    注入后把 store.json 的 mtime 复原成原始值，否则 mtime 会变成注入时刻。
    """
    tmp = Path(tempfile.mkdtemp(prefix="siyi_task_verify_"))
    store_src = data_dir / "store.json"
    shutil.copy2(store_src, tmp / "store.json")
    orig_mtime = store_src.stat().st_mtime
    idmap = data_dir / "desknotes_id_map.json"
    if idmap.exists():
        shutil.copy2(idmap, tmp / "desknotes_id_map.json")

    store = json.loads((tmp / "store.json").read_text(encoding="utf-8"))
    state = store.get("state") or store
    todos = state.get("todos") or []
    old_ms = int((time.time() - 10 * 86400) * 1000)      # 10 天前
    due_ms = int((time.time() + 2 * 86400) * 1000)       # 2 天后
    todos.append({"id": "_verify_no_created", "text": "合成-无createdAt",
                  "done": False, "sortOrder": 1})
    todos.append({"id": "_verify_with_due", "text": "合成-有dueDate",
                  "done": False, "createdAt": old_ms, "dueDate": due_ms, "sortOrder": 2})
    state["todos"] = todos
    (tmp / "store.json").write_text(
        json.dumps(store, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    os.utime(tmp / "store.json", (orig_mtime, orig_mtime))  # 复原 mtime
    return tmp, dt.datetime.fromtimestamp(orig_mtime, tz=dt.timezone.utc)


def read3(adapter, task_id: str, label: str) -> list[dict]:
    out = []
    for i in range(3):
        hit = next((t for t in adapter.list_tasks(include_deleted=True)
                    if t.get("remote_id") == task_id), None)
        out.append(hit or {})
        t = hit or {}
        print(f"    {label} 第{i + 1}次: created_at={t.get('created_at')} "
              f"updated_at={t.get('updated_at')} reminder_time={t.get('reminder_time')}")
        if i < 2:
            time.sleep(1.0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    args = ap.parse_args()
    data_dir = Path(args.data_dir)
    if not (data_dir / "store.json").exists():
        print(f"[FATAL] store.json 不存在: {data_dir / 'store.json'}")
        return 2

    HeadAdapter = load_head_adapter()
    tmp, store_mtime = build_isolated(data_dir)
    store_mtime_ms = tmp.joinpath("store.json").stat().st_mtime * 1000

    head = HeadAdapter(tmp, IDMapper(tmp))
    head_id = IDMapper(tmp); head_id.sync_from_stores(); head = HeadAdapter(tmp, head_id)
    fid = IDMapper(tmp); fid.sync_from_stores()
    fixed = FixedAdapter(tmp, fid)

    print("=" * 84)
    print("desknotes_task_adapter 时间戳 / reminder_time 验证（修复前 HEAD vs 修复后）")
    print(f"数据副本     : {tmp}   （原始 {data_dir} 全程只读）")
    print(f"store.json mtime: {store_mtime.isoformat()}")
    print(f"运行时刻     : {_iso(time.time())}")
    print("=" * 84)

    failures: list[str] = []
    now_ms = time.time() * 1000

    def check(tag: str, reads: list[dict], key: str, expect: str):
        vals = [r.get(key) for r in reads]
        uniq = set(vals)
        if len(uniq) != 1:
            failures.append(f"{tag} {key} 三次不一致: {vals}")
            print(f"    [FAIL] {key} 三次不一致 -> {vals}")
            return None
        v = vals[0]
        print(f"    [OK]   {key} 三次一致 = {v}")
        if expect == "not_now":
            if v is None:
                return v
            d = abs(dt.datetime.fromisoformat(v).timestamp() * 1000 - now_ms) / 1000.0
            if d < 5:
                failures.append(f"{tag} {key} 落在读取时刻 (距now {d:.1f}s)")
                print(f"    [FAIL] {key} 距当前时刻仅 {d:.1f}s —— 仍是伪时间")
            else:
                print(f"    [OK]   {key} 距当前时刻 {d:.1f}s —— 不是读取时刻")
        elif expect == "none":
            if v is not None:
                failures.append(f"{tag} {key} 应为 None，实为 {v}")
                print(f"    [FAIL] {key} 应为 None，实为 {v}")
            else:
                print(f"    [OK]   {key} 为 None（没有提醒时间）")
        return v

    cases = [
        ("① 合成待办：无 createdAt", "_verify_no_created", "not_now"),
        ("② 合成待办：有 dueDate（回归）", "_verify_with_due", "keep"),
    ]
    # 真实数据里挑一条无 dueDate、一条有 dueDate
    real = json.loads((tmp / "store.json").read_text(encoding="utf-8"))
    real_todos = (real.get("state") or real).get("todos") or []
    real_no_due = next((t["id"] for t in real_todos
                        if t.get("id", "").isdigit() or not t["id"].startswith("_verify")
                        if not t.get("dueDate")), None)
    real_due = next((t.get("id") for t in real_todos if t.get("dueDate")
                     and not str(t.get("id")).startswith("_verify")), None)
    if real_no_due:
        cases.append((f"③ 真实待办：无 dueDate ({str(real_no_due)[:24]})", real_no_due, "keep"))
    if real_due:
        cases.append((f"④ 真实待办：有 dueDate ({str(real_due)[:24]})", real_due, "keep"))

    for title, tid, expect in cases:
        print(f"\n{title}")
        print("  ── 修复前（git HEAD）")
        h = read3(head, tid, "HEAD")
        print("  ── 修复后（工作区）")
        f = read3(fixed, tid, "FIX ")
        check("修复后", f, "created_at", expect if expect != "keep" else "not_now")
        if expect == "keep" and tid == "_verify_with_due":
            due_ms = next(t["dueDate"] for t in real_todos if t["id"] == tid)
            want = dt.datetime.fromtimestamp(due_ms / 1000, tz=dt.timezone.utc).isoformat()
            got = f[0].get("reminder_time")
            if got != want:
                failures.append(f"{title} reminder_time 被改动: {got} != {want}")
                print(f"    [FAIL] reminder_time 回归失败: {got} != {want}")
            else:
                print(f"    [OK]   reminder_time 保持原值 = {want}")
        if expect == "keep" and tid == real_no_due:
            check("修复后", f, "reminder_time", "none")
        if expect == "keep" and tid == real_due:
            print(f"    [OK]   reminder_time = {f[0].get('reminder_time')}")

    # 统计：无 dueDate 的待办里，reminder_time 非空的条数
    print("\n⑤ 全量统计：reminder_time 非空的待办数")
    for tag, ad in (("修复前", head), ("修复后", fixed)):
        tasks = ad.list_tasks(include_deleted=True)
        n = sum(1 for t in tasks if t.get("reminder_time"))
        extra = ""
        if tag == "修复前":
            at = time.time() * 1000  # 此刻重算：这批值是刚刚现读的
            n_now = sum(1 for t in tasks if t.get("reminder_time")
                        and abs(dt.datetime.fromisoformat(t["reminder_time"]).timestamp() * 1000
                                - at) / 1000.0 < 10)
            extra = f"  ← 其中 {n_now} 条是\"现在\"（伪提醒时间）"
        print(f"  {tag}: {n} / {len(tasks)}{extra}")

    # store.json mtime 分支说明
    print(f"\n  参考：store.json mtime = {_iso(store_mtime_ms / 1000)}（待办无 per-item 文件，"
          f"mtime 只能退到这一个共享文件）")

    # 排序稳定性
    print("\n⑥ list_tasks() 连读 2 次顺序一致性")
    a = [t["id"] for t in fixed.list_tasks()]
    time.sleep(1.0)
    b = [t["id"] for t in fixed.list_tasks()]
    if a == b:
        print(f"  [OK]   一致（{len(a)} 条）")
    else:
        failures.append("list_tasks 顺序抖动")
        print("  [FAIL] 顺序抖动")

    print("\n" + "=" * 84)
    if failures:
        print(f"结果: FAIL —— {len(failures)} 项")
        for x in failures:
            print(f"  - {x}")
        print("=" * 84)
        return 1
    print("结果: PASS")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
