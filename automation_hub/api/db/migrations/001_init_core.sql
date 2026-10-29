-- 001_init_core.sql
-- 核心认证与权限表

-- 用户表
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- 设备表（手机/笔记本等）
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,
  platform TEXT NOT NULL,      -- windows/mac/linux/android/ios
  created_at TEXT NOT NULL,
  last_seen_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id)
);

-- API Token 表（只存 hash，不存明文）
CREATE TABLE IF NOT EXISTS api_tokens (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  device_id TEXT,
  token_hash TEXT NOT NULL,
  scopes TEXT NOT NULL DEFAULT '[]',   -- JSON array
  expires_at TEXT,
  created_at TEXT NOT NULL,
  revoked_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id),
  FOREIGN KEY(device_id) REFERENCES devices(id)
);

CREATE INDEX IF NOT EXISTS idx_tokens_user ON api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_tokens_hash ON api_tokens(token_hash);
