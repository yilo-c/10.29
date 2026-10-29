from __future__ import annotations

import copy
from typing import Any

from core.action_guard import (
    DEFAULT_ALLOWED_ACTIONS,
    DEFAULT_APPROVAL_POLICY,
    DEFAULT_SANDBOX_LEVEL,
    KEY_ACTION_DENYLIST,
    KEY_ACTION_GUARD_PROFILE,
    KEY_ACTION_RISK_ENFORCEMENT,
    KEY_ACTION_RISK_WARN_ONLY,
    KEY_ACTION_WHITELIST,
    KEY_ASSISTANT_APPROVAL_POLICY,
    KEY_ASSISTANT_SANDBOX_LEVEL,
    KEY_COMMAND_WHITELIST,
)

# ── 统一设置键常量 ──

# 应用启动白名单追加项（B1 合并语义，2026-09-11）。
# 生效名单 = services.app_launch.APP_LAUNCH_ALLOWLIST（代码默认集）∪ 本键。
# 常量本体定义在 services.app_launch（SETTINGS_KEY_EXTRA_ALLOWLIST），
# 此处以字符串字面量登记（core 不反向 import services）。
# 注意：必须定义在 SETTINGS_DEFAULTS 之前（后者引用本常量）。
KEY_APP_LAUNCH_EXTRA_ALLOWLIST = "assistant_app_launch_extra_allowlist"

KEY_YOUDAO_APP_KEY = "youdao_app_key"
KEY_YOUDAO_APP_SECRET = "youdao_app_secret"
KEY_YOUDAO_OAUTH_REDIRECT_URI = "youdao_oauth_redirect_uri"
KEY_TODOIST_API_TOKEN = "todoist_api_token"
KEY_AUTOMATION_HUB_API_BASE = "automation_hub_api_base"
KEY_AUTOMATION_HUB_API_TOKEN = "automation_hub_api_token"
KEY_LLM_API_BASE = "llm_api_base"
KEY_LLM_API_KEY = "llm_api_key"
KEY_LLM_MODEL = "llm_model"
KEY_LLM_TIMEOUT = "llm_timeout"
KEY_LLM_FALLBACK_API_BASE = "llm_fallback_api_base"
KEY_LLM_FALLBACK_API_KEY = "llm_fallback_api_key"  # 应与主密钥不同，否则回退无意义
KEY_LLM_FALLBACK_MODEL = "llm_fallback_model"
KEY_LLM_LOCAL_API_BASE = "llm_local_api_base"
KEY_LLM_LOCAL_API_KEY = "llm_local_api_key"
KEY_LLM_LOCAL_MODEL = "llm_local_model"
KEY_AI_CLOUD_ONLY = "ai_cloud_only"  # 云化模式：AI 全链路走云端（用户已同意数据上云），禁用本地腿路由
# local_first（用户决策 2026-09-02）：本地模型先行、云端兜底。默认 False = 维持现状
# （云端优先），将来部署了更强的本地模型后翻转为 True 即可，无需改代码。
# 与 cloud_only 互斥：local_first 优先级更高（两者同开时 local_first 胜出）。
KEY_AI_LOCAL_FIRST = "ai_local_first"
KEY_LLM_LOCAL_CODER_API_BASE = "llm_local_coder_api_base"
KEY_LLM_LOCAL_CODER_API_KEY = "llm_local_coder_api_key"
KEY_LLM_LOCAL_CODER_MODEL = "llm_local_coder_model"
KEY_WEATHER_CITY = "weather_city"
KEY_WEATHER_CITY_LABEL = "weather_city_label"
KEY_NEWS_AUTO_SYNC_ENABLED = "news_auto_sync_enabled"
KEY_NOTES_AUTO_SYNC_ENABLED = "notes_auto_sync_enabled"
KEY_TASKS_AUTO_SYNC_ENABLED = "tasks_auto_sync_enabled"
KEY_TRANSLATIONS_AUTO_SYNC_ENABLED = "translations_auto_sync_enabled"
KEY_VOCABULARY_AUTO_SYNC_ENABLED = "vocabulary_auto_sync_enabled"
KEY_ASSISTANT_AUTO_PREPARE_DOCKER = "assistant_auto_prepare_docker"
KEY_ASSISTANT_DOCKER_STACK_PORT = "assistant_docker_stack_port"
KEY_ASSISTANT_AUTONOMY_ENABLED = "assistant_autonomy_enabled"
KEY_ASSISTANT_AUTONOMY_TICK_SECONDS = "assistant_autonomy_tick_seconds"
KEY_PROACTIVE_ENABLED = "proactive_enabled"
KEY_ASSISTANT_NEWS_QUERY = "assistant_news_query"
KEY_ASSISTANT_DAILY_BRIEF_TIME = "assistant_daily_brief_time"
KEY_ASSISTANT_DAILY_REVIEW_TIME = "assistant_daily_review_time"
KEY_ASSISTANT_FOCUS_TOPICS = "assistant_focus_topics"
KEY_ASSISTANT_UPGRADE_CHANNEL = "assistant_upgrade_channel"
KEY_ASSISTANT_ALLOW_SELF_UPDATE = "assistant_allow_self_update"
KEY_ASSISTANT_CODEX_VERIFY_COMMANDS = "assistant_codex_verify_commands"
KEY_ASSISTANT_HIGH_RISK_REQUIRE_CONFIRM = "assistant_high_risk_require_confirm"
KEY_STARTUP_GUIDE_COMPLETED = "startup_guide_completed"
KEY_STARTUP_GUIDE_LAST_VERSION = "startup_guide_last_version"
KEY_BROWSER_ASSISTANT_BRIDGE_ENABLED = "browser_assistant_bridge_enabled"
KEY_BROWSER_ASSISTANT_BRIDGE_PORT = "browser_assistant_bridge_port"
KEY_BROWSER_ASSISTANT_TRUSTED_EXTENSION_IDS = "browser_assistant_trusted_extension_ids"
KEY_BROWSER_ASSISTANT_MOBILE_ACCESS_ENABLED = "browser_assistant_mobile_access_enabled"
KEY_RULE_CENTER_ENABLED = "rule_center_enabled"
KEY_AUDIT_TRAIL_ENABLED = "audit_trail_enabled"
KEY_AUDIT_TRAIL_RETENTION_DAYS = "audit_trail_retention_days"
KEY_PERMISSION_MATRIX_ENABLED = "permission_matrix_enabled"
KEY_WRITE_GUARD_DEGRADE_TO_READONLY = "write_guard_degrade_to_readonly"
KEY_WEB_SEARCH_PROVIDER = "web_search_provider"
KEY_WEB_SEARCH_TAVILY_API_KEY = "web_search_tavily_api_key"
KEY_WEB_SEARCH_SEARXNG_BASE_URL = "web_search_searxng_base_url"
KEY_LLM_PROVIDER_ROUTING = "llm_provider_routing"
KEY_LLM_SELECTED_PROVIDER = "llm_selected_provider"  # 当前选中的 provider 名称
KEY_LLM_SELECTED_MODEL = "llm_selected_model"  # 当前选中的模型名
KEY_PROVIDER_CONFIGS = "provider_configs"  # 多 provider 配置列表
KEY_PROVIDER_HEALTH_STATE = "provider_health_state"  # Provider 健康监控持久化状态 (B2)
KEY_PROVIDER_KEY_ROTATION = "provider_key_rotation"  # P2.5 API Key 轮换状态（dict[provider_name -> rotation_state]）
# 对外 LLM 中继网关（relay_gateway）：独立 API key（与 WS 主 token 隔离）+ new-api 风格模型别名映射表
KEY_RELAY_API_KEY = "relay_api_key"
KEY_RELAY_MODEL_MAP = "relay_model_map"  # dict[请求model名 -> "provider/model" 或 ""(走默认自动路由)]

