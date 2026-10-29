"""执行历史：记录完整执行历史，支持重放和回滚。

参考 n8n 执行历史设计：
- 每次执行生成完整的执行记录
- 记录谁触发的、每一步做了什么、结果如何
- 支持查看历史执行过程（重放）
- 对可逆操作提供一键回滚
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .transaction import SnapshotManager

logger = logging.getLogger("siyi.assistant.execution_history")


class ExecutionRecord:
    """单次执行的完整记录。"""

    def __init__(
        self,
        *,
        task_id: str,
        goal: str,
        source: str,
        triggered_by: str = "user",
        plan_id: str | None = None,
        plan_summary: dict[str, Any] | None = None,
    ):
        self.record_id = uuid.uuid4().hex[:12]
        self.task_id = task_id
        self.goal = goal
        self.source = source
        self.triggered_by = triggered_by
        self.plan_id = plan_id
        self.plan_summary = dict(plan_summary or {})
        self.started_at: str | None = None
        self.completed_at: str | None = None
        self.status: str = "pending"  # pending / running / completed / failed / cancelled
        self.steps: list[dict[str, Any]] = []
        self.summary: dict[str, Any] = {}
        self.file_snapshots: dict[str, Any] = {}  # {file_path: snapshot_data}
        self.reversible: bool = False  # 是否支持回滚

    def start(self) -> None:
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.status = "running"

    def complete(self, summary: dict[str, Any]) -> None:
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.status = "completed"
        self.summary = dict(summary)

    def fail(self, reason: str) -> None:
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.status = "failed"
        self.summary = {"error": reason}

    def cancel(self, reason: str = "用户取消") -> None:
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.status = "cancelled"
        self.summary = {"reason": reason}

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "task_id": self.task_id,
            "goal": self.goal,
            "source": self.source,
            "triggered_by": self.triggered_by,
            "plan_id": self.plan_id,
            "plan_summary": self.plan_summary,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "steps": list(self.steps),
            "summary": dict(self.summary),
            "has_file_snapshots": bool(self.file_snapshots),
            "reversible": self.reversible,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionRecord:
        record = cls(
            task_id=data.get("task_id", ""),
            goal=data.get("goal", ""),
            source=data.get("source", ""),
            triggered_by=data.get("triggered_by", "user"),
            plan_id=data.get("plan_id"),
            plan_summary=data.get("plan_summary"),
        )
        record.record_id = data.get("record_id", record.record_id)
        record.started_at = data.get("started_at")
        record.completed_at = data.get("completed_at")
        record.status = data.get("status", "pending")
        record.steps = list(data.get("steps", []))
        record.summary = dict(data.get("summary", {}))
        record.file_snapshots = dict(data.get("file_snapshots", {}))
        record.reversible = bool(data.get("reversible", False))
        return record


class ExecutionHistoryStore:
    """执行历史存储：记录、查询、回滚。"""

    def __init__(self, data_store):
        self._data_store = data_store

    # ── 记录 ─────────────────────────────────────────────

    def save_record(self, record: ExecutionRecord) -> None:
        """保存执行记录。"""
        def _updater(records: Any) -> list[dict[str, Any]]:
            normalized = list(records) if isinstance(records, list) else []
            replacement = record.to_dict()
            updated = False
            for idx, item in enumerate(normalized):
                if isinstance(item, dict) and item.get("record_id") == record.record_id:
                    normalized[idx] = replacement
                    updated = True
                    break
            if not updated:
                normalized.append(replacement)
            return normalized[-500:]  # 保留最近 500 条

        self._data_store.update_json("assistant_execution_history", [], _updater)

    def get_record(self, record_id: str) -> ExecutionRecord | None:
        """获取单条执行记录。"""
        records = self._data_store.load_json("assistant_execution_history", [])
        normalized = list(records) if isinstance(records, list) else []
        for item in normalized:
            if isinstance(item, dict) and item.get("record_id") == record_id:
                return ExecutionRecord.from_dict(item)
        return None

    def list_records(
        self,
        *,
        task_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ExecutionRecord]:
        """列出执行记录，可按 task_id 过滤。"""
        records = self._data_store.load_json("assistant_execution_history", [])
        normalized = list(records) if isinstance(records, list) else []
        result = []
        for item in normalized:
            if not isinstance(item, dict):
                continue
            if task_id and item.get("task_id") != task_id:
                continue
            result.append(ExecutionRecord.from_dict(item))
        # 按时间降序
        result.sort(key=lambda r: r.started_at or "", reverse=True)
        return result[offset:offset + limit]

    # ── 回滚 ─────────────────────────────────────────────

    def rollback_record(self, record_id: str) -> dict[str, Any]:
        """回滚指定执行记录。

        前提：
        - 记录状态为 completed
        - 记录包含文件快照 (has_file_snapshots)
        - 记录标记为可逆 (reversible)

        返回回滚结果。
        """
        record = self.get_record(record_id)
        if record is None:
            return {"success": False, "reason": "record_not_found", "record_id": record_id}

        if record.status != "completed":
            return {"success": False, "reason": "record_not_completed", "record_id": record_id, "status": record.status}

        if not record.file_snapshots:
            return {"success": False, "reason": "no_file_snapshots", "record_id": record_id}

        if not record.reversible:
            return {"success": False, "reason": "not_reversible", "record_id": record_id}

        # 执行回滚
        failed_files = SnapshotManager.restore_files(record.file_snapshots)

        # 记录回滚操作到历史
        self._mark_rolled_back(record_id)

        if failed_files:
            return {
                "success": False,
                "reason": "partial_rollback",
                "record_id": record_id,
                "rolled_back": True,
                "failed_files": failed_files,
                "message": f"部分文件回滚完成，{len(failed_files)} 个文件回滚失败",
            }

        return {
            "success": True,
            "record_id": record_id,
            "rolled_back": True,
            "message": "已成功回滚所有修改",
        }

    def _mark_rolled_back(self, record_id: str) -> None:
        """在历史中标记一条回滚记录。"""
        rollback_record = ExecutionRecord(
            task_id=f"rollback:{record_id}",
            goal=f"回滚执行记录 {record_id}",
            source="rollback",
            triggered_by="user",
        )
        rollback_record.start()
        rollback_record.complete({"rolled_back_record": record_id})
        self.save_record(rollback_record)

    # ── 重放信息 ─────────────────────────────────────────

    def get_replay_data(self, record_id: str) -> dict[str, Any] | None:
        """获取重放所需的数据。

        返回记录中的步骤序列，供 UI 逐步骤展示。
        """
        record = self.get_record(record_id)
        if record is None:
            return None

        return {
            "record_id": record.record_id,
            "task_id": record.task_id,
            "goal": record.goal,
            "status": record.status,
            "total_steps": len(record.steps),
            "steps": [
                {
                    "step_id": s.get("step_id", ""),
                    "action_id": s.get("action_id", ""),
                    "status": s.get("status", ""),
                    "description": s.get("description", s.get("action_id", "")),
                    "duration_seconds": s.get("duration_seconds"),
                    "result_preview": self._preview_result(s.get("payload") or s.get("result", {})),
                }
                for s in record.steps
            ],
            "started_at": record.started_at,
            "completed_at": record.completed_at,
        }

    @staticmethod
    def _preview_result(result: dict[str, Any]) -> str:
        """从结果中提取简短预览。"""
        if not isinstance(result, dict):
            return str(result)[:100]
        for key in ("briefing", "summary", "message", "content"):
            val = result.get(key)
            if val and isinstance(val, str):
                return val[:100]
        return str(result)[:100]


# ── 审计日志集成 ────────────────────────────────────────────

class AuditLogWriter:
    """审计日志写入器，与 action_guard 联动。

    记录每次操作的关键信息，供审计查询。
    """

    def __init__(self, data_store):
        self._data_store = data_store

    def log_action(
        self,
        *,
        action_id: str,
        source: str,
        triggered_by: str = "user",
        status: str,
        details: dict[str, Any] | None = None,
        record_id: str | None = None,
    ) -> None:
        """记录一条审计日志。"""
        entry = {
            "log_id": uuid.uuid4().hex[:12],
            "action_id": action_id,
            "source": source,
            "triggered_by": triggered_by,
            "status": status,
            "details": dict(details or {}),
            "record_id": record_id or "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        def _updater(logs: Any) -> list[dict[str, Any]]:
            normalized = list(logs) if isinstance(logs, list) else []
            normalized.append(entry)
            return normalized[-1000:]  # 保留最近 1000 条

        self._data_store.update_json("assistant_audit_log", [], _updater)

    def get_logs(
        self,
        *,
        action_id: str | None = None,
        source: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """查询审计日志。"""
        logs = self._data_store.load_json("assistant_audit_log", [])
        normalized = list(logs) if isinstance(logs, list) else []
        result = []
        for item in normalized:
            if not isinstance(item, dict):
                continue
            if action_id and item.get("action_id") != action_id:
                continue
            if source and item.get("source") != source:
                continue
            result.append(dict(item))
        result.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return result[offset:offset + limit]
