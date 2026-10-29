"""SSRF 防护工具 — 防止请求内网地址。"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("siyi.util.ssrf")

# 不允许请求的内网地址范围
_PRIVATE_NETWORKS: list[tuple[str, str, int]] = [
    ("127.0.0.0", "127.255.255.255", 8),      # loopback
    ("10.0.0.0", "10.255.255.255", 8),         # private A
    ("172.16.0.0", "172.31.255.255", 16),      # private B
    ("192.168.0.0", "192.168.255.255", 16),    # private C
    ("169.254.0.0", "169.254.255.255", 16),    # link-local
    ("0.0.0.0", "0.255.255.255", 8),           # current network
    ("100.64.0.0", "100.127.255.255", 10),     # carrier-grade NAT
    ("::1", "::1", 128),                        # IPv6 loopback
    ("fc00::", "fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff", 7),  # IPv6 unique local
]


def _is_private_ip(host: str) -> bool:
    """检查 host 是否为内网 IP 或域名指向内网。"""
    # 先检查是否是 IP 地址
    try:
        addr = ipaddress.ip_address(host)
        # 不能是私有/回环/多播
        if addr.is_private or addr.is_loopback or addr.is_multicast or addr.is_link_local or addr.is_unspecified:
            return True
        return False
    except ValueError:
        pass  # 不是 IP 地址，可能是域名

    # 是域名：尝试 DNS 解析（同时查询 IPv4 和 IPv6）
    try:
        addrinfo = socket.getaddrinfo(host, 80, family=socket.AF_UNSPEC)
        for family, _, _, _, sockaddr in addrinfo:
            ip = sockaddr[0]
            try:
                addr = ipaddress.ip_address(ip)
                if addr.is_private or addr.is_loopback or addr.is_link_local:
                    return True
            except ValueError:
                continue
    except (socket.gaierror, OSError):
        logger.warning("DNS resolution failed for %r, blocking", host)
        return True  # fail-closed: DNS 解析失败视为不安全

    return False


def validate_url(
    url: str,
    *,
    allow_http: bool = False,
) -> str | None:
    """验证 URL 是否安全（非 SSRF）。返回 None 表示安全，返回字符串表示拒绝原因。"""
    if not url:
        return "empty_url"

    try:
        parsed = urlparse(url)
    except Exception:
        return "invalid_url"

    if parsed.scheme not in ("http", "https"):
        return f"unsupported_scheme:{parsed.scheme}"

    if not parsed.hostname:
        return "no_hostname"

    # 不允许 HTTP 访问（除特殊信任场景）
    if parsed.scheme == "http" and not allow_http:
        return "http_not_allowed"

    # 检查是否指向内网
    if _is_private_ip(parsed.hostname):
        return "private_ip_not_allowed"

    return None


def validate_url_or_raise(url: str, **kwargs: Any) -> None:
    """验证 URL，不通过则抛出 ValueError。"""
    reason = validate_url(url, **kwargs)
    if reason:
        raise ValueError(f"SSRF check failed: {reason}")
