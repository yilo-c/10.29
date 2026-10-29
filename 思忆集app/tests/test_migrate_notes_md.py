"""M3-2 迁移工具内核测试（纯函数 + tmp_path 临时库，绝不碰生产库）。

任务书出口：①合成 HTML 条目迁移后 backup==原值 ②幂等（跑两次第二次 changed==0）
③纯 MD 条目不动。另覆盖转换规则表与回环自检。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import migrate_notes_html_to_md as mig  # noqa: E402


# ── 纯函数：形态检测 ─────────────────────────────────────────────────────

def test_looks_like_html():
    assert mig.looks_like_html("<p>hello</p>")
    assert mig.looks_like_html('<div style="x"><b>粗</b></div>')
    assert not mig.looks_like_html("普通文本，无标签")
    assert not mig.looks_like_html("# 标题\n\n**粗**\n- 项")
    assert not mig.looks_like_html("")


def test_extract_features_and_nesting():
    feats = mig.extract_features('<h3>t</h3><ul><li><input type="checkbox" checked> a</li></ul><img src="x.png">')
    assert {"heading", "ul", "checklist", "image"} <= set(feats)
    assert not mig.has_nested_list("<ul><li>a</li></ul>")
    assert mig.has_nested_list("<ul><li>a<ul><li>b</li></ul></li></ul>")


# ── 纯函数：转换规则表（契约 §2）──────────────────────────────────────────

@pytest.mark.parametrize(
    ("html_src", "expected"),
    [
        ("<strong>粗</strong>", "**粗**"),
        ("<b>粗</b>", "**粗**"),
        ("<em>斜</em>", "*斜*"),
        ("<i>斜</i>", "*斜*"),
        ("<h1>标题</h1>", "# 标题"),
        ("<h3>三级</h3>", "### 三级"),
        ("<p>甲</p><p>乙</p>", "甲\n\n乙"),
        ("<br>", ""),  # 单独 br → 空
        ("<ul><li>一</li><li>二</li></ul>", "- 一\n- 二"),
        ("<ol><li>一</li><li>二</li></ol>", "1. 一\n2. 二"),
        ('<a href="https://x.cn">链接</a>', "[链接](https://x.cn)"),
        ('<img src="/assets/a.png" alt="图">', "![图](/assets/a.png)"),
        ('<img alt="图" src="/assets/a.png">', "![图](/assets/a.png)"),  # 属性乱序
        ("<pre><code class=\"language-python\">print(1)</code></pre>", "```python\nprint(1)\n```"),
        ("<code>x=1</code>", "`x=1`"),
        ("<blockquote>引</blockquote>", "> 引"),
        ("<hr>", "---"),
        ("<del>删</del>", "~~删~~"),
        ("<mark>高</mark>", "==高=="),
        ('<ul><li><input type="checkbox" checked> 甲</li><li><input type="checkbox"> 乙</li></ul>',
         "- [x] 甲\n- [ ] 乙"),
        ("<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>",
         "| A | B |\n| --- | --- |\n| 1 | 2 |"),
        ("&amp;&lt;&#27721;&gt;", "&<汉>"),
    ],
)
def test_convert_rules(html_src, expected):
    assert mig.html_to_markdown(html_src) == expected


def test_mixed_document():
    html = ('<h2>周记</h2><p>今天<strong>很累</strong>，见<br>图 '
            '<img src="/assets/p.png" alt="p"> <a href="https://a.cn">链</a></p>'
            "<ul><li>买 菜</li><li>写 码</li></ul>")
    md = mig.html_to_markdown(html)
    assert "## 周记" in md
    assert "**很累**" in md
    assert "![p](/assets/p.png)" in md
    assert "[链](https://a.cn)" in md
    assert "- 买 菜" in md
    assert mig.roundtrip_ok(html, md)


# ── 纯函数：回环自检 ─────────────────────────────────────────────────────

def test_roundtrip_detects_loss():
    html = "<p>完整文本</p>"
    assert mig.roundtrip_ok(html, "完整文本")
    assert not mig.roundtrip_ok(html, "丢了几个字")


def test_plain_symmetry_across_shapes():
    html = ('<div><p>段落一 <b>粗</b> <a href="u">文</a></p><ul><li>项1</li></ul>'
            '<hr><img src="i.png" alt="a"><pre><code>code1</code></pre>'
            '<blockquote>q</blockquote></div>')
    assert mig.roundtrip_ok(html, mig.html_to_markdown(html))


def test_plain_symmetry_alt_equals_checkbox_letter():
    # 回归：alt="x"/"X"/空格 曾被 checkbox 清理误食（试转矩阵抓到的真 bug）
    for alt in ("x", "X", " "):
        html = f'<p>前</p><img src="/assets/p.png" alt="{alt}"><p>后</p>'
        assert mig.roundtrip_ok(html, mig.html_to_markdown(html)), alt


# ── 编排：migrate_entries 三出口断言 ─────────────────────────────────────

def _note(i, content, **extra):
    return {"id": i, "title": f"n{i}", "content": content, "version": 3, **extra}


def test_migration_backup_equals_original():
    notes = [_note(1, "<p><b>甲</b>乙</p>"), _note(2, "纯 MD 无标签")]
    stats = mig.MigrationStats()
    out = mig.migrate_entries(notes, stats)
    assert out[0]["content_html_backup"] == "<p><b>甲</b>乙</p>"
    assert out[0]["content"] == "**甲**乙"
    assert out[0]["contentFormat"] == "md"
    # ③ 纯 MD 条目不动（无 backup、无 contentFormat、字段全等）
    assert "content_html_backup" not in out[1]
    assert "contentFormat" not in out[1]
    assert out[1] == notes[1]
    assert (stats.total, stats.candidate, stats.changed, stats.untouched_md) == (2, 1, 1, 1)


def test_migration_idempotent_second_run_zero_changed():
    notes = [_note(i, f"<p>内容{i} <em>x</em></p>") for i in range(1, 6)]
    first = mig.migrate_entries(notes)
    stats2 = mig.MigrationStats()
    second = mig.migrate_entries(first, stats2)
    assert stats2.changed == 0
    assert stats2.already_migrated == 5
    assert second == first


def test_migration_preserves_version_and_metadata():
    n = _note(7, "<p>x</p>", version=42, editMode=None, tags=["a"], updated_at="t")
    out = mig.migrate_entries([n])[0]
    assert out["version"] == 42
    assert out["updated_at"] == "t"
    assert out["tags"] == ["a"]
    assert "editMode" in out and out["editMode"] is None


def test_migration_failure_keeps_entry_untouched():
    # 嵌套列表 → 不写、进 failures
    html = "<ul><li>甲<ul><li>乙</li></ul></li></ul>"
    stats = mig.MigrationStats()
    out = mig.migrate_entries([_note(1, html)], stats)
    assert out[0]["content"] == html
    assert "content_html_backup" not in out[0]
    assert stats.changed == 0 and len(stats.failures) == 1
    assert stats.failures[0]["reason"] == "nested-list"


# ── 磁盘层：tmp_path 临时 kv_store 库全链路 ──────────────────────────────

def _make_db(dirpath: Path, notes: list[dict]) -> Path:
    db = dirpath / "local_storage.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE kv_store (key TEXT PRIMARY KEY, value TEXT NOT NULL,"
        " updated_at REAL NOT NULL, schema_version INTEGER NOT NULL DEFAULT 0)"
    )
    con.execute(
        "INSERT INTO kv_store VALUES ('notes', ?, 0.0, 0)",
        (json.dumps(notes, ensure_ascii=False),),
    )
    con.commit()
    con.close()
    return db


def test_cli_dry_run_then_write_and_rerun_zero(tmp_path, capsys):
    notes = [
        _note(1, "<h2>题</h2><p><strong>文</strong>字</p>"),
        _note(2, "本来就是 MD"),
        _note(3, '<ul><li><input type="checkbox" checked> 项A</li></ul>'),
    ]
    db = _make_db(tmp_path, notes)

    assert mig.main(["--data-dir", str(tmp_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "候选 2 条" in out and "DRY-RUN" in out
    # dry-run 不改盘
    assert json.loads(sqlite3.connect(str(db)).execute(
        "SELECT value FROM kv_store WHERE key='notes'").fetchone()[0]) == notes

    assert mig.main(["--data-dir", str(tmp_path), "--yes"]) == 0
    migrated = json.loads(sqlite3.connect(str(db)).execute(
        "SELECT value FROM kv_store WHERE key='notes'").fetchone()[0])
    assert migrated[0]["content_html_backup"] == notes[0]["content"]
    assert migrated[0]["content"] == "## 题\n\n**文**字"
    assert migrated[1] == notes[1]
    assert migrated[2]["content"] == "- [x] 项A"

    capsys.readouterr()
    assert mig.main(["--data-dir", str(tmp_path)]) == 3  # 无 --yes 拒绝写
    capsys.readouterr()
    rc = mig.main(["--data-dir", str(tmp_path), "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "改写 0 条" in out and "幂等跳过 2 条" in out


def test_cli_missing_notes_key_stops(tmp_path):
    db = tmp_path / "local_storage.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE kv_store (key TEXT PRIMARY KEY, value TEXT NOT NULL,"
                " updated_at REAL NOT NULL, schema_version INTEGER NOT NULL DEFAULT 0)")
    con.commit()
    con.close()
    assert mig.main(["--data-dir", str(tmp_path), "--dry-run"]) == 2


# ── M3-7 验证器：绿路径 + 红路径自证 ─────────────────────────────────────

import verify_notes_migration as ver  # noqa: E402


def test_verifier_green_then_corrupt_backup_red(tmp_path):
    notes = [
        _note(1, "<h2>题</h2><p><b>文</b>字</p>"),
        _note(2, "纯文本条目"),
        _note(3, "<ul><li>甲</li></ul>"),
    ]
    (tmp_path / "before").mkdir()
    (tmp_path / "after").mkdir()
    before_db = _make_db(tmp_path / "before", notes)
    after_db = _make_db(tmp_path / "after", notes)
    assert mig.main(["--data-dir", str(tmp_path / "after"), "--yes"]) == 0

    verdict = ver.compare_entries(_read(before_db), _read(after_db))
    assert verdict.ok, verdict.failed
    assert ver.main(["--before", str(before_db), "--after", str(after_db)]) == 0

    # 红路径自证：人为改坏 1 条 backup → exit 1
    data = _read(after_db)
    data[0]["content_html_backup"] = "<h2>被改坏</h2>"
    con = sqlite3.connect(str(after_db))
    con.execute("UPDATE kv_store SET value = ? WHERE key='notes'", (json.dumps(data, ensure_ascii=False),))
    con.commit()
    con.close()
    assert ver.main(["--before", str(before_db), "--after", str(after_db)]) == 1


def _read(db: Path) -> list[dict]:
    con = sqlite3.connect(str(db))
    try:
        return json.loads(con.execute("SELECT value FROM kv_store WHERE key='notes'").fetchone()[0])
    finally:
        con.close()


def test_rollback_restores_original_and_idempotent(tmp_path):
    notes = [
        _note(1, "<h2>题</h2><p><b>文</b>字</p>"),
        _note(2, "纯文本不动"),
    ]
    before_db = _make_db(tmp_path, notes)
    assert mig.main(["--data-dir", str(tmp_path), "--yes"]) == 0
    migrated = _read(before_db)
    assert migrated[0]["content"] == "## 题\n\n**文**字"
    assert mig.main(["--data-dir", str(tmp_path), "--rollback", "--yes"]) == 0
    rolled = _read(before_db)
    assert rolled == notes  # 逐条字节级还原（含删备份列/标记）
    assert mig.main(["--data-dir", str(tmp_path), "--rollback", "--yes"]) == 0
    assert _read(before_db) == notes  # 回滚幂等
