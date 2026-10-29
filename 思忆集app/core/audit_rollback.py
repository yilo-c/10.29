"""审计回滚：审批-执行-审计闭环 + 写前快照 + 一键回滚。

核心保证：
- 任何写操作前先做快照或版本化
- 支持一键回滚和回滚审计
- API 异常时降级到只读建议模式，不做写操作
- 连续失败触发熔断，停止自动执行并通知
- 记录谁在何时对哪条数据做了什么操作，可回放可追责
"""

from __future__ import annotations

import copy
import logging
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from data.local_storage import LocalDataStore
    from core.observability import ObservabilityService

from core.audit_writer import get_audit_writer

logger = logging.getLogger("siyi.audit_rollback")

# M2.3 QUA-4：快照截断标记。回滚前用它判定「这份快照不完整」——
# 把带标记的快照写回业务数据 = 静默毁数据，必须拒绝而非盲恢复。
SNAPSHOT_TRUNCATION_SUFFIX = "…[truncated]"


def _snapshot_has_truncation(obj: Any) -> bool:
    """递归检测快照内是否含截断标记（QUA-4 ④：截断快照拒绝回滚）。"""
    if isinstance(obj, str):
        return obj.endswith(SNAPSHOT_TRUNCATION_SUFFIX)
    if isinstance(obj, dict):
        return any(_snapshot_has_truncation(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_snapshot_has_truncation(v) for v in obj)
    return False


class AuditTrail:
    """审计轨迹：每次写入可追踪。

    T1.1：写入走独立 SQLite 单写线程（audit_writer），与 notes 写物理隔离，
    消除同库锁竞争；读走 SQL + pending 内存合并，刚完成的写立即可回滚。
    """

    MAX_TRAIL_SIZE = 400
    # 快照内单个字符串值的最大长度（笔记全文等截断，避免单条膨胀）
    MAX_SNAPSHOT_CHARS = 2000

    def __init__(
        self,
        data_store: LocalDataStore | None = None,
        observability_service: ObservabilityService | None = None,
        *,
        data_dir: str | None = None,
    ) -> None:
        self.data_store = data_store
        self.observability_service = observability_service
        if data_dir is None and data_store is not None:
            data_dir = getattr(data_store, "base_dir", None)
        if data_dir is None:
            # 测试 / 无 data_store 场景：落到临时目录，避免写入仓库 data/
            data_dir = tempfile.mkdtemp(prefix="siyi-audit-")
        # 统一审计写入器单例（独立 audit_trail.db，与 notes 写物理隔离，消除 SQLite 锁竞争）
        self.writer = get_audit_writer(data_dir=data_dir, observability_service=observability_service)

    def record_write_intent(
        self,
        *,
        actor: str,
        action_id: str,
        target_type: str,
        target_id: str = "",
        operation: str = "write",
        before_snapshot: dict[str, Any] | list[Any] | None = None,
        metadata: dict[str, Any] | None = None,
        snapshot_str_limit: int | None = None,
    ) -> str:
        entry_id = uuid.uuid4().hex[:16]
        entry = {
            "entry_id": entry_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": str(actor or "assistant").strip(),
            "action_id": str(action_id or "").strip(),
            "target_type": str(target_type or "").strip(),
            "target_id": str(target_id or "").strip(),
            "operation": str(operation or "write").strip(),
            "before_snapshot": self._safe_copy(before_snapshot, limit=snapshot_str_limit),
            "after_snapshot": None,
            "status": "intent",
            "metadata": dict(metadata or {}),
        }
        self.writer.submit_intent(entry)
        return entry_id

    def record_write_complete(
        self,
        entry_id: str,
        after_snapshot: dict[str, Any] | list[Any] | None = None,
    ) -> bool:
        return self._submit_status(entry_id, "completed", after_snapshot=after_snapshot)

    def record_write_failed(
        self,
        entry_id: str,
        error: str = "",
    ) -> bool:
        return self._submit_status(entry_id, "failed", error=error)

    def _submit_status(self, entry_id: str, status: str, *, after_snapshot: Any = None, error: str = "") -> bool:
        # 同步校验存在性（pending 或已落盘），保持「更新不存在条目返回 False」语义
        if self.writer.fetch_entry(entry_id) is None:
            return False
        self.writer.submit_status(entry_id, status, after_snapshot=after_snapshot, error=error or None)
        return True

    def list_entries(self, *, limit: int = 100, action_id: str = "", status: str = "") -> list[dict[str, Any]]:
        trail = self._merged_entries()
        if action_id:
            trail = [e for e in trail if e.get("action_id") == action_id]
        if status:
            trail = [e for e in trail if e.get("status") == status]
        return trail[-limit:]

    def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        return self.writer.fetch_entry(entry_id)

    def list_rollbackable(self, limit: int = 50) -> list[dict[str, Any]]:
        trail = self._merged_entries()
        result = []
        for entry in reversed(trail):
            if entry.get("status") == "completed" and entry.get("before_snapshot") is not None:
                result.append(entry)
                if len(result) >= limit:
                    break
        return result

    # ── 内部方法 ──

    def _merged_entries(self) -> list[dict[str, Any]]:
        """合并 SQL 已落盘 + 队列中未落盘（pending）的审计条目，按时间升序返回。

        逻辑上限为 MAX_TRAIL_SIZE（只保留最近 N 条，与旧整列表存储语义一致）；
        物理上限由写入器的后台清理线程负责（TRAIL_KEEP）。
        """
        by_id: dict[str, dict[str, Any]] = {}
        for e in self.writer.fetch_entries(limit=self.MAX_TRAIL_SIZE * 2):
            by_id[e.get("entry_id")] = e
        for eid, pe in self.writer.pending_entries().items():
            by_id[eid] = pe  # pending 为最新状态，覆盖已落盘旧值
        entries = list(by_id.values())
        entries.sort(key=lambda e: e.get("timestamp") or "")
        return entries[-self.MAX_TRAIL_SIZE:]

    @classmethod
    def _truncate_value(cls, obj: Any, depth: int = 0, limit: int | None = None) -> Any:
        """递归截断快照内的长字符串值（限深度，防畸形数据）。

        limit：per-call 覆盖类级 MAX_SNAPSHOT_CHARS（给定即生效，可放宽到
        65536 供 file 快照用，也可收紧做更小截断）。
        """
        if depth > 8:
            return "<deep>"
        max_chars = cls.MAX_SNAPSHOT_CHARS if limit is None else limit
        if isinstance(obj, str):
            if len(obj) > max_chars:
                return obj[:max_chars] + SNAPSHOT_TRUNCATION_SUFFIX
            return obj
        if isinstance(obj, dict):
            return {k: cls._truncate_value(v, depth + 1, limit) for k, v in obj.items()}
        if isinstance(obj, list):
            return [cls._truncate_value(v, depth + 1, limit) for v in obj]
        return obj

    @classmethod
    def _safe_copy(cls, obj: Any, limit: int | None = None) -> Any:
        if obj is None:
            return None
        try:
            # 深拷贝后立即截断长字符串，从源头限制单条快照体积
            return cls._truncate_value(copy.deepcopy(obj), limit=limit)
        except Exception:
            try:
                max_chars = cls.MAX_SNAPSHOT_CHARS if limit is None else limit
                return str(obj)[:max_chars]
            except Exception:
                return "<unserializable>"


class RollbackManager:
    """Audit and rollback manager for tracking changes."""

    def __init__(
        self,
        data_store: LocalDataStore,
        audit_trail: AuditTrail,
        *,
        note_repository: Any = None,
        task_repository: Any = None,
        inbox_repository: Any = None,
        observability_service: ObservabilityService | None = None,
    ) -> None:
        self.data_store = data_store
        self.audit_trail = audit_trail
        self.note_repository = note_repository
        self.task_repository = task_repository
        self.inbox_repository = inbox_repository
        self.observability_service = observability_service

    def can_rollback(self, entry_id: str) -> dict[str, Any]:
        entry = self.audit_trail.get_entry(entry_id)
        if entry is None:
            return {"can_rollback": False, "reason": "entry_not_found"}
        if entry.get("status") != "completed":
            return {"can_rollback": False, "reason": f"status_is_{entry.get('status')}"}
        if entry.get("before_snapshot") is None and entry.get("target_type") != "file":
            return {"can_rollback": False, "reason": "no_before_snapshot"}
        # QUA-4 ④：快照被截断（含 …[truncated] 标记）→ 拒绝回滚，禁止把截断数据写回业务
        if _snapshot_has_truncation(entry.get("before_snapshot")):
            return {"can_rollback": False, "reason": "snapshot_truncated"}
        return {"can_rollback": True, "entry": entry}

    def rollback(self, entry_id: str) -> dict[str, Any]:
        check = self.can_rollback(entry_id)
        if not check.get("can_rollback"):
            return {"rolled_back": False, "reason": check.get("reason", "cannot_rollback")}

        entry = check["entry"]
        target_type = entry.get("target_type", "")
        target_id = entry.get("target_id", "")
        before_snapshot = entry.get("before_snapshot")
        after_snapshot = entry.get("after_snapshot")

        # QUA-4 ③：带 version 的写条目，当前 version != after.version → 有外部改动，
        # 拒绝用陈旧快照盲目覆盖
        if (
            target_type == "note"
            and self.note_repository is not None
            and isinstance(after_snapshot, dict)
            and "version" in after_snapshot
        ):
            current = self._find_note_row(target_id)
            if current is not None and current.get("version") != after_snapshot.get("version"):
                return {
                    "rolled_back": False,
                    "reason": f"version_conflict: current={current.get('version')} recorded={after_snapshot.get('version')}",
                }

        try:
            self._apply_rollback(target_type, target_id, before_snapshot)
        except Exception as exc:
            logger.warning("rollback failed for entry %s: %s", entry_id, exc)
            self._track("audit.rollback.failed", {"entry_id": entry_id, "error": str(exc)})
            return {"rolled_back": False, "reason": str(exc)}

        # QUA-4 ②：带 version 的写条目回滚成功后置 rolled_back 终态，
        # 二次回滚拒绝（防止陈旧快照覆盖回滚后的新改动）
        if (
            target_type == "note"
            and isinstance(after_snapshot, dict)
            and "version" in after_snapshot
        ):
            self.audit_trail._submit_status(entry_id, "rolled_back")

        rollback_audit_id = self.audit_trail.record_write_intent(
            actor="rollback_manager",
            action_id=f"rollback:{entry.get('action_id', '')}",
            target_type=target_type,
            target_id=target_id,
            operation="rollback",
            before_snapshot=entry.get("after_snapshot"),
            metadata={"original_entry_id": entry_id},
        )
        self.audit_trail.record_write_complete(rollback_audit_id, after_snapshot=before_snapshot)

        self._track("audit.rollback.ok", {"entry_id": entry_id, "target_type": target_type, "target_id": target_id})
        return {"rolled_back": True, "entry_id": entry_id, "rollback_audit_id": rollback_audit_id}

    def batch_rollback(self, entry_ids: list[str]) -> dict[str, Any]:
        results = []
        for eid in entry_ids:
            results.append(self.rollback(eid))
        ok_count = sum(1 for r in results if r.get("rolled_back"))
        return {"total": len(entry_ids), "rolled_back": ok_count, "details": results}

    def _find_note_row(self, target_id: str) -> dict[str, Any] | None:
        """QUA-4 ①：int/str 类型归一 + 含软删行定位真实记录（禁止 str-id 盲插副本）。

        RPC 审计时 target_id 被 str 化，而存储 id 为 int（repo 严格 == 不命中）；
        必须先归一命中真实行（含软删行），回滚走 update 恢复原 id，而不是重建副本。
        """
        if self.note_repository is None:
            return None
        candidates: list[Any] = [target_id]
        try:
            candidates.append(int(target_id))
        except (TypeError, ValueError):
            pass
        try:
            candidates.append(str(target_id))
        except Exception:
            pass
        for cid in candidates:
            try:
                note = self.note_repository.get_note(cid)
            except Exception:
                note = None
            if note is not None:
                return note
        # get_note 跳过软删行：全量含软删扫描兜底（软删回滚也必须恢复原行）
        try:
            list_notes = getattr(self.note_repository, "list_notes", None)
            if list_notes is None:
                return None
            for note in list_notes(include_deleted=True):
                for cid in candidates:
                    if note.get("id") == cid:
                        return note
        except Exception:
            pass
        return None

    def _apply_rollback(self, target_type: str, target_id: str, before_snapshot: dict[str, Any] | list[Any] | None) -> None:
        if target_type == "note" and self.note_repository is not None:
            # QUA-4 ①：归一命中真实行（含软删行），禁止 str-id 盲插副本
            row = self._find_note_row(target_id)
            if before_snapshot is None:
                self.note_repository.delete_note(row["id"] if row is not None else target_id)
            elif isinstance(before_snapshot, dict):
                if row is None:
                    # 真实记录已不存在（如硬删）：用快照内真实 id（int 优先）重建
                    note = dict(before_snapshot)
                    note["id"] = before_snapshot.get("id") or target_id
                    self.note_repository.add_note(note)
                else:
                    self.note_repository.update_note(row["id"], before_snapshot)
            return

        if target_type == "task" and self.task_repository is not None:
            if before_snapshot is None:
                self.task_repository.delete_task(target_id)
            elif isinstance(before_snapshot, dict):
                existing = self.task_repository.get_task(target_id)
                if existing is not None:
                    self.task_repository.update_task(target_id, before_snapshot)
                else:
                    before_snapshot["id"] = target_id
                    self.task_repository.add_task(before_snapshot)
            return

        if target_type == "inbox" and self.inbox_repository is not None:
            if isinstance(before_snapshot, dict):
                self.inbox_repository.update_entry(target_id, before_snapshot)
            return

        if target_type == "json_store":
            if isinstance(before_snapshot, (dict, list)):
                self.data_store.save_json(target_id, before_snapshot)
            return

        if target_type == "file":
            target = Path(target_id)
            if before_snapshot is None:
                # 新建文件：回滚即删除落盘文件
                try:
                    if target.exists():
                        if target.is_dir():
                            target.rmdir()
                        else:
                            target.unlink()
                except OSError as exc:
                    raise ValueError(f"file_rollback_delete_failed: {exc}") from exc
                return
            if isinstance(before_snapshot, dict):
                # QUA-4：快照带 truncated 标记 = 无法完整恢复，拒绝半截写回
                if before_snapshot.get("truncated"):
                    raise ValueError("file_snapshot_truncated_not_restorable")
                content = before_snapshot.get("content")
                if content is None:
                    raise ValueError("file_snapshot_missing_content")
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                except OSError as exc:
                    raise ValueError(f"file_rollback_write_failed: {exc}") from exc
                return
            raise ValueError(f"unsupported_file_snapshot_shape: {target_id}")

        # QUA-4 ⑤：不支持的 target_type 如实报错（修复前 warning 后假报 True）
        raise ValueError(f"unsupported_target_type: {target_type}")

    def _track(self, event: str, payload: dict[str, Any]) -> None:
        if self.observability_service is None:
            return
        try:
            from core.observability import SEVERITY_INFO
            self.observability_service.track_event(event, severity=SEVERITY_INFO, payload=payload)
        except Exception:
            logger.warning("failed to track audit rollback event: %s", event)


class WriteGuard:
    """写操作守卫：包装写操作，自动快照 + 审计 + 异常降级。

    用法：
        with write_guard.protect("note", note_id, actor="assistant", action_id="journal.daily_draft") as guard:
            guard.set_before(old_note)
            result = repo.add_note(new_data)
            guard.set_after(result)
        # 自动记录 complete/failed
    """

    def __init__(self, audit_trail: AuditTrail, observability_service: ObservabilityService | None = None) -> None:
        self.audit_trail = audit_trail
        self.observability_service = observability_service

    def protect(
        self,
        target_type: str,
        target_id: str = "",
        *,
        actor: str = "assistant",
        action_id: str = "",
        operation: str = "write",
        before_snapshot: dict[str, Any] | list[Any] | None = None,
        metadata: dict[str, Any] | None = None,
        snapshot_str_limit: int | None = None,
    ) -> _WriteGuardContext:
        return _WriteGuardContext(
            audit_trail=self.audit_trail,
            target_type=target_type,
            target_id=target_id,
            actor=actor,
            action_id=action_id,
            operation=operation,
            before_snapshot=before_snapshot,
            metadata=metadata,
            snapshot_str_limit=snapshot_str_limit,
        )


class _WriteGuardContext:
    """WriteGuard 上下文管理器。"""

    def __init__(
        self,
        audit_trail: AuditTrail,
        target_type: str,
        target_id: str,
        actor: str,
        action_id: str,
        operation: str,
        before_snapshot: dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any] | None,
        snapshot_str_limit: int | None = None,
    ) -> None:
        self.audit_trail = audit_trail
        self.target_type = target_type
        self.target_id = target_id
        self.actor = actor
        self.action_id = action_id
        self.operation = operation
        self._before_snapshot = before_snapshot
        self._after_snapshot: Any = None
        self._metadata = metadata
        self._snapshot_str_limit = snapshot_str_limit
        self._entry_id: str | None = None

    def set_before(self, snapshot: dict[str, Any] | list[Any] | None) -> None:
        self._before_snapshot = snapshot

    def set_after(self, snapshot: dict[str, Any] | list[Any] | None) -> None:
        self._after_snapshot = snapshot

    @property
    def entry_id(self) -> str | None:
        return self._entry_id

    def __enter__(self):
        self._entry_id = self.audit_trail.record_write_intent(
            actor=self.actor,
            action_id=self.action_id,
            target_type=self.target_type,
            target_id=self.target_id,
            operation=self.operation,
            before_snapshot=self._before_snapshot,
            metadata=self._metadata,
            snapshot_str_limit=self._snapshot_str_limit,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.audit_trail.record_write_failed(self._entry_id or "", error=str(exc_val))
        else:
            self.audit_trail.record_write_complete(self._entry_id or "", after_snapshot=self._after_snapshot)
        return False