# P2.1 本地音频转写（faster-whisper）
KEY_TRANSCRIPTION_MODEL_SIZE = "transcription_model_size"  # tiny/base/small/medium/large-v3
KEY_TRANSCRIPTION_DEVICE = "transcription_device"  # cpu / cuda
KEY_TRANSCRIPTION_COMPUTE_TYPE = "transcription_compute_type"  # int8 / int8_float16 / float16 / float32
KEY_TRANSCRIPTION_LANGUAGE = "transcription_language"  # None=自动检测 / "zh" / "en" / ...
KEY_TRANSCRIPTION_MODEL_CACHE_DIR = "transcription_model_cache_dir"  # 模型缓存目录覆盖

# P2.2 OCR / Tesseract 配置
KEY_OCR_TESSERACT_PATH = "ocr_tesseract_path"  # tesseract 二进制路径覆盖（空=从 PATH/常见路径探测）
KEY_OCR_LANGUAGE = "ocr_language"  # 语言包组合，如 "chi_sim+eng"（默认 chi_sim+eng）

# API 聚合层设置
KEY_AGGREGATION_ENABLED = "aggregation_enabled"
KEY_AGGREGATION_STRATEGY = "aggregation_strategy"
KEY_AGGREGATION_TIMEOUT_MS = "aggregation_timeout_ms"
KEY_AGGREGATION_MAX_PROVIDERS = "aggregation_max_providers"
KEY_AGGREGATION_PROVIDERS = "aggregation_providers"  # 参与聚合的 provider 列表
KEY_CRON_ENGINE = "cron_engine"
KEY_CRON_ENABLED = "cron_enabled"
KEY_MCP_ENABLED = "mcp_enabled"
KEY_MCP_SERVER_CONFIGS = "mcp_server_configs"
KEY_SUBAGENT_MAX_WORKERS = "subagent_max_workers"
KEY_RICH_MEDIA_MARKDOWN_ENABLED = "rich_media_markdown_enabled"
KEY_UI_THEME = "ui_theme"
KEY_TECH_WATCH_ENABLED = "tech_watch_enabled"
KEY_TECH_WATCH_JUDGE_DAY = "tech_watch_judge_day"
KEY_TECH_WATCH_MIN_INSIGHTS = "tech_watch_min_insights"
KEY_EXTENSION_ENABLED = "extension_enabled"
KEY_EXTENSION_TIER_OVERRIDES = "extension_tier_overrides"
KEY_SEARCH_MODE = "search_mode"
KEY_MOBILE_ENABLED = "mobile_enabled"
KEY_MOBILE_AUTOMATION_HUB_PORT = "mobile_automation_hub_port"
KEY_MOBILE_PAIRED_DEVICES = "mobile_paired_devices"
KEY_KB_ENABLED = "knowledge_base_enabled"
KEY_KB_EMBEDDING_MODEL = "knowledge_base_embedding_model"
KEY_KB_OFFLINE_MODE = "knowledge_base_offline_mode"
KEY_KB_CHUNK_TOKENS = "knowledge_base_chunk_tokens"
KEY_KB_CHUNK_OVERLAP = "knowledge_base_chunk_overlap"
KEY_KB_TOP_K = "knowledge_base_top_k"
KEY_KB_ALPHA = "knowledge_base_alpha"
KEY_FLOATING_BUBBLE_BG_MODE = "floating_bubble_bg_mode"
KEY_FLOATING_BUBBLE_INACTIVE_OPACITY = "floating_bubble_inactive_opacity"
KEY_FLOATING_BUBBLE_FULLSCREEN_HIDE = "floating_bubble_fullscreen_hide"
KEY_GLOBAL_SELECTION_ENABLED = "global_selection_enabled"
KEY_INBOX_AUTO_CLASSIFY = "inbox_auto_classify"
KEY_AI_AUTO_TAG_ENABLED = "ai_auto_tag_enabled"
KEY_AI_AUTO_TAG_THRESHOLD = "ai_auto_tag_threshold"
KEY_DAILY_SUMMARY_ENABLED = "daily_summary_enabled"
KEY_DAILY_SUMMARY_TIME = "daily_summary_time"
KEY_USER_INFO_SECTIONS = "user_info_sections"  # 用户自定义信息板块

