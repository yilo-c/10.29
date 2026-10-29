"""
配置管理模块

支持从YAML配置文件和环境变量读取配置

.. deprecated::
    通知、调度器、文件监控字段已合并至 ``api/config.py``（pydantic-settings）。
    本模块仅保留供 REPL 的输出格式（output.*）等可变运行时状态使用。
    新代码请优先使用 ``from api.config import settings``。

    WARNING: 本模块中的 notification / scheduler / watcher 字段与
    api/config.py 存在重复定义，已不再同步更新。所有新增 env var
    只应在 api/config.py 中定义（参考 .env.example）。
"""

import os
import yaml
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, field, asdict


@dataclass
class DatabaseConfig:
    """数据库配置"""
    path: str = "data/automation_hub.sqlite3"
    backup_enabled: bool = True
    backup_path: str = "data/backups"
    backup_retention_days: int = 30


@dataclass
class APIConfig:
    """API配置"""
    host: str = "localhost"
    port: int = 8000
    base_url: str = "http://localhost:8000"
    timeout: int = 30


@dataclass
class SchedulerConfig:
    """调度器配置"""
    enabled: bool = False
    timezone: str = "UTC"
    max_workers: int = 3


@dataclass
class NotificationConfig:
    """通知配置"""
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: list = field(default_factory=list)
    webhook_url: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""


@dataclass
class WatcherConfig:
    """文件监控配置"""
    enabled: bool = False
    paths: list = field(default_factory=list)
    ignore_patterns: list = field(default_factory=lambda: [
        "*.pyc", "__pycache__", ".git", ".venv", "node_modules"
    ])


@dataclass
class OutputConfig:
    """输出配置"""
    format: str = "table"  # table, json, yaml
    color: bool = True
    verbose: bool = False


@dataclass
class Config:
    """全局配置"""
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    api: APIConfig = field(default_factory=APIConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    notification: NotificationConfig = field(default_factory=NotificationConfig)
    watcher: WatcherConfig = field(default_factory=WatcherConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    
    @classmethod
    def get_config_path(cls) -> Path:
        """获取配置文件路径"""
        # 优先级：
        # 1. 环境变量 AUTOMATION_HUB_CONFIG
        # 2. ~/.automation-hub/config.yaml
        # 3. ./config.yaml
        
        env_path = os.getenv("AUTOMATION_HUB_CONFIG")
        if env_path:
            return Path(env_path)
        
        home_config = Path.home() / ".automation-hub" / "config.yaml"
        if home_config.exists():
            return home_config
        
        local_config = Path("config.yaml")
        if local_config.exists():
            return local_config
        
        # 默认返回home配置路径（即使不存在）
        return home_config
    
    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "Config":
        """
        加载配置
        
        Args:
            config_path: 配置文件路径，如果为None则自动查找
            
        Returns:
            Config对象
        """
        if config_path is None:
            config_path = cls.get_config_path()
        
        config = cls()
        
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            
            # 递归更新配置
            if 'database' in data:
                config.database = DatabaseConfig(**data['database'])
            if 'api' in data:
                config.api = APIConfig(**data['api'])
            if 'scheduler' in data:
                config.scheduler = SchedulerConfig(**data['scheduler'])
            if 'notification' in data:
                config.notification = NotificationConfig(**data['notification'])
            if 'watcher' in data:
                config.watcher = WatcherConfig(**data['watcher'])
            if 'output' in data:
                config.output = OutputConfig(**data['output'])
        
        # 环境变量覆盖
        config._load_from_env()
        
        return config
    
    def _load_from_env(self):
        """从环境变量加载配置"""
        # 数据库
        if os.getenv("DB_PATH"):
            self.database.path = os.getenv("DB_PATH")
        
        # API
        if os.getenv("API_HOST"):
            self.api.host = os.getenv("API_HOST")
        if os.getenv("API_PORT"):
            self.api.port = int(os.getenv("API_PORT"))
        
        # 通知
        if os.getenv("SMTP_HOST"):
            self.notification.smtp_host = os.getenv("SMTP_HOST")
        if os.getenv("SMTP_PORT"):
            try:
                self.notification.smtp_port = int(os.getenv("SMTP_PORT"))
            except Exception:
                pass
        if os.getenv("SMTP_USER"):
            self.notification.smtp_user = os.getenv("SMTP_USER")
        if os.getenv("SMTP_PASSWORD"):
            self.notification.smtp_password = os.getenv("SMTP_PASSWORD")
        if os.getenv("SMTP_FROM"):
            self.notification.smtp_from = os.getenv("SMTP_FROM")
        if os.getenv("SMTP_TO"):
            raw = os.getenv("SMTP_TO")
            # 支持逗号/分号分隔
            parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
            self.notification.smtp_to = parts
        if os.getenv("WEBHOOK_URL"):
            self.notification.webhook_url = os.getenv("WEBHOOK_URL")
        if os.getenv("TELEGRAM_TOKEN"):
            self.notification.telegram_token = os.getenv("TELEGRAM_TOKEN")
        if os.getenv("TELEGRAM_CHAT_ID"):
            self.notification.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    def save(self, config_path: Optional[Path] = None):
        """
        保存配置到文件
        
        Args:
            config_path: 配置文件路径，如果为None则使用默认路径
        """
        if config_path is None:
            config_path = self.get_config_path()
        
        # 确保目录存在
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 转换为字典
        data = {
            'database': asdict(self.database),
            'api': asdict(self.api),
            'scheduler': asdict(self.scheduler),
            'notification': asdict(self.notification),
            'watcher': asdict(self.watcher),
            'output': asdict(self.output)
        }
        
        # 保存
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'database': asdict(self.database),
            'api': asdict(self.api),
            'scheduler': asdict(self.scheduler),
            'notification': asdict(self.notification),
            'watcher': asdict(self.watcher),
            'output': asdict(self.output)
        }


# 全局配置实例
_config: Optional[Config] = None


def get_config() -> Config:
    """获取全局配置实例"""
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def reload_config():
    """重新加载配置"""
    global _config
    _config = Config.load()


def init_config():
    """初始化配置文件（创建默认配置）"""
    config = Config()
    config_path = Config.get_config_path()
    
    if config_path.exists():
        print(f"配置文件已存在: {config_path}")
        return False
    
    config.save(config_path)
    print(f"✅ 配置文件已创建: {config_path}")
    
    # 创建示例配置
    print("\n示例配置已生成，你可以编辑以下字段：")
    print("  - database.path: 数据库路径")
    print("  - api.host/port: API服务地址")
    print("  - scheduler.enabled: 启用定时任务")
    print("  - notification: 配置通知服务")
    print("  - watcher: 配置文件监控")
    
    return True


if __name__ == "__main__":
    # 测试
    init_config()
    
    config = get_config()
    print(f"\n当前配置:")
    print(f"  数据库: {config.database.path}")
    print(f"  API: {config.api.base_url}")
    print(f"  调度器: {'启用' if config.scheduler.enabled else '禁用'}")
