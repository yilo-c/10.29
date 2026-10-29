"""求职猎手职位数据持久化与去重。"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .scrapers.base import JobDetail, JobListing

logger = logging.getLogger(__name__)

DEDUP_URL_KEY = "url"
DEDUP_TITLE_SIMILARITY = 0.9
APPLICATION_STATUSES = ["none", "applied", "interviewing", "offered", "rejected", "skipped"]
DEFAULT_STATUS = "none"


class JobStorage:
    """基于 JSON 文件的职位存储（数据量小，无需数据库）。"""

    def __init__(self, data_dir: str | None = None) -> None:
        self.data_dir = data_dir or self._default_data_dir()
        os.makedirs(self.data_dir, exist_ok=True)
        self.jobs_path = os.path.join(self.data_dir, "jobs.json")
        self.details_path = os.path.join(self.data_dir, "details.json")
        self._jobs: list[dict[str, Any]] = []
        self._details: dict[str, Any] = {}
        self._load()

    @staticmethod
    def _default_data_dir() -> str:
        base = os.environ.get("SIYI_DATA_DIR")
        if base:
            return os.path.join(base, "job_hunter")
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data",
            "job_hunter",
        )

    def _load(self) -> None:
        if os.path.exists(self.jobs_path):
            try:
                with open(self.jobs_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._jobs = data
                    for job in self._jobs:
                        if "application_status" not in job:
                            job["application_status"] = DEFAULT_STATUS
            except json.JSONDecodeError as exc:
                logger.warning("jobs.json parse failed: %s", exc)
            except Exception as exc:
                logger.warning("load jobs failed: %s", exc)
        if os.path.exists(self.details_path):
            try:
                with open(self.details_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._details = data
            except json.JSONDecodeError as exc:
                logger.warning("details.json parse failed: %s", exc)
            except Exception as exc:
                logger.warning("load details failed: %s", exc)

    def _save(self) -> None:
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            self._atomic_write(self.jobs_path, self._jobs)
            self._atomic_write(self.details_path, self._details)
        except Exception as exc:
            logger.warning("save job hunter data failed: %s", exc)

    @staticmethod
    def _atomic_write(path: str, data: Any) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    @staticmethod
    def _normalize_url(url: str) -> str:
        url = (url or "").strip()
        # 去掉常见的追踪参数，避免同一职位因来源不同被判为新职位
        url = re.sub(r"[?&](trk|source|spm|from)=[^&]*", "", url)
        return url.rstrip("/")

    @staticmethod
    def _title_similarity(a: str, b: str) -> float:
        a_clean = re.sub(r"[^\w\u4e00-\u9fff]", "", (a or "").lower())
        b_clean = re.sub(r"[^\w\u4e00-\u9fff]", "", (b or "").lower())
        if not a_clean or not b_clean:
            return 0.0
        shorter = min(len(a_clean), len(b_clean))
        if shorter < 6:
            return 0.0
        return SequenceMatcher(None, a_clean, b_clean).ratio()

    def _is_duplicate(self, job: dict[str, Any]) -> bool:
        url = self._normalize_url(job.get(DEDUP_URL_KEY, ""))
        title = job.get("title", "")
        company = job.get("company", "")
        for existing in self._jobs:
            if url and self._normalize_url(existing.get(DEDUP_URL_KEY, "")) == url:
                return True
            if self._title_similarity(title, existing.get("title", "")) > DEDUP_TITLE_SIMILARITY:
                if company == existing.get("company", ""):
                    return True
        return False

    def add_listings(self, listings: list["JobListing"]) -> tuple[list[dict[str, Any]], int]:
        """添加职位列表，返回 (新增条目, 重复数量)。"""
        added: list[dict[str, Any]] = []
        dup_count = 0
        now = datetime.now(timezone.utc).isoformat()
        for listing in listings:
            payload = listing.to_dict()
            payload["first_seen_at"] = now
            payload["updated_at"] = now
            payload["match_score"] = 0.0
            payload["analyzed"] = False
            payload["application_status"] = DEFAULT_STATUS
            if self._is_duplicate(payload):
                dup_count += 1
                continue
            self._jobs.append(payload)
            added.append(payload)
        if added:
            self._save()
        return added, dup_count

    def save_detail(self, detail: "JobDetail") -> None:
        detail_dict = detail.to_dict()
        detail_dict["fetched_at"] = datetime.now(timezone.utc).isoformat()
        self._details[detail.id] = detail_dict
        # 详情回填到列表条目，便于列表页直接展示
        for job in self._jobs:
            if job.get("id") == detail.id:
                job["description"] = detail.description
                job["requirements"] = detail.requirements
                job["benefits"] = detail.benefits
                job["company_size"] = detail.company_size
                job["company_stage"] = detail.company_stage
                job["updated_at"] = detail_dict["fetched_at"]
                break
        self._save()

    def list_jobs(
        self,
        platform: str | None = None,
        keyword: str | None = None,
        min_score: float | None = None,
        analyzed_only: bool = False,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        jobs = list(self._jobs)
        jobs.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        if platform:
            jobs = [j for j in jobs if j.get("platform") == platform]
        if keyword:
            kw = str(keyword).lower()
            jobs = [
                j
                for j in jobs
                if kw in (j.get("title") or "").lower()
                or kw in (j.get("company") or "").lower()
                or kw in (j.get("summary") or "").lower()
                or any(kw in str(t).lower() for t in (j.get("tags") or []))
            ]
        if min_score is not None:
            jobs = [j for j in jobs if float(j.get("match_score", 0) or 0) >= min_score]
        if analyzed_only:
            jobs = [j for j in jobs if j.get("analyzed")]
        if status:
            jobs = [j for j in jobs if j.get("application_status", DEFAULT_STATUS) == status]
        return jobs[offset : offset + limit]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        for job in self._jobs:
            if job.get("id") == job_id:
                result = dict(job)
                result["detail"] = self._details.get(job_id)
                return result
        return None

    def update_job(self, job_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        for job in self._jobs:
            if job.get("id") == job_id:
                job.update(fields)
                job["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._save()
                return dict(job)
        return None

    def batch_update_jobs(self, updates: dict[str, dict[str, Any]]) -> int:
        """批量更新职位字段，只持久化一次。"""
        updated = 0
        now = datetime.now(timezone.utc).isoformat()
        for job in self._jobs:
            job_id = job.get("id")
            if job_id in updates:
                job.update(updates[job_id])
                job["updated_at"] = now
                updated += 1
        if updated:
            self._save()
        return updated

    def set_application_status(self, job_id: str, status: str) -> dict[str, Any] | None:
        """更新职位投递状态，返回更新后的职位。"""
        if status not in APPLICATION_STATUSES:
            return None
        for job in self._jobs:
            if job.get("id") == job_id:
                job["application_status"] = status
                job["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._save()
                return dict(job)
        return None

    def list_by_status(self, status: str, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """按投递状态筛选职位。"""
        jobs = [j for j in self._jobs if j.get("application_status", DEFAULT_STATUS) == status]
        jobs.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return jobs[offset : offset + limit]

    def application_stats(self) -> dict[str, Any]:
        """投递漏斗统计。"""
        counts = {s: 0 for s in APPLICATION_STATUSES}
        for job in self._jobs:
            counts[job.get("application_status", DEFAULT_STATUS)] = (
                counts.get(job.get("application_status", DEFAULT_STATUS), 0) + 1
            )
        total = len(self._jobs)
        return {
            "counts": counts,
            "funnel": {
                "total": total,
                "applied": counts.get("applied", 0),
                "interviewing": counts.get("interviewing", 0),
                "offered": counts.get("offered", 0),
            },
            "conversion": {
                "applied_rate": round(counts.get("applied", 0) / total, 3) if total else 0.0,
                "interview_rate": round(counts.get("interviewing", 0) / total, 3) if total else 0.0,
                "offer_rate": round(counts.get("offered", 0) / total, 3) if total else 0.0,
            },
        }

    def get_stats(self) -> dict[str, Any]:
        platforms: dict[str, int] = {}
        for job in self._jobs:
            platforms[job.get("platform", "unknown")] = (
                platforms.get(job.get("platform", "unknown"), 0) + 1
            )
        analyzed = sum(1 for j in self._jobs if j.get("analyzed"))
        matched = sum(1 for j in self._jobs if float(j.get("match_score", 0) or 0) > 0)
        avg_score = 0.0
        if matched:
            avg_score = round(
                sum(float(j.get("match_score", 0) or 0) for j in self._jobs) / matched, 1
            )
        return {
            "total": len(self._jobs),
            "analyzed": analyzed,
            "matched": matched,
            "avg_match_score": avg_score,
            "platforms": platforms,
            **self.application_stats(),
        }