# ── 执行内核（agent_kernel 配置面）──
# F 项（per-request 内核选择）与 H 项（MCP 工具策略）共用 settings 键 ``agent_kernel``。
# 该键为嵌套 dict：``type``/``dsh``/``claude_code``/``codex``/``auto``/``profiles`` 等
# 内核配置由 services.agent_gateway.factory 解析；以下两个子键是新增的"一等设置"。
KEY_AGENT_KERNEL = "agent_kernel"
# 子键（嵌套在 agent_kernel 下，非顶层 settings 键；不带 KEY_ 前缀避免被
# SETTINGS_DEFAULTS 顶层键一致性测试误判）
AGENT_KERNEL_PER_REQUEST_SELECTION = "per_request_selection"  # F：默认 True（开启，per-request 内核选择生效）
AGENT_KERNEL_MCP_TOOL_POLICY = "mcp_tool_policy"  # H：默认 {"mode":"allow_all"}（现状，全部放行）
DEFAULT_AGENT_KERNEL_SETTINGS: dict[str, Any] = {
    AGENT_KERNEL_PER_REQUEST_SELECTION: True,
    AGENT_KERNEL_MCP_TOOL_POLICY: {"mode": "allow_all"},
}

# ── 云同步（Cloudflare Worker，与手机端 cloudSync.ts 协议兼容）──
KEY_CLOUD_SYNC_ENABLED = "cloud_sync_enabled"
KEY_CLOUD_SYNC_WORKER_URL = "cloud_sync_worker_url"
KEY_CLOUD_SYNC_TOKEN = "cloud_sync_token"  # 用户密码，用于 PBKDF2 派生 + Bearer 认证
KEY_CLOUD_SYNC_REGISTER_SECRET = "cloud_sync_register_secret"  # Worker 注册预共享密钥（H1 修复）
KEY_CLOUD_SYNC_DEVICE_ID = "cloud_sync_device_id"  # 自动生成并持久化
KEY_CLOUD_SYNC_INTERVAL_SECONDS = "cloud_sync_interval_seconds"  # 同步间隔，默认 300s

# ── 外部内容集成（Readwise / Kindle / 浏览器书签）──
KEY_READWISE_ENABLED = "readwise_enabled"
KEY_READWISE_TOKEN = "readwise_token"
KEY_READWISE_LAST_SYNC_AT = "readwise_last_sync_at"
KEY_READWISE_SYNC_HIGHLIGHTS = "readwise_sync_highlights"
KEY_READWISE_SYNC_BOOKS = "readwise_sync_books"
KEY_KINDLE_IMPORT_DIR = "kindle_import_dir"
KEY_BROWSER_BOOKMARKS_ENABLED = "browser_bookmarks_enabled"
KEY_BROWSER_BOOKMARKS_IMPORT_DIR = "browser_bookmarks_import_dir"

# ── Kimi 自动同步 ──
KEY_KIMI_SYNC_ENABLED = "kimi_sync_enabled"
KEY_KIMI_SYNC_INTERVAL_SECONDS = "kimi_sync_interval_seconds"
KEY_KIMI_SYNC_MAX_CHATS = "kimi_sync_max_chats"
KEY_KIMI_BASE_URL = "kimi_base_url"
KEY_KIMI_AUTH_TOKEN = "kimi_auth_token"
KEY_KIMI_REFRESH_TOKEN = "kimi_refresh_token"
KEY_KIMI_DEVICE_ID = "kimi_device_id"
KEY_KIMI_SESSION_ID = "kimi_session_id"
KEY_KIMI_PLATFORM = "kimi_platform"
KEY_KIMI_VERSION = "kimi_version"
KEY_KIMI_LANGUAGE = "kimi_language"
KEY_KIMI_TRAFFIC_ID = "kimi_traffic_id"

# ── 版本 ──

SETTINGS_SCHEMA_VERSION_KEY = "settings_schema_version"
SETTINGS_SCHEMA_VERSION = 2  # P2-4: 从 1 升至 2（warn_only 安全收敛 + 白名单补充）

# ── P2.8 用户反馈学习闭环 ──
KEY_CHAT_FEEDBACK_ENABLED = "chat_feedback_enabled"  # 是否启用反馈记录（默认 True）
KEY_CHAT_FEEDBACK_STORAGE = "chat_feedback"  # local_storage 键：反馈记录列表
KEY_CHAT_FEEDBACK_PREFERENCE = "chat_feedback_preference"  # local_storage 键：聚合后的偏好摘要文本
KEY_CHAT_FEEDBACK_AGGREGATE_THRESHOLD = "chat_feedback_aggregate_threshold"  # 触发聚合的最小反馈数（默认 5）

# ── P2.9 通知推送通道 ──
KEY_NOTIFICATIONS_ENABLED = "notifications_enabled"  # 总开关（默认 True）
KEY_NOTIFICATIONS_STORAGE = "notifications"  # local_storage 键：通知列表
KEY_NOTIFICATION_PREFERENCES = "notification_preferences"  # dict[event_type, {ws, toast, min_priority}]
KEY_NOTIFICATION_SUBSCRIBED_EVENTS = "notification_subscribed_events"  # list[str]：哪些事件自动进通知管道

