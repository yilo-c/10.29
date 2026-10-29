r"""M3-2 迁移工具内核：notes 键 HTML 存量 → Markdown 真源。

契约：deliverables/refined/m3-md-contract.md（v1.0）。
纯函数转换内核（可单测、不碰生产库）+ 一次性写回 CLI（写回需人工批准，M3-8）。

- 转换规则对齐前端 markdownConvert.ts:60 htmlToMarkdown（turndown）：
  atx 标题 / **bold** / *em* / ~~del~~ / `- ` 列表 / [t](u) / ![a](src) /
  fenced 代码 / > 引用 / --- 分隔 / br→换行 / GFM 表格（turndown 核心会丢表，
  本工具升级为管道表，属"只增不错"偏差，见契约 §2 规则表）。
- 安全闸：每条转换先过"文本回环自检"（strip 两侧后文本必须等价，容差=空白折叠），
  不等价的条目 **不改写**、进 failures 清单人工分类——保证写回产物天然全 PASS。
- 幂等：已带 content_html_backup 的条目跳过；非 HTML 条目零触碰。
- 写回：BEGIN IMMEDIATE 事务 UPDATE kv_store 'notes'（对齐 data/sqlite_store.py
  .update 语义）；不 bump version、不动 updated_at/editMode（契约 §1：迁移在
  后端与全部客户端停机窗口执行，乐观锁语义与迁移正交）。

用法::

    python scripts/migrate_notes_html_to_md.py --data-dir <dir> --dry-run
    python scripts/migrate_notes_html_to_md.py --data-dir <dir> --yes      # 写回
"""
from __future__ import annotations

import argparse
import html as _html
import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KV_KEY = "notes"

try:  # Windows 控制台编码防御：不可编码字符降级替换而非崩溃
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

# ── 形态检测 ────────────────────────────────────────────────────────────

# 判定 legacy HTML 存储形态（任务书 M3-2："检测 HTML 标签正则"）
_HTML_TAG_RE = re.compile(
    r"</?(?:p|div|span|h[1-6]|ul|ol|li|a|img|b|i|em|strong|u|s|del|strike|code|pre"
    r"|blockquote|br|hr|table|thead|tbody|tr|td|th|input|font|tt|mark|video|figure)"
    r"\b[^>]*/?>",
    re.IGNORECASE,
)

_FEATURE_RES: dict[str, re.Pattern[str]] = {
    "heading": re.compile(r"<h[1-6]\b", re.I),
    "bold": re.compile(r"<(?:strong|b)\b", re.I),
    "italic": re.compile(r"<(?:em|i)\b", re.I),
    "link": re.compile(r"<a\b", re.I),
    "image": re.compile(r"<img\b", re.I),
    "ul": re.compile(r"<ul\b", re.I),
    "ol": re.compile(r"<ol\b", re.I),
    "checklist": re.compile(r"<input\b[^>]*type=[\"']?checkbox", re.I),
    "code-inline": re.compile(r"<code\b(?![^>]*language-)", re.I),
    "code-block": re.compile(r"<pre\b", re.I),
    "blockquote": re.compile(r"<blockquote\b", re.I),
    "table": re.compile(r"<table\b", re.I),
    "hr": re.compile(r"<hr\b", re.I),
    "br": re.compile(r"<br\b", re.I),
    "mark": re.compile(r"<mark\b", re.I),
    "strike": re.compile(r"<(?:del|s|strike)\b", re.I),
    "video": re.compile(r"<video\b", re.I),
    "font-span": re.compile(r"<(?:font|span)\b", re.I),
    "style-attr": re.compile(r"\sstyle=", re.I),
}


def looks_like_html(content: str) -> bool:
    return bool(content) and bool(_HTML_TAG_RE.search(content))


def extract_features(content: str) -> list[str]:
    return sorted(name for name, rx in _FEATURE_RES.items() if rx.search(content))


