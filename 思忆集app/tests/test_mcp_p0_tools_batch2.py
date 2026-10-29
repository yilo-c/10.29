"""M-mcp 契约测试（第二批：知识 / 记忆 / 词汇 / 信息 / 收件箱分类 P0 工具）。

验证：
- 6 个第二批工具（siyi_vocab_review_status / siyi_vocab_quiz / siyi_info_brief /
  siyi_knowledge_query / siyi_memory_recall / siyi_inbox_classify）已注册。
- 经 ToolSpec.handler 调用能真正落库 / 跨源检索（vocab 打分、info 简报、
  knowledge/memory 融合、inbox 启发式分类）。
- 命名空间不抢 siyi_ 之外前缀、函数名不含 kernel。

隔离约束：本批全部走 SiyiStore 已接入的 repository 门面，不 import
assistant/backend；knowledge/memory 为 unified_service 的本地数据层等效实现，
inbox_classify 为无 AI 的启发式分析。
"""

from __future__ import annotations

import json

import pytest

from mcp_server.store import SiyiStore
from mcp_server.tools import build_tool_specs

P0_NAMES = {
    "siyi_vocab_review_status",
    "siyi_vocab_quiz",
    "siyi_info_brief",
    "siyi_knowledge_query",
    "siyi_memory_recall",
    "siyi_inbox_classify",
}


@pytest.fixture
def store(tmp_path):
    return SiyiStore(data_dir=str(tmp_path))


@pytest.fixture
def specs(store):
    # 七维-安C3(c) 后 policy=None（缺省）为 fail-close（RW/DANGER require_approval）；
    # 本文件测 store 落库/检索管道，显式 allow_all 保持直调语义。
    return build_tool_specs(store, policy={"mode": "allow_all"})


def _call(specs, name, **params):
    spec = next(s for s in specs if s.name == name)
    return json.loads(spec.handler(params))


def test_batch2_tool_names_registered(specs):
    names = {s.name for s in specs}
    assert P0_NAMES <= names


def test_no_kernel_in_tool_names(specs):
    assert not any("kernel" in s.name for s in specs)


def test_vocab_review_and_quiz(store, specs):
    # 造一个全新的待复习词（未复习 → 进入 due）
    wb_id = store.vocab.add_wordbook("test_book")
    wid = store.vocab.add_word(
        {
            "word": "ephemeral",
            "meaning": "短暂的",
            "wordbook_id": wb_id,
        }
    )
    status = _call(specs, "siyi_vocab_review_status", limit=20)
    assert status["due_count"] >= 1
    assert any(d["id"] == wid and d["is_new"] for d in status["due"])

    # 做一次完美回忆打分（quality=5），应进入复习周期
    quiz = _call(specs, "siyi_vocab_quiz", word_id=wid, quality=5)
    assert quiz.get("error") != "not_found"
    assert quiz["repetitions"] >= 1
    assert quiz["interval_days"] >= 1
    assert quiz["next_review"]

    # 打分后该词应不再是「新词待复习」（已排期到未来）
    status2 = _call(specs, "siyi_vocab_review_status", limit=20)
    assert not any(d["id"] == wid and d["is_new"] for d in status2["due"])


def test_vocab_quiz_invalid_quality(store, specs):
    res = _call(specs, "siyi_vocab_quiz", word_id=999, quality=9)
    assert res["error"] == "invalid_quality"


def test_info_brief(store, specs):
    store.news_save(
        url="https://example.com/a",
        title="Example",
        summary="demo",
        source="mcp",
        title_zh="示例",
        summary_zh="演示",
    )
    brief = _call(specs, "siyi_info_brief", limit=15)
    assert brief["total_cached"] >= 1
    assert brief["returned"] >= 1
    assert "by_source" in brief
    top = brief["items"][0]
    assert top["title"] == "示例"
    assert "summary_preview" in top


def test_knowledge_query_cross_source(store, specs):
    store.notes_create("Python lambda 教程", content="lambda 用法示例")
    res = _call(specs, "siyi_knowledge_query", query="python", limit=15)
    assert res["total"] >= 1
    assert any(r["_source"] == "note" for r in res["results"])
    assert res["intent"] in {"factual", "learning", "task", "auto"}


def test_memory_recall_context(store, specs):
    store.notes_create("记忆召回测试笔记", content="recall payload")
    res = _call(specs, "siyi_memory_recall", query="召回", limit=10)
    assert res["total"] >= 1
    assert "recall_context" in res
    assert "记忆召回测试笔记" in res["recall_context"]


def test_inbox_classify_heuristic(store, specs):
    created = store.inbox_add("提醒我明天买菜", title="待办")
    eid = created["id"]
    res = _call(specs, "siyi_inbox_classify", entry_id=eid)
    assert res.get("error") != "not_found"
    assert res["category"] == "task"  # 命中「提醒」关键词
    assert 0 < res["confidence"] <= 1


def test_inbox_classify_not_found(store, specs):
    res = _call(specs, "siyi_inbox_classify", entry_id=99999)
    assert res["error"] == "not_found"
