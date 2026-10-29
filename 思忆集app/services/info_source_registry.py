"""资讯源注册表 — 可配置的资讯源管理。

将原本硬编码在 ``InfoCollectorService.collect()`` 中的 20 个源抽取为可配置的
``SourceConfig``，支持启用/禁用、添加自定义 RSS 源、持久化到 settings。

默认源与 ``info_constants._SOURCE_DISPLAY`` / ``_SOURCE_URLS`` 保持一致，
确保前端来源显示与历史行为不漂移。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger("siyi.services.info_source_registry")


# 源名称 → InfoCollectorService 上的 fetcher 方法名
FETCHER_METHOD_MAP: dict[str, str] = {
    "hackernews": "_hn",
    "devto": "_devto",
    "lobsters": "_lobsters",
    "github": "_github_trending",
    "producthunt": "_producthunt",
    "techcrunch": "_techcrunch",
    "arstechnica": "_arstechnica",
    "theverge": "_theverge",
    "smashing": "_smashing",
    "mittr": "_mittr",
    "solidot": "_solidot",
    "oschina": "_oschina",
    "juejin": "_juejin",
    "36kr": "_36kr",
    "infoq": "_infoq",
    "sspai": "_sspai",
    "jiqizhixin": "_jiqizhixin",
    "ruanyifeng": "_ruanyifeng",
    "ifanr": "_ifanr",
    "cnbeta": "_cnbeta",
    # 2026-08-18 新增源（实测可达的稳定 RSS/Atom）
    "qbitai": "_qbitai",
    "ithome": "_ithome",
    "gcores": "_gcores",
    "chuapp": "_chuapp",
    "arxiv_ai": "_arxiv_ai",
    "simonwillison": "_simonwillison",
    # 2026-09 新增源（16 号文档信源军火库 · 政府站点直连 HTML 列表页，已实测可达）
    "govpolicy": "_govpolicy",
    "statsgov": "_statsgov",
    "moe": "_moe",
    "ynzs": "_ynzs",
}

# settings 中存放用户配置的键
SETTINGS_KEY = "info_sources_config"

_VALID_PARSER_TYPES = ("json", "xml", "rss", "html")
_VALID_LANGUAGES = ("en", "zh")


@dataclass
class SourceConfig:
    """单个资讯源的配置。

    Attributes:
        name: 源唯一标识（如 ``hackernews``），与 fetcher 映射及 item.source 对齐。
        display_name: 前端展示名称（如 ``HackerNews``）。
        url: 源主页或 feed URL（自定义源的抓取入口）。
        parser_type: 解析器类型 — json/xml/rss/html。
        language: 主要语言 — en/zh（影响是否调用翻译）。
        enabled: 是否启用。禁用的源在 collect() 时被跳过。
        category_hint: 分类提示（如 ``ai``/``dev``/``tool``），供预分类参考。
        is_custom: 是否为用户添加的自定义源。仅自定义源可被删除。
    """

    name: str
    display_name: str
    url: str
    parser_type: str
    language: str
    enabled: bool = True
    category_hint: str = ""
    is_custom: bool = False


def _default_sources() -> list[SourceConfig]:
    """返回全部内置默认源配置（与 info_constants.all_source_names() 一致）。"""
    defaults = [
        ("hackernews", "HackerNews", "https://news.ycombinator.com", "json", "en", "dev"),
        ("devto", "Dev.to", "https://dev.to", "json", "en", "dev"),
        ("lobsters", "Lobsters", "https://lobste.rs", "rss", "en", "dev"),
        ("github", "GitHub Trending", "https://github.com/trending", "html", "en", "dev"),
        ("producthunt", "Product Hunt", "https://www.producthunt.com", "rss", "en", "tool"),
        ("techcrunch", "TechCrunch", "https://techcrunch.com", "rss", "en", "internet"),
        ("arstechnica", "Ars Technica", "https://arstechnica.com", "rss", "en", "science"),
        ("theverge", "The Verge", "https://www.theverge.com", "rss", "en", "internet"),
        ("smashing", "Smashing Magazine", "https://www.smashingmagazine.com", "rss", "en", "dev"),
        ("mittr", "MIT科技评论", "https://www.technologyreview.com", "rss", "en", "science"),
        ("solidot", "Solidot", "https://www.solidot.org", "rss", "zh", "science"),
        ("oschina", "开源中国", "https://www.oschina.net", "rss", "zh", "dev"),
        ("juejin", "掘金", "https://juejin.cn", "rss", "zh", "dev"),
        ("36kr", "36氪", "https://36kr.com", "rss", "zh", "internet"),
        ("infoq", "InfoQ中文", "https://www.infoq.cn", "rss", "zh", "dev"),
        ("sspai", "少数派", "https://sspai.com", "rss", "zh", "tool"),
        ("jiqizhixin", "机器之心", "https://www.jiqizhixin.com", "rss", "zh", "ai"),
        ("ruanyifeng", "阮一峰周刊", "https://www.ruanyifeng.com/blog", "rss", "zh", "dev"),
        ("ifanr", "爱范儿", "https://www.ifanr.com", "rss", "zh", "internet"),
        ("cnbeta", "cnBeta", "https://www.cnbeta.com.tw", "rss", "zh", "internet"),
        # 2026-08-18 新增内置源（纯 RSS/Atom，走通用 _fetch_rss，已实测可达）
        ("qbitai", "量子位", "https://www.qbitai.com/feed", "rss", "zh", "ai"),
        ("ithome", "IT之家", "https://www.ithome.com/rss/", "rss", "zh", "internet"),
        ("gcores", "机核", "https://www.gcores.com/rss", "rss", "zh", "entertainment"),
        ("chuapp", "触乐", "https://www.chuapp.com/feed", "rss", "zh", "entertainment"),
        ("arxiv_ai", "arXiv AI", "https://export.arxiv.org/rss/cs.AI", "rss", "en", "ai"),
        ("simonwillison", "Simon Willison", "https://simonwillison.net/atom/everything/", "rss", "en", "ai"),
        # 2026-09 新增源（16 号文档信源军火库 · 政府站点直连 HTML 列表页，已实测可达）
        ("govpolicy", "国务院文件", "https://www.gov.cn/zhengce/zhengceku/gwywj/home.htm", "html", "zh", "policy"),
        ("statsgov", "国家统计局", "https://www.stats.gov.cn/sj/zxfb/", "html", "zh", "policy"),
        ("moe", "教育部", "https://www.moe.gov.cn/jyb_xwfb/", "html", "zh", "policy"),
        ("ynzs", "云南招考院", "https://www.ynzs.cn", "html", "zh", "local"),
    ]
    return [
        SourceConfig(
            name=n,
            display_name=d,
            url=u,
            parser_type=p,
            language=lang,
            category_hint=c,
            enabled=True,
            is_custom=False,
        )
        for n, d, u, p, lang, c in defaults
    ]


class InfoSourceRegistry:
    """资讯源注册表，管理默认源与用户自定义源的启用/禁用/持久化。

    设计要点：
      - 默认源始终在内存中（``_default_sources()``），用户 override 仅记录差异
        （哪些默认源被禁用）+ 自定义源完整定义。
      - 多个 collector 实例应共享同一 registry（通过 ``context.info_source_registry``），
        这样 RPC 层的 toggle/add/remove 会即时反映到调度器下一次 collect()。
      - 无 ``settings_repository`` 时退化为纯内存模式（默认源全启用，不持久化），
        便于测试与无配置环境运行。
    """

    def __init__(self, settings_repository: Any = None) -> None:
        self._settings_repository = settings_repository
        self._sources: dict[str, SourceConfig] = {}
        for src in _default_sources():
            self._sources[src.name] = src
        self._load_user_config()

    def _load_user_config(self) -> None:
        """从 settings 读取用户配置（overrides + custom）并应用到内存。"""
        if self._settings_repository is None:
            return
        try:
            raw = self._settings_repository.get_setting(SETTINGS_KEY, None)
        except Exception as exc:
            logger.debug("load %s failed: %s", SETTINGS_KEY, exc)
            return
        if not isinstance(raw, dict):
            return
        overrides = raw.get("overrides", {})
        if isinstance(overrides, dict):
            for name, patch in overrides.items():
                if not isinstance(patch, dict) or name not in self._sources:
                    continue
                if "enabled" in patch:
                    self._sources[name].enabled = bool(patch["enabled"])
        custom = raw.get("custom", [])
        if isinstance(custom, list):
            for entry in custom:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "")).strip()
                if not name or name in self._sources:
                    continue
                self._sources[name] = SourceConfig(
                    name=name,
                    display_name=str(entry.get("display_name") or name),
                    url=str(entry.get("url", "")),
                    parser_type=str(entry.get("parser_type", "rss")),
                    language=str(entry.get("language", "en")),
                    enabled=bool(entry.get("enabled", True)),
                    category_hint=str(entry.get("category_hint", "")),
                    is_custom=True,
                )

    def _persist(self) -> None:
        """将用户配置（被禁用的默认源 + 全部自定义源）写入 settings。"""
        if self._settings_repository is None:
            return
        try:
            overrides: dict[str, Any] = {}
            for name, src in self._sources.items():
                if not src.is_custom and not src.enabled:
                    overrides[name] = {"enabled": False}
            custom = [
                {
                    "name": s.name,
                    "display_name": s.display_name,
                    "url": s.url,
                    "parser_type": s.parser_type,
                    "language": s.language,
                    "enabled": s.enabled,
                    "category_hint": s.category_hint,
                }
                for s in self._sources.values()
                if s.is_custom
            ]
            self._settings_repository.set_setting(
                SETTINGS_KEY,
                {"overrides": overrides, "custom": custom},
            )
        except Exception as exc:
            logger.warning("persist %s failed: %s", SETTINGS_KEY, exc)

    def list_sources(self) -> list[SourceConfig]:
        """返回所有已配置的源（默认 + 自定义，含禁用项）。"""
        return list(self._sources.values())

    def get_source(self, name: str) -> SourceConfig | None:
        return self._sources.get(name)

    def get_enabled_sources(self) -> list[SourceConfig]:
        """返回所有已启用的源。"""
        return [s for s in self._sources.values() if s.enabled]

    def get_enabled_source_names(self) -> list[str]:
        return [s.name for s in self._sources.values() if s.enabled]

    @staticmethod
    def get_fetcher_method_name(name: str) -> str | None:
        """返回内置源对应的 fetcher 方法名；自定义源返回 None。"""
        return FETCHER_METHOD_MAP.get(name)

    def enable_source(self, name: str) -> bool:
        src = self._sources.get(name)
        if src is None:
            return False
        if not src.enabled:
            src.enabled = True
            self._persist()
            logger.info("info source enabled: %s", name)
        return True

    def disable_source(self, name: str) -> bool:
        src = self._sources.get(name)
        if src is None:
            return False
        if src.enabled:
            src.enabled = False
            self._persist()
            logger.info("info source disabled: %s", name)
        return True

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """统一开关入口，供 RPC info.sources.toggle 调用。"""
        return self.enable_source(name) if enabled else self.disable_source(name)

    def add_custom_source(
        self,
        name: str,
        url: str,
        parser_type: str = "rss",
        language: str = "en",
        category_hint: str = "",
        display_name: str = "",
    ) -> SourceConfig | None:
        """添加用户自定义源。name 或 url 为空、或 name 已存在时返回 None。"""
        name = (name or "").strip()
        if not name or not (url or "").strip():
            return None
        if name in self._sources:
            return None
        if parser_type not in _VALID_PARSER_TYPES:
            parser_type = "rss"
        if language not in _VALID_LANGUAGES:
            language = "en"
        src = SourceConfig(
            name=name,
            display_name=(display_name or name).strip(),
            url=url.strip(),
            parser_type=parser_type,
            language=language,
            enabled=True,
            category_hint=category_hint,
            is_custom=True,
        )
        self._sources[name] = src
        self._persist()
        logger.info("info custom source added: %s (%s)", name, url)
        return src

    def remove_custom_source(self, name: str) -> bool:
        """删除自定义源。内置源不可删除，返回 False。"""
        src = self._sources.get(name)
        if src is None or not src.is_custom:
            return False
        del self._sources[name]
        self._persist()
        logger.info("info custom source removed: %s", name)
        return True

    def reset_to_defaults(self) -> None:
        """移除所有自定义源并重新启用全部默认源。"""
        new_sources: dict[str, SourceConfig] = {}
        for src in _default_sources():
            new_sources[src.name] = src
        self._sources = new_sources
        self._persist()
        logger.info("info sources reset to defaults")


__all__ = [
    "SourceConfig",
    "InfoSourceRegistry",
    "FETCHER_METHOD_MAP",
    "SETTINGS_KEY",
]
