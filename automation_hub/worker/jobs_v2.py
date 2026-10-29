"""Worker V2：统一工具执行入口。

集成策略检查、审批验证、执行器调度、审计日志等完整流程。
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from automation_hub.api.db import get_db_connection
from automation_hub.api.tools.registry import get_tool
from automation_hub.worker.policy_enforce import is_run_approved
from automation_hub.worker.executors.host import HostExecutor
from automation_hub.worker.executors.docker import DockerExecutor
from automation_hub.api.audit.service import log_event
from jsonschema import validate as js_validate
from jsonschema.exceptions import ValidationError


logger = logging.getLogger(__name__)


def now_iso() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


def run_tool_job(run_id: str, tool_id: str, args: dict, user_id: str | None = None) -> None:
    """执行工具任务（RQ Job 入口）。
    
    完整流程：
    1. 检查审批状态
    2. 加载工具配置
    3. 选择执行器
    4. 执行工具
    5. 记录审计日志
    
    Args:
        run_id: 运行 ID
        tool_id: 工具 ID
        args: 工具参数
        user_id: 用户 ID（用于审计）
    """
    logger.info(f"Starting run {run_id} for tool {tool_id}")
    execution_started_at = datetime.now(timezone.utc)

    # Normalize args to avoid None edge-cases
    args = args or {}
    
    # 1. 检查审批状态（通常应在 API 侧通过审批后再入队；这里做兜底）
    if not is_run_approved(run_id):
        logger.warning(f"Run {run_id} not approved, skipping execution")
        with get_db_connection() as conn:
            # 如果审批被拒绝/未批准，保持 pending_approval/denied 由审批表决定
            row = conn.execute(
                """SELECT status FROM approval_requests
                   WHERE resource_type='run' AND resource_id=?
                   ORDER BY created_at DESC
                   LIMIT 1""",
                (run_id,),
            ).fetchone()
            approval_status = row["status"] if row else None
            new_status = "denied" if approval_status in {"denied", "expired", "cancelled"} else "pending_approval"
            conn.execute("UPDATE tool_runs SET status=? WHERE id=?", (new_status, run_id))
            conn.commit()

        log_event(
            event_type="run.blocked",
            action="execute",
            status="fail",
            actor_user_id=user_id,
            resource_type="run",
            resource_id=run_id,
            message="Run blocked: not approved",
            meta={"tool_id": tool_id, "approval_status": approval_status},
        )
        return
    
    # 2. 加载工具配置
    tool = get_tool(tool_id)
    if not tool:
        logger.error(f"Tool {tool_id} not found")
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE tool_runs SET status='failed', finished_at=? WHERE id=?",
                (now_iso(), run_id),
            )
            conn.commit()
        return
    
    # 3. 获取运行信息
    with get_db_connection() as conn:
        run = conn.execute(
            "SELECT * FROM tool_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        
        if not run:
            logger.error(f"Run {run_id} not found")
            return
        
        # 更新状态为运行中
        started_at_iso = execution_started_at.isoformat()
        conn.execute(
            "UPDATE tool_runs SET status='running', started_at=? WHERE id=?",
            (started_at_iso, run_id),
        )
        conn.commit()
        
        run = dict(run)
    
    # 4. 准备执行环境
    command = json.loads(tool["command_json"])
    cwd = tool.get("cwd")
    timeout = int(tool.get("timeout_sec") or 120)

    # 4.1 参数强校验（args_schema）
    try:
        schema_raw = tool.get("args_schema_json")
        if schema_raw:
            schema = json.loads(schema_raw)
            js_validate(instance=args or {}, schema=schema)
    except ValidationError as e:
        msg = f"args_schema 校验失败: {e.message}"
        logger.warning(msg)
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE tool_runs SET status='failed', finished_at=?, error_msg=? WHERE id=?",
                (now_iso(), msg, run_id),
            )
            conn.commit()
        log_event(
            event_type="run.invalid_args",
            action="execute",
            status="fail",
            actor_user_id=user_id,
            resource_type="run",
            resource_id=run_id,
            message=msg,
            meta={"tool_id": tool_id},
        )
        return
    except Exception as e:
        msg = f"args_schema 解析/校验异常: {str(e)}"
        logger.exception(msg)
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE tool_runs SET status='failed', finished_at=?, error_msg=? WHERE id=?",
                (now_iso(), msg, run_id),
            )
            conn.commit()
        return

    # 环境变量（后续执行器与安全检查都会使用）
    env = os.environ.copy()
    env["WORKSPACE"] = env.get("WORKSPACE") or "/workspace"
    env["DATA_DIR"] = env.get("DATA_DIR") or "/data"
    env["RUN_ID"] = run_id
    env["TOOL_ID"] = tool_id

    # 为工具参数创建环境变量
    for key, value in args.items():
        env[f"ARG_{key.upper()}"] = str(value)

    # 4.2 allowed_paths 白名单检查（尽量保守：只检查常见路径参数）
    def _extract_paths(a: dict) -> list[str]:
        keys = {"path", "file", "files", "dir", "directory", "cwd", "root", "source", "destination", "target"}
        out: list[str] = []
        for k, v in (a or {}).items():
            if k not in keys:
                continue
            if isinstance(v, str) and v.strip():
                out.append(v)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and item.strip():
                        out.append(item)
        return out

    def _is_allowed(raw: str, allowed_prefixes: list[Path], base: Path) -> bool:
        if not allowed_prefixes:
            return True
        candidate = Path(raw)
        try:
            if not candidate.is_absolute():
                candidate = (base / candidate)
            candidate = candidate.resolve(strict=False)
        except Exception:
            return False

        for pref in allowed_prefixes:
            try:
                p = pref
                if str(p) == ".":
                    p = base
                if not p.is_absolute():
                    p = (base / p)
                p = p.resolve(strict=False)
                if candidate == p or p in candidate.parents:
                    return True
            except Exception:
                continue
        return False

    try:
        allowed_raw = tool.get("allowed_paths_json") or "[]"
        allowed_list = json.loads(allowed_raw)
        if not isinstance(allowed_list, list):
            allowed_list = []
    except Exception:
        allowed_list = []

    base_dir = Path(env.get("WORKSPACE") or os.getcwd())
    allowed_prefixes = [Path(str(x)) for x in allowed_list if isinstance(x, str) and x.strip()]
    for pv in _extract_paths(args or {}):
        if not _is_allowed(pv, allowed_prefixes, base_dir):
            msg = f"路径不在 allowed_paths 白名单内: {pv}"
            logger.warning(msg)
            with get_db_connection() as conn:
                conn.execute(
                    "UPDATE tool_runs SET status='failed', finished_at=?, error_msg=? WHERE id=?",
                    (now_iso(), msg, run_id),
                )
                conn.commit()
            log_event(
                event_type="run.path_blocked",
                action="execute",
                status="fail",
                actor_user_id=user_id,
                resource_type="run",
                resource_id=run_id,
                message=msg,
                meta={"tool_id": tool_id, "allowed_paths": allowed_list},
            )
            return
    
    # env 已在上方准备
    
    # 5. 选择执行器
    executor_name = tool.get("executor", "docker")
    if executor_name == "host":
        executor = HostExecutor()
    else:
        # 默认使用 Docker
        executor = DockerExecutor()
    
    logger.info(f"Executing with {executor_name} executor")
    
    # 6. 执行工具
    try:
        exit_code = executor.run(
            command=command,
            cwd=cwd,
            env=env,
            timeout=timeout,
            stdout_path=run["stdout_path"],
            stderr_path=run["stderr_path"]
        )

        finished_at = datetime.now(timezone.utc)
        duration_sec = round((finished_at - execution_started_at).total_seconds(), 3)
        finished_at_iso = finished_at.isoformat()
        
        status = "succeeded" if exit_code == 0 else "failed"
        logger.info(f"Run {run_id} finished with status={status}, exit_code={exit_code}")
        
        # 更新运行状态
        with get_db_connection() as conn:
            conn.execute(
                """UPDATE tool_runs
                   SET status=?, finished_at=?, exit_code=?
                   WHERE id=?""",
                (status, finished_at_iso, exit_code, run_id),
            )
            conn.commit()
        
        # 记录审计
        log_event(
            event_type="run.executed",
            action="execute",
            status="success" if exit_code == 0 else "fail",
            actor_user_id=user_id,
            resource_type="run",
            resource_id=run_id,
            message=f"Tool executed with exit code {exit_code}",
            meta={
                "tool_id": tool_id,
                "exit_code": exit_code,
                "duration_sec": duration_sec,
            }
        )
        
    except Exception as e:
        logger.exception(f"Run {run_id} failed with exception")
        failed_at = datetime.now(timezone.utc)
        duration_sec = round((failed_at - execution_started_at).total_seconds(), 3)

        # 更新状态为失败
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE tool_runs SET status='failed', finished_at=? WHERE id=?",
                (failed_at.isoformat(), run_id),
            )
            conn.commit()

        # 记录审计
        log_event(
            event_type="run.failed",
            action="execute",
            status="fail",
            actor_user_id=user_id,
            resource_type="run",
            resource_id=run_id,
            message=f"Tool execution failed: {str(e)}",
            meta={"tool_id": tool_id, "error": str(e), "duration_sec": duration_sec}
        )


# ---- ARQ async wrapper ----


async def arq_run_tool_job(ctx: dict, run_id: str, tool_id: str, args: dict,
                           user_id: str | None = None) -> None:
    """ARQ 异步任务包装器：在线程池中调用 ``run_tool_job``。"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, run_tool_job, run_id, tool_id, args, user_id,
    )