# P2-4: 废弃键标记（已迁移到 credential_store 或 provider_configs）
DEPRECATED_KEYS: frozenset[str] = frozenset(
    {
        "llm_api_key",
        "llm_fallback_api_key",
        "moonshot_api_key",
        "openai_api_key",
    }
)

# ── 默认 MCP Server 配置 ──

DEFAULT_MCP_SERVER_CONFIGS: list[dict[str, Any]] = [
    {
        "name": "filesystem",
        "description": "读/写/搜索本地文件系统",
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
        "enabled": False,
        "timeout_seconds": 30,
    },
    {
        "name": "sqlite",
        "description": "查询本地 SQLite 数据库",
        "command": ["npx", "-y", "@modelcontextprotocol/server-sqlite"],
        "enabled": False,
        "timeout_seconds": 30,
    },
    {
        "name": "web-fetch",
        "description": "获取网页 URL 并提取内容",
        "command": ["npx", "-y", "@modelcontextprotocol/server-fetch"],
        "enabled": False,
        "timeout_seconds": 30,
    },
    {
        "name": "git",
        "description": "Git 状态、日志、差异查询",
        "command": ["npx", "-y", "@modelcontextprotocol/server-git"],
        "enabled": False,
        "timeout_seconds": 30,
    },
    # B7 修复：calendar-memory 原本错误地指向 server-sqlite，改为 memory server
    {
        "name": "memory",
        "description": "日历与记忆存储读写 (JSON 文件)",
        "command": ["npx", "-y", "@modelcontextprotocol/server-memory"],
        "cwd": None,
        "enabled": False,
        "timeout_seconds": 30,
    },
]

# ── 默认值 ──

