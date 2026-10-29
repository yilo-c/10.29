-- 002_tools.sql
-- 工具注册表（Tool Registry）

CREATE TABLE IF NOT EXISTS tools (
  id TEXT PRIMARY KEY,               -- tool_id
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  risk_level TEXT NOT NULL,          -- read/write/exec_low/exec_high
  executor TEXT NOT NULL,            -- host/docker/k8s_job
  args_schema_json TEXT NOT NULL,    -- JSON Schema
  command_json TEXT NOT NULL,        -- ["python","/app/scripts/x.py"] or ["bash","..."]
  cwd TEXT,
  timeout_sec INTEGER NOT NULL DEFAULT 120,
  allowed_paths_json TEXT NOT NULL DEFAULT '[]', -- 允许读写的路径前缀
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  is_enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tool_versions (
  id TEXT PRIMARY KEY,
  tool_id TEXT NOT NULL,
  version TEXT NOT NULL,             -- semver or timestamp
  spec_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(tool_id) REFERENCES tools(id)
);

CREATE INDEX IF NOT EXISTS idx_tools_enabled ON tools(is_enabled);
CREATE INDEX IF NOT EXISTS idx_tool_versions_tool ON tool_versions(tool_id);
