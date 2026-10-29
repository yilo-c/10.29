"""@ 引用系统单元测试 '"覆盖搜索、解鏋愩€佹牸式化、持久化。"""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.at_reference import AtReferenceEngine, AtReference, ReferenceChain


# 鈹€鈹€ AtReference 数据模型 鈹€鈹€

def test_at_reference_creation():
    ref = AtReference(
        ref_type="memory",
        ref_id="mem_001",
        display_name="测试记忆 · note",
        category_label="[记忆]",
    )
    assert ref.ref_type == "memory"
    assert ref.ref_id == "mem_001"
    assert ref.display_name == "测试记忆 · note"
    assert ref.resolved_content == ""


def test_at_reference_with_content():
    ref = AtReference(
        ref_type="file",
        ref_id="/path/to/file.md",
        display_name="file.md",
        category_label="[文件]",
        resolved_content="[文件鍐呭]",
    )
    assert ref.resolved_content == "[文件鍐呭]"


# 鈹€鈹€ ReferenceChain 鈹€鈹€

def test_reference_chain_to_dict():
    chain = ReferenceChain(
        message_id="msg_001",
        references=[
            {"ref_type": "memory", "ref_id": "m1", "display_name": "记忆1", "category_label": "[记忆]"},
        ],
        created_at="2026-05-02T10:00:00",
    )
    d = chain.to_dict()
    assert d["message_id"] == "msg_001"
    assert len(d["references"]) == 1
    assert d["references"][0]["ref_type"] == "memory"


# 鈹€鈹€ AtReferenceEngine 搜索 鈹€鈹€

def test_engine_search_empty_query():
    engine = AtReferenceEngine()
    results = engine.search("")
    assert isinstance(results, list)


def test_engine_search_categorized():
    engine = AtReferenceEngine()
    cat = engine.search_categorized("测试")
    assert "memory" in cat
    assert "file" in cat
    assert "device" in cat
    assert "time" in cat


def test_engine_search_time_ranges():
    engine = AtReferenceEngine()
    results = engine.search("今天", category="time")
    assert len(results) >= 1
    assert results[0].ref_type == "time"
    assert results[0].display_name == "今天"


def test_engine_search_time_ranges_partial():
    engine = AtReferenceEngine()
    results = engine.search("今", category="time")
    # 应该能匹配到"今天"、"最近7天"等
    assert len(results) >= 1
    names = [r.display_name for r in results]
    assert "今天" in names


def test_engine_format_for_prompt():
    engine = AtReferenceEngine()
    refs = [
        AtReference(
            ref_type="memory",
            ref_id="m1",
            display_name="测试记忆",
            category_label="[记忆]",
            resolved_content="这是测试记忆鍐呭",
        ),
        AtReference(
            ref_type="time",
            ref_id="今天",
            display_name="今天",
            category_label="[时间]",
            resolved_content="[时间范围: 今天]\n2026-05-02 ~ 2026-05-02",
        ),
    ]
    result = engine.format_for_prompt(refs)
    assert "[用户 @ 引用上下文]" in result
    assert "这是测试记忆鍐呭" in result
    assert "今天" in result


def test_engine_format_empty():
    engine = AtReferenceEngine()
    assert engine.format_for_prompt([]) == ""


def test_engine_format_reference_summary():
    engine = AtReferenceEngine()
    refs = [
        AtReference(ref_type="memory", ref_id="m1", display_name="记忆A", category_label="[记忆]"),
        AtReference(ref_type="file", ref_id="f1", display_name="文件B", category_label="[文件]"),
    ]
    summary = engine.format_reference_summary(refs)
    assert len(summary) == 2
    assert "[记忆]记忆A" in summary
    assert "[文件]文件B" in summary


# 鈹€鈹€ 引用链持久化 鈹€鈹€

def test_save_and_load_reference_chains(tmp_path):
    from data.local_storage import LocalDataStore

    store = LocalDataStore(base_dir=str(tmp_path))
    engine = AtReferenceEngine(data_store=store)

    refs = [
        AtReference(ref_type="memory", ref_id="m1", display_name="记忆1", category_label="[记忆]"),
    ]
    engine.save_reference_chain("msg_001", refs)

    chains = engine.load_reference_chains()
    assert len(chains) >= 1
    assert chains[-1]["message_id"] == "msg_001"


def test_load_reference_chains_empty(tmp_path):
    from data.local_storage import LocalDataStore

    store = LocalDataStore(base_dir=str(tmp_path))
    engine = AtReferenceEngine(data_store=store)

    chains = engine.load_reference_chains()
    assert chains == []


def test_get_references_for_message(tmp_path):
    from data.local_storage import LocalDataStore

    store = LocalDataStore(base_dir=str(tmp_path))
    engine = AtReferenceEngine(data_store=store)

    refs = [
        AtReference(ref_type="memory", ref_id="m1", display_name="记忆1", category_label="[记忆]"),
        AtReference(ref_type="file", ref_id="f1", display_name="文件1", category_label="[文件]"),
    ]
    engine.save_reference_chain("msg_002", refs)

    loaded = engine.get_references_for_message("msg_002")
    assert len(loaded) == 2
    assert loaded[0]["ref_type"] == "memory"


# 鈹€鈹€ 鍐呭解析 鈹€鈹€

def test_resolve_time_range():
    engine = AtReferenceEngine()
    ref = AtReference(ref_type="time", ref_id="今天", display_name="今天", category_label="[时间]")
    content = engine.resolve_content(ref)
    assert content.startswith("[时间范围: 今天]")


def test_resolve_memory_no_service():
    engine = AtReferenceEngine()
    ref = AtReference(ref_type="memory", ref_id="m1", display_name="测试", category_label="[记忆]")
    content = engine.resolve_content(ref)
    assert content == "[记忆: 测试]"


def test_resolve_device_no_store():
    engine = AtReferenceEngine()
    ref = AtReference(ref_type="device", ref_id="d1", display_name="我的PC", category_label="[设备]")
    content = engine.resolve_content(ref)
    assert content == "[设备: 我的PC]"


def test_resolve_file_not_found():
    engine = AtReferenceEngine()
    ref = AtReference(
        ref_type="file",
        ref_id="/nonexistent/path/file.txt",
        display_name="file.txt",
        category_label="[文件]",
    )
    content = engine.resolve_content(ref)
    assert "文件不存在" in content


# 鈹€鈹€ 工具方法 鈹€鈹€

def test_extract_at_references():
    text = "请参考@memory1和@file2的内瀹"
    refs = AtReferenceEngine.extract_at_references(text)
    assert "memory1" in refs
    assert "file2" in refs


def test_extract_at_references_empty():
    assert AtReferenceEngine.extract_at_references("没有引用") == []


def test_truncate_content():
    short = "hello"
    assert AtReferenceEngine._truncate_content(short, max_len=10) == "hello"

    long_text = "a" * 100
    truncated = AtReferenceEngine._truncate_content(long_text, max_len=20)
    assert len(truncated) < len(long_text)
    assert "内容已截断" in truncated
