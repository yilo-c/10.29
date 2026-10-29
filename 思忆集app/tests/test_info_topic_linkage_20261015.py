"""topics→采集联动 + 简报 prompt 收紧（2026-10-15 用户拍板）。

用户裁决：
1. 简报格式 = 编辑型收紧（10-20→10-15 条、点评 2-3 句→1-2 句、去 --- 分隔）；
2. topics 要联动到采集层（此前只影响 AI 挑选，抓取固定）；
3. 不减功能 → 联动只重排不删条目。

覆盖：
- apply_topic_linkage：不删条目 / 相关度排序 / ASCII 词边界 / 中文子串 /
  板块关键词桥接 / 空 topics 退回热度排序 / 翻译字段参与命中
- build_brief_prompt：收紧断言 + compact 变体
"""

from __future__ import annotations

from typing import Any

from services.info_collector_service import apply_topic_linkage, build_brief_prompt


def _item(title: str = "", summary: str = "", score: int = 0, **extra: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"title": title, "summary": summary, "score": score}
    d.update(extra)
    return d


class TestApplyTopicLinkage:
    def test_no_items_dropped_and_score_attached(self):
        items = [_item("a"), _item("b"), _item("c")]
        out = apply_topic_linkage(items, ["AI"])
        assert len(out) == 3
        assert all("_topic_score" in i for i in out)

    def test_title_beats_summary_beats_none(self):
        # 注意：词边界匹配下 "OpenAI" 不命中独立词 "ai"（防 "email" 误报同款），
        # 标题需含独立词 "AI"；OpenAI 类条目靠板块关键词桥接命中
        a = _item("New AI agent model released", score=1)  # 标题命中 → 2
        b = _item("weekly digest", summary="updates about AI tools", score=99)  # 摘要命中 → 1
        c = _item("cooking recipes", score=500)  # 无命中 → 0
        out = apply_topic_linkage([c, b, a], ["AI"])
        assert [i["title"] for i in out] == [a["title"], b["title"], c["title"]]

    def test_ascii_word_boundary(self):
        # "email" 含 "ai" 子串但不是独立词 → 不得命中
        items = [_item("email startup roundup")]
        out = apply_topic_linkage(items, ["ai"])
        assert out[0]["_topic_score"] == 0

    def test_cjk_substring_match(self):
        items = [_item("国内首个千亿大模型开源")]
        out = apply_topic_linkage(items, ["大模型"])
        assert out[0]["_topic_score"] >= 2

    def test_section_keyword_bridge(self):
        # topic "AI" 经 ai 板块关键词表桥接到 category="ai" 条目（标题不含 AI）
        ai_item = _item("weekly digest", category="ai")
        other = _item("weekly digest", category="policy")
        out = apply_topic_linkage([other, ai_item], ["AI"])
        assert out[0] is ai_item
        assert ai_item["_topic_score"] >= 1
        assert other["_topic_score"] == 0

    def test_empty_topics_falls_back_to_score_sort(self):
        items = [_item("a", score=1), _item("b", score=9), _item("c", score=5)]
        out = apply_topic_linkage(list(items), [])
        assert [i["score"] for i in out] == [9, 5, 1]

    def test_translated_fields_participate(self):
        # 翻译字段（title_zh）命中中文 topic（采集后重跑联动场景）
        items = [_item("some english news", title_zh="大模型发布")]
        out = apply_topic_linkage(items, ["大模型"])
        assert out[0]["_topic_score"] >= 2


class TestBriefPromptTightened:
    _KWARGS = dict(
        now_str="2026年10月15日",
        topics=["AI", "前端开发"],
        sections_desc="🤖 AI",
        section_instructions="### 🤖 AI\n（相关条目）\n\n",
        raw_text="RAW_PAYLOAD",
        total_count=37,
    )

    def test_non_compact_tightened(self):
        p = build_brief_prompt(compact=False, **self._KWARGS)
        assert "10-15条" in p
        assert "1-2句点评" in p
        assert "条目之间用 --- 分隔" not in p
        assert "RAW_PAYLOAD" in p
        assert "按相关度预排序" in p
        # 结构保留（编辑型）：速览/板块/编辑推荐
        assert "今日速览" in p
        assert "编辑推荐" in p
        # 渲染友好（2026-10-15 二轮）：条目间空行 + 锚文本「原文」
        assert "条目与条目之间必须空一行" in p
        assert "[原文](URL)" in p
        assert "[原文链接](URL)" not in p

    def test_compact_variant(self):
        p = build_brief_prompt(compact=True, **self._KWARGS)
        assert "条目格式（紧凑）" in p
        assert "10-15条" in p
        assert "RAW_PAYLOAD" in p

    def test_no_triple_dash_separator_requirement(self):
        # 收紧点：两个变体都不得再要求 --- 分隔
        for compact in (False, True):
            p = build_brief_prompt(compact=compact, **self._KWARGS)
            assert "--- 分隔" not in p