_NESTED_LIST_RE = re.compile(r"<li\b[^>]*>(?:(?!</li>).)*?<(?:ul|ol)\b", re.S | re.I)


def has_nested_list(content: str) -> bool:
    return bool(_NESTED_LIST_RE.search(content))


# ── 属性解析（不依赖 src/alt 书写顺序）──────────────────────────────────

def _attr(tag: str, name: str) -> str:
    m = re.search(name + r"\s*=\s*[\"']([^\"']*)[\"']", tag, re.I)
    if not m:
        m = re.search(name + r"\s*=\s*([^\s\"'>]+)", tag, re.I)
    return m.group(1) if m else ""


# ── HTML → Markdown 纯函数转换 ──────────────────────────────────────────

def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _inline(text: str) -> str:
    """行内标签 → MD。顺序敏感：strong 先于 em；a 先于残余标签剥离。"""

    def _img(m: re.Match) -> str:
        tag = m.group(0)
        return "![%s](%s)" % (_attr(tag, "alt"), _attr(tag, "src"))

    def _video(m: re.Match) -> str:  # 对齐前端 turndown video 规则
        tag = m.group(0)
        src = _attr(tag, "src")
        return "![%s](%s)" % (_attr(tag, "alt"), src) if src else ""

    def _link(m: re.Match) -> str:
        tag, inner = m.group(0), m.group(1)
        return "[%s](%s)" % (inner, _attr(tag, "href"))

    text = re.sub(r"<video\b[^>]*>", _video, text, flags=re.I)
    text = re.sub(r"<img\b[^>]*/?>", _img, text, flags=re.I)
    text = re.sub(r'<a\b[^>]*href=["\'][^"\']*["\'][^>]*>(.*?)</a>', _link, text, flags=re.S | re.I)
    text = re.sub(r"<strong\b[^>]*>(.*?)</strong>", r"**\1**", text, flags=re.S | re.I)
    text = re.sub(r"<b\b[^>]*>(.*?)</b>", r"**\1**", text, flags=re.S | re.I)
    text = re.sub(r"<em\b[^>]*>(.*?)</em>", r"*\1*", text, flags=re.S | re.I)
    text = re.sub(r"<i\b[^>]*>(.*?)</i>", r"*\1*", text, flags=re.S | re.I)
    for tag in ("del", "s", "strike"):
        text = re.sub(r"<%s\b[^>]*>(.*?)</%s>" % (tag, tag), r"~~\1~~", text, flags=re.S | re.I)
    text = re.sub(r"<mark\b[^>]*>(.*?)</mark>", r"==\1==", text, flags=re.S | re.I)
    text = re.sub(r"<code\b[^>]*>(.*?)</code>", r"`\1`", text, flags=re.S | re.I)
    text = re.sub(r"</?(?:u|span|font|small|big|label|sup|sub)\b[^>]*>", "", text, flags=re.I)
    return text