SETTINGS_DEFAULTS: dict[str, Any] = {
    KEY_YOUDAO_APP_KEY: "",
    KEY_YOUDAO_APP_SECRET: "",
    KEY_YOUDAO_OAUTH_REDIRECT_URI: "http://127.0.0.1:53682/callback/youdao",
    KEY_TODOIST_API_TOKEN: "",
    KEY_AUTOMATION_HUB_API_BASE: "http://127.0.0.1:8000",
    KEY_AUTOMATION_HUB_API_TOKEN: "",
    KEY_LLM_API_BASE: "",
    KEY_LLM_API_KEY: "",
    KEY_LLM_MODEL: "",
    KEY_LLM_TIMEOUT: 60,
    KEY_LLM_FALLBACK_API_BASE: "",
    KEY_LLM_FALLBACK_API_KEY: "",
    KEY_LLM_FALLBACK_MODEL: "",
    KEY_LLM_LOCAL_API_BASE: "",
    KEY_LLM_LOCAL_API_KEY: "",
    KEY_LLM_LOCAL_MODEL: "",
    KEY_LLM_LOCAL_CODER_API_BASE: "",
    KEY_LLM_LOCAL_CODER_API_KEY: "",
    KEY_LLM_LOCAL_CODER_MODEL: "",
    KEY_WEATHER_CITY: "Shanghai",
    KEY_WEATHER_CITY_LABEL: "上海",
    KEY_NEWS_AUTO_SYNC_ENABLED: False,
    KEY_NOTES_AUTO_SYNC_ENABLED: False,
    KEY_TASKS_AUTO_SYNC_ENABLED: False,
    KEY_TRANSLATIONS_AUTO_SYNC_ENABLED: False,
    KEY_VOCABULARY_AUTO_SYNC_ENABLED: False,
    KEY_ASSISTANT_AUTO_PREPARE_DOCKER: True,
    KEY_ASSISTANT_DOCKER_STACK_PORT: 8010,
    KEY_ASSISTANT_AUTONOMY_ENABLED: True,
    KEY_ASSISTANT_AUTONOMY_TICK_SECONDS: 60,
    KEY_PROACTIVE_ENABLED: True,
    KEY_TECH_WATCH_ENABLED: True,
    KEY_TECH_WATCH_JUDGE_DAY: "sun",
    KEY_TECH_WATCH_MIN_INSIGHTS: 5,
    KEY_ASSISTANT_NEWS_QUERY: "AI OR 生产力 OR 软件工程",
    KEY_ASSISTANT_DAILY_BRIEF_TIME: "08:30",
    KEY_ASSISTANT_DAILY_REVIEW_TIME: "22:30",
    KEY_ASSISTANT_FOCUS_TOPICS: ["AI", "自动化", "软件工程"],  # 字面量，每次构造独立
    KEY_ASSISTANT_UPGRADE_CHANNEL: "stable",
    KEY_ASSISTANT_ALLOW_SELF_UPDATE: False,
    KEY_ASSISTANT_CODEX_VERIFY_COMMANDS: [],
    KEY_ASSISTANT_HIGH_RISK_REQUIRE_CONFIRM: True,
    KEY_STARTUP_GUIDE_COMPLETED: False,
    KEY_STARTUP_GUIDE_LAST_VERSION: "",
    KEY_BROWSER_ASSISTANT_BRIDGE_ENABLED: True,
    KEY_BROWSER_ASSISTANT_BRIDGE_PORT: 18766,
    KEY_BROWSER_ASSISTANT_TRUSTED_EXTENSION_IDS: [],
    KEY_BROWSER_ASSISTANT_MOBILE_ACCESS_ENABLED: False,
    KEY_MOBILE_ENABLED: False,
    KEY_MOBILE_AUTOMATION_HUB_PORT: 8000,
    KEY_MOBILE_PAIRED_DEVICES: [],
    KEY_RULE_CENTER_ENABLED: True,
    KEY_AUDIT_TRAIL_ENABLED: True,
    KEY_AUDIT_TRAIL_RETENTION_DAYS: 90,
    KEY_PERMISSION_MATRIX_ENABLED: True,
    KEY_WRITE_GUARD_DEGRADE_TO_READONLY: True,
    KEY_WEB_SEARCH_PROVIDER: "",
    KEY_WEB_SEARCH_TAVILY_API_KEY: "",
    KEY_WEB_SEARCH_SEARXNG_BASE_URL: "",
    KEY_LLM_PROVIDER_ROUTING: "",
    KEY_LLM_SELECTED_PROVIDER: "",
    KEY_LLM_SELECTED_MODEL: "",
    KEY_PROVIDER_CONFIGS: [],
    KEY_PROVIDER_HEALTH_STATE: {},  # B2: provider 健康状态持久化（dict[name -> state]）
    KEY_PROVIDER_KEY_ROTATION: {},  # P2.5 API Key 轮换状态（dict[provider_name -> rotation_state]）
    KEY_RELAY_API_KEY: "",  # 对外中继网关 API key（与 WS 主 token 隔离）
    KEY_RELAY_MODEL_MAP: {},  # 对外中继网关模型别名映射表
    # P2.1 本地音频转写默认值
    KEY_TRANSCRIPTION_MODEL_SIZE: "base",
    KEY_TRANSCRIPTION_DEVICE: "cpu",
    KEY_TRANSCRIPTION_COMPUTE_TYPE: "int8",
    KEY_TRANSCRIPTION_LANGUAGE: "",  # 空字符串=自动检测
    KEY_TRANSCRIPTION_MODEL_CACHE_DIR: "",  # 空字符串=使用 faster-whisper 默认 (~/.cache/huggingface)
    # P2.2 OCR 默认值
    KEY_OCR_TESSERACT_PATH: "",  # 空字符串=自动探测 PATH/常见安装路径
    KEY_OCR_LANGUAGE: "chi_sim+eng",  # Tesseract 语言包组合
    # API 聚合层默认关闭，策略为竞速
    KEY_AGGREGATION_ENABLED: False,
    KEY_AGGREGATION_STRATEGY: "race",
    KEY_AGGREGATION_TIMEOUT_MS: 15_000,
    KEY_AGGREGATION_MAX_PROVIDERS: 3,
    KEY_AGGREGATION_PROVIDERS: [],
    KEY_CRON_ENGINE: "auto",
    KEY_CRON_ENABLED: True,
    KEY_MCP_ENABLED: False,
    # B2 修复：深拷贝避免全局可变默认值被污染
    KEY_MCP_SERVER_CONFIGS: copy.deepcopy(DEFAULT_MCP_SERVER_CONFIGS),
    KEY_SUBAGENT_MAX_WORKERS: 2,
    KEY_RICH_MEDIA_MARKDOWN_ENABLED: True,
    KEY_UI_THEME: "light",
    KEY_EXTENSION_ENABLED: True,
    KEY_EXTENSION_TIER_OVERRIDES: {},
    KEY_SEARCH_MODE: "hybrid",
    KEY_KB_ENABLED: True,
    KEY_KB_EMBEDDING_MODEL: "bge-small-zh-v1.5",
    KEY_KB_OFFLINE_MODE: False,
    KEY_KB_CHUNK_TOKENS: 512,
    KEY_KB_CHUNK_OVERLAP: 64,
    KEY_KB_TOP_K: 5,
    KEY_KB_ALPHA: 0.7,
    KEY_FLOATING_BUBBLE_BG_MODE: "translucent",
    KEY_FLOATING_BUBBLE_INACTIVE_OPACITY: 0.3,
    KEY_FLOATING_BUBBLE_FULLSCREEN_HIDE: True,
    KEY_GLOBAL_SELECTION_ENABLED: False,
    KEY_INBOX_AUTO_CLASSIFY: False,
    KEY_AI_AUTO_TAG_ENABLED: False,
    KEY_AI_AUTO_TAG_THRESHOLD: 0.7,
    KEY_DAILY_SUMMARY_ENABLED: False,
    KEY_DAILY_SUMMARY_TIME: "09:00",
    KEY_USER_INFO_SECTIONS: [],  # 用户自定义板块，每项 {id, name, emoji, keywords, enabled, is_default}
    # agent_kernel：默认 F/H 开关（per_request_selection=True / mcp_tool_policy=allow_all）
    KEY_AGENT_KERNEL: copy.deepcopy(DEFAULT_AGENT_KERNEL_SETTINGS),
    # 云同步默认关闭，需用户显式启用并配置
    KEY_CLOUD_SYNC_ENABLED: False,
    KEY_CLOUD_SYNC_WORKER_URL: "",
    KEY_CLOUD_SYNC_TOKEN: "",
    KEY_CLOUD_SYNC_REGISTER_SECRET: "",
    KEY_CLOUD_SYNC_DEVICE_ID: "",
    KEY_CLOUD_SYNC_INTERVAL_SECONDS: 300,
    # Kimi 自动同步默认关闭，需用户显式配置 token
    KEY_KIMI_SYNC_ENABLED: False,
    KEY_KIMI_SYNC_INTERVAL_SECONDS: 3600,
    KEY_KIMI_SYNC_MAX_CHATS: 50,
    KEY_KIMI_BASE_URL: "https://www.kimi.com",
    KEY_KIMI_AUTH_TOKEN: "",
    KEY_KIMI_REFRESH_TOKEN: "",
    KEY_KIMI_DEVICE_ID: "",
    KEY_KIMI_SESSION_ID: "",
    KEY_KIMI_PLATFORM: "web",
    KEY_KIMI_VERSION: "1.0.0",
    KEY_KIMI_LANGUAGE: "zh-CN",
    KEY_KIMI_TRAFFIC_ID: "",
    # 外部内容集成
    KEY_READWISE_ENABLED: False,
    KEY_READWISE_TOKEN: "",
    KEY_READWISE_LAST_SYNC_AT: 0,
    KEY_READWISE_SYNC_HIGHLIGHTS: True,
    KEY_READWISE_SYNC_BOOKS: True,
    KEY_KINDLE_IMPORT_DIR: "",
    KEY_BROWSER_BOOKMARKS_ENABLED: False,
    KEY_BROWSER_BOOKMARKS_IMPORT_DIR: "",
    # P2.8 用户反馈学习闭环
    KEY_CHAT_FEEDBACK_ENABLED: True,
    KEY_CHAT_FEEDBACK_STORAGE: [],  # local_storage 键占位（实际通过 load_json 访问）
    KEY_CHAT_FEEDBACK_PREFERENCE: "",
    KEY_CHAT_FEEDBACK_AGGREGATE_THRESHOLD: 5,
    # P2.9 通知推送通道
    KEY_NOTIFICATIONS_ENABLED: True,
    KEY_NOTIFICATIONS_STORAGE: [],  # local_storage 键占位（实际通过 load_json 访问）
    # 云化模式：AI 全链路走云端（用户已同意数据上云），禁用本地腿路由；默认关保持既有行为
    KEY_AI_CLOUD_ONLY: False,
    # local_first：本地模型先行（默认关 = 云端优先现状，见 KEY_AI_LOCAL_FIRST 注释）
    KEY_AI_LOCAL_FIRST: False,
    KEY_NOTIFICATION_PREFERENCES: {},  # dict[event_type, {ws, toast, min_priority}]
    KEY_NOTIFICATION_SUBSCRIBED_EVENTS: [
        "inbox.delivered",
        "proactive.triggered",
        "vocabulary.due_reminder",
        "task.due",
        "sync.failed",
        "job_hunter.new_jobs",
        "ai.usage_alert",
    ],
    # 2026-09-11：改为引用 action_guard 的单一事实来源。
    # 此前这里是独立手写的 38 项列表，与 DEFAULT_ALLOWED_ACTIONS（36 项）
    # 不同步——12 项只在此处、10 项只在彼处，导致设置界面展示的默认值
    # 与实际生效的准入默认值不一致（同一份「默认」有两个版本）。
    KEY_ACTION_WHITELIST: list(DEFAULT_ALLOWED_ACTIONS),
    # 显式禁用列表：allowed = (DEFAULT ∪ whitelist) − denylist
    KEY_ACTION_DENYLIST: [],
    # 应用启动白名单追加项（与 services.app_launch 默认集合并）
    KEY_APP_LAUNCH_EXTRA_ALLOWLIST: [],
    # B1（2026-09-11）：权限模型正交两维。默认值刻意等同现状，
    # 引入即零行为变化；无人值守场景可显式设 approval_policy=never。
    KEY_ASSISTANT_SANDBOX_LEVEL: DEFAULT_SANDBOX_LEVEL,
    KEY_ASSISTANT_APPROVAL_POLICY: DEFAULT_APPROVAL_POLICY,
    KEY_ACTION_RISK_WARN_ONLY: False,  # P0-4: 安全默认 — LLM 高危操作默认拦截，非仅警告
    KEY_ACTION_GUARD_PROFILE: "balanced",
    KEY_ACTION_RISK_ENFORCEMENT: "auto",
    KEY_COMMAND_WHITELIST: [
        "detect_nana_pet_process",
        "launch_nana_pet",
        "docker_version_check",
        "start_local_agent_backend",
        "start_docker_stack",
        "open_external_url",
    ],
}

