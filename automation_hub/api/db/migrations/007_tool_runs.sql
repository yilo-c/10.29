-- 007_tool_runs.sql
-- 工具运行表（Tool Runs）：记录基于 tools 表的每次工具执行

CREATE TABLE IF NOT EXISTS tool_runs (
  id TEXT PRIMARY KEY,                  -- run_id
  tool_id TEXT NOT NULL,
  args_json TEXT NOT NULL,              -- JSON
  status TEXT NOT NULL,                 -- queued/pending_approval/running/succeeded/failed/denied/blocked
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  exit_code INTEGER,
  stdout_path TEXT,
  stderr_path TEXT,
  result_json TEXT,
  error_msg TEXT,
  created_by_user_id TEXT,
  approval_request_id TEXT,
  FOREIGN KEY(tool_id) REFERENCES tools(id),
  FOREIGN KEY(created_by_user_id) REFERENCES users(id),
  FOREIGN KEY(approval_request_id) REFERENCES approval_requests(id)
);

CREATE INDEX IF NOT EXISTS idx_tool_runs_tool ON tool_runs(tool_id);
CREATE INDEX IF NOT EXISTS idx_tool_runs_status ON tool_runs(status);
CREATE INDEX IF NOT EXISTS idx_tool_runs_created_at ON tool_runs(created_at);