def _plain_inline(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _rich(fragment: str) -> str:
    """片段 → 单行 MD 文本：先保格式转 MD，再剥残余标签，最后解实体。"""
    return _plain_inline(_html.unescape(_strip_tags(_inline(fragment))))


def _convert_list(block: str) -> str:
    """单层 <ul>/<ol> → MD 列表；checkbox → GFM task。"""
    ordered = block.lstrip().lower().startswith("<ol")
    items = re.findall(r"<li\b[^>]*>(.*?)</li>", block, flags=re.S | re.I)
    out = []
    for idx, raw in enumerate(items, start=1):
        box = ""
        cm = re.search(r"<input\b[^>]*type=[\"']?checkbox[^>]*>", raw, re.I)
        if cm:
            box = "[x] " if re.search(r"\bchecked\b", cm.group(0), re.I) else "[ ] "
            raw = raw[: cm.start()] + " " + raw[cm.end():]
        text = _rich(raw)
        marker = f"{idx}." if ordered else "-"
        out.append(f"{marker} {box}{text}".rstrip())
    return "\n".join(out)


def _convert_code(block: str) -> str:
    m = re.search(r"<pre\b[^>]*>(.*?)</pre>", block, flags=re.S | re.I)
    inner = m.group(1) if m else block
    lm = re.search(r"\blanguage-(\w+)", block, re.I) or re.search(r"<code\b[^>]*class=[\"'][^\"']*?(\w+)", block, re.I)
    lang = lm.group(1) if lm else ""
    code = _html.unescape(_strip_tags(inner)).strip("\n")
    return "```%s\n%s\n```" % (lang, code)


def _convert_table(block: str) -> str:
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", block, flags=re.S | re.I)
    out = []
    for r in rows:
        cells = re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", r, flags=re.S | re.I)
        cells = [_rich(c).replace("|", "\\|") or " " for c in cells]
        out.append("| " + " | ".join(cells) + " |")
    if not out:
        return ""
    ncol = out[0].count("|") - 1
    out.insert(1, "| " + " | ".join(["---"] * ncol) + " |")
    return "\n".join(out)


def html_to_markdown(content: str) -> str:
    """legacy HTML → Markdown（纯函数）。规则见表：契约 §2。"""
    if not content:
        return ""
    text = content

    code_blocks: list[str] = []

    def _keep_pre(m: re.Match) -> str:
        code_blocks.append(_convert_code(m.group(0)))
        return "\n\x00CODE%d\x00\n" % (len(code_blocks) - 1)

    text = re.sub(r"<pre\b[^>]*>.*?</pre>", _keep_pre, text, flags=re.S | re.I)

    def _heading(m: re.Match) -> str:
        return "\n%s %s\n" % ("#" * int(m.group(1)), _rich(m.group(2)))

    text = re.sub(r"<h([1-6])\b[^>]*>(.*?)</h\1>", _heading, text, flags=re.S | re.I)

    text = re.sub(r"<table\b[^>]*>.*?</table>", lambda m: "\n" + _convert_table(m.group(0)) + "\n", text, flags=re.S | re.I)

    text = re.sub(r"<(?:ul|ol)\b[^>]*>.*?</(?:ul|ol)>", lambda m: "\n" + _convert_list(m.group(0)) + "\n", text, flags=re.S | re.I)

    def _quote(m: re.Match) -> str:
        inner = _rich(m.group(1))
        return "\n" + "\n".join("> " + ln for ln in inner.splitlines()) + "\n"

    text = re.sub(r"<blockquote\b[^>]*>(.*?)</blockquote>", _quote, text, flags=re.S | re.I)

    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<hr\s*/?>", "\n\n---\n\n", text, flags=re.I)
    # 块级容器 → 换行；行内标签此时尚未处理，留给 _inline 保格式
    text = re.sub(
        r"</?(?:p|div|figure|figcaption|section|article|main|header|footer"
        r"|table|thead|tbody|tr|td|th|video|source|ul|ol|li|input|blockquote)\b[^>]*/?>",
        "\n", text, flags=re.I,
    )
    text = _inline(text)
    text = _strip_tags(text)  # 残余未知标签兜底剥离（对齐前端 catch 分支）
    text = _html.unescape(text)

    for i, blk in enumerate(code_blocks):
        text = text.replace("\x00CODE%d\x00" % i, blk)

    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── 文本回环自检（M3-7 规则③的机内实现）────────────────────────────────

_WS_RE = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    return _WS_RE.sub(" ", s).strip()


_BLOCK_SEP_RE = re.compile(
    r"</?(?:p|div|h[1-6]|ul|ol|li|blockquote|table|thead|tbody|tr|td|th|br|hr"
    r"|figure|figcaption|section|article|main|header|footer|video|input|pre)\b[^>]*/?>",
    re.I,
)


def plain_from_html(html_src: str) -> str:
    # 块级标签→空格（与 MD 块边界折叠对齐）；行内标签直接剥
    s = _BLOCK_SEP_RE.sub(" ", html_src)
    return normalize_text(_html.unescape(_strip_tags(s)))


def plain_from_md(md: str) -> str:
    s = md
    s = re.sub(r"^```[a-zA-Z0-9]*\s*$", "", s, flags=re.M)  # 围栏行去掉、代码文本保留
    s = re.sub(r"^[\s|:-]+\|[\s|:-]*$", "", s, flags=re.M)   # 表格分隔行
    s = re.sub(r"^\s*---+\s*$", "", s, flags=re.M)           # hr（HTML 侧 <hr> 剥离后无文本，须对称）
    s = s.replace("|", " ")
    s = re.sub(r"^#{1,6}\s+", "", s, flags=re.M)
    s = re.sub(r"^>\s?", "", s, flags=re.M)
    s = re.sub(r"^(\s*)(?:[-*+]\s+\[[ xX]\]\s+|[-*+]\s+|\d+\.\s+)", r"\1", s, flags=re.M)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)               # 图片两侧都归零（须先于 checkbox 清理）
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[[ xX]\]", " ", s)                          # 列表外残余 checkbox
    s = s.replace("**", "").replace("~~", "").replace("==", "")
    s = re.sub(r"(?<!\S)\*|\*(?!\S)|(?<=[\u4e00-\u9fff])\*(?=[\u4e00-\u9fff])", "", s)
    s = s.replace("`", "")
    return normalize_text(s)


