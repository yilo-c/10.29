"""工作流数据持久化层。

依赖 LocalDataStore，工作流数据存储在 data/workflows.json。
"""
from __future__ import annotations

import logging

from workflow.models import Workflow

logger = logging.getLogger("siyi.workflow.repository")

_STORAGE_KEY = "workflows"


class WorkflowRepository:
    """工作流 CRUD 仓储。"""

    def __init__(self, data_store) -> None:
        self._data_store = data_store

    def list_workflows(self) -> list[Workflow]:
        raw = self._data_store.load_json(_STORAGE_KEY, [])
        return [Workflow.from_dict(item) for item in raw if isinstance(item, dict)]

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        for wf in self.list_workflows():
            if wf.workflow_id == workflow_id:
                return wf
        return None

    def save_workflow(self, workflow: Workflow) -> None:
        workflow.touch()
        raw = self._data_store.load_json(_STORAGE_KEY, [])
        replaced = False
        for i, item in enumerate(raw):
            if isinstance(item, dict) and item.get("workflow_id") == workflow.workflow_id:
                raw[i] = workflow.to_dict()
                replaced = True
                break
        if not replaced:
            raw.append(workflow.to_dict())
        self._data_store.save_json(_STORAGE_KEY, raw)
        logger.info("工作流已保存: %s (%s)", workflow.name, workflow.workflow_id)

    def delete_workflow(self, workflow_id: str) -> bool:
        raw = self._data_store.load_json(_STORAGE_KEY, [])
        new_raw = [item for item in raw if isinstance(item, dict) and item.get("workflow_id") != workflow_id]
        if len(new_raw) == len(raw):
            return False
        self._data_store.save_json(_STORAGE_KEY, new_raw)
        logger.info("工作流已删除: %s", workflow_id)
        return True

    def duplicate_workflow(self, workflow_id: str) -> Workflow | None:
        wf = self.get_workflow(workflow_id)
        if wf is None:
            return None
        import copy
        new_wf = copy.deepcopy(wf)
        import uuid
        new_wf.workflow_id = uuid.uuid4().hex[:12]
        new_wf.name = f"{wf.name} (副本)"
        from datetime import datetime, timezone
        new_wf.created_at = datetime.now(timezone.utc).isoformat()
        new_wf.updated_at = new_wf.created_at
        new_wf.enabled = False
        self.save_workflow(new_wf)
        return new_wf
