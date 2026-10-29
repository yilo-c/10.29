"""
文件监控系统

基于watchdog实现文件/目录变化监控，自动触发任务

.. deprecated:: 2026-08-02 (P0-1)
    ``FileWatcherService`` 为独立守护进程脚本（``__main__`` 块），未被 automation_hub
    FastAPI 主应用或桌面端运行进程引用。注意：本模块与桌面端
    ``思忆集app/core/file_watcher.py``（``FileWatcher`` 类，已接入 AppContext）是不同实现。
    定时/触发型任务统一入口已收敛到 ``思忆集app/services/cron_service.py``。
    新代码请勿使用本模块。
"""

import warnings

warnings.warn(
    "automation_hub.file_watcher is deprecated (P0-1): use 思忆集app/services/cron_service.py "
    "for scheduled tasks, or 思忆集app/core/file_watcher.py for desktop file watching. "
    "This standalone module is not connected to any running process.",
    DeprecationWarning,
    stacklevel=2,
)

import time
import sqlite3
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import logging

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

logger = logging.getLogger(__name__)


@dataclass
class WatchRule:
    """监控规则"""
    id: str
    name: str
    path: str
    tool_id: str
    event_types: List[str]  # created, modified, deleted, moved
    pattern: str = "*"
    args_json: str = "{}"
    enabled: bool = True


class AutomationEventHandler(FileSystemEventHandler):
    """自动化事件处理器"""
    
    def __init__(self, rule: WatchRule, db_path: str):
        """
        初始化处理器
        
        Args:
            rule: 监控规则
            db_path: 数据库路径
        """
        super().__init__()
        self.rule = rule
        self.db_path = db_path
    
    def on_any_event(self, event: FileSystemEvent):
        """处理任何文件系统事件"""
        # 过滤事件类型
        event_type_map = {
            'created': 'created',
            'modified': 'modified',
            'deleted': 'deleted',
            'moved': 'moved'
        }
        
        event_type = event_type_map.get(event.event_type)
        
        if event_type not in self.rule.event_types:
            return
        
        # 检查路径模式
        if self.rule.pattern != "*":
            from fnmatch import fnmatch
            if not fnmatch(event.src_path, self.rule.pattern):
                return
        
        logger.info(f"文件事件触发: {event.event_type} - {event.src_path}")
        
        # 执行工具
        self._trigger_tool(event)
    
    def _trigger_tool(self, event: FileSystemEvent):
        """触发工具执行"""
        try:
            from automation_hub.simple_executor import SimpleExecutor
            
            # 解析参数
            args = json.loads(self.rule.args_json)
            
            # 添加事件信息到参数
            args['_event_type'] = event.event_type
            args['_event_path'] = event.src_path
            
            if event.event_type == 'moved':
                args['_event_dest_path'] = event.dest_path
            
            # 执行工具
            executor = SimpleExecutor(self.db_path)
            result = executor.execute_tool(
                tool_id=self.rule.tool_id,
                args=args,
                user_id=f"watcher:{self.rule.id}"
            )
            
            logger.info(f"工具执行完成: {self.rule.tool_id}, 成功: {result.get('success')}")
        
        except Exception as e:
            logger.exception(f"触发工具失败: {self.rule.tool_id}")


