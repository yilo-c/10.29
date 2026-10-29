"""Evolution (受控进化) JSON-RPC handlers — 技术雷达扫描 / 沙箱验证 / 升级应用。

对应 complete-architecture-design.md 第 6 章 P4.1 受控进化流程：
- ``evolution.scan_and_report``：触发雷达扫描 + 生成早报板块
- ``evolution.last_briefing``：返回最近一次早报
- ``evolution.confirm_upgrade``：用户确认升级 → 进入沙箱
- ``evolution.final_apply``：沙箱通过后用户最终确认 → 应用升级
- ``evolution.skip_upgrade``：跳过某项
- ``evolution.list_results`` / ``evolution.get_result``：查询升级结果
- ``evolution.status``：整体状态快照（雷达 + 沙箱 + 编排器）
- ``evolution.is_redline``：检查名称是否触及安全红线
- ``evolution.sandbox_status`` / ``evolution.sandbox_recent``：沙箱运行状态

@deprecated (S11, 2026-08-07): 本模块全部 10 个注册方法均为僵尸接口——
后端已实现但前端从未调用（前端无 EvolutionModule 组件，仅文档提及）。
列入 UNUSED_METHODS_BASELINE (tests/test_jsonrpc_contract.py)。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.handlers._async_rpc import POLLER_SCHEMA
from backend.handlers._async_rpc import poll_job_result as _poll_job_result

logger = logging.getLogger("siyi.backend.handlers.evolution")


def _get_orchestrator(context):
    """从 AppContext 获取 EvolutionOrchestrator；不存在则返回 None。"""
    return getattr(context, "evolution_orchestrator", None)


def run_evolution_scan_and_report(context: Any) -> dict[str, Any]:
    """雷达扫描 + 早报生成的真实现 —— cron worker 与 RPC 降级共用（S3-2）。

    同步阻塞函数（内部 orch.scan_and_report 是同步的，含 LLM 调用）。
    """
    orch = _get_orchestrator(context)
    if orch is None:
        return {"error": "evolution_orchestrator unavailable"}
    try:
        briefing = orch.scan_and_report()
        return briefing.to_dict()
    except Exception as exc:
        logger.exception("evolution.scan_and_report 失败: %s", exc)
        return {"error": f"scan failed: {exc}"}


def register_evolution(registry, context) -> None:
    """注册 evolution.* JSON-RPC 方法。"""

    # ── evolution.scan_and_report ──
    async def scan_and_report(**kwargs: Any) -> dict[str, Any]:
        """触发一次技术雷达扫描并生成早报板块（同步返回终局结果）。

        S3-2 回退（2026-09-16 架构复审，audit §1）：入队信封版曾于本入口
        秒回 job_id，但前端 ManagementModule.tsx:121 按同步形状消费
        （res?.error 判成败、随后立即 reload），导致「入队瞬间假报扫描完成、
        列表恒为旧数据」。消费面前置条件不成立 → 回退为同步直调真实现。
        异步骨架（cron_actions worker、.status 轮询器、completed 事件）**保留**，
        待 S3-2b 先建前端轮询消费层后再翻转入口。
        """
        return await asyncio.to_thread(run_evolution_scan_and_report, context)

    async def scan_and_report_status(
        job_id: str = "",
        task_id: str = "",
        wait_seconds: float = 0.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """查询 evolution.scan_and_report 异步任务的状态与结果。"""
        return await _poll_job_result(
            context,
            job_id=job_id,
            task_id=task_id,
            capability_id="evolution.scan_and_report",
            wait_seconds=wait_seconds,
        )

    registry.register("evolution.scan_and_report", scan_and_report)
    registry.register_with_schema("evolution.scan_and_report.status", scan_and_report_status, schema=POLLER_SCHEMA)

    # ── evolution.last_briefing ──
    async def last_briefing(**kwargs: Any) -> dict[str, Any]:
        """获取最近一次早报板块（未扫描过则返回 null briefing）。"""
        orch = _get_orchestrator(context)
        if orch is None:
            return {"error": "evolution_orchestrator unavailable"}
        section = orch.get_last_briefing()
        if section is None:
            return {"briefing": None}
        return {"briefing": section.to_dict()}

    registry.register("evolution.last_briefing", last_briefing)

    # ── evolution.confirm_upgrade ──
    async def confirm_upgrade(name: str = "", **kwargs: Any) -> dict[str, Any]:
        """用户确认升级某项 → 进入沙箱验证（同步阻塞，最多 ~25s）。"""
        if not name:
            return {"error": "name required"}
        orch = _get_orchestrator(context)
        if orch is None:
            return {"error": "evolution_orchestrator unavailable"}
        try:
            result = await asyncio.to_thread(orch.confirm_upgrade, name)
        except Exception as exc:
            logger.exception("evolution.confirm_upgrade(%s) 失败: %s", name, exc)
            return {"error": f"confirm failed: {exc}"}
        if result is None:
            return {"error": f"no upgrade item: {name}"}
        return result.to_dict()

    registry.register("evolution.confirm_upgrade", confirm_upgrade)

    # ── evolution.final_apply ──
    async def final_apply(name: str = "", **kwargs: Any) -> dict[str, Any]:
        """沙箱通过后的最终升级动作。"""
        if not name:
            return {"error": "name required"}
        orch = _get_orchestrator(context)
        if orch is None:
            return {"error": "evolution_orchestrator unavailable"}
        try:
            result = await asyncio.to_thread(orch.final_apply, name)
        except Exception as exc:
            logger.exception("evolution.final_apply(%s) 失败: %s", name, exc)
            return {"error": f"apply failed: {exc}"}
        if result is None:
            return {"error": f"no upgrade item: {name}"}
        return result.to_dict()

    registry.register("evolution.final_apply", final_apply)

    # ── evolution.skip_upgrade ──
    async def skip_upgrade(name: str = "", **kwargs: Any) -> dict[str, Any]:
        """跳过某项升级。"""
        if not name:
            return {"error": "name required"}
        orch = _get_orchestrator(context)
        if orch is None:
            return {"error": "evolution_orchestrator unavailable"}
        result = orch.skip_upgrade(name)
        if result is None:
            return {"error": f"no upgrade item: {name}"}
        return result.to_dict()

    registry.register("evolution.skip_upgrade", skip_upgrade)

    # ── evolution.list_results ──
    async def list_results(limit: int = 10, **kwargs: Any) -> list[dict[str, Any]]:
        """列出最近若干次升级结果。"""
        orch = _get_orchestrator(context)
        if orch is None:
            return []
        try:
            lim = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            lim = 10
        return [r.to_dict() for r in orch.get_recent_results(limit=lim)]

    registry.register("evolution.list_results", list_results)

    # ── evolution.get_result ──
    async def get_result(name: str = "", **kwargs: Any) -> dict[str, Any]:
        """获取单项升级结果。"""
        if not name:
            return {"error": "name required"}
        orch = _get_orchestrator(context)
        if orch is None:
            return {"error": "evolution_orchestrator unavailable"}
        result = orch.get_result(name)
        if result is None:
            return {"error": f"no upgrade item: {name}"}
        return result.to_dict()

    registry.register("evolution.get_result", get_result)

    # ── evolution.status ──
    async def status(**kwargs: Any) -> dict[str, Any]:
        """整体状态快照（编排器 + 沙箱）。"""
        orch = _get_orchestrator(context)
        if orch is None:
            return {"available": False, "error": "evolution_orchestrator unavailable"}
        snap = orch.get_status_snapshot()
        # 附带沙箱执行统计（如果可拿）
        try:
            sandbox = getattr(orch, "_sandbox", None)
            if sandbox is not None and hasattr(sandbox, "get_status_snapshot"):
                snap["sandbox"] = sandbox.get_status_snapshot()
        except Exception:
            logger.exception("获取沙箱执行统计失败")
            pass
        snap["available"] = True
        return snap

    registry.register("evolution.status", status)

    # ── evolution.is_redline ──
    async def is_redline(name: str = "", **kwargs: Any) -> dict[str, Any]:
        """检查名称是否触及安全红线（用于前端按钮禁用提示）。"""
        if not name:
            return {"redline": False, "name": name}
        orch = _get_orchestrator(context)
        if orch is None:
            return {"redline": False, "name": name, "error": "evolution_orchestrator unavailable"}
        return {"redline": bool(orch.is_redline_item(name)), "name": name}

    registry.register("evolution.is_redline", is_redline)

    # ── evolution.sandbox_recent ──
    async def sandbox_recent(limit: int = 5, **kwargs: Any) -> list[dict[str, Any]]:
        """沙箱最近 N 次运行记录。"""
        orch = _get_orchestrator(context)
        if orch is None:
            return []
        sandbox = getattr(orch, "_sandbox", None)
        if sandbox is None:
            return []
        try:
            lim = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            lim = 5
        return [run.to_dict() for run in sandbox.get_recent_runs(limit=lim)]

    registry.register("evolution.sandbox_recent", sandbox_recent)
