# -*- coding: utf-8 -*-
"""P1-B 联网搜索死链测试（2026-09-18 实测驱动）。

锁三件事：
  1. import 路径正确 —— 真模块是 services.web_search，不存在顶层 web_search_service；
  2. web_search() 在 mock 掉底层 search 后能跑通完整信封（含 format_results_for_prompt
     对治理后 dict 的 SimpleNamespace 适配 —— 原实现直接传 dict 会 AttributeError）；
  3. 全仓结构断言：能力 handler 里不得再出现 `from web_search_service import`
     这种顶层不存在的 import（防"断链被 except 吞 → 静默死链"复发）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]


def test_real_module_is_services_web_search():
    from services.web_search import WebSearchService  # noqa: F401

    with pytest.raises(ModuleNotFoundError):
        import web_search_service  # noqa: F401  # 旧 import 的模块本就不存在


def test_web_search_envelope_with_mocked_backend(monkeypatch):
    from assistant.capability import _knowledge
    from services.web_search import WebSearchResult

    fake_results = [
        WebSearchResult(title="Alpha", url="https://a.example.com", snippet="first hit"),
        WebSearchResult(title="Beta", url="https://b.example.com", snippet="second hit"),
    ]

    def fake_search(self, query, num_results=5, provider=None):  # noqa: ARG001
        return list(fake_results)

    monkeypatch.setattr("services.web_search.WebSearchService.search", fake_search, raising=True)

    class _Router:
        settings_repository = None
        source_registry = None  # 走无 registry 的去重分支

    domain = _knowledge.KnowledgeCapabilityDomain(_Router())
    out = domain.web_search({"query": "测试"})

    assert out["success"] is True
    assert out["count"] == 2
    assert "No module named" not in str(out)
    # formatted 必须含标题/链接（证明 SimpleNamespace 适配生效，未 AttributeError）
    assert "Alpha" in out["formatted"]
    assert "https://a.example.com" in out["formatted"]


def test_no_dead_web_search_service_import_in_repo():
    """全仓扫：`from web_search_service import` 顶层模块不存在，出现即测试红。"""
    dead_import = re.compile(r"^\s*(?:from|import)\s+web_search_service\b")
    offenders: list[str] = []
    for py in APP_ROOT.rglob("*.py"):
        if any(seg in py.parts for seg in (".venv", "__pycache__", "node_modules", "verify_tmp", "backups", "tests")):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if dead_import.match(line):
                offenders.append(f"{py.relative_to(APP_ROOT)}:{ln}")
    assert not offenders, f"发现断链 import（顶层无 web_search_service 模块）：{offenders}"
