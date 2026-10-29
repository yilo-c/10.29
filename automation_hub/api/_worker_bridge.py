"""Worker 依赖软桥接（阶段二 W2 收口）。

背景
----
Hub 的 API 路由（approvals / proposals / runs / tool_runs / metrics /
agent）原本在**模块顶层**直接 ``import automation_hub.worker.*``。而
``worker.arq_settings`` 顶层又 import ``arq`` + ``redis``，worker 链还反向
依赖 ``worker.jobs_v2``。结果是：只要 Redis/arq/worker 链任一环节缺失，
``import api.main`` 立即 ``ImportError``，Hub 无法独立启动，ADR 中判定可删的
``deploy/intel/repos`` 也因此无法干净裁掉。

这里把"顶层硬 import"改为"调用时延迟 import"。worker 本体**暂不删除**（它
仍撑着审批/提议的 ARQ 入队核心），但 API 层不再在导入期与之耦合：
- Hub 在没有 Redis 的纯 API / 契约测试环境下也能启动；
- 待删的 deploy/intel/repos 与 worker 解耦后，可安全裁掉而不牵连 api.main。

语义：worker 在场时一切照旧；不在场（ImportError）时抛出清晰异常，而非静默
返回 None，避免掩盖部署缺失。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("automation_hub.worker_bridge")


class WorkerUnavailableError(RuntimeError):
    """worker 依赖（Redis/arq/worker 链）在当前环境不可用时抛出。"""


def _import(modpath: str, symbol: str):
    try:
        import importlib

        module = importlib.import_module(modpath)
        return getattr(module, symbol)
    except Exception as exc:  # pragma: no cover - 依赖环境差异
        raise WorkerUnavailableError(
            f"worker 依赖不可用，无法加载 {modpath}:{symbol} —— {exc!r}"
        ) from exc


# ---- arq_settings 桥接 ----
def enqueue_job_sync(*args, **kwargs):
    return _import("automation_hub.worker.arq_settings", "enqueue_job_sync")(*args, **kwargs)


async def enqueue_job(*args, **kwargs):
    return await _import("automation_hub.worker.arq_settings", "enqueue_job")(*args, **kwargs)


def arq_queue_length() -> int:
    return _import("automation_hub.worker.arq_settings", "queue_length")()


# ---- jobs_v2 桥接 ----
def run_tool_job(*args, **kwargs):
    return _import("automation_hub.worker.jobs_v2", "run_tool_job")(*args, **kwargs)


def is_proposal_approved(*args, **kwargs):
    return _import("automation_hub.worker.policy_enforce", "is_proposal_approved")(*args, **kwargs)


# ---- jobs 桥接 ----
def execute_script_job(*args, **kwargs):
    return _import("automation_hub.worker.jobs", "execute_script_job")(*args, **kwargs)


def load_scripts_manifest(*args, **kwargs):
    return _import("automation_hub.worker.jobs", "load_scripts_manifest")(*args, **kwargs)
