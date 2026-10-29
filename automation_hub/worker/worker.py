"""模块说明：ARQ Worker 启动入口。

使用方式：
    python -m worker.worker
"""

from __future__ import annotations

import asyncio
import logging

from automation_hub.api.config import settings
from automation_hub.worker.arq_settings import build_worker_kwargs, close_redis_pool
from automation_hub.worker.executors.docker import _preflight

logger = logging.getLogger("automation_hub.worker")


async def main() -> None:
    """启动 ARQ Worker。"""
    logging.basicConfig(level=logging.INFO)

    # 生产建议：禁用 host fallback 时，worker 启动即自检 docker CLI/socket/image
    if not settings.DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK:
        ok, msg = _preflight(settings.DOCKER_EXECUTOR_IMAGE)
        if not ok:
            logger.error("DockerExecutor preflight failed: %s", msg)
            raise SystemExit(1)

    queue_names = [name.strip() for name in settings.QUEUE_NAME.split(",") if name.strip()]
    logger.info("Starting ARQ worker for queues: %s", ", ".join(queue_names))

    from arq import Worker

    worker_kwargs = build_worker_kwargs()
    worker = Worker(**worker_kwargs)
    try:
        await worker.run()
    finally:
        await close_redis_pool()


if __name__ == "__main__":
    asyncio.run(main())
