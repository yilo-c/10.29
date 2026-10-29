"""Tests for 思忆集app.services.web_fetch."""
from unittest.mock import patch

import pytest

from services.web_fetch import (
    WebFetchResult,
    _html_to_markdown,
    _is_private_ip,
    _strip_tags,
    _validate_url_safe,
)


@pytest.mark.parametrize(
    "ip,expected",
    [
        ("127.0.0.1", True),
        ("10.0.0.1", True),
        ("192.168.1.1", True),
        ("::1", True),
        ("8.8.8.8", False),
        ("1.1.1.1", False),
        ("2001:4860:4860::8888", False),
    ],
)
def test_is_private_ip(ip, expected):
    assert _is_private_ip(ip) is expected


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file.txt",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/plain;base64,abc",
    ],
)
def test_validate_url_safe_rejects_non_http_schemes(url):
    is_safe, reason = _validate_url_safe(url)
    assert is_safe is False
    assert "http" in reason.lower() or "协议" in reason


def test_validate_url_safe_rejects_private_ips():
    is_safe, reason = _validate_url_safe("http://127.0.0.1/")
    assert is_safe is False
    assert "内网" in reason or "127.0.0.1" in reason


def test_validate_url_safe_rejects_missing_hostname():
    is_safe, reason = _validate_url_safe("http:///path/to/resource")
    assert is_safe is False
    assert "域名" in reason


@patch(
    "services.web_fetch.socket.getaddrinfo",
    return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
)
def test_validate_url_safe_accepts_public_url(mock_getaddrinfo):
    is_safe, reason = _validate_url_safe("https://example.com/index.html")
    assert is_safe is True
    assert reason == ""
    mock_getaddrinfo.assert_called_once()


def test_html_to_markdown_converts_common_elements():
    html_content = (
        "<h1>Heading 1</h1>"
        "<h2>Heading 2</h2>"
        "<p>This is a paragraph with a "
        "<a href=\"https://example.com\">link</a>.</p>"
        "<ul><li>Item one</li><li>Item two</li></ul>"
    )
    md = _html_to_markdown(html_content)
    assert "# Heading 1" in md
    assert "## Heading 2" in md
    assert "[link](https://example.com)" in md
    assert "- Item one" in md
    assert "- Item two" in md
    assert "This is a paragraph" in md


def test_strip_tags_removes_script_style_and_html_tags():
    html_content = (
        "<script>alert('xss')</script>"
        "<style>body { color: red; }</style>"
        "<p>Visible text &amp; more</p>"
    )
    text = _strip_tags(html_content)
    assert "<script>" not in text
    assert "alert" not in text
    assert "<style>" not in text
    assert "body" not in text
    assert "<p>" not in text and "</p>" not in text
    assert "Visible text & more" in text


def test_web_fetch_result_ok_and_to_dict():
    ok_result = WebFetchResult(
        url="https://example.com", title="Example", content="hello world", extractor="bs4"
    )
    assert ok_result.ok is True
    d = ok_result.to_dict()
    assert d["url"] == "https://example.com"
    assert d["title"] == "Example"
    assert d["content"] == "hello world"
    assert d["extractor"] == "bs4"
    assert d["truncated"] is False
    assert d["error"] == ""
    assert d["untrusted"] is True

    empty_content = WebFetchResult(url="https://example.com", content="")
    assert empty_content.ok is False

    error_result = WebFetchResult(
        url="https://example.com", content="some content", error="network error"
    )
    assert error_result.ok is False


def test_web_fetch_result_to_dict_truncates_content():
    long_content = "x" * 3000
    result = WebFetchResult(url="https://example.com", content=long_content)
    d = result.to_dict()
    assert len(d["content"]) == 2000
    assert d["content"] == long_content[:2000]
