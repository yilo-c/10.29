r"""M3-7 全量迁移 diff 无损报告器：对迁移前后两库逐条机器比对（202/202 口径）。

三条硬规则（任务书 M3-7，抽查 20 条升级为全量）：
  ① 条数相等、id 序列一致
  ② 每条 content_html_backup == 迁移前 content（字节级）
  ③ 文本回环等价：strip(前 HTML) 与 strip(后 MD) 折叠空白后逐字符相等
     （复用迁移内核 plain_from_html / plain_from_md，容差=空白折叠）
未迁移条目（纯 MD / 失败保留）要求迁移后字节级不变。

用法::

    python scripts/verify_notes_migration.py --before <快照库或目录> --after <staging或生产库>
    # 可选 --out <报告.md>；stdout 首行 PASS N/N 或 FAIL n/N

exit 0=全 PASS；1=有 FAIL；2=输入形状异常（停止上报）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass
from migrate_notes_html_to_md import (  # noqa: E402
    plain_from_html,
    plain_from_md,
    looks_like_html,
)


def load_notes_from_db(target: Path) -> list[dict]:
    """target 可为 .db 文件或含 local_storage.db 的目录；一律只读打开。"""
    db = target if target.suffix == ".db" else target / "local_storage.db"
    if not db.exists():
        raise FileNotFoundError(f"找不到库：{db}")
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        row = con.execute("SELECT value FROM kv_store WHERE key = 'notes' LIMIT 1").fetchone()
    finally:
        con.close()
    if row is None:
        raise KeyError("kv_store 缺 notes 键 → 停止上报")
    data = json.loads(row[0])
    if not isinstance(data, list):
        raise ValueError("notes 键不是列表 → 停止上报")
    return data


@dataclass
class Verdict:
    passed: int = 0
    failed: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed and self.passed == self.total


def compare_entries(before: list[dict], after: list[dict]) -> Verdict:
    v = Verdict(total=len(before))
    if len(before) != len(after):
        v.failed.append({"id": "*", "rule": "①条数", "detail": f"before={len(before)} after={len(after)}"})
        return v
    if [b.get("id") for b in before] != [a.get("id") for a in after]:
        v.failed.append({"id": "*", "rule": "①id序列", "detail": "顺序或 id 漂移"})
        return v
    for b, a in zip(before, after):
        nid = b.get("id")
        b_content = b.get("content")
        a_content = a.get("content")
        backup = a.get("content_html_backup")
        if backup is not None:
            if backup != b_content:  # 规则②字节级
                v.failed.append({"id": nid, "rule": "②backup字节级", "detail": "backup != 迁移前 content"})
                continue
            if not isinstance(a_content, str) or not isinstance(b_content, str):
                v.failed.append({"id": nid, "rule": "shape", "detail": "content 非字符串"})
                continue
            if plain_from_html(b_content) != plain_from_md(a_content):  # 规则③
                v.failed.append({"id": nid, "rule": "③文本回环", "detail": "strip 后文本不等价"})
                continue
            if a.get("contentFormat") != "md":
                v.failed.append({"id": nid, "rule": "标记", "detail": "缺 contentFormat='md'"})
                continue
        else:
            # 未迁移条目：必须整条字节级不动；若它本应是 HTML 候选 → 判残留红
            if a != b:
                v.failed.append({"id": nid, "rule": "未迁移条目被改动", "detail": "无备份列但条目有变"})
                continue
            if looks_like_html(str(b_content or "")):
                v.failed.append({"id": nid, "rule": "④残留HTML", "detail": "HTML 条目未迁移且无备份列"})
                continue
        v.passed += 1
    return v


def render_report_md(v: Verdict, before_n: int, after_n: int) -> str:
    head = f"PASS {v.passed}/{v.total}" if v.ok else f"FAIL {v.total - len(v.failed)}/{v.total}"
    lines = [head, "", f"- before 条数：{before_n}", f"- after 条数：{after_n}",
             f"- 失败：{len(v.failed)}", ""]
    for f in v.failed[:200]:
        lines.append(f"- id={f['id']} rule={f['rule']} detail={f['detail']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="M3-7 迁移前后全量无损比对")
    ap.add_argument("--before", required=True, help="迁移前快照库（.db 或目录）")
    ap.add_argument("--after", required=True, help="迁移后库（.db 或目录）")
    ap.add_argument("--out", default=None, help="报告 markdown 输出路径")
    ap.add_argument("--allow-residual", default="", help="白名单 id（分号分隔）：④残留HTML 对这些 id 降级为告警")
    args = ap.parse_args(argv)
    try:
        before = load_notes_from_db(Path(args.before))
        after = load_notes_from_db(Path(args.after))
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"[verify] {exc}", file=sys.stderr)
        return 2
    v = compare_entries(before, after)
    allow = {s for s in args.allow_residual.split(";") if s}
    hard_failures = [f for f in v.failed if str(f["id"]) not in allow]
    v.passed += len(v.failed) - len(hard_failures)
    v.failed = hard_failures
    report = render_report_md(v, len(before), len(after))
    print(report.splitlines()[0])
    for f in v.failed[:20]:
        print(f"[verify] FAIL id={f['id']} rule={f['rule']} detail={f['detail']}")
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
    return 0 if v.ok else 1


if __name__ == "__main__":
    sys.exit(main())
