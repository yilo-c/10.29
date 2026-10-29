-- 008_agent_nodes.sql
-- 本地代理节点相关表

-- 代理节点表
CREATE TABLE IF NOT EXISTS agent_nodes (
    id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    labels_json TEXT NOT NULL DEFAULT '{}',
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    issued_token_hash TEXT,
    status TEXT NOT NULL DEFAULT 'offline',
    policy_version TEXT NOT NULL DEFAULT 'v0',
    cpu_percent REAL DEFAULT 0.0,
    mem_percent REAL DEFAULT 0.0,
    active_runs INTEGER DEFAULT 0,
    drain_reason TEXT,
    revoke_reason TEXT,
    registered_at TEXT NOT NULL,
    last_heartbeat_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_nodes_status ON agent_nodes(status);
CREATE INDEX IF NOT EXISTS idx_agent_nodes_heartbeat ON agent_nodes(last_heartbeat_at);

-- 代理节点会话事件
CREATE TABLE IF NOT EXISTS agent_node_session_events (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(node_id) REFERENCES agent_nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_session_events_node ON agent_node_session_events(node_id);
CREATE INDEX IF NOT EXISTS idx_agent_session_events_session ON agent_node_session_events(session_id);

-- 执行授权
CREATE TABLE IF NOT EXISTS execution_authorizations (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL DEFAULT '{}',
    risk_level TEXT NOT NULL DEFAULT 'low',
    decision TEXT NOT NULL DEFAULT 'pending',
    reason TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by_user_id TEXT,
    FOREIGN KEY(node_id) REFERENCES agent_nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_exec_auth_node ON execution_authorizations(node_id);
CREATE INDEX IF NOT EXISTS idx_exec_auth_decision ON execution_authorizations(decision);

-- Run 事件（stdout 片段、完成/失败状态）
CREATE TABLE IF NOT EXISTS run_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    data TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id);

-- Run 产物
CREATE TABLE IF NOT EXISTS run_artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    content_type TEXT,
    data BLOB,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_artifacts_run ON run_artifacts(run_id);
