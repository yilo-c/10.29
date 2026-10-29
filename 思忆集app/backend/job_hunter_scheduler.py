"""求职猎手调度器模块级单例（向后兼容）。"""
from __future__ import annotations

from services.job_hunter.scheduler import JobHunterScheduler

_scheduler = JobHunterScheduler()

set_service = _scheduler.set_service
configure = _scheduler.configure
restart = _scheduler.restart
cancel = _scheduler.cancel
get_config = _scheduler.get_config
