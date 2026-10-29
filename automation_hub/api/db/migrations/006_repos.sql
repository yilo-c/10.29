-- 006_repos.sql
-- 仓库索引表（代码理解基础）

CREATE TABLE IF NOT EXISTS repos (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  root_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_indexed_at TEXT
);

CREATE TABLE IF NOT EXISTS repo_files (
  id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  path TEXT NOT NULL,
  mtime INTEGER NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  FOREIGN KEY(repo_id) REFERENCES repos(id)
);

CREATE INDEX IF NOT EXISTS idx_repo_files_repo ON repo_files(repo_id);
CREATE INDEX IF NOT EXISTS idx_repo_files_path ON repo_files(repo_id, path);
CREATE INDEX IF NOT EXISTS idx_repo_files_sha256 ON repo_files(sha256);
