# -*- coding: utf-8 -*-
"""L2 集成测试：词汇本 / 生词本（Vocab / Wordbook）。

覆盖方法：wordbook.list / word.list / word.add(新增) / word.delete(清理) /
translate.extract_words(文本抽取)。

设计：wordbook.list 与 word.list 直接返回列表；word.add 新增一条带唯一标记的生词，
随后 word.delete 清理，保证测试对真实数据零残留。translate.extract_words 调用抽取管线，
断言返回结构化结果（可能为空命中，但不应崩溃）。
"""

import uuid

import pytest

pytestmark = pytest.mark.integration

_MARK = f"obs_test_{uuid.uuid4().hex[:8]}"


async def _wrapped(rpc, method, params=None, skip_if_absent=False):
    resp = await rpc(method, params or {})
    assert isinstance(resp, dict) and ("result" in resp or "error" in resp), f"{method} 未返回结构化 JSON-RPC 响应: {resp}"
    err = resp.get("error")
    if skip_if_absent and err and err.get("code") == -32601:
        pytest.skip(f"{method} 未在 headless 后端注册（桌面端路径）")
    return resp.get("result"), err


async def test_wordbook_list_reachable(rpc):
    result, err = await _wrapped(rpc, "wordbook.list", {})
    assert err is None, f"wordbook.list 报错: {err}"
    assert isinstance(result, list), "wordbook.list 应返回列表"


async def test_word_list_reachable(rpc):
    result, err = await _wrapped(rpc, "word.list", {"limit": 10})
    assert err is None, f"word.list 报错: {err}"
    assert isinstance(result, list), "word.list 应返回列表"


async def test_word_add_and_delete_roundtrip(rpc_approved):
    # 批次2：word.add 创建（R2）；word.delete 为 R4 破坏性方法 → 走 rpc_approved（审批后执行）
    added_resp = await rpc_approved("word.add", {"word": _MARK, "meaning": "observation test entry"})
    added = added_resp.get("result")
    err = added_resp.get("error")
    assert err is None, f"word.add 报错: {err}"
    assert isinstance(added, dict)
    assert added.get("success") is True, f"word.add 失败: {added}"
    wid = added.get("id")
    assert wid is not None
    deleted_resp = await rpc_approved("word.delete", {"word_id": wid})
    deleted = deleted_resp.get("result")
    err2 = deleted_resp.get("error")
    assert err2 is None, f"word.delete 报错: {err2}"
    assert isinstance(deleted, dict)
    assert deleted.get("success") is True, f"word.delete 失败: {deleted}"


async def test_word_extract_returns_structured(rpc):
    result, err = await _wrapped(
        rpc,
        "translate.extract_words",
        {"text": "The quick brown fox jumps over the lazy dog during observation."},
    )
    assert err is None, f"translate.extract_words 报错: {err}"
    assert isinstance(result, dict)
    assert "words" in result, f"translate.extract_words 应含 words 字段: {result}"
