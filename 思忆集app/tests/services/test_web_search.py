"""WebSearchService 单元测试：评鍒嗐€佽繃婊ゃ€佸幓閲嶃€佺紦瀛樸€佹牸式化。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from services.web_search import (
    WebSearchResult,
    WebSearchService,
    score_search_result,
    filter_by_quality,
)


# ══════════════════════════════════════════════════════════鈺"# WebSearchResult
# ══════════════════════════════════════════════════════════鈺"

class TestWebSearchResult:
    def test_create_result(self):
        r = WebSearchResult(title="测试结果", url="https://example.com", snippet="这是涓€涓憳瑕")
        assert r.title == "测试结果"
        assert r.url == "https://example.com"
        assert r.snippet == "这是涓€涓憳瑕"
        assert r.quality_score == 0.0

    def test_to_dict_without_quality(self):
        r = WebSearchResult(title="T", url="https://e.com", snippet="S")
        d = r.to_dict()
        assert d["title"] == "T"
        assert d["url"] == "https://e.com"
        assert d["snippet"] == "S"
        assert "quality_score" not in d

    def test_to_dict_with_quality(self):
        r = WebSearchResult(title="T", url="https://e.com", snippet="S", quality_score=0.85)
        d = r.to_dict()
        assert d["quality_score"] == 0.85


# ══════════════════════════════════════════════════════════鈺"# score_search_result
# ══════════════════════════════════════════════════════════鈺"

class TestScoreSearchResult:
    def test_baseline_score(self):
        """鍩虹分应一"0.4。"""
        r = WebSearchResult(title="鏅€氱粨鏋", url="https://example.com", snippet="涓瓑长度鎽樿" * 10)
        score = score_search_result(r)
        assert 0.3 <= score <= 0.6

    def test_authority_domain_boost(self):
        """权威域名锛"edu, .gov）应加分。"""
        r = WebSearchResult(title="学术论文", url="https://university.edu/research", snippet="A" * 200)
        score = score_search_result(r)
        assert score > 0.5

    def test_low_quality_domain_penalty(self):
        """低质量域名应扣分。"""
        r = WebSearchResult(title="百度知道", url="https://zhidao.baidu.com/question/123", snippet="A" * 200)
        score = score_search_result(r)
        # 鍩虹 0.4 + 长度加分 0.1 - 扣分 0.1 = ~0.4
        assert score < 0.5

    def test_empty_title_penalty(self):
        """空标题应扣分。"""
        r = WebSearchResult(title="", url="https://example.com", snippet="A" * 200)
        score = score_search_result(r)
        assert score == pytest.approx(0.35, abs=0.01)  # 0.4 + 0.1(snippet_len>150) - 0.15(no title)

    def test_short_title_penalty(self):
        """过短鏍囬锛"5 瀛楃）应扣分。"""
        r = WebSearchResult(title="AB", url="https://example.com", snippet="A" * 200)
        score = score_search_result(r)
        assert score < 0.5

    def test_query_relevance_boost(self):
        """鏍囬命中鏌ヨ关键词应加分。"""
        r = WebSearchResult(title="AI Agent 鏈€新进灞", url="https://example.com", snippet="A" * 200)
        score = score_search_result(r, query="AI Agent")
        assert score > 0.5

    def test_long_snippet_boost(self):
        """超长鎽樿锛"300 瀛楃）应棰濆加分。"""
        r = WebSearchResult(title="长摘要结鏋", url="https://example.com", snippet="A" * 350)
        score = score_search_result(r)
        assert score > 0.5  # 0.4 + 0.15(long snippet)

    def test_short_snippet_penalty(self):
        """过短鎽樿锛"30 瀛楃）应扣分。"""
        r = WebSearchResult(title="完整鏍囬", url="https://example.com", snippet="鐭")
        score = score_search_result(r)
        assert score < 0.4  # 0.4 - 0.1(short snippet)

    def test_score_clamped_to_range(self):
        """评分应在 0.0 - 1.0 范围鍐呫€"""
        r = WebSearchResult(title="", url="", snippet="")
        for _ in range(100):
            score = score_search_result(r)
            assert 0.0 <= score <= 1.0

    def test_url_like_title_penalty(self):
        """浠"http 鎴"www 寮€头的鏍囬应扣鍒嗐€"""
        r = WebSearchResult(title="https://example.com/page", url="https://example.com", snippet="A" * 150)
        score = score_search_result(r)
        assert score < 0.45


# ══════════════════════════════════════════════════════════鈺"# filter_by_quality
# ══════════════════════════════════════════════════════════鈺"

class TestFilterByQuality:
    def test_filter_low_scores(self):
        results = [
            WebSearchResult(title="高质閲", url="https://edu.edu", snippet="A" * 200),  # 高分
            WebSearchResult(title="低质閲", url="https://baidu.com", snippet="鐭"),      # 低分
        ]
        filtered = filter_by_quality(results, min_score=0.5)
        assert len(filtered) == 1
        assert filtered[0].title == "高质閲"

    def test_filter_top_k(self):
        results = [
            WebSearchResult(title="绗簩", url="https://b.com", snippet="A" * 150),
            WebSearchResult(title="绗竴", url="https://edu.edu", snippet="A" * 200),
            WebSearchResult(title="绗笁", url="https://c.com", snippet="A" * 150),
        ]
        filtered = filter_by_quality(results, top_k=2)
        assert len(filtered) == 2
        assert filtered[0].title == "绗竴"

    def test_filter_empty_input(self):
        assert filter_by_quality([]) == []

    def test_filter_all_below_threshold(self):
        results = [
            WebSearchResult(title="X", url="https://x.com", snippet="鐭"),
        ]
        filtered = filter_by_quality(results, min_score=0.9)
        assert filtered == []


