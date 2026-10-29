# -*- coding: utf-8 -*-
"""NoteTemplate / NoteTemplateManager（services/note_templates.py）单测。

TASK-V2-03 覆盖率棘轮：services.note_templates 此前 0%，本文件覆盖：
- NoteTemplate.render：变量替换、缺省占位、内置变量
- NoteTemplate.detect_variables：去重、排除内置、保序
- NoteTemplateManager：builtin 查询 / 分类，create/update/delete/duplicate、
  search、export/import 往返，非法 JSON import 返回 0
"""
from __future__ import annotations

import json

import pytest

from services.note_templates import (
    BUILTIN_TEMPLATES,
    NoteTemplate,
    NoteTemplateManager,
)


# ── NoteTemplate.render ────────────────────────────────────────

def test_render_substitutes_variables():
    t = NoteTemplate(id="x", name="x", description="", content="你好 {{name}}，今天是 {{date}}", variables=["name"])
    out = t.render(name="小明")
    assert "小明" in out
    assert "{{name}}" not in out


def test_render_missing_variable_uses_bracket_placeholder():
    t = NoteTemplate(id="x", name="x", description="", content="主题：{{topic}}", variables=["topic"])
    assert t.render() == "主题：[topic]"


def test_render_multiple_variables_and_unknown_var_ignored():
    t = NoteTemplate(
        id="x", name="x", description="",
        content="{{a}}|{{b}}|{{c}}",
        variables=["a", "b", "c"],
    )
    out = t.render(a="1", b="2")
    assert out == "1|2|[c]"


def test_render_builtin_date_replaced():
    import datetime
    t = NoteTemplate(id="x", name="x", description="", content="date={{date}} year={{year}}", variables=[])
    out = t.render()
    assert "{{date}}" not in out
    assert "{{year}}" not in out
    assert "date=" in out


# ── NoteTemplate.detect_variables ──────────────────────────────

def test_detect_variables_order_and_dedup():
    content = "{{name}} {{name}} {{title}}"
    assert NoteTemplate.detect_variables(content) == ["name", "title"]


def test_detect_variables_excludes_builtin():
    content = "{{name}} {{date}} {{year}} {{task}}"
    assert "date" not in NoteTemplate.detect_variables(content)
    assert "year" not in NoteTemplate.detect_variables(content)
    assert NoteTemplate.detect_variables(content) == ["name", "task"]


def test_detect_variables_empty_and_invalid_identifiers():
    assert NoteTemplate.detect_variables("no vars here") == []
    assert NoteTemplate.detect_variables("{{1bad}}") == []


# ── NoteTemplateManager builtin ────────────────────────────────

def test_manager_loads_builtins_without_data_dir():
    m = NoteTemplateManager(data_dir=None)
    assert len(BUILTIN_TEMPLATES) > 0
    first = BUILTIN_TEMPLATES[0]
    assert m.get(first.id) is first
    assert m.get("nonexistent") is None


def test_manager_list_all_and_categories():
    m = NoteTemplateManager(data_dir=None)
    all_t = m.list_all()
    assert len(all_t) >= len(BUILTIN_TEMPLATES)
    cats = m.list_categories()
    assert isinstance(cats, list) and cats == sorted(cats)


def test_manager_render_missing_returns_none():
    m = NoteTemplateManager(data_dir=None)
    assert m.render("does-not-exist", name="x") is None


# ── NoteTemplateManager file-backed CRUD ───────────────────────

def test_create_persists_to_file(tmp_path):
    m = NoteTemplateManager(data_dir=tmp_path)
    t = m.create(name="日报", content="今日 {{task}}", variables=["task"])
    assert t.is_builtin is False
    # 重新实例化应从文件加载自定义模板
    m2 = NoteTemplateManager(data_dir=tmp_path)
    assert m2.get(t.id) is not None
    assert m2.get(t.id).name == "日报"


def test_update_and_reject_builtin_modification(tmp_path):
    m = NoteTemplateManager(data_dir=tmp_path)
    t = m.create(name="旧", content="old")
    assert m.update(t.id, name="新", content="new").name == "新"
    assert m.get(t.id).content == "new"
    # 更新 builtin 返回 None
    builtin_id = BUILTIN_TEMPLATES[0].id
    assert m.update(builtin_id, name="hack") is None
    # 更新不存在返回 None
    assert m.update("nope", name="x") is None


def test_delete_and_reject_builtin(tmp_path):
    m = NoteTemplateManager(data_dir=tmp_path)
    t = m.create(name="临时", content="x")
    assert m.delete(t.id) is True
    assert m.get(t.id) is None
    assert m.delete("nope") is False
    assert m.delete(BUILTIN_TEMPLATES[0].id) is False


def test_duplicate(tmp_path):
    m = NoteTemplateManager(data_dir=tmp_path)
    t = m.create(name="源模板", content="内容 {{v}}")
    dup = m.duplicate(t.id)
    assert dup is not None and dup.id != t.id
    assert dup.content == t.content
    assert dup.name == "源模板(副本)"
    # 复制不存在返回 None
    assert m.duplicate("nope") is None


def test_search_matches_name_description_category(tmp_path):
    m = NoteTemplateManager(data_dir=tmp_path)
    m.create(name="周报模板", content="x", description="每周总结")
    assert any(t.name == "周报模板" for t in m.search("周报"))
    assert any("每周" in (t.description or "") for t in m.search("每周"))


def test_export_import_roundtrip(tmp_path):
    m = NoteTemplateManager(data_dir=tmp_path)
    m.create(name="导出项", content="hello {{who}}", variables=["who"])
    data = m.export_templates()
    payload = json.loads(data)
    assert payload["version"] == "1.0"
    assert any(t["name"] == "导出项" for t in payload["templates"])

    m2 = NoteTemplateManager(data_dir=tmp_path / "other")
    n = m2.import_templates(data)
    assert n >= 1
    assert any(t.name == "导出项" for t in m2.list_all())


def test_import_invalid_json_returns_zero(tmp_path):
    m = NoteTemplateManager(data_dir=tmp_path)
    assert m.import_templates("not json{") == 0


def test_import_overwrite_flag(tmp_path):
    m = NoteTemplateManager(data_dir=tmp_path)
    t = m.create(name="原", content="{{a}}", variables=["a"])
    payload = json.dumps({"templates": [{
        "id": t.id, "name": "覆盖", "content": "c", "category": "general", "variables": [],
    }]})
    # 不 overwrite → 生成新 id（改名带后缀）
    m.import_templates(payload, overwrite=False)
    assert m.get(t.id).name == "原"
    # overwrite=True → 覆盖原 id
    m.import_templates(payload, overwrite=True)
    assert m.get(t.id).name == "覆盖"