-- 005_proposals.sql
-- 提案系统表（AI 正确接入点）

CREATE TABLE IF NOT EXISTS proposals (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  plan_md TEXT NOT NULL,            -- AI/人写的计划
  patch_diff TEXT,                  -- unified diff
  verify_commands_json TEXT NOT NULL DEFAULT '[]',
  risk_level TEXT NOT NULL,
  status TEXT NOT NULL,             -- draft/pending_approval/approved/applied/rejected
  target_repo_id TEXT,
  created_by_user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  applied_at TEXT,
  FOREIGN KEY(created_by_user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
CREATE INDEX IF NOT EXISTS idx_proposals_created_by ON proposals(created_by_user_id);
CREATE INDEX IF NOT EXISTS idx_proposals_created_at ON proposals(created_at);
