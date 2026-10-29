"""验证 desknotes_note_adapter 的时间戳稳定性（只读，不碰用户数据）。

背景（P0）：_note_to_dict 在 frontmatter 缺 createdAt/updatedAt 时回退
_now_iso()，导致 created_at 被伪造成"读取时刻"：同一笔记每次读都不一样，
排序键 / 时间线 / 日历 / 回顾全部建立在一个不存在的时间上。

本脚本：
  1. 把 notes 目录 copy2 到临时目录（保留 mtime），在副本上跑，绝不改用户数据；
  2. 对同一笔记连续读 3 次（间隔 1s），断言 created_at 三次完全相同；
  3. 对"缺失 createdAt"的笔记，断言返回值贴近文件 mtime 而不是当前时刻；
  4. 顺便验证 list_notes() 两次的排序结果一致（顺序不再抖动）。

用法：
  思忆集app/.venv/Scripts/python.exe scripts/verify_note_timestamp_stability.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "思忆集app"
sys.path.insert(0, str(APP_DIR))

from data.desknotes_id_map import IDMapper  # noqa: E402
from data.repositories.desknotes_note_adapter import DeskNotesNoteAdapter  # noqa: E402

DEFAULT_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "desk-notes" / "siyi-notes"


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).isoformat()


def _ms(iso_str: str | None) -> float | None:
    if not iso_str:
        return None
    return dt.datetime.fromisoformat(iso_str).timestamp() * 1000


def build_isolated_adapter(data_dir: Path) -> tuple[DeskNotesNoteAdapter, Path]:
    """在临时副本上构造 adapter，原始目录只读。"""
    tmp = Path(tempfile.mkdtemp(prefix="siyi_ts_verify_"))
    # copy2 保留 mtime —— 否则副本 mtime 会变成"现在"，测不出真实回退源
    shutil.copytree(data_dir / "notes", tmp / "notes", copy_function=shutil.copy2)
    for name in ("store.json", "desknotes_id_map.json"):
        src = data_dir / name
        if src.exists():
            shutil.copy2(src, tmp / name)
    mapper = IDMapper(tmp)
    mapper.sync_from_stores()
    return DeskNotesNoteAdapter(tmp, mapper), tmp


def pick_samples(notes_dir: Path) -> tuple[str | None, str | None, str | None]:
    """返回 (无 createdAt 的笔记 stem, 有 createdAt 的笔记 stem, 归档笔记 stem)。"""
    no_created = with_created = archived = None
    for p in sorted(notes_dir.glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "createdAt" in text:
            with_created = with_created or p.stem
        else:
            no_created = no_created or p.stem
        if "archived: true" in text or "deletedAt" in text:
            archived = archived or p.stem
    return no_created, with_created, archived


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    if not (data_dir / "notes").is_dir():
        print(f"[FATAL] notes 目录不存在: {data_dir / 'notes'}")
        return 2

    adapter, tmp = build_isolated_adapter(data_dir)
    notes_dir = tmp / "notes"
    no_created, with_created, archived = pick_samples(notes_dir)

    print("=" * 78)
    print("desknotes_note_adapter 时间戳稳定性验证")
    print(f"数据源副本 : {tmp}   (原始 {data_dir} 全程只读)")
    print(f"运行时刻   : {_iso(time.time())}")
    print("=" * 78)

    failures: list[str] = []
    now_ms = time.time() * 1000

    for label, stem in (
        ("A. 缺失 createdAt 的笔记", no_created),
        ("B. 含 createdAt 的笔记（回归）", with_created),
        ("C. 归档笔记（deleted_at 链）", archived),
    ):
        if not stem:
            print(f"\n{label}: 未找到样本，跳过")
            continue
        md = notes_dir / f"{stem}.md"
        mtime_ms = md.stat().st_mtime * 1000
        print(f"\n{label}: {stem}.md")
        print(f"  文件 mtime      : {_iso(mtime_ms / 1000)}")

        reads = []
        for i in range(3):
            if label.startswith("C"):
                # 归档笔记 get_note() 会按协议返回 None，改从 include_deleted 列表取
                hit = next(
                    (n for n in adapter.list_notes(include_deleted=True)
                     if n.get("remote_id") == stem),
                    None,
                )
                note = hit
            else:
                note = adapter.get_note(stem)
            reads.append(note or {})
            print(
                f"  第 {i + 1} 次读 created_at={note.get('created_at') if note else None}"
                f"  updated_at={note.get('updated_at') if note else None}"
                f"  deleted_at={note.get('deleted_at') if note else None}"
            )
            if i < 2:
                time.sleep(1.0)

        created_vals = [r.get("created_at") for r in reads]
        if len(set(created_vals)) != 1:
            failures.append(f"{label} created_at 三次不一致: {created_vals}")
            print(f"  [FAIL] created_at 三次不一致 -> {created_vals}")
        else:
            print(f"  [OK]   created_at 三次一致 = {created_vals[0]}")

        if label.startswith("A"):
            c_ms = _ms(created_vals[0])
            if c_ms is None:
                failures.append(f"{label} created_at 为空")
            else:
                dist_now = abs(c_ms - now_ms) / 1000.0
                dist_mtime = abs(c_ms - mtime_ms) / 1000.0
                print(
                    f"  距当前时刻 {dist_now:.1f}s ；距文件 mtime {dist_mtime:.1f}s"
                )
                if dist_now < 5 and dist_mtime > 5:
                    failures.append(
                        f"{label} created_at 落在'当前时刻'而非文件 mtime "
                        f"(dist_now={dist_now:.1f}s, dist_mtime={dist_mtime:.1f}s)"
                    )
                    print("  [FAIL] 时间戳被伪造成读取时刻")
                else:
                    print("  [OK]   时间戳来自真实时间源，不是读取时刻")

    # E. 合成样本：连 updatedAt 都没有 —— 必须落到文件 mtime 分支
    #    （真实数据里 288 篇都带 updatedAt，构造此例专门验证 mtime 回退本身）
    print("\nE. 合成样本：frontmatter 无任何时间字段（验证 mtime 回退分支）")
    synth = notes_dir / "_verify_no_timestamp.md"
    synth.write_text("---\nid: _verify_no_timestamp\ntitle: 无时间字段\n---\n\n内容\n",
                     encoding="utf-8")
    fake_mtime = time.time() - 3 * 86400  # 3 天前
    os.utime(synth, (fake_mtime, fake_mtime))
    synth_mtime_ms = synth.stat().st_mtime * 1000
    print(f"  文件 mtime      : {_iso(synth_mtime_ms / 1000)}  (人为设为 3 天前)")
    synth_reads = []
    for i in range(3):
        hit = next(
            (n for n in adapter.list_notes(include_deleted=True)
             if n.get("remote_id") == "_verify_no_timestamp"),
            None,
        )
        synth_reads.append((hit or {}).get("created_at"))
        print(f"  第 {i + 1} 次读 created_at={(hit or {}).get('created_at')}")
        if i < 2:
            time.sleep(1.0)
    if len(set(synth_reads)) != 1:
        failures.append(f"E 合成样本 created_at 三次不一致: {synth_reads}")
        print(f"  [FAIL] 三次不一致 -> {synth_reads}")
    else:
        c_ms = _ms(synth_reads[0])
        dist_mtime = abs(c_ms - synth_mtime_ms) / 1000.0
        dist_now = abs(c_ms - now_ms) / 1000.0
        print(f"  距文件 mtime {dist_mtime:.1f}s ；距当前时刻 {dist_now:.1f}s")
        if dist_mtime > 2.0 or dist_now < 3600:
            failures.append(
                f"E 合成样本 created_at 未落到文件 mtime "
                f"(dist_mtime={dist_mtime:.1f}s, dist_now={dist_now:.1f}s)"
            )
            print("  [FAIL] mtime 回退未生效")
        else:
            print("  [OK]   created_at == 文件 mtime（3 天前），且三次一致")
    synth.unlink()

    # 排序稳定性
    print("\nD. list_notes() 排序稳定性（同一副本连读 2 次）")
    first = [n["id"] for n in adapter.list_notes()]
    time.sleep(1.0)
    second = [n["id"] for n in adapter.list_notes()]
    if first == second:
        print(f"  [OK]   两次顺序完全一致（共 {len(first)} 条）")
    else:
        diff = [(i, a, b) for i, (a, b) in enumerate(zip(first, second)) if a != b]
        failures.append(f"list_notes 顺序抖动，首个差异 {diff[:3]}")
        print(f"  [FAIL] 顺序抖动，前 3 处差异 {diff[:3]}")

    print("\n" + "=" * 78)
    if failures:
        print(f"结果: FAIL —— {len(failures)} 项")
        for f in failures:
            print(f"  - {f}")
        print("=" * 78)
        return 1
    print("结果: PASS —— 时间戳稳定且来自真实时间源")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