def roundtrip_ok(html_src: str, md: str) -> bool:
    return plain_from_html(html_src) == plain_from_md(md)


# ── 迁移编排（纯数据，不碰磁盘）─────────────────────────────────────────

@dataclass
class MigrationStats:
    total: int = 0
    candidate: int = 0      # looks_like_html 且未迁移
    changed: int = 0        # 实际改写
    untouched_md: int = 0   # 非 HTML（已是 MD/纯文本）零触碰
    already_migrated: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    features: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "candidate": self.candidate,
            "changed": self.changed,
            "untouched_md": self.untouched_md,
            "already_migrated": self.already_migrated,
            "failures": self.failures,
            "features": self.features,
        }


def migrate_entries(notes: list[dict], stats: MigrationStats | None = None) -> list[dict]:
    """内存逐条转换并返回新列表（不改入参）。

    每条 HTML 条目：备份原值 → 转换 → 回环自检；自检不过则**不写**，进 failures。
    """
    stats = stats if stats is not None else MigrationStats()
    stats.total = len(notes)
    out: list[dict] = []
    for raw in notes:
        note = dict(raw)
        content = note.get("content")
        if "content_html_backup" in note:
            stats.already_migrated += 1
            out.append(note)
            continue
        if not isinstance(content, str) or not looks_like_html(content):
            stats.untouched_md += 1
            out.append(note)
            continue
        stats.candidate += 1
        feats = extract_features(content)
        for f in feats:
            stats.features[f] = stats.features.get(f, 0) + 1
        if has_nested_list(content):
            stats.failures.append({"id": note.get("id"), "reason": "nested-list", "features": feats})
            out.append(note)
            continue
        try:
            md = html_to_markdown(content)
        except Exception as exc:  # noqa: BLE001 —— 单条失败不阻断全量
            stats.failures.append({"id": note.get("id"), "reason": "convert-error:%s" % exc, "features": feats})
            out.append(note)
            continue
        if not roundtrip_ok(content, md):
            stats.failures.append({"id": note.get("id"), "reason": "roundtrip-mismatch", "features": feats})
            out.append(note)
            continue
        note["content_html_backup"] = content  # 字节级原值备份
        note["content"] = md
        note["contentFormat"] = "md"
        stats.changed += 1
        out.append(note)
    return out


# ── 磁盘 IO 层（仅 CLI 调用；单测只喂内存 list / tmp_path 副本）─────────

def db_path_of(data_dir: Path) -> Path:
    return data_dir / "local_storage.db"


