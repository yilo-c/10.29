"""招聘平台爬虫注册。"""

from __future__ import annotations

from .base import BaseScraper, JobDetail, JobListing
from .boss import BossScraper
from .job51 import Job51Scraper
from .lagou import LagouScraper
from .liepin import LiepinScraper
from .zhilian import ZhilianScraper

__all__ = [
    "BaseScraper",
    "BossScraper",
    "Job51Scraper",
    "LagouScraper",
    "LiepinScraper",
    "ZhilianScraper",
    "get_scraper_classes",
]


def get_scraper_classes() -> "dict[str, type[BaseScraper]]":
    """返回平台名称到爬虫类的映射。"""
    return {
        "boss": BossScraper,
        "job51": Job51Scraper,
        "lagou": LagouScraper,
        "liepin": LiepinScraper,
        "zhilian": ZhilianScraper,
    }
