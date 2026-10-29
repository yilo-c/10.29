"""P0-5 验证：services.url_safety 的 SSRF 防护与 info_radar / weather_service 集成。

历史 bug：info_radar._fetch_and_parse_rss 和 weather_service._fetch_json 都用
原始 urllib.urlopen，完全绕过 web_fetch.py 的 _SSRFAdapter 防护。攻击者可
配置 RSS 源 URL 为 http://169.254.169.254/、http://127.0.0.1:18765/ 等读
内部接口。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


# ── url_safety 单元 ────────────────────────────────────────────────────────


def test_validate_url_blocks_file_protocol():
    from services.url_safety import SSRFBlocked, validate_url

    with pytest.raises(SSRFBlocked, match="仅允许 http/https"):
        validate_url("file:///etc/passwd")


def test_validate_url_blocks_ftp():
    from services.url_safety import SSRFBlocked, validate_url

    with pytest.raises(SSRFBlocked, match="仅允许 http/https"):
        validate_url("ftp://example.com/file")


def test_validate_url_blocks_gopher():
    from services.url_safety import SSRFBlocked, validate_url

    with pytest.raises(SSRFBlocked, match="仅允许 http/https"):
        validate_url("gopher://example.com/")


def test_validate_url_blocks_localhost_ipv4():
    from services.url_safety import SSRFBlocked, validate_url

    # 跳过 DNS 解析，直接给 IP
    with pytest.raises(SSRFBlocked, match="内网"):
        validate_url("http://127.0.0.1:18765/secret")


def test_validate_url_blocks_localhost_ipv6():
    from services.url_safety import SSRFBlocked, validate_url

    with pytest.raises(SSRFBlocked, match="内网"):
        validate_url("http://[::1]:8080/")


def test_validate_url_blocks_aws_metadata():
    """AWS metadata service: 169.254.169.254 — 经典 SSRF 目标。"""
    from services.url_safety import SSRFBlocked, validate_url

    with pytest.raises(SSRFBlocked, match="内网"):
        validate_url("http://169.254.169.254/latest/meta-data/")


def test_validate_url_blocks_private_10_range():
    from services.url_safety import SSRFBlocked, validate_url

    with pytest.raises(SSRFBlocked, match="内网"):
        validate_url("http://10.0.0.1/")


def test_validate_url_blocks_private_192_168_range():
    from services.url_safety import SSRFBlocked, validate_url

    with pytest.raises(SSRFBlocked, match="内网"):
        validate_url("http://192.168.1.1/")


def test_validate_url_blocks_empty_url():
    from services.url_safety import SSRFBlocked, validate_url

    with pytest.raises(SSRFBlocked):
        validate_url("")


def test_validate_url_blocks_url_without_host():
    from services.url_safety import SSRFBlocked, validate_url

    with pytest.raises(SSRFBlocked, match="缺少域名"):
        validate_url("http:///path-only")


def test_validate_url_allows_public_domain_when_resolves_to_public_ip():
    """正常公网域名应通过校验。

    用 patch 确保不依赖真实 DNS（CI 环境可能无网）。
    """
    from services import url_safety

    fake_addr_info = [(2, 1, 6, "", ("8.8.8.8", 443))]
    with patch.object(url_safety.socket, "getaddrinfo", return_value=fake_addr_info):
        url_safety.validate_url("https://example.com/path")  # 不抛异常


def test_validate_url_blocks_dns_to_private_ip():
    """正常域名但解析到内网 IP（DNS rebinding 类）也要被拦截。"""
    from services import url_safety

    fake_addr_info = [(2, 1, 6, "", ("127.0.0.1", 80))]
    with patch.object(url_safety.socket, "getaddrinfo", return_value=fake_addr_info):
        with pytest.raises(url_safety.SSRFBlocked, match="内网"):
            url_safety.validate_url("http://attacker-controlled.example.com/")


# ── V2.1 批次1#8-2: CGNAT / IPv4-mapped 专项（url_safety 已实现，此处补回归锁定）──


def test_validate_url_blocks_cgnat_range():
    """100.64.0.0/10（运营商级 NAT / Tailscale 共享段）属私网，必须拦截。

    批次1#8-2 点名缺口：CGNAT 段不在传统 RFC1918 内网清单，历史上曾被漏判。
    """
    from services.url_safety import SSRFBlocked, validate_url

    with pytest.raises(SSRFBlocked, match="内网"):
        validate_url("http://100.64.0.1/")
    with pytest.raises(SSRFBlocked, match="内网"):
        validate_url("http://100.127.255.254/")


def test_validate_url_blocks_ipv4_mapped_loopback():
    """IPv4-mapped IPv6（::ffff:127.0.0.1）必须规范化后按内网拦截，防绕过。"""
    from services.url_safety import SSRFBlocked, validate_url

    with pytest.raises(SSRFBlocked, match="内网"):
        validate_url("http://[::ffff:127.0.0.1]:8080/")


def test_validate_url_allows_ipv4_mapped_public():
    """IPv4-mapped 公网地址（::ffff:8.8.8.8）规范化后应正常放行，不得误杀。"""
    from services import url_safety

    fake_addr_info = [(10, 1, 6, "", ("::ffff:8.8.8.8", 80))]
    with patch.object(url_safety.socket, "getaddrinfo", return_value=fake_addr_info):
        url_safety.validate_url("http://[::ffff:8.8.8.8]/")  # 不抛异常


# ── info_radar 集成测试 ────────────────────────────────────────────────────


def test_info_radar_blocks_internal_rss_url():
    """info_radar._fetch_and_parse_rss 必须拦截内网 RSS feed URL。"""
    from services.info_radar import InfoRadar

    radar = InfoRadar()
    feed = {"name": "evil-feed", "url": "http://127.0.0.1:18765/secret"}
    items = radar._fetch_and_parse_rss(feed)
    assert items == [], "内网 URL 不应能拉到任何条目"


def test_info_radar_blocks_aws_metadata_rss():
    from services.info_radar import InfoRadar

    radar = InfoRadar()
    feed = {"name": "metadata", "url": "http://169.254.169.254/latest/meta-data/iam/"}
    assert radar._fetch_and_parse_rss(feed) == []


def test_info_radar_blocks_file_protocol_rss():
    from services.info_radar import InfoRadar

    radar = InfoRadar()
    feed = {"name": "file-leak", "url": "file:///etc/passwd"}
    assert radar._fetch_and_parse_rss(feed) == []


# ── weather_service 集成测试 ───────────────────────────────────────────────


def test_weather_fetch_json_blocks_internal_url():
    """weather_service._fetch_json 必须拦截内网 URL。"""
    from services.weather_service import WeatherService

    svc = WeatherService(
        settings_repository=MagicMock(),
        default_city="beijing",
        default_city_label="北京",
    )
    # _fetch_json 在被 SSRFBlocked 拦截后会抛 OSError
    with pytest.raises(OSError, match="blocked"):
        svc._fetch_json("http://127.0.0.1:8000/weather", timeout=1)


def test_weather_fetch_json_blocks_file_protocol():
    from services.weather_service import WeatherService

    svc = WeatherService(
        settings_repository=MagicMock(),
        default_city="beijing",
        default_city_label="北京",
    )
    with pytest.raises(OSError, match="blocked"):
        svc._fetch_json("file:///etc/passwd")
