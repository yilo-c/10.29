-- 004_audit.sql
-- 审计日志表

CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY,
  actor_user_id TEXT,
  actor_device_id TEXT,
  event_type TEXT NOT NULL,         -- auth.token_created / tool.executed / proposal.applied ...
  resource_type TEXT,
  resource_id TEXT,
  action TEXT NOT NULL,
  status TEXT NOT NULL,             -- success/fail
  message TEXT,
  meta_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_events(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_events(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor_user_id);
