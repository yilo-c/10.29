# -*- coding: utf-8 -*-
"""Regression tests for memory.supertag_search URL tokenization (P3-7).

Before the fix, ``https://example.com`` was tokenized as the field/value
pair ``https`` == ``//example.com``. The lexer now rejects ``scheme:`` when
followed by ``//`` so URLs survive as plain value tokens.
"""
from __future__ import annotations

from memory.supertag_search import _tokenize


def test_bare_url_is_single_value_token() -> None:
    tokens = _tokenize("https://example.com")
    assert tokens[0] == ("value", "https://example.com")


def test_url_inside_text_does_not_split() -> None:
    tokens = _tokenize("看 https://example.com 这个网站")
    values = [t for t in tokens if t[0] == "value"]
    assert ("value", "https://example.com") in values
    # 不应该出现 field 'https'
    assert all(t[0] != "field" or "https" not in t[1].split("==")[0] for t in tokens)


def test_normal_field_value_still_works() -> None:
    tokens = _tokenize("tag:python")
    assert ("field", "tag==python") in tokens


def test_explicit_url_field_still_works() -> None:
    """``url:https://x.com`` 仍应解析成 field url==https://x.com。"""
    tokens = _tokenize("url:https://x.com")
    assert ("field", "url==https://x.com") in tokens


def test_http_scheme_also_protected() -> None:
    tokens = _tokenize("http://foo.bar/baz")
    assert tokens[0] == ("value", "http://foo.bar/baz")


def test_field_operator_other_than_colon_not_affected() -> None:
    """!=, >=, <= 等操作符不会触发 URL 启发式。"""
    tokens = _tokenize("count>=10")
    assert tokens[0][0] == "field"
    assert "count" in tokens[0][1]