def read_notes(con: sqlite3.Connection) -> list[dict]:
    row = con.execute("SELECT value FROM kv_store WHERE key = ? LIMIT 1", (KV_KEY,)).fetchone()
    if row is None:
        raise KeyError("kv_store 缺 notes 键")
    data = json.loads(row[0])
    if not isinstance(data, list):
        raise ValueError("notes 键不是列表（与 _notes.py:253 形状不符）→ 停止上报")
    return data


def write_notes(con: sqlite3.Connection, notes: list[dict]) -> None:
    payload = json.dumps(notes, ensure_ascii=False)
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(
            "UPDATE kv_store SET value = ?, updated_at = ? WHERE key = ?",
            (payload, time.time(), KV_KEY),
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def rollback_entries(notes: list[dict]) -> tuple[list[dict], int]:
    """回滚：凡带 content_html_backup 的条目还原 content 并删备份列/标记。幂等。"""
    out: list[dict] = []
    reverted = 0
    for raw in notes:
        note = dict(raw)
        if "content_html_backup" in note:
            note["content"] = note.pop("content_html_backup")
            note.pop("contentFormat", None)
            reverted += 1
        out.append(note)
    return out, reverted


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="M3-2 notes HTML→MD 存量迁移（契约 m3-md-contract.md）")
    ap.add_argument("--data-dir", required=True, help="含 local_storage.db 的目录（staging 副本）")
    ap.add_argument("--dry-run", action="store_true", help="只报数不写回")
    ap.add_argument("--yes", action="store_true", help="确认写回（人工批准闸，M3-8）")
    ap.add_argument("--rollback", action="store_true", help="回滚：备份列还原 content（需 --yes）")
    ap.add_argument("--report-out", default=None, help="把迁移报告 JSON 写到该路径（试转矩阵用）")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    db = db_path_of(data_dir)
    if not db.exists():
        print("[migrate] 找不到库：%s" % db, file=sys.stderr)
        return 2
    con = (sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
           if args.dry_run else sqlite3.connect(str(db)))
    try:
        notes = read_notes(con)
    except (KeyError, ValueError) as exc:
        print("[migrate] %s" % exc, file=sys.stderr)
        con.close()
        return 2
    try:
        if args.rollback:
            new_notes, reverted = rollback_entries(notes)
            print("[rollback] 总 %d 条 / 可回滚 %d 条" % (len(notes), reverted))
            if args.dry_run:
                print("[rollback] DRY-RUN：未写回。")
                return 0
            if not args.yes:
                print("[rollback] 写回需 --yes（人工批准闸）。", file=sys.stderr)
                return 3
            write_notes(con, new_notes)
            print("[rollback] 已回滚 %d 条。" % reverted)
            return 0
        stats = MigrationStats()
        new_notes = migrate_entries(notes, stats)
        print("[migrate] 库=%s" % db)
        print("[migrate] 总 %d 条 / 候选 %d 条 / 改写 %d 条 / 已是MD零触碰 %d 条 / 已迁移幂等跳过 %d 条 / 失败 %d 条"
              % (stats.total, stats.candidate, stats.changed, stats.untouched_md, stats.already_migrated, len(stats.failures)))
        print("[migrate] 形态矩阵=%s" % json.dumps(stats.features, ensure_ascii=False, sort_keys=True))
        for f in stats.failures[:20]:
            print("[migrate] FAIL id=%s reason=%s features=%s" % (f["id"], f["reason"], f["features"]))
        if args.report_out:
            Path(args.report_out).write_text(json.dumps(stats.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        if args.dry_run:
            print("[migrate] DRY-RUN：未写回。")
            return 0
        if not args.yes:
            print("[migrate] 写回需 --yes（人工批准闸）。", file=sys.stderr)
            return 3
        write_notes(con, new_notes)
        print("[migrate] 已写回 %d 条。" % stats.changed)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
