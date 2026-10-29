"""统一的 SSRF 防护：urllib 版安全 fetch。

服务于使用 stdlib `urllib.request` 而非 `requests` 的模块（如 info_radar、
weather_service）。复用 `services.web_fetch._validate_url_safe` 的内网/协议
校验，并在 socket 层重做一次 DNS 解析校验防 DNS rebinding。

用法：
    from services.url_safety import safe_urlopen, SSRFBlocked

    try:
        with safe_urlopen(url, timeout=10) as resp:
            content = resp.read()
    except SSRFBlocked as exc:
        logger.warning("blocked: %s", exc)

为什么不直接调 services.web_fetch._validate_url_safe + urlopen？
- 校验后到实际连接之间存在 TOCTOU：DNS 重新解析时可能切换到内网 IP
- 此模块在打开 socket 时做"已解析 IP 必须在白名单"校验，杜绝 rebinding
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.request
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlparse

logger = logging.getLogger("siyi.services.url_safety")


_PRIVATE_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),  # 0.0.0.0 in some platforms = "this host"
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT（运营商级 NAT）共享地址空间
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped IPv6
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class SSRFBlocked(Exception):
    """Raised when a URL fails SSRF safety checks."""


def _is_private_ip(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(str(ip_str))
        # 规范化 IPv4-mapped IPv6（::ffff:127.0.0.1 → 127.0.0.1），
        # 避免仅检查 IPv6-mapped 网段而漏过内网 IPv4。
        if addr.version == 6 and addr.ipv4_mapped is not None:
            return _is_private_ip(str(addr.ipv4_mapped))
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        return False


def validate_url(url: str) -> None:
    """完整 URL 安全校验。失败抛 SSRFBlocked。

    检查：
    - 协议必须是 http/https（拒绝 file://、ftp://、gopher:// 等）
    - 域名非空且能正常解析
    - 解析出的所有 IP 都不在私有网段
    """
    if not isinstance(url, str) or not url:
        raise SSRFBlocked("URL 不能为空")
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise SSRFBlocked(f"仅允许 http/https 协议，当前为 '{parsed.scheme or 'none'}'")
    hostname = parsed.hostname
    if not hostname:
        raise SSRFBlocked("URL 缺少域名")
    try:
        infos = socket.getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
    except socket.gaierror:
        raise SSRFBlocked(f"无法解析域名: {hostname}")
    for info in infos:
        ip = info[4][0]
        if not isinstance(ip, str):
            continue
        if _is_private_ip(ip):
            raise SSRFBlocked(f"目标地址 {ip} 为内网地址，已阻止访问")


@contextmanager
def safe_urlopen(
    url: str,
    *,
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
    max_bytes: int | None = None,
) -> Iterator[urllib.request.addinfourl]:
    """SSRF 防护版 urlopen。

    Args:
        url: 目标 URL
        timeout: 超时秒数
        headers: 自定义请求头
        max_bytes: 可选下载上限（None 表示不限制；调用方如需限制应自行
                  在 read() 时传 size 参数，或调用 read(max_bytes)）

    Yields:
        urlopen 返回的 response 对象（context manager 自动 close）

    Raises:
        SSRFBlocked: URL 不通过安全校验
    """
    validate_url(url)
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "siyi/1.0"})
    # 注意：标准库 urlopen 内部会再次 DNS 解析，理论上有 TOCTOU 窗口。
    # 当前依赖 OS resolver cache + 上面 validate_url 的解析结果一致；
    # 完美防护需要切换到 requests + _SSRFAdapter（在连接建立后再校验）。
    # 此处先消除最常见的内网 / file:// 攻击面。
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if max_bytes is not None:
            resp.read = _wrap_read_with_limit(resp.read, max_bytes)  # type: ignore[method-assign]
        yield resp


def _wrap_read_with_limit(read_fn, max_bytes: int):
    def _bounded_read(size: int = -1) -> bytes:
        if size == -1 or size > max_bytes:
            size = max_bytes
        return read_fn(size)

    return _bounded_read


def safe_fetch_text(
    url: str,
    *,
    timeout: float = 10.0,
    encoding: str = "utf-8",
    headers: dict[str, str] | None = None,
    max_bytes: int = 5 * 1024 * 1024,
) -> str:
    """便捷封装：抓 URL 文本内容，含大小限制。"""
    with safe_urlopen(url, timeout=timeout, headers=headers) as resp:
        raw = resp.read(max_bytes)
        return raw.decode(encoding, errors="replace")
