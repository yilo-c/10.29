-- 思忆集云同步 D1 Schema

CREATE TABLE IF NOT EXISTS devices (
  device_id   TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  token_hash  TEXT NOT NULL,  -- SHA-256 of device token
  created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
  revoked_at  INTEGER        -- 吊销时间戳（V3-02），NULL=有效
);

CREATE TABLE IF NOT EXISTS sync_data (
  device_id TEXT NOT NULL,
  resource  TEXT NOT NULL,         -- 'notes' | 'todos' | 'settings' | 'info_brief' | 'info_sections'
  data_encrypted TEXT NOT NULL,    -- AES-256-GCM encrypted JSON
  checksum    TEXT NOT NULL,       -- SHA-256 of plaintext (for dedup)
  updated_at  INTEGER NOT NULL,
  PRIMARY KEY (device_id, resource)
);

CREATE INDEX idx_sync_updated ON sync_data(updated_at);
CREATE INDEX idx_sync_resource ON sync_data(resource);
