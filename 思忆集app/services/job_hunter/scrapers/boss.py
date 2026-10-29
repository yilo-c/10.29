"""BOSS直聘爬虫实现。

使用 Playwright 模拟浏览器访问公开搜索页，解析职位列表与详情。
注意：BOSS 直聘有较强反爬，未登录状态可能只能看到少量职位或触发验证。
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any

from .base import BaseScraper, JobDetail, JobListing

logger = logging.getLogger(__name__)


class BossScraper(BaseScraper):
    """BOSS直聘爬虫。"""

    def __init__(self, headless: bool = True, slow_mo: int = 0, data_dir: str | None = None) -> None:
        super().__init__(headless, slow_mo, data_dir, min_request_interval=5.0)

    @property
    def platform_name(self) -> str:
        return "boss"

    async def search(self, keywords: list[str], city: str = "", page: int = 1) -> list[JobListing]:
        if not keywords:
            return []
        query = urllib.parse.quote(" ".join(keywords))
        city_code = self._resolve_city_code(city)
        url = f"https://www.zhipin.com/web/geek/job?query={query}&city={city_code}&page={page}"

        async def _scrape() -> list[JobListing]:
            context = await self._ensure_browser()
            page_obj = await context.new_page()
            listings: list[JobListing] = []
            try:
                await self._rate_limit()
                await page_obj.goto(url, wait_until="networkidle", timeout=30000)
                await self._random_delay(1500, 3000)
                await page_obj.wait_for_selector(
                    ".job-card-wrapper, .search-job-result, .job-list-box",
                    timeout=15000,
                )
                cards = await page_obj.locator(".job-card-wrapper").all()
                logger.info(
                    "boss search found %d cards for query=%s page=%d", len(cards), query, page
                )
                for card in cards:
                    try:
                        listing = await self._parse_list_card(card)
                        if listing:
                            listings.append(listing)
                    except Exception as exc:
                        logger.debug("parse boss card failed: %s", exc)
            finally:
                try:
                    await page_obj.close()
                except Exception:
                    pass
            return listings

        return await self._with_retry(_scrape, label="boss search")

    async def get_detail(self, url: str) -> JobDetail | None:
        async def _scrape() -> JobDetail | None:
            context = await self._ensure_browser()
            page_obj = await context.new_page()
            try:
                await self._rate_limit()
                await page_obj.goto(url, wait_until="networkidle", timeout=30000)
                await self._random_delay(2000, 4000)
                detail = await self._parse_detail_page(page_obj, url)
                return detail
            finally:
                try:
                    await page_obj.close()
                except Exception:
                    pass

        return await self._with_retry(_scrape, label="boss detail")

    async def _parse_list_card(self, card: Any) -> JobListing | None:
        try:
            title_el = card.locator(".job-name").first
            title = await title_el.inner_text() if await title_el.count() > 0 else ""
            if not title:
                return None

            company_el = card.locator(".company-name").first
            company = await company_el.inner_text() if await company_el.count() > 0 else ""

            salary_el = card.locator(".salary").first
            salary = await salary_el.inner_text() if await salary_el.count() > 0 else ""

            info_els = await card.locator(".job-info-tag, .info-desc").all_inner_texts()
            experience = ""
            education = ""
            city = ""
            for info in info_els:
                info = info.strip()
                if "经验" in info or "年" in info:
                    experience = info
                elif "学历" in info or "本科" in info or "硕士" in info or "大专" in info:
                    education = info
                elif not city and len(info) <= 8:
                    city = info

            tags = await card.locator(".tag-list .tag-item, .job-tags .tag-item").all_inner_texts()
            tags = [t.strip() for t in tags if t.strip()]

            summary = ""
            summary_el = card.locator(".job-desc").first
            if await summary_el.count() > 0:
                summary = await summary_el.inner_text()

            link_el = card.locator("a[href*='/job_detail/']").first
            detail_url = ""
            if await link_el.count() > 0:
                detail_url = await link_el.get_attribute("href") or ""
                if detail_url and not detail_url.startswith("http"):
                    detail_url = "https://www.zhipin.com" + detail_url

            job_id = self._extract_job_id(detail_url) or f"boss-{hash(detail_url) & 2147483647}"
            return JobListing(
                id=job_id,
                title=title.strip(),
                company=company.strip(),
                city=city.strip(),
                salary=salary.strip(),
                experience=experience.strip(),
                education=education.strip(),
                tags=tags,
                summary=summary.strip(),
                url=detail_url,
                platform=self.platform_name,
                published_at=self._now_iso(),
            )
        except Exception as exc:
            logger.debug("parse list card error: %s", exc)
            return None

    async def _parse_detail_page(self, page_obj: Any, url: str) -> JobDetail | None:
        try:
            title = ""
            title_el = page_obj.locator(".name h1, .job-name").first
            if await title_el.count() > 0:
                title = await title_el.inner_text()

            company = ""
            company_el = page_obj.locator(".company-name, .business-info .name").first
            if await company_el.count() > 0:
                company = await company_el.inner_text()

            salary = ""
            salary_el = page_obj.locator(".salary").first
            if await salary_el.count() > 0:
                salary = await salary_el.inner_text()

            city = ""
            experience = ""
            education = ""
            info_els = await page_obj.locator(
                ".job-info .info-label, .job-primary .info-primary span"
            ).all_inner_texts()
            for info in info_els:
                info = info.strip()
                if "经验" in info or "年" in info:
                    experience = info
                elif "本科" in info or "硕士" in info or "大专" in info or "学历" in info:
                    education = info
                elif len(info) <= 8 and not city:
                    city = info

            tags = await page_obj.locator(".job-tags .tag-item, .job-labels .tag").all_inner_texts()
            tags = [t.strip() for t in tags if t.strip()]

            description = ""
            desc_el = page_obj.locator(".job-sec-text, .job-description").first
            if await desc_el.count() > 0:
                description = await desc_el.inner_text()

            requirements = ""
            req_el = page_obj.locator(".job-sec .text, .job-requirement").first
            if await req_el.count() > 0:
                requirements = await req_el.inner_text()

            benefits: list[str] = []
            benefits_els = await page_obj.locator(
                ".welfare-list .welfare-item, .company-benefits .tag"
            ).all_inner_texts()
            benefits = [b.strip() for b in benefits_els if b.strip()]

            company_size = ""
            company_stage = ""
            comp_info_els = await page_obj.locator(
                ".company-info .company-tags .tag, .sider-company .info-tag"
            ).all_inner_texts()
            for info in comp_info_els:
                info = info.strip()
                if "人" in info:
                    company_size = info
                elif "轮" in info or "上市" in info or "未融资" in info:
                    company_stage = info

            recruiter = ""
            rec_el = page_obj.locator(".recruiter-name, .boss-name").first
            if await rec_el.count() > 0:
                recruiter = await rec_el.inner_text()

            job_id = self._extract_job_id(url) or f"boss-{hash(url) & 2147483647}"
            return JobDetail(
                id=job_id,
                title=title.strip(),
                company=company.strip(),
                company_size=company_size,
                company_stage=company_stage,
                city=city,
                salary=salary.strip(),
                experience=experience.strip(),
                education=education.strip(),
                tags=tags,
                description=description.strip(),
                requirements=requirements.strip(),
                benefits=benefits,
                recruiter=recruiter.strip(),
                url=url,
                platform=self.platform_name,
                published_at=self._now_iso(),
            )
        except Exception as exc:
            logger.debug("parse detail page error: %s", exc)
            return None

    @staticmethod
    def _extract_job_id(url: str) -> str:
        match = re.search(r"/job_detail/([a-zA-Z0-9_]+)\.html", url)
        if match:
            return f"boss-{match.group(1)}"
        return ""

    @staticmethod
    def _resolve_city_code(city: str) -> str:
        """BOSS 城市编码（简化版，仅支持部分热门城市）。"""
        mapping = {
            "北京": "101010100",
            "上海": "101020100",
            "广州": "101280100",
            "深圳": "101280600",
            "杭州": "101210100",
            "成都": "101270100",
            "武汉": "101200100",
            "西安": "101110100",
            "南京": "101190100",
            "苏州": "101190400",
            "重庆": "101040100",
        }
        return mapping.get(city.strip(), "") if city else ""
