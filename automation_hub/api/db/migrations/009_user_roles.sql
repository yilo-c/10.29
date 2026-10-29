-- 009_user_roles.sql
-- 用户角色表（用于显式角色管理）

CREATE TABLE IF NOT EXISTS user_roles (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'agent', 'cronjob', 'readonly')),
    assigned_by TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_user_roles_user ON user_roles(user_id);
CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles(role);

-- 预定义角色 → 权限范围映射（仅作为参考注释，实际在代码中实现）
-- admin:   all scopes
-- agent:   agent:use, tool:execute, tool:read
-- cronjob: tool:execute, tool:read
-- readonly: read-only scopes (news:read, note:read, task:read, translation:read, vocabulary:read, tool:read, approval:read, audit:read, repo:read, proposal:read)
