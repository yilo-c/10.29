"""Worker 策略执行检查模块。

在 Worker 执行任务前检查审批状态。
"""

from __future__ import annotations
import logging
from automation_hub.api.approvals.service import reconcile_pending_approvals
from automation_hub.api.db import get_db_connection


logger = logging.getLogger(__name__)


def is_run_approved(run_id: str) -> bool:
    """检查运行是否已获批准。
    
    Args:
        run_id: 运行 ID
        
    Returns:
        True 表示已批准或不需要审批，False 表示未批准
    """
    reconcile_pending_approvals()
    with get_db_connection() as conn:
        row = conn.execute(
            """SELECT status FROM approval_requests
               WHERE resource_type='run' AND resource_id=?
               ORDER BY created_at DESC
               LIMIT 1""",
            (run_id,)
        ).fetchone()
        
        if not row:
            # 没有审批记录，表示不需要审批
            logger.info(f"Run {run_id}: No approval required")
            return True
        
        status = row["status"]
        approved = status == "approved"
        
        if not approved:
            logger.warning(f"Run {run_id}: Not approved (status={status})")
        else:
            logger.info(f"Run {run_id}: Approved")
        
        return approved


def is_proposal_approved(proposal_id: str) -> bool:
    """检查提案是否已获批准。
    
    Args:
        proposal_id: 提案 ID
        
    Returns:
        True 表示已批准，False 表示未批准
    """
    reconcile_pending_approvals()
    with get_db_connection() as conn:
        row = conn.execute(
            """SELECT status FROM approval_requests
               WHERE resource_type='proposal' AND resource_id=?
               ORDER BY created_at DESC
               LIMIT 1""",
            (proposal_id,)
        ).fetchone()
        
        if not row:
            logger.warning(f"Proposal {proposal_id}: No approval record found")
            return False
        
        status = row["status"]
        approved = status == "approved"
        
        if not approved:
            logger.warning(f"Proposal {proposal_id}: Not approved (status={status})")
        else:
            logger.info(f"Proposal {proposal_id}: Approved")
        
        return approved
