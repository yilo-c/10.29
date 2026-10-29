"""P2-1: info.fetch_proxy 按 host 分发 Referer 的回归。

旧实现把 Referer 硬编码为 zhihu，抓取 weibo/tophub 会带错来源 → 反爬/日志失真。
注：tophub 已于 2026-06 移除（api.tophubdata.com 返回 404），相关用例已删除。
"""

from __future__ import annotations

from backend.handlers import _info_proxy


def test_referer_table_covers_all_allowed_hosts():
    # 每个允许主机都要有专属 Referer
    for host in _info_proxy._ALLOWED_HOSTS:
        assert host in _info_proxy._HOST_REFERER, (
            f"_HOST_REFERER 缺少 host: {host}"
        )


def test_referer_values_self_consistent():
    # Referer 协议必须是 https
    for host, referer in _info_proxy._HOST_REFERER.items():
        assert referer.startswith("https://"), (host, referer)


def test_zhihu_referer_unchanged():
    assert _info_proxy._HOST_REFERER["www.zhihu.com"] == "https://www.zhihu.com/"


def test_weibo_referer_is_weibo_not_zhihu():
    referer = _info_proxy._HOST_REFERER["weibo.com"]
    assert "weibo" in referer
    assert "zhihu" not in referer


def test_unknown_host_has_no_referer_entry():
    # 任何未在白名单中的主机也不会出现在 Referer 表（否则可能默认伪装）
    assert "example.com" not in _info_proxy._HOST_REFERER


def test_tophub_removed_from_sources():
    """tophub API (api.tophubdata.com) 已于 2026-06 返回 404，应从 SOURCES 移除。"""
    assert "tophub" not in _info_proxy.SOURCES
    assert "api.tophubdata.com" not in _info_proxy._ALLOWED_HOSTS
