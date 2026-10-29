-- 数据库表结构定义

-- 脚本表
CREATE TABLE IF NOT EXISTS scripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 任务表
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    script_name TEXT NOT NULL,
    parameters TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    scheduled_time TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (script_name) REFERENCES scripts (name)
);

-- 运行记录表
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE NOT NULL,
    script_name TEXT NOT NULL,
    parameters TEXT,
    status TEXT DEFAULT 'queued',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    log_file_path TEXT,
    result TEXT,
    error_msg TEXT,
    FOREIGN KEY (script_name) REFERENCES scripts (name)
);

-- 新增：认证、工具、审批、审计、提案与仓库索引相关表

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    device_id TEXT,
    token_hash TEXT NOT NULL,
    scopes TEXT NOT NULL DEFAULT '[]',
    expires_at TEXT,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(device_id) REFERENCES devices(id)
);

CREATE INDEX IF NOT EXISTS idx_tokens_user ON api_tokens(user_id);

CREATE TABLE IF NOT EXISTS tools (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    executor TEXT NOT NULL,
    args_schema_json TEXT NOT NULL,
    command_json TEXT NOT NULL,
    cwd TEXT,
    timeout_sec INTEGER NOT NULL DEFAULT 120,
    allowed_paths_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tool_versions (
    id TEXT PRIMARY KEY,
    tool_id TEXT NOT NULL,
    version TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(tool_id) REFERENCES tools(id)
);

CREATE INDEX IF NOT EXISTS idx_tools_enabled ON tools(is_enabled);

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    created_by_user_id TEXT NOT NULL,
    created_by_device_id TEXT,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    action TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    request_reason TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by_user_id TEXT,
    decision_note TEXT,
    FOREIGN KEY(created_by_user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_approvals_status ON approval_requests(status);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    actor_user_id TEXT,
    actor_device_id TEXT,
    event_type TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_events(created_at);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    plan_md TEXT NOT NULL,
    patch_diff TEXT,
    verify_commands_json TEXT NOT NULL DEFAULT '[]',
    risk_level TEXT NOT NULL,
    status TEXT NOT NULL,
    target_repo_id TEXT,
    created_by_user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    applied_at TEXT,
    FOREIGN KEY(created_by_user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);

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

CREATE TABLE IF NOT EXISTS personal_notes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    remote_id TEXT UNIQUE,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    category TEXT,
    folder_path TEXT NOT NULL DEFAULT '/',
    favorite INTEGER NOT NULL DEFAULT 0,
    related_news_url TEXT,
    linked_task_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    device_id TEXT,
    sync_state TEXT NOT NULL DEFAULT 'synced',
    last_synced_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_personal_notes_user_updated ON personal_notes(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS personal_tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    remote_id TEXT UNIQUE,
    name TEXT NOT NULL,
    note TEXT,
    is_completed INTEGER NOT NULL DEFAULT 0,
    reminder_time TEXT,
    linked_note_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    device_id TEXT,
    sync_state TEXT NOT NULL DEFAULT 'synced',
    last_synced_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_personal_tasks_user_updated ON personal_tasks(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS personal_news_items (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    remote_id TEXT UNIQUE,
    item_key TEXT,
    title TEXT NOT NULL,
    url TEXT,
    summary TEXT,
    content TEXT,
    source TEXT,
    category TEXT,
    published_at TEXT,
    image_url TEXT,
    is_favorite INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    device_id TEXT,
    sync_state TEXT NOT NULL DEFAULT 'synced',
    last_synced_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_personal_news_items_user_updated ON personal_news_items(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS personal_translation_history (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    remote_id TEXT UNIQUE,
    history_key TEXT,
    source_text TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    from_lang TEXT NOT NULL,
    to_lang TEXT NOT NULL,
    raw_result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    device_id TEXT,
    sync_state TEXT NOT NULL DEFAULT 'synced',
    last_synced_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_personal_translation_history_user_updated ON personal_translation_history(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS personal_wordbooks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    remote_id TEXT UNIQUE,
    book_key TEXT,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    device_id TEXT,
    sync_state TEXT NOT NULL DEFAULT 'synced',
    last_synced_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_personal_wordbooks_user_updated ON personal_wordbooks(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS personal_words (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    remote_id TEXT UNIQUE,
    word_key TEXT,
    word TEXT NOT NULL,
    phonetic TEXT,
    meaning TEXT,
    example TEXT,
    wordbook_remote_id TEXT,
    wordbook_name TEXT,
    wordbook_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    device_id TEXT,
    sync_state TEXT NOT NULL DEFAULT 'synced',
    last_synced_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_personal_words_user_updated ON personal_words(user_id, updated_at DESC);

-- =====================================================================
-- 本地代理节点 (Local Agent Nodes)
-- =====================================================================

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

-- Tool Runs 主执行记录
CREATE TABLE IF NOT EXISTS tool_runs (
    id TEXT PRIMARY KEY,
    tool_id TEXT NOT NULL,
    tool_name TEXT,
    args_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    completed_at TEXT,
    exit_code INTEGER,
    result_json TEXT,
    error_msg TEXT,
    stdout_path TEXT,
    stderr_path TEXT,
    created_by_user_id TEXT,
    approval_request_id TEXT,
    claimed_by TEXT,
    FOREIGN KEY(created_by_user_id) REFERENCES users(id),
    FOREIGN KEY(approval_request_id) REFERENCES approval_requests(id)
);

CREATE INDEX IF NOT EXISTS idx_tool_runs_status ON tool_runs(status);
CREATE INDEX IF NOT EXISTS idx_tool_runs_created_at ON tool_runs(created_at);

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