class FileWatcherService:
    """文件监控服务"""
    
    def __init__(self, db_path: str):
        """
        初始化监控服务
        
        Args:
            db_path: 数据库路径
        """
        self.db_path = db_path
        self.observer = Observer()
        self.handlers = {}
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS watch_rules (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                tool_id TEXT NOT NULL,
                event_types TEXT NOT NULL,
                pattern TEXT DEFAULT '*',
                args_json TEXT DEFAULT '{}',
                enabled INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (tool_id) REFERENCES tools(id)
            )
        """)
        
        conn.commit()
        conn.close()
    
    def start(self):
        """启动监控服务"""
        # 加载所有启用的规则
        rules = self.list_rules(enabled_only=True)
        
        for rule in rules:
            self._start_watch(rule)
        
        self.observer.start()
        logger.info(f"文件监控服务已启动，监控 {len(rules)} 个路径")
    
    def stop(self):
        """停止监控服务"""
        self.observer.stop()
        self.observer.join()
        logger.info("文件监控服务已停止")
    
    def _start_watch(self, rule: WatchRule):
        """启动单个监控规则"""
        path = Path(rule.path)
        
        if not path.exists():
            logger.warning(f"路径不存在，跳过监控: {rule.path}")
            return
        
        handler = AutomationEventHandler(rule, self.db_path)
        
        self.observer.schedule(
            handler,
            str(path),
            recursive=True
        )
        
        self.handlers[rule.id] = handler
        logger.info(f"已启动监控: {rule.name} -> {rule.path}")
    
    def create_rule(
        self,
        name: str,
        path: str,
        tool_id: str,
        event_types: List[str],
        pattern: str = "*",
        args: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        创建监控规则
        
        Args:
            name: 规则名称
            path: 监控路径
            tool_id: 触发的工具ID
            event_types: 事件类型列表 (created, modified, deleted, moved)
            pattern: 文件匹配模式
            args: 工具参数
            
        Returns:
            规则ID
        """
        import uuid
        from datetime import datetime
        
        rule_id = str(uuid.uuid4())
        args_json = json.dumps(args or {})
        event_types_json = json.dumps(event_types)
        now = datetime.utcnow().isoformat()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO watch_rules
            (id, name, path, tool_id, event_types, pattern, args_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (rule_id, name, path, tool_id, event_types_json, pattern, args_json, now))
        
        conn.commit()
        conn.close()
        
        logger.info(f"已创建监控规则: {name} ({rule_id})")
        
        return rule_id
    
    def delete_rule(self, rule_id: str):
        """删除监控规则"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM watch_rules WHERE id = ?", (rule_id,))
        
        conn.commit()
        conn.close()
        
        # 停止监控
        if rule_id in self.handlers:
            del self.handlers[rule_id]
        
        logger.info(f"已删除监控规则: {rule_id}")
    
    def enable_rule(self, rule_id: str):
        """启用监控规则"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE watch_rules
            SET enabled = 1
            WHERE id = ?
        """, (rule_id,))
        
        conn.commit()
        
        # 获取规则
        cursor.execute("SELECT * FROM watch_rules WHERE id = ?", (rule_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row and self.observer.is_alive():
            rule = self._row_to_rule(row)
            self._start_watch(rule)
        
        logger.info(f"已启用监控规则: {rule_id}")
    
    def disable_rule(self, rule_id: str):
        """禁用监控规则"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE watch_rules
            SET enabled = 0
            WHERE id = ?
        """, (rule_id,))
        
        conn.commit()
        conn.close()
        
        if rule_id in self.handlers:
            del self.handlers[rule_id]
        
        logger.info(f"已禁用监控规则: {rule_id}")
    
    def list_rules(self, enabled_only: bool = False) -> List[WatchRule]:
        """列出所有监控规则"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        query = "SELECT * FROM watch_rules"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY created_at DESC"
        
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        
        return [self._row_to_rule(row) for row in rows]
    
    def _row_to_rule(self, row) -> WatchRule:
        """将数据库行转换为WatchRule对象"""
        return WatchRule(
            id=row[0],
            name=row[1],
            path=row[2],
            tool_id=row[3],
            event_types=json.loads(row[4]),
            pattern=row[5],
            args_json=row[6],
            enabled=bool(row[7])
        )


def create_example_rules(db_path: str):
    """创建示例监控规则"""
    watcher = FileWatcherService(db_path)
    
    # 监控Python文件变化，运行测试
    watcher.create_rule(
        name="Python文件变化时运行测试",
        path="./automation-hub",
        tool_id="run_pytest",
        event_types=["modified"],
        pattern="*.py",
        args={"path": "tests/"}
    )
    
    # 监控配置文件变化
    watcher.create_rule(
        name="配置文件变化通知",
        path=".",
        tool_id="code_search",  # 示例，实际应该是通知工具
        event_types=["modified"],
        pattern="config.yaml"
    )
    
    print("✅ 已创建示例监控规则")


def run_watcher_daemon(db_path: str):
    """运行监控守护进程"""
    watcher = FileWatcherService(db_path)
    
    try:
        watcher.start()
        
        print("文件监控服务运行中...")
        print("按 Ctrl+C 停止")
        
        while True:
            time.sleep(1)
    
    except KeyboardInterrupt:
        print("\n正在停止...")
        watcher.stop()
        print("已停止")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "examples":
        create_example_rules("data/automation_hub.sqlite3")
    elif len(sys.argv) > 1 and sys.argv[1] == "daemon":
        run_watcher_daemon("data/automation_hub.sqlite3")
    else:
        print("用法:")
        print("  python file_watcher.py examples  - 创建示例规则")
        print("  python file_watcher.py daemon    - 运行监控守护进程")
