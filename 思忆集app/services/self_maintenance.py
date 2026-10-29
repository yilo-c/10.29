# -*- coding: utf-8 -*-
"""监督式自优化服务（P2-7）。

链路：巡检（cron 每日）→ 生成维护提案 → 用户审批 → 白名单动作执行 →
验证复查 → 报告落收件箱。硬边界：只允许白名单维护动作，任何代码/配置
修改类操作一律不在提案范围（产品铁律：无审查自治自修改为永久红线）。

白名单动作：
- ``purge_completed_queue_tasks``：清理任务队列已完成条目（>1 天）
- ``archive_old_checkpoints``：归档过期 checkpoint（>默认保留天数）
"""
from __future__ import annotations

import logging
import secrets
import time
from typing import Any

logger = logging.getLogger("siyi.services.self_maintenance")

_PROPOSALS_KEY = "self_maintenance_proposals"
_MAX_PROPOSALS = 100

# 提案触发阈值
_PURGE_COMPLETED_THRESHOLD = 20   # 队列已完成条目超过该值才提案清理
_ARCHIVE_INTERVAL_DAYS = 7        # checkpoint 归档提案的最小间隔天数

WHITELISTED_ACTIONS = (
    "purge_completed_queue_tasks",
    "archive_old_checkpoints",
)


