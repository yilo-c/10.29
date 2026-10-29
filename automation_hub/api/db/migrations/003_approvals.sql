-- 003_approvals.sql
-- 审批系统表

CREATE TABLE IF NOT EXISTS approval_requests (
  id TEXT PRIMARY KEY,
  created_by_user_id TEXT NOT NULL,
  created_by_device_id TEXT,
  resource_type TEXT NOT NULL,      -- run/proposal/tool
  resource_id TEXT NOT NULL,
  action TEXT NOT NULL,             -- execute/write/apply_patch
  risk_level TEXT NOT NULL,
  request_reason TEXT NOT NULL,
  payload_json TEXT NOT NULL,       -- 需要审批的关键参数（tool_id、args、diff摘要等）
  status TEXT NOT NULL,             -- pending/approved/denied/expired
  created_at TEXT NOT NULL,
  decided_at TEXT,
  decided_by_user_id TEXT,
  decision_note TEXT,
  FOREIGN KEY(created_by_user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_approvals_status ON approval_requests(status);
CREATE INDEX IF NOT EXISTS idx_approvals_resource ON approval_requests(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_approvals_created_at ON approval_requests(created_at);