# ---- P0-4 失败回收看门狗 ----

# running 状态超时阈值（秒）。超过此时间仍为 running 的 run 强制置为 failed。
# 30 分钟 = 1800 秒，覆盖大多数工具执行场景；可通过环境变量 RUN_TIMEOUT_OVERRIDE 覆盖。
DEFAULT_RUN_TIMEOUT_SECONDS = 1800


def reap_stale_runs(timeout_seconds: int | None = None) -> dict:
    """回收超时的 running 状态 run（P0-4 看门狗）。

    扫描 ``tool_runs`` 表中 status='running' 且 started_at 早于 ``timeout_seconds``
    前的记录，将其强制重置为 'failed' 并记录 error_msg。

    Args:
        timeout_seconds: 超时阈值（秒），默认 1800（30 分钟）。

    Returns:
        {"reaped": int, "threshold_seconds": int} 回收的 run 数量 + 阈值。
    """
    if timeout_seconds is None:
        timeout_seconds = DEFAULT_RUN_TIMEOUT_SECONDS

    from datetime import timedelta
    threshold = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
    threshold_iso = threshold.isoformat()
    reaped_ids: list[str] = []

    with get_db_connection() as conn:
        # 查找超时的 running run
        stale_rows = conn.execute(
            """SELECT id, started_at FROM tool_runs
               WHERE status='running' AND started_at IS NOT NULL
                 AND started_at < ?""",
            (threshold_iso,),
        ).fetchall()

        if not stale_rows:
            return {"reaped": 0, "threshold_seconds": timeout_seconds}

        for row in stale_rows:
            run_id = row["id"]
            error_msg = f"watchdog reaped: running timeout (>{timeout_seconds}s, started_at={row['started_at']})"
            conn.execute(
                "UPDATE tool_runs SET status='failed', finished_at=?, error_msg=? WHERE id=?",
                (now_iso(), error_msg, run_id),
            )
            reaped_ids.append(run_id)
            logger.warning("reaped stale run %s (started_at=%s, exceeded %ds timeout)",
                           run_id, row["started_at"], timeout_seconds)

        conn.commit()

    # 记录审计日志（批量）
    for rid in reaped_ids:
        try:
            log_event(
                event_type="run.reaped",
                action="watchdog",
                status="fail",
                resource_type="run",
                resource_id=rid,
                message=f"Run reaped by watchdog: running exceeded {timeout_seconds}s timeout",
                meta={"timeout_seconds": timeout_seconds},
            )
        except Exception:
            logger.debug("audit log for reaped run %s failed", rid, exc_info=True)

    logger.info("watchdog reaped %d stale run(s) (threshold=%ds)", len(reaped_ids), timeout_seconds)
    return {"reaped": len(reaped_ids), "threshold_seconds": timeout_seconds, "run_ids": reaped_ids}


async def arq_reap_stale_runs(ctx: dict, timeout_seconds: int | None = None) -> dict:
    """ARQ 异步包装器：在线程池中调用 ``reap_stale_runs``。"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, reap_stale_runs, timeout_seconds,
    )