# ── 类型集合 ──

BOOL_KEYS = {
    KEY_NEWS_AUTO_SYNC_ENABLED,
    KEY_NOTES_AUTO_SYNC_ENABLED,
    KEY_TASKS_AUTO_SYNC_ENABLED,
    KEY_TRANSLATIONS_AUTO_SYNC_ENABLED,
    KEY_VOCABULARY_AUTO_SYNC_ENABLED,
    KEY_ASSISTANT_AUTO_PREPARE_DOCKER,
    KEY_ACTION_RISK_WARN_ONLY,
    KEY_ASSISTANT_AUTONOMY_ENABLED,
    KEY_ASSISTANT_ALLOW_SELF_UPDATE,
    KEY_ASSISTANT_HIGH_RISK_REQUIRE_CONFIRM,
    KEY_STARTUP_GUIDE_COMPLETED,
    KEY_BROWSER_ASSISTANT_BRIDGE_ENABLED,
    KEY_BROWSER_ASSISTANT_MOBILE_ACCESS_ENABLED,
    KEY_MOBILE_ENABLED,
    KEY_RULE_CENTER_ENABLED,
    KEY_AUDIT_TRAIL_ENABLED,
    KEY_PERMISSION_MATRIX_ENABLED,
    KEY_WRITE_GUARD_DEGRADE_TO_READONLY,
    KEY_CRON_ENABLED,
    KEY_MCP_ENABLED,
    KEY_EXTENSION_ENABLED,
    KEY_RICH_MEDIA_MARKDOWN_ENABLED,
    KEY_KB_ENABLED,
    KEY_GLOBAL_SELECTION_ENABLED,
    KEY_INBOX_AUTO_CLASSIFY,
    KEY_AI_AUTO_TAG_ENABLED,
    KEY_DAILY_SUMMARY_ENABLED,
    KEY_CLOUD_SYNC_ENABLED,
    KEY_AGGREGATION_ENABLED,
    KEY_KIMI_SYNC_ENABLED,
    KEY_READWISE_ENABLED,
    KEY_READWISE_SYNC_HIGHLIGHTS,
    KEY_READWISE_SYNC_BOOKS,
    KEY_BROWSER_BOOKMARKS_ENABLED,
    KEY_CHAT_FEEDBACK_ENABLED,
    KEY_NOTIFICATIONS_ENABLED,
    KEY_AI_CLOUD_ONLY,
    KEY_AI_LOCAL_FIRST,
}

