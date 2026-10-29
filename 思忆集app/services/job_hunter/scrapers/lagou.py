"""拉勾网爬虫实现。"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any

from .base import BaseScraper, JobDetail, JobListing

logger = logging.getLogger(__name__)


class LagouScraper(BaseScraper):
    """拉勾网爬虫。"""

    def __init__(self, headless: bool = True, slow_mo: int = 0, data_dir: str | None = None) -> None:
        super().__init__(headless, slow_mo, data_dir, min_request_interval=5.0)

    @property
    def platform_name(self) -> str:
        return "lagou"

    async def search(self, keywords: list[str], city: str = "", page: int = 1) -> list[JobListing]:
        if not keywords:
            return []
        query = urllib.parse.quote(" ".join(keywords))
        city_encoded = urllib.parse.quote(city.strip()) if city else ""
        url = f"https://www.lagou.com/wn/jobs?kd={query}&city={city_encoded}&pn={page}"

        async def _scrape() -> list[JobListing]:
            context = await self._ensure_browser()
            page_obj = await context.new_page()
            listings: list[JobListing] = []
            try:
                await self._rate_limit()
                await page_obj.goto(url, wait_until="networkidle", timeout=30000)
                await self._random_delay(1500, 3000)
                await page_obj.wait_for_selector(
                    ".list_item, .job-list-box, .s_position_list",
                    timeout=15000,
                )
                cards = await page_obj.locator(".list_item").all()
                logger.info(
                    "lagou search found %d cards for query=%s page=%d", len(cards), query, page
                )
                for card in cards:
                    try:
                        listing = await self._parse_list_card(card)
                        if listing:
                            listings.append(listing)
                    except Exception as exc:
                        logger.debug("parse lagou card failed: %s", exc)
            finally:
                try:
                    await page_obj.close()
                except Exception:
                    pass
            return listings

        return await self._with_retry(_scrape, label="lagou search")

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

        return await self._with_retry(_scrape, label="lagou detail")

    async def _parse_list_card(self, card: Any) -> JobListing | None:
        try:
            title_el = card.locator(".position_name h2, .p_top h3, .job-title").first
            title = await title_el.inner_text() if await title_el.count() > 0 else ""
            if not title:
                return None

            company_el = card.locator(
                ".company_name, .company-name .company-text, .company-text"
            ).first
            company = await company_el.inner_text() if await company_el.count() > 0 else ""

            salary_el = card.locator(".money, .salary").first
            salary = await salary_el.inner_text() if await salary_el.count() > 0 else ""

            city = ""
            experience = ""
            education = ""
            info_els = await card.locator(
                ".p_top span, .job-info span, .requirement span"
            ).all_inner_texts()
            for info in info_els:
                info = info.strip().strip("/")
                if "经验" in info or "年" in info:
                    experience = info
                elif "本科" in info or "硕士" in info or "大专" in info or "学历" in info:
                    education = info
                elif not city and len(info) <= 10:
                    city = info

            tags = await card.locator(
                ".list_item_bot .li_b_l span, .job-tags span, .labels span"
            ).all_inner_texts()
            tags = [t.strip() for t in tags if t.strip()]

            summary = ""
            summary_el = card.locator(".li_b_l, .job-desc").first
            if await summary_el.count() > 0:
                summary = await summary_el.inner_text()

            link_el = card.locator("a[href*='/jobs/']").first
            detail_url = ""
            if await link_el.count() > 0:
                detail_url = await link_el.get_attribute("href") or ""
                if detail_url and not detail_url.startswith("http"):
                    detail_url = "https://www.lagou.com" + detail_url

            job_id = self._extract_job_id(detail_url) or f"lagou-{hash(detail_url) & 2147483647}"
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
            logger.debug("parse lagou list card error: %s", exc)
            return None

    async def _parse_detail_page(self, page_obj: Any, url: str) -> JobDetail | None:
        try:
            title = ""
            title_el = page_obj.locator(".job-name h2, .position-head h1, .job-title").first
            if await title_el.count() > 0:
                title = await title_el.inner_text()

            company = ""
            company_el = page_obj.locator(".company, .company-name, .job-company h2").first
            if await company_el.count() > 0:
                company = await company_el.inner_text()

            salary = ""
            salary_el = page_obj.locator(".salary, .job-salary").first
            if await salary_el.count() > 0:
                salary = await salary_el.inner_text()

            city = ""
            experience = ""
            education = ""
            info_els = await page_obj.locator(".job_request span, .job-info span").all_inner_texts()
            for info in info_els:
                info = info.strip().strip("/")
                if "经验" in info or "年" in info:
                    experience = info
                elif "本科" in info or "硕士" in info or "大专" in info or "学历" in info:
                    education = info
                elif not city and len(info) <= 10:
                    city = info

            tags = await page_obj.locator(
                ".position-label li, .job-tags span, .labels span"
            ).all_inner_texts()
            tags = [t.strip() for t in tags if t.strip()]

            description = ""
            desc_el = page_obj.locator(
                ".job_bt .job_detail, .job-section .text, .job-description"
            ).first
            if await desc_el.count() > 0:
                description = await desc_el.inner_text()

            requirements = ""
            req_el = page_obj.locator(".job-requirement, .requirement").first
            if await req_el.count() > 0:
                requirements = await req_el.inner_text()

            benefits: list[str] = []
            benefits_els = await page_obj.locator(
                ".job_welfare span, .welfare-item, .company-benefits span"
            ).all_inner_texts()
            benefits = [b.strip() for b in benefits_els if b.strip()]

            company_size = ""
            company_stage = ""
            comp_info_els = await page_obj.locator(
                ".job_company_content .c_feature li, .company-info li, .company-tags span"
            ).all_inner_texts()
            for info in comp_info_els:
                info = info.strip()
                if "人" in info:
                    company_size = info
                elif "轮" in info or "上市" in info or "未融资" in info or "不需要融资" in info:
                    company_stage = info

            recruiter = ""
            rec_el = page_obj.locator(".publisher_name, .recruiter-name, .boss-name").first
            if await rec_el.count() > 0:
                recruiter = await rec_el.inner_text()

            job_id = self._extract_job_id(url) or f"lagou-{hash(url) & 2147483647}"
            return JobDetail(
                **{
                    "id": job_id,
                    "title": title.strip(),
                    "company": company.strip(),
                    "company_size": company_size,
                    "company_stage": company_stage,
                    "city": city,
                    "salary": salary.strip(),
                    "experience": experience.strip(),
                    "education": education.strip(),
                    "tags": tags,
                    "description": description.strip(),
                    "requirements": requirements.strip(),
                    "benefits": benefits,
                    "recruiter": recruiter.strip(),
                    "url": url,
                    "platform": self.platform_name,
                    "published_at": self._now_iso(),
                }
            )
        except Exception as exc:
            logger.debug("parse lagou detail page error: %s", exc)
            return None

    @staticmethod
    def _extract_job_id(url: str) -> str:
        match = re.search(r"/jobs/(\d+)\.html", url)
        if match:
            return f"lagou-{match.group(1)}"
        return ""
