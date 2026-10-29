# -*- coding: utf-8 -*-
"""求职猎手主服务。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.job_hunter.analyzer import JobAnalyzer
from services.job_hunter.cold_message import ColdMessageGenerator
from services.job_hunter.config import JobHunterProfile, get_profile, set_profile
from services.job_hunter.interview_prep import InterviewPrep
from services.job_hunter.matcher import JobMatcher
from services.job_hunter.profile_extractor import ProfileExtractor
from services.job_hunter.resume import ResumeAnalyzer, ResumeParser, ResumeRewriter
from services.job_hunter.resume_advisor import ResumeAdvisor
from services.job_hunter.scrapers import get_scraper_classes
from services.job_hunter.scrapers.base import BaseScraper, JobDetail
from services.job_hunter.storage import APPLICATION_STATUSES, JobStorage

logger = logging.getLogger("siyi.services.job_hunter")

SUPPORTED_PLATFORMS = ["boss", "lagou", "liepin", "zhilian", "job51"]

_PLATFORM_RETRY_DELAY = 2.0


class JobHunterService:
    """求职猎手核心服务：资料管理、抓取、匹配、分析、推荐。"""

    def __init__(
        self,
        ai_client: Any,
        settings_repository: Any,
        storage: JobStorage | None = None,
        scrapers: dict[str, BaseScraper] | None = None,
    ) -> None:
        self._ai_client = ai_client
        self._settings_repository = settings_repository
        self._storage = storage or JobStorage()
        self._matcher = JobMatcher(ai_client)
        self._analyzer = JobAnalyzer(ai_client)
        self._cold_message_generator = ColdMessageGenerator(ai_client)
        self._resume_advisor = ResumeAdvisor(ai_client)
        self._interview_prep = InterviewPrep(ai_client)
        self._profile_extractor = ProfileExtractor(ai_client)
        self._resume_parser = ResumeParser(self._storage.data_dir)
        self._resume_analyzer = ResumeAnalyzer(ai_client)
        self._resume_rewriter = ResumeRewriter(ai_client)
        self._scrapers = scrapers if scrapers is not None else {}
        if scrapers is None:
            scraper_data_dir = self._storage.data_dir
            for name, cls in get_scraper_classes().items():
                try:
                    self._scrapers[name] = cls(data_dir=scraper_data_dir)
                except Exception as exc:
                    logger.warning("init scraper %s failed: %s", name, exc)

    def get_profile(self) -> dict[str, Any]:
        return get_profile(self._settings_repository).to_dict()

    def set_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        profile = JobHunterProfile.from_dict(data)
        return set_profile(self._settings_repository, profile)

    async def extract_profile_from_resume(self, resume_text: str) -> dict[str, Any]:
        """从简历文本中 AI 提取画像建议，同时缓存简历原文到当前资料。

        Returns: ``{"success": True, "profile": {...}}`` 与前端契约对齐，
        其中 profile 覆盖 ``JobHunterProfile`` 各字段。
        """
        result = await self._profile_extractor.extract(resume_text)
        if not result.get("success"):
            return result
        profile = result.get("profile") or result  # 兼容新/旧两种返回形状

        def _cache_profile() -> None:
            p = get_profile(self._settings_repository)
            p.resume_text = resume_text[:12000]
            p.resume_keywords = profile.get("resume_keywords", [])
            set_profile(self._settings_repository, p)

        try:
            await asyncio.to_thread(_cache_profile)
        except Exception as exc:
            logger.warning("cache resume_text failed: %s", exc)
        return {"success": True, "profile": profile}

    def upload_and_parse_resume(self, filename: str, file_data: bytes) -> dict[str, Any]:
        """上传并解析简历文件，返回文本与元数据。"""
        try:
            doc = self._resume_parser.parse_bytes(filename, file_data)
            return {"success": True, **doc.to_dict(), "data_dir": self._storage.data_dir}
        except Exception as exc:
            logger.warning("upload_and_parse_resume failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def diagnose_resume(self, resume_text: str, target_role: str = "", industry: str = "") -> dict[str, Any]:
        """对简历进行 AI 诊断评分。"""
        result = await self._resume_analyzer.diagnose(resume_text, target_role, industry)
        return result.to_dict()

    async def rewrite_resume(
        self,
        resume_text: str,
        target_role: str = "",
        industry: str = "",
        style: str = "professional",
    ) -> dict[str, Any]:
        """AI 改写整份简历。"""
        result = await self._resume_rewriter.rewrite(resume_text, target_role, industry, style)
        return result.to_dict()

    async def rewrite_resume_section(self, resume_text: str, section: str, target_role: str = "") -> dict[str, Any]:
        """AI 改写简历指定章节。"""
        return await self._resume_rewriter.rewrite_section(resume_text, section, target_role)

    def list_platforms(self) -> list[dict[str, Any]]:
        return [
            {
                "id": p,
                "name": self._platform_display_name(p),
                "enabled": p in self._scrapers,
                "implemented": p in self._scrapers,
            }
            for p in SUPPORTED_PLATFORMS
        ]

    @staticmethod
    def _platform_display_name(platform: str) -> str:
        mapping = {
            "boss": "BOSS直聘",
            "lagou": "拉勾",
            "liepin": "猎聘",
            "zhilian": "智联招聘",
            "job51": "前程无忧",
        }
        return mapping.get(platform, platform)

    async def search(
        self,
        platforms: list[str] | None = None,
        keywords: list[str] | None = None,
        city: str = "",
        page: int = 1,
    ) -> dict[str, Any]:
        """多平台并发搜索并持久化。"""
        platforms = platforms or ["boss"]
        keywords = keywords or ["python"]
        keywords = [k.strip() for k in keywords if k.strip()]
        if not keywords:
            return {"success": False, "error": "keywords required"}

        tasks = []
        names = []
        for p in platforms:
            scraper = self._scrapers.get(p)
            if scraper is None:
                continue
            tasks.append(scraper.search(keywords, city=city, page=page))
            names.append(p)

        if not tasks:
            return {"success": False, "error": "no available scrapers"}

        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_listings: list[Any] = []

        platform_counts: dict[str, int] = {}
        errors: list[dict[str, str]] = []

        failed: list[tuple[str, Exception]] = []
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                failed.append((name, result))
                logger.warning("scrape %s failed: %s", name, result)
                continue
            all_listings.extend(result)
            platform_counts[name] = len(result)

        # 失败平台延时重试一次（多为反爬/网络抖动）
        if failed:
            await asyncio.sleep(_PLATFORM_RETRY_DELAY)
            for name, original_exc in failed:
                scraper = self._scrapers.get(name)
                if scraper is None:
                    errors.append({"platform": name, "error": str(original_exc)})
                    continue
                try:
                    logger.info("retrying scraper %s after initial failure", name)
                    retry_result = await scraper.search(keywords, city=city, page=page)
                    all_listings.extend(retry_result)
                    platform_counts[name] = len(retry_result)
                    logger.info(
                        "scraper %s retry succeeded: %d listings",
                        name,
                        len(retry_result),
                    )
                except Exception as retry_exc:
                    logger.warning("scraper %s retry also failed: %s", name, retry_exc)
                    errors.append({"platform": name, "error": str(retry_exc)})

        added, dup_count = self._storage.add_listings(all_listings)
        # D-PG-2（2026-09-17）：原实现无条件返回 success=True，即使全平台抓取
        # 失败、0 条入库也返回"成功"——任务被标记 completed，用户见"完成"实则
        # 空结果。改为：全平台失败（errors 非空且 0 入库）如实报失败；部分失败
        # 保留 success=True 但显式带 failed_platforms 数量，不掩盖降级。
        common = {
            "total_fetched": len(all_listings),
            "added": len(added),
            "added_ids": [j.get("id") for j in added if j.get("id")],
            "duplicates": dup_count,
            "platform_counts": platform_counts,
            "errors": errors,
            "failed_platforms": len(errors),
        }
        if errors and not added:
            return {
                "success": False,
                "error": "all_platforms_failed",
                "reason": "all_platforms_failed",
                "message": "所有平台抓取均失败，未获取到任何职位",
                **common,
            }
        # PD-H-1（2026-09-17，r2-verify 实测真相③）：全平台"成功但零结果"
        # （errors=[]、total_fetched=0、added=0）是反爬/风控页静默返回空列表的
        # 典型形态——不报错但什么都没抓到。success 保持 True（无平台报错，
        # 任务语义上完成），但追加 degraded 标记，调用方/前端据此提示
        # "本次抓取可能不可信"，不再静默假成功。
        # 边界：全重复（total_fetched>0 且 duplicates>0）**不算** degraded——
        # 那证明抓取本身成功，只是内容已入库（test_all_duplicates 契约）。
        if not errors and common["total_fetched"] == 0 and common["added"] == 0:
            return {
                "success": True,
                "degraded": True,
                "message": "所有平台均返回 0 条结果（可能被反爬拦截，抓取结果不可信）",
                **common,
            }
        return {"success": True, **common}

    async def match_jobs(self, job_ids: list[str] | None = None) -> dict[str, Any]:
        """对指定职位或全部未打分职位进行 AI 匹配。"""
        profile = await asyncio.to_thread(get_profile, self._settings_repository)
        if job_ids:
            jobs = [self._storage.get_job(jid) for jid in job_ids]
            jobs = [j for j in jobs if j]
        else:
            jobs = self._storage.list_jobs(limit=1000)

        if not jobs:
            return {"success": True, "matched": 0}

        scored = await self._matcher.match_batch(profile, jobs)
        updates: dict[str, dict[str, Any]] = {}
        for job in scored:
            jid = job.get("id")
            if not jid:
                continue
            updates[jid] = {
                "match_score": job.get("match_score", 0),
                "match_reasons": job.get("match_reasons", []),
            }

        updated = 0
        if updates:
            updated = await asyncio.to_thread(self._storage.batch_update_jobs, updates)
        return {"success": True, "matched": updated}

    async def analyze_job(self, job_id: str) -> dict[str, Any]:
        job = self._storage.get_job(job_id)
        if not job:
            return {"success": False, "error": "job not found"}
        profile = await asyncio.to_thread(get_profile, self._settings_repository)
        result = await self._analyzer.analyze(profile, job)
        if "error" not in result:
            await asyncio.to_thread(
                self._storage.update_job,
                job_id,
                {
                    "analysis": result.get("analysis", ""),
                    "analyzed": True,
                    "analysis_dimensions": result.get("dimensions", {}),
                },
            )
        return {"success": "error" not in result, **result}

    async def analyze_job_stream(self, job_id: str):
        """流式分析生成器。"""
        job = self._storage.get_job(job_id)
        if not job:
            yield "职位不存在"
            return
        profile = await asyncio.to_thread(get_profile, self._settings_repository)
        full_text = ""
        async for chunk in self._analyzer.analyze_stream(profile, job):
            full_text += chunk
            yield chunk
        await asyncio.to_thread(
            self._storage.update_job,
            job_id,
            {"analysis": full_text, "analyzed": True},
        )

    async def generate_cold_message(self, job_id: str) -> dict[str, Any]:
        """生成 AI 招呼语。"""
        job = self._storage.get_job(job_id)
        if not job:
            return {"success": False, "error": "job not found"}
        profile = get_profile(self._settings_repository)
        return await self._cold_message_generator.generate(profile, job)

    async def get_resume_advice(self, job_id: str) -> dict[str, Any]:
        """获取简历优化建议。"""
        job = self._storage.get_job(job_id)
        if not job:
            return {"success": False, "error": "job not found"}
        profile = get_profile(self._settings_repository)
        return await self._resume_advisor.advise(profile, job)

    async def get_interview_prep(self, job_id: str) -> dict[str, Any]:
        """获取面试问题预测。"""
        job = self._storage.get_job(job_id)
        if not job:
            return {"success": False, "error": "job not found"}
        profile = get_profile(self._settings_repository)
        return await self._interview_prep.prepare(profile, job)

    def get_recommendations(self, limit: int = 10, min_score: float = 60.0) -> list[dict[str, Any]]:
        # P0-2026-08-17: 原 `list_jobs(min_score, limit)` 按位置传参，把 int min_score
        # 误当 keyword、把 limit 误当 min_score，导致 keyword.lower() 对 int 调用崩溃。
        jobs = self._storage.list_jobs(min_score=min_score, limit=limit)
        jobs.sort(key=lambda x: float(x.get("match_score", 0) or 0), reverse=True)
        return jobs[:limit]

    def list_jobs(self, **filters: Any) -> list[dict[str, Any]]:
        return self._storage.list_jobs(**filters)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self._storage.get_job(job_id)

    def set_job_status(self, job_id: str, status: str) -> dict[str, Any]:
        """更新职位投递状态。"""
        if status not in APPLICATION_STATUSES:
            return {"success": False, "error": f"invalid status: {status}"}
        job = self._storage.set_application_status(job_id, status)
        if job is None:
            return {"success": False, "error": "job not found"}
        return {"success": True, "job": job}

    async def fetch_detail(self, job_id: str) -> dict[str, Any]:
        job = self._storage.get_job(job_id)
        if not job:
            return {"success": False, "error": "job not found"}
        platform = job.get("platform", "")
        scraper = self._scrapers.get(platform)
        if scraper is None:
            return {"success": False, "error": f"scraper for {platform} not available"}
        detail = await scraper.get_detail(job.get("url", ""))
        if detail is None:
            return {"success": False, "error": "fetch detail failed"}
        self._storage.save_detail(detail)
        return {"success": True, "detail": detail.to_dict()}

    def get_stats(self) -> dict[str, Any]:
        return self._storage.get_stats()

    async def close(self) -> None:
        for scraper in self._scrapers.values():
            try:
                await scraper.close()
            except Exception as exc:
                logger.debug("close scraper failed: %s", exc)

    def set_ai_client(self, new_client: Any) -> None:
        self._ai_client = new_client
        self._matcher = JobMatcher(new_client)
        self._analyzer = JobAnalyzer(new_client)
        self._cold_message_generator = ColdMessageGenerator(new_client)
        self._resume_advisor = ResumeAdvisor(new_client)
        self._interview_prep = InterviewPrep(new_client)
        self._profile_extractor = ProfileExtractor(new_client)
        self._resume_analyzer = ResumeAnalyzer(new_client)
        self._resume_rewriter = ResumeRewriter(new_client)
