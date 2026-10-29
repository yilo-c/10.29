# -*- coding: utf-8 -*-
"""数据库模块包。

所有 raw SQL CRUD 已从 api/db.py 拆分到子模块并使用 SQLAlchemy 2.0 ORM：
- connection:  init_db, now_iso（遗留 get_db_connection 保留供过渡）
- legacy:      旧表 CRUD（scripts / tasks / runs / tool_runs）
- personal:    个人数据 CRUD（笔记 / 任务 / 新闻 / 翻译）
- vocabulary:  词汇 CRUD（单词本 / 单词）
- session:     SQLAlchemy 2.0 会话管理器
- approvals:   审批 CRUD（SQLAlchemy 2.0）
"""

# 连接与初始化
from .connection import init_db, now_iso, DATABASE_PATH

# 遗留 sqlite3 连接（过渡用, 推荐使用 session.get_db_session 替代）
from .connection import get_db_connection

# 旧表 CRUD
from .legacy import (
    # scripts
    get_scripts, get_script_by_name, create_script, update_script, delete_script,
    # tasks
    get_tasks, get_task_by_id, create_task, update_task_status, delete_task,
    # runs
    get_runs, list_runs, get_run_by_id, create_run, add_run_log, get_run_logs, update_run_status,
    # tool_runs
    create_tool_run, get_tool_run_by_id, list_tool_runs, update_tool_run_status, set_tool_run_approval,
)

# 个人数据 CRUD
from .personal import (
    list_personal_notes, get_personal_note, upsert_personal_note, delete_personal_note,
    list_personal_tasks, get_personal_task, upsert_personal_task, delete_personal_task,
    list_personal_news_items, upsert_personal_news_item, delete_personal_news_item,
    list_personal_translation_history, upsert_personal_translation_record,
    delete_personal_translation_record,
)

# 词汇 CRUD
from .vocabulary import (
    list_personal_wordbooks, upsert_personal_wordbook, delete_personal_wordbook,
    list_personal_words, upsert_personal_word, delete_personal_word,
)

# SQLAlchemy 2.0 会话管理器（新代码推荐使用）
from .session import get_db_session, get_session_factory

