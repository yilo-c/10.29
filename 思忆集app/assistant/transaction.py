"""事务性多文件修改：原子化文件操作，全部成功则提交，部分失败则全部回滚。

参考 Cursor Composer 的多文件原子编辑机制。
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("siyi.assistant.transaction")


class TransactionError(Exception):
    """事务操作异常。"""
    pass


class FileSnapshot:
    """单个文件的快照，包含路径、原始内容和元数据。"""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.content_before: str | None = None
        self.exists_before: bool = False
        self.mode_before: int | None = None
        self._snapshot_taken = False

    def take_snapshot(self) -> None:
        """记录文件的当前状态。"""
        path = Path(self.file_path)
        self.exists_before = path.exists()
        if self.exists_before:
            self.content_before = path.read_text(encoding="utf-8")
            self.mode_before = path.stat().st_mode
        self._snapshot_taken = True

    def restore(self) -> None:
        """恢复文件到快照时的状态。"""
        if not self._snapshot_taken:
            return
        path = Path(self.file_path)
        if self.exists_before and self.content_before is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.content_before, encoding="utf-8")
            if self.mode_before is not None:
                try:
                    os.chmod(path, self.mode_before)
                except Exception:
                    logger.warning("failed to restore file mode during rollback: %s", path)
        else:
            # 快照时文件不存在 → 删除
            try:
                path.unlink(missing_ok=True)
            except Exception:
                logger.warning("failed to delete file during rollback: %s", path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "exists_before": self.exists_before,
            "content_length_before": len(self.content_before) if self.content_before is not None else 0,
            "mode_before": self.mode_before,
        }


class TransactionStep:
    """事务中的单个步骤。"""

    def __init__(
        self,
        step_id: str,
        action: Callable[[], dict[str, Any]],
        *,
        description: str = "",
        file_paths: list[str] | None = None,
    ):
        self.step_id = step_id
        self.action = action
        self.description = description
        self.file_paths = list(file_paths or [])
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self.snapshots: list[FileSnapshot] = []


class FileTransaction:
    """事务性多文件操作。

    用法:
        txn = FileTransaction("修改配置")
        txn.add_step("step_1", "修改 settings.json", lambda: write_file(...), file_paths=["settings.json"])
        txn.add_step("step_2", "修改 config.yaml", lambda: write_file(...), file_paths=["config.yaml"])
        result = txn.execute()
        # result.success == True → 全部成功
        # result.success == False → 全部已回滚，result.failed_steps 包含失败步骤详情
    """

    def __init__(self, transaction_id: str, *, description: str = ""):
        self.transaction_id = transaction_id
        self.description = description
        self._steps: list[TransactionStep] = []
        self._executed: list[TransactionStep] = []

    def add_step(
        self,
        step_id: str,
        action: Callable[[], dict[str, Any]],
        *,
        description: str = "",
        file_paths: list[str] | None = None,
    ) -> None:
        """添加一个事务步骤。"""
        self._steps.append(TransactionStep(
            step_id=step_id,
            action=action,
            description=description,
            file_paths=file_paths,
        ))

    def execute(self) -> dict[str, Any]:
        """执行事务：所有步骤成功则提交，否则全部回滚。"""
        if not self._steps:
            return {
                "success": True,
                "transaction_id": self.transaction_id,
                "total": 0,
                "executed": 0,
                "failed_steps": [],
                "message": "没有步骤需要执行",
            }

        # Phase 1: 对所有文件拍快照
        for step in self._steps:
            for fp in step.file_paths:
                snapshot = FileSnapshot(fp)
                try:
                    snapshot.take_snapshot()
                except Exception as exc:
                    logger.warning("文件快照失败 %s: %s", fp, exc)
                    continue
                step.snapshots.append(snapshot)

        # Phase 2: 逐步骤执行
        failed_steps: list[dict[str, Any]] = []
        for step in self._steps:
            try:
                result = step.action()
                step.result = result if isinstance(result, dict) else {"result": result}
                self._executed.append(step)
            except Exception as exc:
                step.error = str(exc)
                failed_steps.append({
                    "step_id": step.step_id,
                    "description": step.description,
                    "error": str(exc),
                })
                # 执行失败 → 回滚所有已执行的步骤
                self._rollback()
                return {
                    "success": False,
                    "transaction_id": self.transaction_id,
                    "total": len(self._steps),
                    "executed": len(self._executed),
                    "failed_steps": failed_steps,
                    "rolled_back": True,
                    "message": f"步骤 {step.step_id} 失败，已全部回滚",
                }

        # Phase 3: 全部成功 → 清除快照（不需要回滚了）
        for step in self._steps:
            step.snapshots.clear()

        return {
            "success": True,
            "transaction_id": self.transaction_id,
            "total": len(self._steps),
            "executed": len(self._steps),
            "failed_steps": [],
            "rolled_back": False,
            "message": "全部步骤执行成功",
        }

    def _rollback(self) -> None:
        """回滚所有已执行步骤的文件修改。"""
        logger.info("事务 %s 回滚中，已执行 %d 步", self.transaction_id, len(self._executed))
        for step in reversed(self._executed):
            for snapshot in step.snapshots:
                try:
                    snapshot.restore()
                except Exception as exc:
                    logger.error("回滚文件 %s 失败: %s", snapshot.file_path, exc)
            step.snapshots.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "description": self.description,
            "steps": [
                {
                    "step_id": s.step_id,
                    "description": s.description,
                    "file_paths": s.file_paths,
                    "has_result": s.result is not None,
                    "error": s.error,
                }
                for s in self._steps
            ],
        }


# ── 快照管理器 ──────────────────────────────────────────────

class SnapshotManager:
    """管理文件的快照和恢复。

    与 CheckpointManager 配合使用：
    - 执行前调用 snapshot_files() 创建快照
    - 需要回滚时调用 restore_files()
    - 快照字典可以存入 checkpoint
    """

    @staticmethod
    def snapshot_files(file_paths: list[str]) -> dict[str, Any]:
        """对多个文件拍快照。返回可序列化的快照字典。"""
        snapshots: dict[str, Any] = {}
        for fp in file_paths:
            try:
                path = Path(fp)
                snapshot = FileSnapshot(fp)
                snapshot.take_snapshot()
                snapshots[fp] = {
                    "exists_before": snapshot.exists_before,
                    "content_before": snapshot.content_before,
                    "mode_before": snapshot.mode_before,
                }
            except Exception as exc:
                logger.warning("快照失败 %s: %s", fp, exc)
                snapshots[fp] = {"exists_before": False, "content_before": None, "error": str(exc)}
        return snapshots

    @staticmethod
    def restore_files(snapshots: dict[str, Any]) -> list[str]:
        """从快照字典恢复文件。返回恢复失败的文件列表。"""
        failed: list[str] = []
        for fp, data in snapshots.items():
            if not isinstance(data, dict):
                continue
            try:
                path = Path(fp)
                if data.get("exists_before") and data.get("content_before") is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(data["content_before"], encoding="utf-8")
                else:
                    path.unlink(missing_ok=True)
            except Exception as exc:
                logger.error("恢复文件 %s 失败: %s", fp, exc)
                failed.append(fp)
        return failed
