from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore
from repositories import LocalSettingsRepository
from services.web_search import WebSearchResult, WebSearchService, _search_duckduckgo


def test_web_search_service_uses_cache(tmp_path, monkeypatch):
    store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(store)
    service = WebSearchService(settings_repository=settings_repo)
    calls = {"count": 0}

    def _fake_search(query, num_results, timeout=10):
        calls["count"] += 1
        return [WebSearchResult("结果", "https://example.com/a", "鎽樿")]

    monkeypatch.setattr("services.web_search._search_duckduckgo", _fake_search)

    first = service.search("AI Agent", num_results=3, provider="duckduckgo")
    second = service.search("AI Agent", num_results=3, provider="duckduckgo")

    assert calls["count"] == 1
    assert first[0].url == second[0].url


def test_web_search_service_dedupes_results(tmp_path, monkeypatch):
    store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(store)
    service = WebSearchService(settings_repository=settings_repo)

    def _fake_search(query, num_results, timeout=10):
        return [
            WebSearchResult("閲嶅结果", "https://example.com/a", "鎽樿1"),
            WebSearchResult("閲嶅结果", "https://example.com/a", "鎽樿2"),
            WebSearchResult("不同结果", "https://example.com/b", "鎽樿3"),
        ]

    monkeypatch.setattr("services.web_search._search_duckduckgo", _fake_search)

    results = service.search("AI Agent", num_results=5, provider="duckduckgo")

    assert len(results) == 2
    assert results[0].url == "https://example.com/a"


def test_search_duckduckgo_falls_back_to_lite_results(monkeypatch):
    calls = []

    class _Response:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    def _fake_get(url, params=None, headers=None, timeout=10):
        calls.append(url)
        if "html.duckduckgo.com" in url:
            return _Response("<html><body><div>no structured results</div></body></html>")
        return _Response(
            """
            <html><body>
            <table>
              <tr><td><a rel=\"nofollow\" class=\"result-link\" href=\"https://example.com/lite\">Lite Result</a></td></tr>
              <tr><td class=\"result-snippet\">Lite snippet for fallback parsing.</td></tr>
            </table>
            </body></html>
            """
        )

    monkeypatch.setattr("services.web_search.requests.get", _fake_get)

    results = _search_duckduckgo("AI Agent", num_results=3, timeout=10)

    assert [result.title for result in results] == ["Lite Result"]
    assert results[0].url == "https://example.com/lite"
    assert results[0].snippet == "Lite snippet for fallback parsing."
    assert any("html.duckduckgo.com" in url for url in calls)
    assert any("lite.duckduckgo.com" in url for url in calls)
