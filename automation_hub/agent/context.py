"""
对话上下文管理

管理对话历史、工作目录、最近访问文件等上下文信息
"""

from typing import Dict, List, Any, Optional
from datetime import datetime
import json


class ConversationContext:
    """对话上下文管理器"""
    
    def __init__(self, user_id: str, session_id: str):
        """
        初始化对话上下文
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
        """
        self.user_id = user_id
        self.session_id = session_id
        self.history: List[Dict[str, Any]] = []
        self.working_directory: Optional[str] = None
        self.recent_files: List[str] = []
        self.project_type: Optional[str] = None
        self.preferences: Dict[str, Any] = {}
        self.variables: Dict[str, Any] = {}  # 临时变量存储
    
    def add_message(
        self,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        添加对话消息
        
        Args:
            role: 消息角色 (user/assistant/system)
            content: 消息内容
            metadata: 附加元数据
        """
        self.history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": metadata or {}
        })
        
        # 保持最近 20 条消息（避免上下文过长）
        if len(self.history) > 20:
            self.history = self.history[-20:]
    
    def get_context_summary(self, max_messages: int = 3) -> str:
        """
        获取上下文摘要（用于 LLM Prompt）
        
        Args:
            max_messages: 包含的最近消息数量
            
        Returns:
            格式化的上下文摘要
        """
        context_parts = []
        
        # 工作目录
        if self.working_directory:
            context_parts.append(f"工作目录: {self.working_directory}")
        
        # 项目类型
        if self.project_type:
            context_parts.append(f"项目类型: {self.project_type}")
        
        # 最近访问的文件
        if self.recent_files:
            files_str = ", ".join(self.recent_files[:5])
            context_parts.append(f"最近访问: {files_str}")
        
        # 用户偏好
        if self.preferences:
            prefs_str = ", ".join([f"{k}={v}" for k, v in self.preferences.items()])
            context_parts.append(f"用户偏好: {prefs_str}")
        
        # 最近对话
        if len(self.history) > 0:
            recent_messages = self.history[-max_messages:]
            recent_str = "\n".join([
                f"  {msg['role']}: {msg['content'][:100]}{'...' if len(msg['content']) > 100 else ''}"
                for msg in recent_messages
            ])
            context_parts.append(f"最近对话:\n{recent_str}")
        
        # 临时变量
        if self.variables:
            vars_str = ", ".join([f"{k}={v}" for k, v in self.variables.items()])
            context_parts.append(f"临时变量: {vars_str}")
        
        return "\n".join(context_parts) if context_parts else "（无上下文）"
    
    def update_working_context(
        self,
        cwd: Optional[str] = None,
        files: Optional[List[str]] = None,
        project_type: Optional[str] = None
    ) -> None:
        """
        更新工作上下文
        
        Args:
            cwd: 当前工作目录
            files: 最近访问的文件列表
            project_type: 项目类型（如: python, typescript, go）
        """
        if cwd:
            self.working_directory = cwd
        
        if files:
            # 合并并去重，保留最近10个
            all_files = files + self.recent_files
            seen = set()
            unique_files = []
            for f in all_files:
                if f not in seen:
                    seen.add(f)
                    unique_files.append(f)
            self.recent_files = unique_files[:10]
        
        if project_type:
            self.project_type = project_type
    
    def set_preference(self, key: str, value: Any) -> None:
        """设置用户偏好"""
        self.preferences[key] = value
    
    def get_preference(self, key: str, default: Any = None) -> Any:
        """获取用户偏好"""
        return self.preferences.get(key, default)
    
    def set_variable(self, key: str, value: Any) -> None:
        """设置临时变量"""
        self.variables[key] = value
    
    def get_variable(self, key: str, default: Any = None) -> Any:
        """获取临时变量"""
        return self.variables.get(key, default)
    
    def clear_variables(self) -> None:
        """清空临时变量"""
        self.variables.clear()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于序列化）"""
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "history": self.history,
            "working_directory": self.working_directory,
            "recent_files": self.recent_files,
            "project_type": self.project_type,
            "preferences": self.preferences,
            "variables": self.variables
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationContext":
        """从字典创建（用于反序列化）"""
        ctx = cls(
            user_id=data["user_id"],
            session_id=data["session_id"]
        )
        ctx.history = data.get("history", [])
        ctx.working_directory = data.get("working_directory")
        ctx.recent_files = data.get("recent_files", [])
        ctx.project_type = data.get("project_type")
        ctx.preferences = data.get("preferences", {})
        ctx.variables = data.get("variables", {})
        return ctx
    
    def __repr__(self) -> str:
        return (
            f"ConversationContext(user={self.user_id}, "
            f"session={self.session_id}, "
            f"messages={len(self.history)})"
        )