FLOAT_KEYS = {
    KEY_AI_AUTO_TAG_THRESHOLD,
}

INT_KEYS = {
    KEY_LLM_TIMEOUT,
    KEY_ASSISTANT_DOCKER_STACK_PORT,
    KEY_ASSISTANT_AUTONOMY_TICK_SECONDS,
    KEY_BROWSER_ASSISTANT_BRIDGE_PORT,
    KEY_MOBILE_AUTOMATION_HUB_PORT,
    KEY_AUDIT_TRAIL_RETENTION_DAYS,
    KEY_SUBAGENT_MAX_WORKERS,
    KEY_KB_CHUNK_TOKENS,
    KEY_KB_CHUNK_OVERLAP,
    KEY_KB_TOP_K,
    KEY_CLOUD_SYNC_INTERVAL_SECONDS,
    KEY_AGGREGATION_TIMEOUT_MS,
    KEY_AGGREGATION_MAX_PROVIDERS,
    KEY_KIMI_SYNC_INTERVAL_SECONDS,
    KEY_KIMI_SYNC_MAX_CHATS,
    KEY_READWISE_LAST_SYNC_AT,
    KEY_CHAT_FEEDBACK_AGGREGATE_THRESHOLD,
}

STRING_KEYS = {
    KEY_YOUDAO_APP_KEY,
    KEY_YOUDAO_APP_SECRET,
    KEY_YOUDAO_OAUTH_REDIRECT_URI,
    KEY_AUTOMATION_HUB_API_BASE,
    KEY_AUTOMATION_HUB_API_TOKEN,
    KEY_LLM_API_BASE,
    KEY_LLM_API_KEY,
    KEY_LLM_MODEL,
    KEY_LLM_FALLBACK_API_BASE,
    KEY_LLM_FALLBACK_API_KEY,
    KEY_LLM_FALLBACK_MODEL,
    KEY_LLM_LOCAL_API_BASE,
    KEY_LLM_LOCAL_API_KEY,
    KEY_LLM_LOCAL_MODEL,
    KEY_LLM_LOCAL_CODER_API_BASE,
    KEY_LLM_LOCAL_CODER_API_KEY,
    KEY_LLM_LOCAL_CODER_MODEL,
    KEY_WEATHER_CITY,
    KEY_WEATHER_CITY_LABEL,
    KEY_ASSISTANT_NEWS_QUERY,
    KEY_ASSISTANT_DAILY_BRIEF_TIME,
    KEY_ASSISTANT_DAILY_REVIEW_TIME,
    KEY_ASSISTANT_UPGRADE_CHANNEL,
    KEY_STARTUP_GUIDE_LAST_VERSION,
    KEY_ACTION_GUARD_PROFILE,
    KEY_ACTION_RISK_ENFORCEMENT,
    KEY_WEB_SEARCH_PROVIDER,
    KEY_WEB_SEARCH_TAVILY_API_KEY,
    KEY_WEB_SEARCH_SEARXNG_BASE_URL,
    KEY_LLM_PROVIDER_ROUTING,
    KEY_SEARCH_MODE,
    KEY_UI_THEME,
    KEY_KB_EMBEDDING_MODEL,
    KEY_TODOIST_API_TOKEN,
    KEY_LLM_SELECTED_PROVIDER,
    KEY_LLM_SELECTED_MODEL,
    KEY_FLOATING_BUBBLE_BG_MODE,
    KEY_DAILY_SUMMARY_TIME,
    KEY_CLOUD_SYNC_WORKER_URL,
    KEY_CLOUD_SYNC_TOKEN,
    KEY_CLOUD_SYNC_REGISTER_SECRET,
    KEY_CLOUD_SYNC_DEVICE_ID,
    KEY_AGGREGATION_STRATEGY,
    KEY_KIMI_BASE_URL,
    KEY_KIMI_AUTH_TOKEN,
    KEY_KIMI_DEVICE_ID,
    KEY_KIMI_SESSION_ID,
    KEY_KIMI_PLATFORM,
    KEY_KIMI_VERSION,
    KEY_KIMI_LANGUAGE,
    KEY_KIMI_TRAFFIC_ID,
    KEY_READWISE_TOKEN,
    KEY_KINDLE_IMPORT_DIR,
    KEY_BROWSER_BOOKMARKS_IMPORT_DIR,
    # P2.1 转写配置（非敏感，但需在 UI 中可编辑）
    KEY_TRANSCRIPTION_MODEL_SIZE,
    KEY_TRANSCRIPTION_DEVICE,
    KEY_TRANSCRIPTION_COMPUTE_TYPE,
    KEY_TRANSCRIPTION_LANGUAGE,
    KEY_TRANSCRIPTION_MODEL_CACHE_DIR,
    # P2.2 OCR 配置
    KEY_OCR_TESSERACT_PATH,
    KEY_OCR_LANGUAGE,
    # P2.8 反馈偏好摘要文本
    KEY_CHAT_FEEDBACK_PREFERENCE,
    # B1（2026-09-11）：权限模型正交两维
    KEY_ASSISTANT_SANDBOX_LEVEL,
    KEY_ASSISTANT_APPROVAL_POLICY,
    # 对外 LLM 中继网关鉴权 key（与 WS 主 token 隔离）
    KEY_RELAY_API_KEY,
}

# ── 应用启动白名单追加项（常量定义见文件头部；此处仅登记类型与默认值）──