# ══════════════════════════════════════════════════════════鈺"# _dedupe_results
# ══════════════════════════════════════════════════════════鈺"

class TestDeduplication:
    def test_removes_duplicates(self):
        """相同 url+title 的重复结果应琚幓閲嶃€"""
        results = [
            WebSearchResult("相同", "https://example.com/a", "鎽樿1"),
            WebSearchResult("相同", "https://example.com/a", "鎽樿2"),
            WebSearchResult("不同", "https://example.com/b", "鎽樿3"),
        ]
        deduped = WebSearchService._dedupe_results(results)
        assert len(deduped) == 2

    def test_dedupe_case_insensitive(self):
        results = [
            WebSearchResult("Title", "https://example.com/A", "Snippet"),
            WebSearchResult("title", "https://example.com/a", "snippet"),
        ]
        deduped = WebSearchService._dedupe_results(results)
        assert len(deduped) == 1


# ══════════════════════════════════════════════════════════鈺"# format_results_for_prompt
# ══════════════════════════════════════════════════════════鈺"

class TestFormatResults:
    def test_format_with_results(self):
        service = WebSearchService(settings_repository=None)
        results = [
            WebSearchResult("结果涓€", "https://a.com", "鎽樿A"),
            WebSearchResult("结果了", "https://b.com", "鎽樿B"),
        ]
        text = service.format_results_for_prompt(results, query="测试鏌ヨ")
        assert "测试鏌ヨ" in text
        assert "结果涓€" in text
        assert "结果了" in text
        assert "鎽樿A" in text
        assert "https://a.com" in text

    def test_format_without_results(self):
        service = WebSearchService(settings_repository=None)
        text = service.format_results_for_prompt([], query="空结果")
        assert "空结果" in text
        assert "未找到" in text

    def test_format_without_results_no_query(self):
        service = WebSearchService(settings_repository=None)
        text = service.format_results_for_prompt([])
        assert "未找到" in text


# ══════════════════════════════════════════════════════════鈺"# WebSearchService.search (边缘情况)
# ══════════════════════════════════════════════════════════鈺"

class TestSearchEdgeCases:
    def test_empty_query_returns_empty(self):
        """空查询应直接返回空列琛ㄣ€"""
        service = WebSearchService(settings_repository=None)
        assert service.search("") == []
        assert service.search("   ") == []

    def test_search_num_results_clamped(self, monkeypatch):
        """num_results 应在 1-10 范围鍐呫€"""
        service = WebSearchService(settings_repository=None)
        calls = []

        def _fake_ddg(q, num, timeout=10):
            calls.append(num)
            return [WebSearchResult(f"R{i}", f"https://e.com/{i}", "S") for i in range(num)]

        monkeypatch.setattr("services.web_search._search_duckduckgo", _fake_ddg)

        # 超过上限搴旇 clamp
        service.search("test", num_results=20, provider="duckduckgo")
        assert calls[-1] == 10

    def test_cache_hits_same_query(self, tmp_path, monkeypatch):
        """相同 query/provder/num_results 鐨勭浜屾搜索应命涓紦瀛樸€"""
        from data.local_storage import LocalDataStore
        from repositories import LocalSettingsRepository

        store = LocalDataStore(base_dir=str(tmp_path))
        settings_repo = LocalSettingsRepository(store)
        service = WebSearchService(settings_repository=settings_repo)
        calls = {"count": 0}

        def _fake_search(q, num, timeout=10):
            calls["count"] += 1
            return [WebSearchResult("缓存测试", "https://e.com", "S")]

        monkeypatch.setattr("services.web_search._search_duckduckgo", _fake_search)

        service.search("cache-test", num_results=3, provider="duckduckgo")
        service.search("cache-test", num_results=3, provider="duckduckgo")

        assert calls["count"] == 1


# ══════════════════════════════════════════════════════════鈺"# WebSearchService 鍥為€€测试
# ══════════════════════════════════════════════════════════鈺"

class TestSearchFallback:
    def test_tavily_falls_back_to_duckduckgo(self, tmp_path, monkeypatch):
        """TAVILY_API_KEY 鏈缃椂 Tavily 鑷姩鍥為€€鍒"DuckDuckGo。"""
        from data.local_storage import LocalDataStore
        from repositories import LocalSettingsRepository

        store = LocalDataStore(base_dir=str(tmp_path))
        settings_repo = LocalSettingsRepository(store)
        settings_repo.set_setting("web_search_provider", "tavily")
        service = WebSearchService(settings_repository=settings_repo, environ={"TAVILY_API_KEY": ""})
        calls = []

        def _fake_ddg(q, num, timeout=10):
            calls.append(q)
            return [WebSearchResult("鍥為€€结果", "https://e.com", "S")]

        monkeypatch.setattr("services.web_search._search_duckduckgo", _fake_ddg)

        results = service.search("fallback test", num_results=3)
        assert len(calls) == 1
        assert len(results) == 1