class SelfMaintenanceService:
    """巡检生成提案、审批后执行白名单维护动作。"""

    def __init__(self, data_store: Any) -> None:
        self._data_store = data_store

    # ── 提案存取 ──

    def _load_proposals(self) -> list[dict[str, Any]]:
        try:
            raw = self._data_store.load_json(_PROPOSALS_KEY, [])
            return list(raw) if isinstance(raw, list) else []
        except Exception:  # noqa: BLE001
            logger.debug("load proposals failed", exc_info=True)
            return []

    def _save_proposals(self, proposals: list[dict[str, Any]]) -> None:
        try:
            self._data_store.save_json(_PROPOSALS_KEY, proposals[-_MAX_PROPOSALS:])
        except Exception:  # noqa: BLE001
            logger.warning("save proposals failed", exc_info=True)

    # ── 巡检 ──

    def patrol(self, context: Any) -> list[dict[str, Any]]:
        """巡检并生成待审批提案（只生成不执行）。返回新增提案列表。"""
        new_proposals: list[dict[str, Any]] = []
        existing = self._load_proposals()
        open_actions = {
            p["action"] for p in existing if p.get("status") == "pending"
        }

        # 1. 任务队列已完成条目清理
        try:
            task_queue = getattr(context, "task_queue", None)
            if task_queue is not None:
                stats = task_queue.stats()
                completed = int(stats.get("completed", 0))
                if (
                    completed > _PURGE_COMPLETED_THRESHOLD
                    and "purge_completed_queue_tasks" not in open_actions
                ):
                    new_proposals.append(self._make_proposal(
                        action="purge_completed_queue_tasks",
                        reason=f"任务队列有 {completed} 条已完成记录（>1 天可清理）",
                        detail={"completed_count": completed},
                    ))
        except Exception:  # noqa: BLE001
            logger.debug("patrol: task_queue check failed", exc_info=True)

        # 2. checkpoint 定期归档（按间隔节流，避免每天提案）
        try:
            checkpoint_manager = self._get_checkpoint_manager(context)
            if checkpoint_manager is not None:
                last_archive = self._load_last_archive_ts()
                if time.time() - last_archive > _ARCHIVE_INTERVAL_DAYS * 86400 \
                        and "archive_old_checkpoints" not in open_actions:
                    new_proposals.append(self._make_proposal(
                        action="archive_old_checkpoints",
                        reason=f"超过 {_ARCHIVE_INTERVAL_DAYS} 天未归档过期 checkpoint",
                        detail={"last_archive_at": last_archive},
                    ))
        except Exception:  # noqa: BLE001
            logger.debug("patrol: checkpoint check failed", exc_info=True)

        if new_proposals:
            self._save_proposals(existing + new_proposals)
            logger.info("self-maintenance patrol generated %d proposal(s)", len(new_proposals))
        return new_proposals

    def _make_proposal(self, *, action: str, reason: str, detail: dict) -> dict[str, Any]:
        return {
            "proposal_id": secrets.token_hex(6),
            "action": action,
            "reason": reason,
            "detail": detail,
            "status": "pending",
            "created_at": time.time(),
            "result": None,
        }

    @staticmethod
    def _get_checkpoint_manager(context: Any) -> Any | None:
        autonomy = getattr(context, "autonomous_assistant_manager", None)
        task_manager = getattr(autonomy, "task_manager", None) if autonomy else None
        return getattr(task_manager, "_checkpoint_manager", None) if task_manager else None

    def _load_last_archive_ts(self) -> float:
        try:
            proposals = self._load_proposals()
            executed = [
                p for p in proposals
                if p.get("action") == "archive_old_checkpoints"
                and p.get("status") == "executed"
            ]
            if executed:
                return float(max(p.get("executed_at", 0) for p in executed))
        except Exception:  # noqa: BLE001
            pass
        return 0.0

    # ── 审批与执行 ──

    def list_proposals(self, status: str = "") -> list[dict[str, Any]]:
        proposals = self._load_proposals()
        if status:
            proposals = [p for p in proposals if p.get("status") == status]
        return sorted(proposals, key=lambda p: p.get("created_at", 0), reverse=True)

    def approve(self, proposal_id: str, context: Any) -> dict[str, Any]:
        """批准提案：执行白名单动作 + 验证复查。"""
        proposals = self._load_proposals()
        proposal = next((p for p in proposals if p.get("proposal_id") == proposal_id), None)
        if proposal is None:
            return {"success": False, "error": f"proposal {proposal_id} not found"}
        if proposal.get("status") != "pending":
            return {"success": False, "error": f"proposal status is {proposal.get('status')}"}
        action = proposal.get("action", "")
        if action not in WHITELISTED_ACTIONS:
            return {"success": False, "error": f"action {action} not whitelisted"}

        try:
            result = self._execute_action(action, context)
        except Exception as exc:  # noqa: BLE001
            proposal["status"] = "failed"
            proposal["result"] = {"error": str(exc)}
            self._save_proposals(proposals)
            logger.warning("self-maintenance action %s failed: %s", action, exc)
            return {"success": False, "error": str(exc)}

        proposal["status"] = "executed"
        proposal["executed_at"] = time.time()
        proposal["result"] = result
        self._save_proposals(proposals)
        logger.info("self-maintenance action %s executed: %s", action, result)
        return {"success": True, "proposal_id": proposal_id, "action": action, "result": result}

    def reject(self, proposal_id: str) -> dict[str, Any]:
        proposals = self._load_proposals()
        proposal = next((p for p in proposals if p.get("proposal_id") == proposal_id), None)
        if proposal is None:
            return {"success": False, "error": f"proposal {proposal_id} not found"}
        proposal["status"] = "rejected"
        self._save_proposals(proposals)
        return {"success": True, "proposal_id": proposal_id}

    def _execute_action(self, action: str, context: Any) -> dict[str, Any]:
        """执行白名单动作并做验证复查。"""
        if action == "purge_completed_queue_tasks":
            task_queue = getattr(context, "task_queue", None)
            if task_queue is None:
                raise RuntimeError("task_queue unavailable")
            purged = task_queue.purge_completed(older_than_seconds=86400.0)
            after = task_queue.stats()
            return {"purged": int(purged), "completed_after": int(after.get("completed", 0))}

        if action == "archive_old_checkpoints":
            checkpoint_manager = self._get_checkpoint_manager(context)
            if checkpoint_manager is None:
                raise RuntimeError("checkpoint manager unavailable")
            archived = checkpoint_manager.archive_old_checkpoints()
            return {"archived": int(archived)}

        raise RuntimeError(f"unknown action: {action}")


_service_instance: SelfMaintenanceService | None = None


def get_self_maintenance_service(data_store: Any) -> SelfMaintenanceService:
    """全局单例（按首个 data_store 创建，与 sandbox_manager 同模式）。"""
    global _service_instance
    if _service_instance is None:
        _service_instance = SelfMaintenanceService(data_store)
    return _service_instance
