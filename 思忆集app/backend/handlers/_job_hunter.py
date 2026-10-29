# -*- coding: utf-8 -*-
"""求职猎手 JSON-RPC handlers。"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

from backend.handlers._list_guard import clamp_limit
from core.events import event_bus, AppEvent

logger = logging.getLogger("siyi.backend.handlers.job_hunter")


def register_job_hunter(registry, context) -> None:
    """注册求职猎手相关 JSON-RPC 方法。"""

    def _get_service():
        svc = getattr(context, "job_hunter_service", None)
        if svc is None:
            raise RuntimeError("job_hunter_service not initialized")
        return svc

    async def profile_get(**kwargs: Any) -> "dict[str, Any]":
        svc = _get_service()
        return {"success": True, "profile": svc.get_profile()}

    async def profile_set(profile: "dict[str, Any] | None" = None, **kwargs: Any) -> "dict[str, Any]":
        if profile is None:
            return {"success": False, "error": "profile required"}
        svc = _get_service()
        return await asyncio.to_thread(svc.set_profile, profile)

    async def profile_extract_from_resume(
        resume_text: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        if not resume_text.strip():
            return {"success": False, "error": "resume_text required"}
        svc = _get_service()
        return await svc.extract_profile_from_resume(resume_text)

    async def resume_upload_and_parse(
        filename: str = "",
        file_data: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        if not filename:
            return {"success": False, "error": "filename required"}
        if not file_data:
            return {"success": False, "error": "file_data required"}
        MAX_RESUME_BASE64_LEN = 14680064
        if len(file_data) > MAX_RESUME_BASE64_LEN:
            return {"success": False, "error": "file too large, max 10MB"}
        try:
            raw = base64.b64decode(file_data)
        except Exception as exc:
            logger.exception("简历文件数据解码失败")
            return {"success": False, "error": f"invalid file_data: {exc}"}
        svc = _get_service()
        return await asyncio.to_thread(svc.upload_and_parse_resume, filename, raw)

    async def resume_diagnose(
        resume_text: str = "",
        target_role: str = "",
        industry: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        if not resume_text.strip():
            return {"success": False, "error": "resume_text required"}
        svc = _get_service()
        return await svc.diagnose_resume(resume_text, target_role, industry)

    async def resume_rewrite(
        resume_text: str = "",
        target_role: str = "",
        industry: str = "",
        style: str = "professional",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        if not resume_text.strip():
            return {"success": False, "error": "resume_text required"}
        svc = _get_service()
        return await svc.rewrite_resume(resume_text, target_role, industry, style)

    async def resume_rewrite_section(
        resume_text: str = "",
        section: str = "",
        target_role: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        if not resume_text.strip():
            return {"success": False, "error": "resume_text required"}
        if not section:
            return {"success": False, "error": "section required"}
        svc = _get_service()
        return await svc.rewrite_resume_section(resume_text, section, target_role)

    async def platforms(**kwargs: Any) -> "dict[str, Any]":
        svc = _get_service()
        return {"success": True, "platforms": svc.list_platforms()}

    async def scrape_run(
        platforms: "list[str] | None" = None,
        keywords: "list[str] | None" = None,
        city: str = "",
        page: int = 1,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        svc = _get_service()
        # P2 风险册：search 可达 15s+，同步等待会让 RPC 普查超时。
        # 改走持久任务队列：入队后立即返回 job_id，客户端用
        # job_hunter.scrape.status(job_id=...) 轮询；实际抓取由 task_queue
        # 的 cron worker 经 services.cron_actions 的 "job_hunter.scrape" 执行。
        q = getattr(context, "task_queue", None)
        if q is not None:
            task_id = await asyncio.to_thread(
                q.enqueue,
                capability_id="job_hunter.scrape",
                payload={
                    "platforms": platforms,
                    "keywords": keywords,
                    "city": city,
                    "page": page,
                },
                queue_name="cron",
                # 抓取失败已在 svc.search 内部按平台重试过一次，队列层不重试
                max_retries=0,
            )
            return {
                "success": True,
                "job_id": task_id,
                "task_id": task_id,
                "status": "queued",
                "queue": "cron",
            }
        # 降级：任务队列不可用时保持旧行为（同步执行 + 事件通知）
        result = await svc.search(platforms, keywords, city, page=page)
        event_bus.emit(AppEvent("job_hunter.scrape.completed", payload=result))
        return result

    async def scrape_status(
        job_id: str = "",
        task_id: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        # 带 job_id/task_id：轮询抓取任务状态（任务队列持久记录）。
        # 不带参数：保留既有契约，返回调度器配置。
        # 审查修复（2026-08-31）：RPC 参数可能被客户端传成 int，直接 .strip()
        # 会 AttributeError→result=null，须先 str() 归一化再判空。
        tid = str(job_id or task_id or "").strip()
        if tid:
            q = getattr(context, "task_queue", None)
            if q is not None:
                rec = await asyncio.to_thread(q.get, tid)
                if rec is None:
                    return {"success": False, "error": "task not found", "job_id": tid}
                d = rec.to_dict()
                # pending 对外呈现为 queued，与 scrape.run 返回的初始态一致
                display_status = {
                    "pending": "queued",
                    "running": "running",
                    "completed": "completed",
                    "failed": "failed",
                    "dead_letter": "failed",
                }.get(d.get("status"), d.get("status"))
                return {
                    "success": True,
                    "job_id": tid,
                    "task_id": tid,
                    "status": display_status,
                    "task": d,
                }
        # P0-2026-08-17: job_hunter_scheduler 模块内仅有模块级 _scheduler 与
        # get_config() 等函数，无 `job_hunter_scheduler` 符号，import 会抛 ImportError。
        from backend import job_hunter_scheduler as _jhs

        return {"success": True, "scheduler": _jhs.get_config()}

    async def jobs_list(
        platform: str = "",
        keyword: str = "",
        min_score: "float | None" = None,
        analyzed_only: bool = False,
        status: str = "",
        limit: int = 50,
        offset: int = 0,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        svc = _get_service()
        filters: dict[str, Any] = {"limit": clamp_limit(limit, default=50), "offset": offset}
        if platform:
            filters["platform"] = platform
        if keyword:
            filters["keyword"] = keyword
        if min_score is not None:
            filters["min_score"] = min_score
        if analyzed_only:
            filters["analyzed_only"] = analyzed_only
        if status:
            filters["status"] = status
        jobs = svc.list_jobs(**filters)
        return {"success": True, "jobs": jobs, "count": len(jobs)}

    async def jobs_get(job_id: str = "", **kwargs: Any) -> "dict[str, Any]":
        if not job_id:
            return {"success": False, "error": "job_id required"}
        svc = _get_service()
        job = svc.get_job(job_id)
        if not job:
            return {"success": False, "error": "job not found"}
        return {"success": True, "job": job}

    async def jobs_set_status(
        job_id: str = "",
        status: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        if not job_id:
            return {"success": False, "error": "job_id required"}
        if not status:
            return {"success": False, "error": "status required"}
        svc = _get_service()
        return await asyncio.to_thread(svc.set_job_status, job_id, status)

    async def jobs_fetch_detail(
        job_id: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        if not job_id:
            return {"success": False, "error": "job_id required"}
        svc = _get_service()
        return await svc.fetch_detail(job_id)

    async def match(
        job_ids: "list[str] | None" = None,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        svc = _get_service()
        return await svc.match_jobs(job_ids=job_ids)

    async def analyze(job_id: str = "", **kwargs: Any) -> "dict[str, Any]":
        if not job_id:
            return {"success": False, "error": "job_id required"}
        svc = _get_service()
        return await svc.analyze_job(job_id)

    async def analyze_stream(
        job_id: str = "",
        send_chunk: "Any" = None,
        **kwargs: Any,
    ) -> "Any":
        """流式职位分析（async generator）。

        D-PG-4（2026-09-17）真实机制：``MethodRegistry.dispatch_stream``
        （backend/method_registry.py:515-533）拿 handler 的返回值直接
        ``await stream.__anext__()``，因此 handler **本身必须是 async generator
        函数**。原实现是普通协程（无 yield，内部 await send_chunk），
        ``__anext__`` 必抛 AttributeError（finally 里 ``stream.aclose()`` 再抛一次）
        → 一条 chunk 都没到客户端，无 job 时用户也看不到"职位不存在"。
        现改为 async generator：每个 chunk 先经 send_chunk 下发（流式链路），
        同时作为迭代值 yield 出去（dispatch_stream 的迭代契约）；
        非流式 dispatch（不带 stream:true）由 registry 的 asyncgen 分支收集为
        chunk 列表返回，不再是空结果。
        """
        if not job_id:
            if send_chunk:
                await send_chunk("job_id required", chunk_type="error")
            yield "job_id required"
            return
        svc = _get_service()
        async for chunk in svc.analyze_job_stream(job_id):
            if send_chunk is not None:
                await send_chunk(chunk)
            yield chunk

    async def recommendations(
        limit: int = 10,
        min_score: float = 60.0,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        svc = _get_service()
        jobs = svc.get_recommendations(clamp_limit(limit, default=10), min_score=min_score)
        return {"success": True, "jobs": jobs}

    async def cold_message_generate(
        job_id: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        if not job_id:
            return {"success": False, "error": "job_id required"}
        svc = _get_service()
        return await svc.generate_cold_message(job_id)

    async def resume_advice(
        job_id: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        if not job_id:
            return {"success": False, "error": "job_id required"}
        svc = _get_service()
        return await svc.get_resume_advice(job_id)

    async def interview_prep(
        job_id: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        if not job_id:
            return {"success": False, "error": "job_id required"}
        svc = _get_service()
        return await svc.get_interview_prep(job_id)

    async def stats(**kwargs: Any) -> "dict[str, Any]":
        svc = _get_service()
        return {"success": True, "stats": svc.get_stats()}

    registry.register("job_hunter.profile.get", profile_get)
    registry.register("job_hunter.profile.set", profile_set)
    registry.register("job_hunter.profile.extract_from_resume", profile_extract_from_resume)
    registry.register("job_hunter.resume.upload_and_parse", resume_upload_and_parse)
    registry.register("job_hunter.resume.diagnose", resume_diagnose)
    registry.register("job_hunter.resume.rewrite", resume_rewrite)
    registry.register("job_hunter.resume.rewrite_section", resume_rewrite_section)
    registry.register("job_hunter.platforms", platforms)
    registry.register("job_hunter.scrape.run", scrape_run)
    registry.register("job_hunter.scrape.status", scrape_status)
    registry.register("job_hunter.jobs.list", jobs_list)
    registry.register("job_hunter.jobs.get", jobs_get)
    registry.register("job_hunter.jobs.set_status", jobs_set_status)
    registry.register("job_hunter.jobs.fetch_detail", jobs_fetch_detail)
    registry.register("job_hunter.match", match)
    registry.register("job_hunter.analyze", analyze)
    registry.register("job_hunter.analyze_stream", analyze_stream)
    registry.register("job_hunter.recommendations", recommendations)
    registry.register("job_hunter.cold_message.generate", cold_message_generate)
    registry.register("job_hunter.resume_advice", resume_advice)
    registry.register("job_hunter.interview_prep", interview_prep)
    registry.register("job_hunter.stats", stats)