LIST_KEYS = {
    KEY_ASSISTANT_CODEX_VERIFY_COMMANDS,
    KEY_ASSISTANT_FOCUS_TOPICS,
    KEY_BROWSER_ASSISTANT_TRUSTED_EXTENSION_IDS,
    KEY_MCP_SERVER_CONFIGS,
    KEY_MOBILE_PAIRED_DEVICES,
    KEY_USER_INFO_SECTIONS,
    KEY_AGGREGATION_PROVIDERS,
    KEY_CHAT_FEEDBACK_STORAGE,
    KEY_NOTIFICATIONS_STORAGE,
    KEY_NOTIFICATION_SUBSCRIBED_EVENTS,
    KEY_ACTION_DENYLIST,
    KEY_APP_LAUNCH_EXTRA_ALLOWLIST,
}

# dict 类型设置键：复杂结构化值（不需要逐字段迁移，仅保证类型与默认值兜底）
DICT_KEYS = {
    KEY_PROVIDER_HEALTH_STATE,  # dict[name -> state]
    KEY_PROVIDER_KEY_ROTATION,  # dict[provider_name -> rotation_state] (P2.5)
    KEY_EXTENSION_TIER_OVERRIDES,  # dict[extension_id -> tier]
    KEY_NOTIFICATION_PREFERENCES,  # dict[event_type -> {ws, toast, min_priority}] (P2.9)
    KEY_AGENT_KERNEL,  # dict 内核配置（F/H 项一等设置：per_request_selection + mcp_tool_policy）
    KEY_RELAY_MODEL_MAP,  # 对外中继模型重定向表 dict[请求model -> "provider/model" 或 ""]
}

# ── agent_kernel 校验（P0 生产接线：per_request_selection + mcp_tool_policy）──


def _is_bool_like(value: Any) -> bool:
    """是否可安全转换为 bool（True/False / 0/1 / 常见字符串）。"""
    if isinstance(value, bool):
        return True
    if isinstance(value, int) and value in (0, 1):
        return True
    if isinstance(value, str):
        return value.strip().lower() in (
            "true",
            "1",
            "yes",
            "on",
            "false",
            "0",
            "no",
            "off",
            "",
        )
    return False


def _is_str_list(value: Any) -> bool:
    """是否为字符串列表（白名单/黑名单结构）。"""
    return isinstance(value, (list, tuple, set)) and all(isinstance(v, str) for v in value)


def validate_mcp_tool_policy_value(raw: Any) -> tuple[bool, str | None]:
    """校验 ``agent_kernel.mcp_tool_policy`` 结构（H 项工具策略）。

    合法形态：None / dict（``mode∈{allow_all,restrict}``、``allow``/``deny`` 字符串列表、
    ``per_kernel`` dict）或可解析为 dict 的 JSON 字符串。坏值返回 ``(False, 原因)``，
    由 settings.set 拒绝写入。
    """
    if raw is None:
        return True, None
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return True, None
        try:
            import json as _json

            parsed = _json.loads(raw)
        except Exception as exc:
            return False, f"agent_kernel.mcp_tool_policy JSON 解析失败: {exc}"
        if not isinstance(parsed, dict):
            return False, "agent_kernel.mcp_tool_policy 必须是 JSON 对象"
        raw = parsed
    if not isinstance(raw, dict):
        return False, f"agent_kernel.mcp_tool_policy 期望 dict，实际 {type(raw).__name__}"
    mode = raw.get("mode", "allow_all")
    if not (isinstance(mode, str) and mode.strip().lower() in ("allow_all", "restrict")):
        return False, f"agent_kernel.mcp_tool_policy.mode 期望 allow_all|restrict，实际 {mode!r}"
    for sub in ("allow", "deny"):
        if sub in raw and not _is_str_list(raw[sub]):
            return False, f"agent_kernel.mcp_tool_policy.{sub} 期望字符串列表"
    per_kernel = raw.get("per_kernel")
    if per_kernel is not None and not isinstance(per_kernel, dict):
        return False, "agent_kernel.mcp_tool_policy.per_kernel 期望 dict"
    return True, None


def validate_agent_kernel_settings(value: Any) -> tuple[bool, str | None]:
    """校验 ``agent_kernel`` 配置对象（P0 生产接线）。

    严格校验新增的两个一等设置键：
    - ``per_request_selection``（F 项开关）：bool 或 bool 可转值
    - ``mcp_tool_policy``（H 项工具策略）：allow_all|restrict 策略结构

    其余子键（``type`` / ``dsh`` / ``claude_code`` / ``codex`` / ``auto`` / ``profiles``）
    保持宽松：工厂层（services.agent_gateway.factory）已有防御式兜底，未知子键允许
    （前向兼容）。
    """
    if value is None:
        return True, None
    if isinstance(value, str):
        try:
            import json as _json

            parsed = _json.loads(value)
        except Exception as exc:
            return False, f"agent_kernel JSON 解析失败: {exc}"
        if not isinstance(parsed, dict):
            return False, "agent_kernel 必须是 JSON 对象或 dict"
        value = parsed
    if not isinstance(value, dict):
        return False, f"agent_kernel 期望 dict，实际 {type(value).__name__}"
    if AGENT_KERNEL_PER_REQUEST_SELECTION in value:
        prs = value[AGENT_KERNEL_PER_REQUEST_SELECTION]
        if not _is_bool_like(prs):
            return False, f"agent_kernel.per_request_selection 期望 bool，实际 {prs!r}"
    if AGENT_KERNEL_MCP_TOOL_POLICY in value:
        ok, err = validate_mcp_tool_policy_value(value[AGENT_KERNEL_MCP_TOOL_POLICY])
        if not ok:
            return False, err
    return True, None
