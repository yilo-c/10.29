-- 模块说明：数据库表结构定义。
-- scripts 表: 存储可注册的脚本元信息
CREATE TABLE IF NOT EXISTS scripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- tasks 表: 存储用户创建的任务
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    script_name TEXT NOT NULL,
    parameters TEXT,
    status TEXT DEFAULT 'pending',
    scheduled_time DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    FOREIGN KEY(script_name) REFERENCES scripts(name) ON DELETE SET NULL
);

-- runs 表: 存储每次脚本执行的记录，字段设计与 `api/db.py` 交互一致
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE NOT NULL,
    script_name TEXT NOT NULL,
    parameters TEXT,
    status TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    started_at DATETIME,
    completed_at DATETIME,
    result TEXT,
    error_msg TEXT,
    log_file_path TEXT,
    output_path TEXT,
    -- 审计字段
    triggered_by TEXT DEFAULT 'system',
    result_status TEXT CHECK(result_status IN ('success', 'failure')),
    failure_type TEXT CHECK(failure_type IN ('timeout', 'nonzero', 'exception'))
);

-- 索引以优化常用查询
CREATE INDEX IF NOT EXISTS idx_runs_run_id ON runs(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_script_name ON runs(script_name);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_triggered_by ON runs(triggered_by);
CREATE INDEX IF NOT EXISTS idx_runs_failure_type ON runs(failure_type);

-- RSS 来源表
CREATE TABLE IF NOT EXISTS rss_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    name TEXT,
    tags TEXT,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rss_sources_enabled ON rss_sources(is_enabled);

-- RSS 条目表
CREATE TABLE IF NOT EXISTS rss_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id TEXT UNIQUE NOT NULL,
    source_id INTEGER NOT NULL,
    title TEXT,
    link TEXT,
    published_at TEXT,
    summary TEXT,
    raw_json TEXT,
    fetched_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(source_id) REFERENCES rss_sources(id)
);

CREATE INDEX IF NOT EXISTS idx_rss_items_source ON rss_items(source_id);
CREATE INDEX IF NOT EXISTS idx_rss_items_fetched_at ON rss_items(fetched_at);

-- 日报索引表
CREATE TABLE IF NOT EXISTS daily_briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_date TEXT UNIQUE NOT NULL,
    stats_json TEXT NOT NULL,
    md_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source_counts_json TEXT NOT NULL
);