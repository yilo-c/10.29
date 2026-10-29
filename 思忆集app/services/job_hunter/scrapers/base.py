"""求职猎手爬虫抽象基类与标准化数据模型。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class JobListing:
    """标准化职位列表项（各平台抓取后统一转换为此结构）。"""

    id: str
    title: str
    company: str
    city: str = ""
    salary: str = ""
    experience: str = ""
    education: str = ""
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    url: str = ""
    platform: str = ""
    published_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "company": self.company,
            "city": self.city,
            "salary": self.salary,
            "experience": self.experience,
            "education": self.education,
            "tags": self.tags,
            "summary": self.summary,
            "url": self.url,
            "platform": self.platform,
            "published_at": self.published_at,
        }


@dataclass
class JobDetail:
    """标准化职位详情。"""

    id: str
    title: str
    company: str
    company_size: str = ""
    company_stage: str = ""
    city: str = ""
    salary: str = ""
    experience: str = ""
    education: str = ""
    tags: list[str] = field(default_factory=list)
    description: str = ""
    requirements: str = ""
    benefits: list[str] = field(default_factory=list)
    recruiter: str = ""
    url: str = ""
    platform: str = ""
    published_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "company": self.company,
            "company_size": self.company_size,
            "company_stage": self.company_stage,
            "city": self.city,
            "salary": self.salary,
            "experience": self.experience,
            "education": self.education,
            "tags": self.tags,
            "description": self.description,
            "requirements": self.requirements,
            "benefits": self.benefits,
            "recruiter": self.recruiter,
            "url": self.url,
            "platform": self.platform,
            "published_at": self.published_at,
        }


class BaseScraper(ABC):
    """招聘平台爬虫抽象基类。"""

    def __init__(
        self,
        headless: bool = True,
        slow_mo: int = 0,
        data_dir: str | None = None,
        min_request_interval: float = 3.0,
    ) -> None:
        self.headless = headless
        self.slow_mo = slow_mo
        self._data_dir = data_dir
        self._min_request_interval = min_request_interval
        self._last_request_time = 0.0
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台名称，如 ``boss``、``lagou``。"""

    @abstractmethod
    async def search(self, keywords: list[str], city: str = "", page: int = 1) -> list[JobListing]:
        """搜索职位列表。"""

    @abstractmethod
    async def get_detail(self, url: str) -> JobDetail | None:
        """获取职位详情。"""

    async def _ensure_browser(self) -> Any:
        """延迟启动 Playwright 浏览器实例（带基本反爬 UA）并加载 Cookie。"""
        if self._browser is not None:
            return self._context
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            logger.error("playwright not installed: %s", exc)
            raise
        self._playwright = await async_playwright().start()
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        ]
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
        )
        self._context = await self._browser.new_context(
            user_agent=random.choice(user_agents),
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
        )
        await self._load_cookies()
        return self._context

    async def _random_delay(self, min_ms: int = 2000, max_ms: int = 5000) -> None:
        """随机延迟，降低反爬风险。"""
        await asyncio.sleep(random.randint(min_ms, max_ms) / 1000.0)

    async def _rate_limit(self) -> None:
        """控制请求间隔，避免触发平台频率限制。"""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            await asyncio.sleep(self._min_request_interval - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    async def _load_cookies(self) -> None:
        """从数据目录加载平台 Cookie（如果存在）。"""
        path = self._cookie_path()
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            if isinstance(cookies, list) and self._context is not None:
                await self._context.add_cookies(cookies)
                logger.debug("loaded %d cookies for %s", len(cookies), self.platform_name)
        except Exception as exc:
            logger.debug("load cookies failed for %s: %s", self.platform_name, exc)

    async def _save_cookies(self) -> None:
        """将平台 Cookie 持久化到数据目录。"""
        path = self._cookie_path()
        if not path or self._context is None:
            return
        try:
            cookies = await self._context.cookies()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            logger.debug("saved %d cookies for %s", len(cookies), self.platform_name)
        except Exception as exc:
            logger.debug("save cookies failed for %s: %s", self.platform_name, exc)

    def _cookie_path(self) -> str | None:
        """Cookie 文件路径。子类可覆盖目录。"""
        if not self._data_dir:
            return None
        return os.path.join(self._data_dir, f"cookies_{self.platform_name}.json")

    async def close(self) -> None:
        """关闭浏览器上下文与 Playwright 实例（关闭前保存 Cookie）。"""
        if self._context is not None:
            try:
                await self._save_cookies()
                await self._context.close()
            except Exception as exc:
                logger.debug("close scraper context failed: %s", exc)
            self._context = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as exc:
                logger.debug("close scraper browser failed: %s", exc)
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:
                logger.debug("stop playwright failed: %s", exc)
            self._playwright = None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def _with_retry(self, coro_factory, label: str, max_retries: int = 2):
        """带指数退避的重试包装。"""
        for attempt in range(max_retries + 1):
            try:
                return await coro_factory()
            except Exception as exc:
                logger.warning("%s attempt %d failed: %s", label, attempt + 1, exc)
                if attempt == max_retries:
                    raise
                await asyncio.sleep(2**attempt + random.random())

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} platform={self.platform_name}>"
