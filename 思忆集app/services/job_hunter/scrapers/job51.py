"""前程无忧（51job）爬虫实现。"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any

from .base import BaseScraper, JobDetail, JobListing

logger = logging.getLogger(__name__)


class Job51Scraper(BaseScraper):
    """前程无忧爬虫。"""

    def __init__(self, headless: bool = True, slow_mo: int = 0, data_dir: str | None = None) -> None:
        super().__init__(headless, slow_mo, data_dir, min_request_interval=5.0)

    @property
    def platform_name(self) -> str:
        return "job51"

    async def search(self, keywords: list[str], city: str = "", page: int = 1) -> list[JobListing]:
        if not keywords:
            return []
        query = urllib.parse.quote(" ".join(keywords))
        city_encoded = urllib.parse.quote(city.strip()) if city else ""
        if city_encoded:
            url = f"https://search.51job.com/list/{city_encoded},000000,0000,00,9,99,{query},2,{page}.html"
        else:
            url = f"https://search.51job.com/list/000000,000000,0000,00,9,99,{query},2,{page}.html"

        async def _scrape() -> list[JobListing]:
            context = await self._ensure_browser()
            page_obj = await context.new_page()
            listings: list[JobListing] = []
            try:
                await self._rate_limit()
                await page_obj.goto(url, wait_until="networkidle", timeout=30000)
                await self._random_delay(1500, 3000)
                await page_obj.wait_for_selector(
                    ".el, .j_joblist .e, .job_item, .sr_list",
                    timeout=15000,
                )
                cards = await page_obj.locator(".el").all()
                logger.info(
                    "job51 search found %d cards for query=%s page=%d", len(cards), query, page
                )
                for card in cards:
                    try:
                        listing = await self._parse_list_card(card)
                        if listing:
                            listings.append(listing)
                    except Exception as exc:
                        logger.debug("parse job51 card failed: %s", exc)
            finally:
                try:
                    await page_obj.close()
                except Exception:
                    pass
            return listings

        return await self._with_retry(_scrape, label="job51 search")

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

        return await self._with_retry(_scrape, label="job51 detail")

    async def _parse_list_card(self, card: Any) -> JobListing | None:
        try:
            title_el = card.locator(".jname, .job_name, .t1 span a").first
            title = await title_el.inner_text() if await title_el.count() > 0 else ""
            if not title:
                return None

            company_el = card.locator(".cname, .company_name, .t2 a").first
            company = await company_el.inner_text() if await company_el.count() > 0 else ""

            salary_el = card.locator(".salary, .strong, .t4").first
            salary = await salary_el.inner_text() if await salary_el.count() > 0 else ""

            city = ""
            experience = ""
            education = ""
            info_els = await card.locator(".info span, .tags span, .t3, .t5").all_inner_texts()
            for info in info_els:
                info = info.strip().strip("/")
                if "经验" in info or "年" in info:
                    experience = info
                elif "本科" in info or "硕士" in info or "大专" in info or "学历" in info:
                    education = info
                elif not city and len(info) <= 10:
                    city = info

            tags = await card.locator(".tags span, .tag-item, .wlf").all_inner_texts()
            tags = [t.strip() for t in tags if t.strip()]

            link_el = card.locator("a[href*='/jobs.51job.com/']").first
            detail_url = ""
            if await link_el.count() > 0:
                detail_url = await link_el.get_attribute("href") or ""
                if detail_url and not detail_url.startswith("http"):
                    detail_url = "https:" + detail_url

            job_id = self._extract_job_id(detail_url) or f"job51-{hash(detail_url) & 2147483647}"
            return JobListing(
                id=job_id,
                title=title.strip(),
                company=company.strip(),
                city=city.strip(),
                salary=salary.strip(),
                experience=experience.strip(),
                education=education.strip(),
                tags=tags,
                summary="",
                url=detail_url,
                platform=self.platform_name,
                published_at=self._now_iso(),
            )
        except Exception as exc:
            logger.debug("parse job51 list card error: %s", exc)
            return None

    async def _parse_detail_page(self, page_obj: Any, url: str) -> JobDetail | None:
        try:
            title = ""
            title_el = page_obj.locator("h1, .job-name, .cn h1").first
            if await title_el.count() > 0:
                title = await title_el.inner_text()

            company = ""
            company_el = page_obj.locator(".cname, .com-name, .comp_baseInfo_title").first
            if await company_el.count() > 0:
                company = await company_el.inner_text()

            salary = ""
            salary_el = page_obj.locator(".cn strong, .job-salary, .salary").first
            if await salary_el.count() > 0:
                salary = await salary_el.inner_text()

            city = ""
            experience = ""
            education = ""
            info_els = await page_obj.locator(
                ".msg ltv, .job_msg span, .info span, .cn .ltype"
            ).all_inner_texts()
            for info in info_els:
                info = info.strip().strip("/|\xa0")
                if "经验" in info or "年" in info:
                    experience = info
                elif "本科" in info or "硕士" in info or "大专" in info or "学历" in info:
                    education = info
                elif not city and len(info) <= 10:
                    city = info

            tags = await page_obj.locator(
                ".jtag .sp4, .job-tags span, .tag-item"
            ).all_inner_texts()
            tags = [t.strip() for t in tags if t.strip()]

            description = ""
            desc_el = page_obj.locator(".job_msg, .job-description, .bmsg").first
            if await desc_el.count() > 0:
                description = await desc_el.inner_text()

            requirements = ""
            req_el = page_obj.locator(".job_request, .requirement").first
            if await req_el.count() > 0:
                requirements = await req_el.inner_text()

            benefits: list[str] = []
            benefits_els = await page_obj.locator(
                ".t1 span, .welfare span, .job_welfare span"
            ).all_inner_texts()
            benefits = [b.strip() for b in benefits_els if b.strip()]

            company_size = ""
            company_stage = ""
            comp_info_els = await page_obj.locator(
                ".com_tag p, .comp_baseInfo__title, .company-info span"
            ).all_inner_texts()
            for info in comp_info_els:
                info = info.strip()
                if "人" in info:
                    company_size = info
                elif "轮" in info or "上市" in info or "未融资" in info or "不需要融资" in info:
                    company_stage = info

            recruiter = ""
            rec_el = page_obj.locator(". recruiter-name, .boss-name, .publisher-name").first
            if await rec_el.count() > 0:
                recruiter = await rec_el.inner_text()

            job_id = self._extract_job_id(url) or f"job51-{hash(url) & 2147483647}"
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
            logger.debug("parse job51 detail page error: %s", exc)
            return None

    @staticmethod
    def _extract_job_id(url: str) -> str:
        match = re.search(r"/jobs\.51job\.com/.+?/(\d+)\.html", url)
        if match:
            return f"job51-{match.group(1)}"
        return ""
