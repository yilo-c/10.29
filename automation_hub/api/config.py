"""模块说明：应用配置定义 — 环境变量唯一入口。

==================== VALUES POLICY (策略冻结) ====================
编译期确定 (Compile-time / Docker image):
  APP_NAME, VERSION — 随镜像发布，不因部署环境改变。
  PYTHONUNBUFFERED, PYTHONDONTWRITEBYTECODE — Python 运行时行为。

运行期确定 (Runtime / Helm values or .env):
  所有 Path 类字段 (DATA_DIR, DATABASE_PATH 等) — 取决于挂载卷布局。
  服务绑定 (HOST, PORT) — 开发 127.0.0.1，生产由反向代理/Service 决定。
  功能开关 (SCHEDULER_ENABLED, WATCHER_ENABLED, AGENT_ENABLED 等) — 按环境开关。
  治理参数 (TOKEN_DEFAULT_TTL_DAYS, APPROVAL_TTL_SECONDS 等) — 按安全策略调整。
  连接地址 (REDIS_URL) — 依部署拓扑而定。

密钥/凭据 (Secrets, 永不进代码):
  SMTP_PASSWORD, TELEGRAM_TOKEN, 各类 API_KEY — 仅通过 .env / Secret 注入。
========================================================================

从旧版 ``automation-hub/config.py``（dataclass 版）合并的通知、
调度器与文件监控字段已纳入此处。旧版 ``config.py`` 仅保留供 REPL
输出格式等可变运行时状态使用。
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """应用配置类 — 所有环境变量在此处定义唯一入口。

    策略说明 (Values Policy):
    - [C] = Compile-time: 值在构建镜像时确定，不依赖部署环境
    - [R] = Runtime: 值在部署时通过 Helm values / .env / 环境变量注入
    - [S] = Secret: 仅通过安全渠道注入，永不进入代码或 values 文件
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra='allow'
    )

    # ═══════════════════════════════════════════════════════════
    # [C] 项目元信息 — 编译期确定，随镜像发布
    # ═══════════════════════════════════════════════════════════
    APP_NAME: str = "Automation Hub"
    VERSION: str = "0.1.0"

    # ═══════════════════════════════════════════════════════════
    # [R] 环境标识与服务绑定 — 运行期确定
    # ═══════════════════════════════════════════════════════════
    ENVIRONMENT: str = "development"

    # 服务绑定 — 生产环境建议：
    #   - 本地模式（桌面↔底座）：127.0.0.1
    #   - 远程通信：通过反向代理（nginx/caddy）暴露 HTTPS
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # CORS 允许来源 — 逗号分隔列表，如 "http://localhost:5173,https://siyi.example.com"
    # 留空则默认只允许 localhost；设为 "*" 时强制关闭 credentials（浏览器规范要求）
    CORS_ORIGINS: str = ""

    # ═══════════════════════════════════════════════════════════
    # [R] 目录与路径 — 运行期确定，取决于挂载卷布局
    # ═══════════════════════════════════════════════════════════
    DATA_DIR: Path = BASE_DIR / "data"
    WORKSPACE_DIR: Path = BASE_DIR / "workspace"
    SCRIPTS_DIR: Path = BASE_DIR / "scripts"

    # 数据库
    DATABASE_PATH: Path = DATA_DIR / "automation_hub.sqlite3"
    RUNS_DIR: Path = DATA_DIR / "runs"
    LOGS_DIR: Path = DATA_DIR / "logs"

    # ═══════════════════════════════════════════════════════════
    # [R] 产物清理治理
    # 默认关闭：仅当显式设置 >0 时，系统才会清理过期 run / proposal 产物目录。
    # ═══════════════════════════════════════════════════════════
    RUN_ARTIFACT_RETENTION_DAYS: int = 0
    PROPOSAL_ARTIFACT_RETENTION_DAYS: int = 0
    ARTIFACT_CLEANER_INTERVAL_SECONDS: int = 3600

    # ═══════════════════════════════════════════════════════════
    # [R] Redis / 任务队列 — 运行期确定
    # ═══════════════════════════════════════════════════════════
    REDIS_URL: str = "redis://localhost:6379/0"
    QUEUE_NAME: str = "default"

    # ═══════════════════════════════════════════════════════════
    # [R] Token 生命周期治理
    # 默认关闭：仅当显式设置 >0 时，/auth/tokens 在未提供 expires_at/ttl_days 时
    # 才会应用默认过期。
    # ═══════════════════════════════════════════════════════════
    TOKEN_DEFAULT_TTL_DAYS: int = 0

    # ═══════════════════════════════════════════════════════════
    # [R] 审批治理
    # 默认关闭：仅当显式设置 >0 时，系统会将超时 pending 审批标记为 expired
    # 并推进相关资源为终态。
    # ═══════════════════════════════════════════════════════════
    APPROVAL_TTL_SECONDS: int = 0
    APPROVAL_EXPIRER_INTERVAL_SECONDS: int = 60

    # ═══════════════════════════════════════════════════════════
    # [R] Agent — 运行期开关
    # 默认关闭：需要显式开启并配置鉴权 scopes
    # ═══════════════════════════════════════════════════════════
    AGENT_ENABLED: bool = False

    # ═══════════════════════════════════════════════════════════
    # [R] Docker Executor
    # 说明：当工具 executor=docker 时，worker 会优先尝试通过 docker CLI
    # 在隔离容器中运行。若 docker 不可用/失败，可选择是否允许回退到 host
    # 执行（开发态可开，生产建议关）。
    # ═══════════════════════════════════════════════════════════
    DOCKER_EXECUTOR_IMAGE: str = "automation-hub:latest"
    DOCKER_EXECUTOR_NETWORK: str = "none"
    DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK: bool = True
    DOCKER_EXECUTOR_SOCK_PATH: str = "/var/run/docker.sock"
    DOCKER_EXECUTOR_PREFLIGHT_PULL: bool = True
    DOCKER_EXECUTOR_PREFLIGHT_TIMEOUT_SECONDS: int = 5

    # ═══════════════════════════════════════════════════════════
    # [R/S] 通知（旧 config.py notification.* 合并至此）
    # SMTP_PASSWORD / TELEGRAM_TOKEN 属 [S] 类，仅通过 Secret 注入
    # ═══════════════════════════════════════════════════════════
    NOTIFICATION_ENABLED: bool = False
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""      # [S]
    SMTP_FROM: str = ""
    SMTP_TO: str = ""
    WEBHOOK_URL: str = ""
    TELEGRAM_TOKEN: str = ""     # [S]
    TELEGRAM_CHAT_ID: str = ""

    # ═══════════════════════════════════════════════════════════
    # [R] 调度器（旧 config.py scheduler.* 合并至此）
    # ═══════════════════════════════════════════════════════════
    SCHEDULER_ENABLED: bool = False
    SCHEDULER_TIMEZONE: str = "UTC"
    SCHEDULER_MAX_WORKERS: int = 3

    # ═══════════════════════════════════════════════════════════
    # [R] 文件监控（旧 config.py watcher.* 合并至此）
    # ═══════════════════════════════════════════════════════════
    WATCHER_ENABLED: bool = False
    WATCHER_PATHS: list[str] = []
    WATCHER_IGNORE_PATTERNS: list[str] = ["*.pyc", "__pycache__", ".git", ".venv", "node_modules"]

    def ensure_dirs(self) -> None:
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.RUNS_DIR.mkdir(parents=True, exist_ok=True)
        self.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)


# 创建全局配置实例
settings = Settings()